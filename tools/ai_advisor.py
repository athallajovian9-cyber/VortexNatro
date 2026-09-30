#!/usr/bin/env python3
"""
ai_advisor.py - the AI as SUGGESTER, colour thresholds as EXECUTOR.

WHY THIS SHAPE
    Detection in a macro loop is a threshold problem, not an inference problem.
    "Find #00FF33 in this rectangle" should cost one StretchBlt and a loop, run
    every frame, work offline, and give the same answer every time. A model call
    costs hundreds of milliseconds, may vary between identical frames, and dies
    with the connection.

    But a threshold has to be TOLD the right numbers, and that is where a model
    is genuinely better than a human with a colour picker: it can look at a
    frame and say "the marker is that green, roughly x520..560, tolerance about
    40 per channel - and by the way your region is clipping it".

    So:
        LOOP      -> thresholds, from a profile on disk.  Fast, local, exact.
        AUTHORING -> the model proposes those thresholds and the region.
        FAILURE   -> the model reads the frame that failed and says what changed.

    The model never blocks the loop, and the loop never waits on a network call.

USAGE
    # propose thresholds from a screenshot
    python ai_advisor.py suggest --image frame.png --target "the sprinkler marker"

    # diagnose a failed detection
    python ai_advisor.py diagnose --image fail.png --expected "neon green marker" \
        --profile profiles/sprinkler.ini

    Writes profiles/<name>.ini, which the macro reads. Nothing is applied until
    the macro reloads the profile.
"""
import argparse
import base64
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROFILES = ROOT / "profiles"

# The router Hermes/9router serves vision on. Confirmed working earlier:
# gemini-3.5-flash-lite, 1M context, reads an image correctly.
ROUTER = "http://localhost:20128/v1"
MODEL = "gemini/gemini-3.5-flash-lite"


def _key() -> str:
    """Key from the Hermes env, if present. Never hardcoded."""
    env = Path(os.environ.get("LOCALAPPDATA", "")) / "hermes" / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("HERMES_CUSTOM_LOCALHOST_20128_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return os.environ.get("ROUTER_KEY", "")


def ask(image_path: Path, prompt: str, max_tokens: int = 700) -> str:
    """Send the image + prompt, return the model's text.

    The router streams SSE (`data: {...}` chunks), so the body is reassembled
    from the deltas rather than parsed as one JSON object - parsing it whole
    fails with 'Expecting value: line 1 column 1'.
    """
    b64 = base64.b64encode(image_path.read_bytes()).decode()
    body = json.dumps({
        "model": MODEL,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
        ]}],
    }).encode()

    req = urllib.request.Request(
        f"{ROUTER}/chat/completions", data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {_key()}"})
    with urllib.request.urlopen(req, timeout=120) as r:
        raw = r.read().decode("utf-8", "replace")

    out = []
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            obj = json.loads(payload)
        except Exception:
            continue
        for ch in obj.get("choices", []):
            c = (ch.get("delta") or {}).get("content")
            if c:
                out.append(c)
    return "".join(out)


def verify_proposal(image_path: Path, data: dict, trial_tol: int = None):
    """Check the model's OWN proposal against the frame it came from.

    This is the step that makes an AI suggestion safe to apply. A language model
    can describe a button fluently and be wrong about both its colour and its
    rectangle - measured: asked for a #17263A button, it answered #111111 and
    gave the wrong region. Writing that profile would produce a detector that
    silently never matches.

    So the proposal is TESTED: does the stated colour actually appear inside the
    stated region, at the stated tolerance? Returns (ok, detail).
    """
    try:
        from PIL import Image
    except Exception:
        return (False, "Pillow not available - cannot verify, refusing to save")

    rgb = data.get("rgb")
    if not rgb:
        return (False, "no colour proposed")
    tol = trial_tol if trial_tol is not None else int(data.get("tolerance", 40))

    im = Image.open(image_path).convert("RGB")
    W, H = im.size
    x1, y1, x2, y2 = (int(data.get(k, 0)) for k in ("x1", "y1", "x2", "y2"))
    # Clamp BOTH ends into the image, then reject an empty region. Clamping only
    # one end lets a region that starts outside the frame (measured: y528 in a
    # 513-tall image) pass through and index past the end of the pixel buffer.
    x1 = max(0, min(W - 1, x1))
    x2 = max(0, min(W - 1, x2))
    y1 = max(0, min(H - 1, y1))
    y2 = max(0, min(H - 1, y2))
    x1, x2 = sorted((x1, x2))
    y1, y2 = sorted((y1, y2))
    if x2 - x1 < 2 or y2 - y1 < 2:
        return (False, f"degenerate or out-of-frame region ({x1},{y1})-({x2},{y2}) "
                       f"in a {W}x{H} image - the proposal does not fit the frame")

    r0, g0, b0 = rgb
    hits = 0
    total = 0
    for py in range(y1, y2):
        for px in range(x1, x2):
            r, g, b = im.getpixel((px, py))
            total += 1
            if abs(r - r0) <= tol and abs(g - g0) <= tol and abs(b - b0) <= tol:
                hits += 1
    frac = hits / total if total else 0.0

    # A target worth detecting fills a meaningful part of its own region. Under
    # 1% means the region and the colour do not belong together.
    if frac < 0.01:
        return (False, f"colour {rgb} found in only {frac*100:.2f}% of the proposed "
                       f"region - region and colour disagree")
    return (True, f"{frac*100:.1f}% of the proposed region matches {rgb} "
                  f"at tolerance {tol}")


SUGGEST_PROMPT = """You are calibrating a colour-threshold detector for a game macro.

Look at the image and find: {target}

Answer with ONLY a JSON object, no prose, no markdown fence:

{{
  "found": true or false,
  "x1": int, "y1": int, "x2": int, "y2": int,   // bounding box of the target
  "rgb": [r, g, b],                             // its most typical colour
  "tolerance": int,                             // per-channel tolerance, 10-80
  "note": "one short sentence on anything that would break a naive threshold"
}}

Rules:
- Sample the colour from the MIDDLE of the target, not an edge or a highlight.
- Tolerance is how far a pixel may differ per channel and still count. Raise it
  for a gradient or glow, lower it for a flat UI colour.
- If the target is not visible, set found to false and still give your best guess
  at what it WOULD look like.
- If the image is too low-resolution or the target too small to sample honestly,
  say so in "note" - a wrong threshold is worse than none."""


DIAGNOSE_PROMPT = """A colour-threshold detector just failed. You are diagnosing why.

It was looking for: {expected}
Its configured threshold was: {profile}

Look at the attached frame - this is what the macro saw when it failed.

Answer with ONLY a JSON object:

{{
  "target_present": true or false,
  "why_it_failed": "one of: absent, occluded, colour_changed, region_wrong, too_small, different_ui",
  "suggested_fix": "one short sentence, concrete",
  "rgb": [r, g, b] or null,
  "region": [x1, y1, x2, y2] or null
}}"""


def cmd_suggest(args):
    target = args.target
    text = ask(args.image, SUGGEST_PROMPT.format(target=target))
    data = _extract_json(text)
    if not data:
        print("  the model did not return usable JSON. Raw reply:\n")
        print("  " + text.strip()[:900])
        return 1

    print(f"  target      : {target}")
    print(f"  found       : {data.get('found')}")
    if data.get("found"):
        print(f"  region      : ({data.get('x1')},{data.get('y1')}) - ({data.get('x2')},{data.get('y2')})")
    rgb = data.get("rgb")
    print(f"  rgb         : {rgb}")
    print(f"  tolerance   : {data.get('tolerance')}")
    print(f"  note        : {data.get('note','')}")

    if not data.get("found") or not rgb:
        print("\n  NOT written to a profile: the model could not sample the target.")
        print("  A threshold guessed from an image where the target is absent is a")
        print("  fabricated constant, which is the failure this whole design avoids.")
        return 1

    ok, detail = verify_proposal(args.image, data)
    print(f"  VERIFY      : {'PASS' if ok else 'FAIL'} - {detail}")
    if not ok:
        print("  NOT saved. The model's proposal did not survive testing against")
        print("  the frame it came from, so it would have shipped a detector that")
        print("  never matches. Try a tighter --target description, or a frame")
        print("  where the target is larger and unobstructed.")
        return 1

    PROFILES.mkdir(exist_ok=True)
    out = PROFILES / f"{args.name}.ini"
    out.write_text(
        f"; Generated by ai_advisor.py - AI-proposed, macro-executed.\n"
        f"; Target: {target}\n"
        f"; Source image: {args.image.name}\n"
        f";\n"
        f"; The model proposed these; the macro only ever APPLIES them. Re-run\n"
        f"; `ai_advisor.py diagnose` on a failed frame if detection starts missing.\n"
        f"\n[detect]\n"
        f"rgb={','.join(str(v) for v in rgb)}\n"
        f"tolerance={int(data.get('tolerance', 40))}\n"
        f"x1={int(data.get('x1', 0))}\n"
        f"y1={int(data.get('y1', 0))}\n"
        f"x2={int(data.get('x2', 0))}\n"
        f"y2={int(data.get('y2', 0))}\n"
        f"; the model's own caveat, kept so the number is not treated as gospel\n"
        f"note={data.get('note','')}\n",
        encoding="utf-8")
    print(f"\n  profile written: {out}")
    print("  the macro reads this; reload it to apply.")
    return 0


def cmd_diagnose(args):
    prof = args.profile.read_text(encoding="utf-8") if args.profile and args.profile.exists() else "(none)"
    text = ask(args.image, DIAGNOSE_PROMPT.format(expected=args.expected, profile=prof.strip()))
    data = _extract_json(text)
    if not data:
        print("  no usable JSON. Raw reply:\n")
        print("  " + text.strip()[:900])
        return 1
    print(f"  target present : {data.get('target_present')}")
    print(f"  why it failed  : {data.get('why_it_failed')}")
    print(f"  suggested fix  : {data.get('suggested_fix')}")
    if data.get("rgb"):
        print(f"  colour now     : {data['rgb']}")
    if data.get("region"):
        print(f"  region now     : {data['region']}")
    return 0


def _extract_json(text: str):
    """The model sometimes wraps JSON in prose or a fence. Take the object."""
    if not text:
        return None
    t = text.strip()
    t = re.sub(r"^```(?:json)?|```$", "", t, flags=re.M).strip()
    try:
        return json.loads(t)
    except Exception:
        m = re.search(r"\{.*\}", t, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return None
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("suggest", help="propose thresholds from a screenshot")
    s.add_argument("--image", type=Path, required=True)
    s.add_argument("--target", required=True, help="what to find, in words")
    s.add_argument("--name", default="detector", help="profile name to write")
    s.set_defaults(fn=cmd_suggest)

    d = sub.add_parser("diagnose", help="why did detection fail on this frame")
    d.add_argument("--image", type=Path, required=True)
    d.add_argument("--expected", required=True)
    d.add_argument("--profile", type=Path)
    d.set_defaults(fn=cmd_diagnose)

    args = ap.parse_args()
    if not args.image.exists():
        print(f"  image not found: {args.image}")
        return 2
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())