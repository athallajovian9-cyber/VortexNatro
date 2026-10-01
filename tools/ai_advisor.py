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

WHAT THE MODEL'S NUMBER IS NOT
    The tolerance the model returns is a guess dressed as a measurement. So it
    is not trusted: the proposal is tested against the frame it came from, and
    the tolerance actually written is MEASURED from that frame - the tightest
    value that still isolates the target, plus how much room there is before the
    next nearest colour. If a proposal cannot be isolated, nothing is written.

WHO READS THE PROFILE
    lib/nm_verify.ahk, by file presence: drop profiles/marker.ini next to the
    macro and the drift detector uses it, with no config change. Delete it and
    the built-in constants apply again.

USAGE
    # propose thresholds from a screenshot
    python ai_advisor.py suggest --image frame.png --target "the sprinkler marker"

    # also require the result to hold on a second frame, so it is not fitted to one
    python ai_advisor.py suggest --image f1.png --also f2.png --target "..."

    # diagnose a failed detection
    python ai_advisor.py diagnose --image fail.png --expected "neon green marker" \\
        --profile profiles/marker.ini

    # which vision endpoints can actually answer right now
    python ai_advisor.py check
"""
import argparse
import base64
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROFILES = ROOT / "profiles"

# A normal browser User-Agent. Not cosmetic: urllib's default ("Python-urllib/3.x")
# is on Cloudflare's bot list, and the nutaraline endpoint answers it with
# HTTP 403 / "error code: 1010" - a working endpoint that looks dead. Measured:
# same request, same key, only the header changed -> 403 becomes 200.
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# Vision endpoints, tried in order. Each one is verified live with `check`
# rather than assumed: a gateway that lists models and returns 402 on every
# completion looks configured and answers nothing.
ENDPOINTS = [
    {
        "name": "router",
        "base": "http://localhost:20128/v1",
        "model": "gemini/gemini-3.5-flash-lite",
        "key_env": "HERMES_CUSTOM_LOCALHOST_20128_API_KEY",
        "sse": True,
    },
    {
        "name": "nutaraline",
        "base": "https://nutaraline.co.uk/v1",
        "model": "mimo-v2.6-flash",
        "key_env": "HERMES_CUSTOM_NUTARALINE_CO_UK_API_KEY",
        "sse": True,
    },
]

# Longest edge to SEND. The endpoint drops or mangles large images; the full
# resolution frame is still what the proposal is verified against, and the
# model's coordinates are scaled back up before use.
SEND_MAX_EDGE = 720

MIN_REGION_FRACTION = 0.01     # a target filling less than this is not a target
MIN_SWEEP_FRACTION = 0.005     # the near cluster must be at least this big
TOL_FLOOR = 8                  # below this, one shade of noise decides the result
TOL_CEIL = 120                 # above this, everything matches and it discriminates nothing


def _env_key(name: str) -> str:
    """Key from the Hermes env file, else the process environment. Never hardcoded."""
    env = Path(os.environ.get("LOCALAPPDATA", "")) / "hermes" / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return os.environ.get(name, "")


# ---------------------------------------------------------------- transport

def _read_plain(raw: str) -> str:
    """One JSON completion - the non-streaming shape."""
    try:
        obj = json.loads(raw)
    except Exception:
        return ""
    msg = (obj.get("choices") or [{}])[0].get("message", {})
    content = msg.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return ""


def _read_sse(raw: str) -> str:
    """Reassemble a streamed reply from its `data: {...}` deltas.

    Parsing the body whole fails with "Expecting value: line 1 column 1", so the
    deltas are joined instead.
    """
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
            if isinstance(c, str):
                out.append(c)
            elif isinstance(c, list):
                out.extend(p.get("text", "") for p in c if isinstance(p, dict))
    return "".join(out)


def _empty_reason(raw: str) -> str:
    """Why a 200 with no text in it happened. 'Empty body' is not an answer."""
    try:
        obj = json.loads(raw)
    except Exception:
        return "answered with an empty body"
    usage = obj.get("usage") or {}
    det = usage.get("completion_tokens_details") or {}
    rt = det.get("reasoning_tokens") or usage.get("reasoning_tokens") or 0
    if rt:
        return (f"the model used its whole {usage.get('completion_tokens')}-token budget "
                f"reasoning ({rt} reasoning tokens) and never wrote an answer - "
                f"raise max_tokens")
    if ((obj.get("choices") or [{}])[0].get("finish_reason")) == "length":
        return "the reply hit max_tokens before any content was produced"
    return "answered with an empty body"


def _post(endpoint: dict, image_b64: str, prompt: str, max_tokens: int, timeout: int = 120):
    """One request. Raises on any failure - the caller decides to fall through."""
    body = json.dumps({
        "model": endpoint["model"],
        "max_tokens": max_tokens,
        "stream": bool(endpoint.get("sse")),
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url",
             "image_url": {"url": "data:image/png;base64," + image_b64}},
        ]}],
    }).encode()

    req = urllib.request.Request(
        endpoint["base"].rstrip("/") + "/chat/completions", data=body,
        headers={"Content-Type": "application/json",
                 "User-Agent": endpoint.get("ua", UA),
                 "Authorization": "Bearer " + _env_key(endpoint["key_env"])})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8", "replace")

    # "stream": true is a request, not a guarantee. Measured: one gateway ignores
    # it and answers with a single JSON body, which the SSE reader sees as zero
    # content - an endpoint that works, reported as answering with an empty body.
    # So both shapes are read, and the plain reader returns "" for a real stream.
    text = _read_sse(raw) if endpoint.get("sse") else _read_plain(raw)
    if not text.strip() and endpoint.get("sse"):
        text = _read_plain(raw)
    if not text.strip():
        raise RuntimeError(_empty_reason(raw))
    return text


def ask(image_path: Path, prompt: str, max_tokens: int = 700,
        endpoints=None, only=None, scale=None):
    """Send the image + prompt to the first endpoint that answers.

    Returns (text, endpoint_name, report) where report is a list of
    (name, "ok" | the failure) so the caller can say WHICH endpoint replied and
    why the others did not. A model call to a dead gateway must never look like
    a model that had nothing to say.
    """
    from PIL import Image

    im = Image.open(image_path).convert("RGB")
    send = im
    scale = 1.0
    if max(im.size) > SEND_MAX_EDGE:
        (sw, sh), scale = send_size(im.size)
        resample = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
        send = im.resize((sw, sh), resample)

    import io
    buf = io.BytesIO()
    send.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()

    cands = [e for e in (endpoints or ENDPOINTS)
             if only is None or e["name"] == only]
    if not cands:
        return None, None, [("(none)", "no endpoint matched --endpoint %r" % only)]

    report = []
    for ep in cands:
        try:
            text = _post(ep, b64, prompt, max_tokens)
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "replace")[:160]
            except Exception:
                pass
            report.append((ep["name"], f"HTTP {e.code} {detail}"))
            continue
        except Exception as e:
            report.append((ep["name"], f"{type(e).__name__}: {str(e)[:120]}"))
            continue
        if not text.strip():
            report.append((ep["name"], "answered with an empty body"))
            continue
        report.append((ep["name"], "ok"))
        return text, ep["name"], report
    return None, None, report


# ------------------------------------------------------------------ measuring

def locate_colour(image_path: Path, rgb, tol: int):
    """Where the named colour ACTUALLY is, found by measurement.

    The model is good at naming a colour and unreliable at saying where it is -
    measured twice on the same frame: the colour it returned was within 14 levels
    of the true marker while its region was 1000 pixels away from it. Rejecting
    on that basis alone throws away a correct colour.

    So the colour is taken and the region is measured: a per-channel mask via
    Image.point (C loops, no numpy), bounding box from the mask, and the mean of
    the pixels inside it. The mean is what goes in the profile - the colour is
    then measured too, not taken on trust.

    Returns None when the colour is absent, or when it is spread across the frame
    rather than forming a feature: a colour everywhere identifies nothing.
    """
    from PIL import Image, ImageChops

    im = Image.open(image_path).convert("RGB")
    W, H = im.size
    chans = im.split()

    def band(ch, target):
        return ch.point(lambda v, t=target: 255 if abs(v - t) <= tol else 0)

    mask = ImageChops.darker(ImageChops.darker(band(chans[0], rgb[0]), band(chans[1], rgb[1])),
                             band(chans[2], rgb[2]))
    box = mask.getbbox()
    if not box:
        return None
    x1, y1, x2, y2 = box
    area = (x2 - x1) * (y2 - y1)
    if area > 0.30 * W * H:
        return None                      # everywhere: not a marker, a colour cast

    inside = mask.crop(box)
    hits = inside.histogram()[255]
    coverage = hits / max(1, area)
    if coverage < MIN_REGION_FRACTION:
        return None                      # scattered, so the box spans unrelated things

    crop = im.crop(box)
    px = _panel_pixels(crop, (0, 0, crop.width, crop.height))
    matched = [(r, g, b) for (r, g, b) in px
               if abs(r - rgb[0]) <= tol and abs(g - rgb[1]) <= tol and abs(b - rgb[2]) <= tol]
    if not matched:
        return None
    mean = tuple(int(round(sum(c[i] for c in matched) / len(matched))) for i in range(3))
    return {"box": box, "coverage": coverage, "mean_rgb": mean,
            "hits": len(matched), "pixels": area, "frame": (W, H)}


def send_size(size):
    """The dimensions the model will actually see, and the factor to undo it.

    The prompt must state THESE numbers, not the original ones. Measured: told the
    original 1920x1080 while being sent a 720x405 copy, the model answered with
    y=741 - a coordinate that only exists in the original frame - and scaling it
    back pushed it further off. The model can only measure the pixels it is given.
    """
    W, H = size
    if max(W, H) <= SEND_MAX_EDGE:
        return (W, H), 1.0
    f = SEND_MAX_EDGE / max(W, H)
    return (max(1, int(W * f)), max(1, int(H * f))), f


def _panel_pixels(im, box):
    """Pixels of a crop, without the deprecated getdata() on newer Pillow."""
    crop = im.crop(box)
    if hasattr(crop, "get_flattened_data"):
        return list(crop.get_flattened_data())
    return list(crop.getdata())


def _region_pixels(im, box):
    """Every pixel in the box, clamped into the frame and sanity-checked.

    The box is half-open - [x1, x2) - which is what Image.crop takes. Clamping
    x2/y2 to W-1/H-1 instead would silently drop the last row and column, so a
    region ending at the frame edge came back one pixel short and, measured on a
    100-wide frame, lost the very column the test colour was in.
    """
    W, H = im.size
    x1, y1, x2, y2 = box
    x1 = max(0, min(W - 1, int(x1)))
    x2 = max(0, min(W, int(x2)))
    y1 = max(0, min(H - 1, int(y1)))
    y2 = max(0, min(H, int(y2)))
    x1, x2 = sorted((x1, x2))
    y1, y2 = sorted((y1, y2))
    if x2 - x1 < 2 or y2 - y1 < 2:
        return None, (x1, y1, x2, y2), (W, H)
    return _panel_pixels(im, (x1, y1, x2, y2)), (x1, y1, x2, y2), (W, H)


def verify_proposal(image_path: Path, data: dict, trial_tol: int = None):
    """Check the model's OWN proposal against the frame it came from.

    This is the step that makes an AI suggestion safe to apply. A language model
    can describe a button fluently and be wrong about both its colour and its
    rectangle - measured: asked for a #17263A button, it answered #111111 and
    gave the wrong region. Writing that profile would produce a detector that
    silently never matches.

    So the proposal is TESTED: does the stated colour actually appear inside the
    stated region, at the stated tolerance? Returns (ok, detail, stats).
    """
    try:
        from PIL import Image
    except Exception:
        return (False, "Pillow not available - cannot verify, refusing to save", {})

    rgb = data.get("rgb")
    if not rgb:
        return (False, "no colour proposed", {})
    tol = trial_tol if trial_tol is not None else int(data.get("tolerance", 40))

    im = Image.open(image_path).convert("RGB")
    px, box, (W, H) = _region_pixels(im, (data.get("x1", 0), data.get("y1", 0),
                                          data.get("x2", 0), data.get("y2", 0)))
    if px is None:
        return (False, f"degenerate or out-of-frame region {box} in a {W}x{H} image - "
                       f"the proposal does not fit the frame", {})

    r0, g0, b0 = rgb
    hits = sum(1 for (r, g, b) in px
               if abs(r - r0) <= tol and abs(g - g0) <= tol and abs(b - b0) <= tol)
    frac = hits / len(px)
    stats = {"fraction": frac, "pixels": len(px), "box": box, "image": (W, H)}

    # A target worth detecting fills a meaningful part of its own region. Under
    # 1% means the region and the colour do not belong together.
    if frac < MIN_REGION_FRACTION:
        return (False, f"colour {rgb} found in only {frac * 100:.2f}% of the proposed "
                       f"region - region and colour disagree", stats)
    return (True, f"{frac * 100:.1f}% of the proposed region matches {rgb} "
                  f"at tolerance {tol}", stats)


def measure_tolerance(image_path: Path, box, rgb) -> dict:
    """The tightest tolerance that still isolates the target, measured not guessed.

    Per-channel Chebyshev distance from the proposal's colour, histogrammed over
    the region. The near cluster is the target; the next cluster is everything
    else. The tolerance goes at the top of the near cluster, and the GAP between
    the two is the margin - how much a future frame can drift before the
    detector starts catching background.

    Returns a dict with tolerance / margin / fraction / verdict, where verdict is
    "ok", "too_small" (barely any target), or "no_separation" (one smear of
    colour, so no threshold can discriminate).
    """
    from PIL import Image

    im = Image.open(image_path).convert("RGB")
    px, clamped, (W, H) = _region_pixels(im, box)
    if px is None:
        return {"verdict": "bad_region", "detail": f"region {clamped} does not fit {W}x{H}"}

    r0, g0, b0 = rgb
    hist = [0] * 256
    for (r, g, b) in px:
        d = max(abs(r - r0), abs(g - g0), abs(b - b0))
        hist[min(255, d)] += 1

    total = len(px)
    floor = max(1, int(total * MIN_SWEEP_FRACTION))

    # The near cluster starts at the FIRST populated bin, which is not bin 0.
    # A measured mean colour is by definition not an exact pixel value - the mean
    # of (0,255,51) and (51,255,102) is (9,255,60) - so requiring hist[0] > 0
    # found "0 pixels anywhere near the colour" on a region that was 82.8% that
    # colour. The nearest pixel is `first` levels away; that is measured, not zero.
    first = next((i for i in range(256) if hist[i]), None)
    if first is None:
        return {"verdict": "too_small", "near": 0, "pixels": total,
                "detail": f"no pixel in the region is within 255 of {tuple(rgb)}"}

    top = first
    while top < 255 and hist[top + 1] > 0:
        top += 1
    near = sum(hist[first:top + 1])
    if near < floor:
        return {"verdict": "too_small", "near": near, "pixels": total,
                "detail": f"only {near}/{total} pixels are anywhere near {tuple(rgb)} "
                          f"(nearest is {first} levels away)"}

    # The margin is the empty span between the near cluster and the next colour:
    # how much a future frame can drift before the detector starts catching
    # background.
    j = top + 1
    while j < 255 and hist[j] == 0:
        j += 1
    margin = j - top - 1
    far = sum(hist[j:]) if j < 256 else 0

    if far == 0:
        # The region holds one colour and nothing else. That is the ideal frame for
        # detection and a useless one for measuring a margin: there is no other
        # colour to measure against. But "one colour" is not the same as "the
        # colour asked for" - a region of field green is also one colour, 178
        # levels from the marker - so how far the cluster sits from the request is
        # what separates a hit from a miss.
        if first > TOL_CEIL:
            return {"verdict": "too_small", "near": near, "pixels": total,
                    "detail": f"the region holds one colour, {first} levels from "
                              f"{tuple(rgb)} - this is not the target"}
        tol = max(TOL_FLOOR, min(TOL_CEIL, top))
        frac = sum(hist[:tol + 1]) / total
        return {"verdict": "ok", "tolerance": tol, "margin": None, "margin_measured": False,
                "near": near, "far": 0, "pixels": total, "fraction": frac,
                "detail": f"the region is entirely {tuple(rgb)} - tolerance {tol} is the "
                          f"floor, not a measurement; widen the region to include the "
                          f"background and it can be measured properly"}

    if margin < TOL_FLOOR:
        # No empty band wide enough to put a threshold in: the colours grade into
        # each other, so any value is arbitrary. Say so instead of picking one.
        tol = max(TOL_FLOOR, top)
        return {"verdict": "no_separation", "tolerance": tol, "margin": margin,
                "margin_measured": True, "near": near, "far": far, "pixels": total,
                "detail": f"no clear gap between the target and the background "
                          f"(widest empty band is {margin} levels) - a threshold here "
                          f"is arbitrary"}

    tol = max(TOL_FLOOR, min(TOL_CEIL, top))
    frac = sum(hist[:tol + 1]) / total
    return {"verdict": "ok", "tolerance": tol, "margin": margin, "margin_measured": True,
            "near": near, "far": far, "pixels": total, "fraction": frac,
            "detail": f"target isolates at tolerance {tol} "
                      f"({frac * 100:.1f}% of the region); nearest other colour is "
                      f"{margin} levels further out"}


# -------------------------------------------------------------------- prompts

SUGGEST_PROMPT = """You are calibrating a colour-threshold detector for a game macro.

Look at the image and find: {target}

The image is {w}x{h} pixels. Coordinates must be inside that frame.

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
- The region must be INSIDE the frame. A y coordinate past the image height is
  rejected outright, so check the height above before answering.
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


# --------------------------------------------------------------------- output

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


def scale_box(data: dict, factor: float) -> dict:
    """Map a box the model gave on the SENT (downscaled) image back to the original.

    Without this, every coordinate is wrong by the downscale factor - a marker at
    x600 in a 720-wide copy of a 1920-wide frame is really at x1600.
    """
    if factor == 1.0:
        return data
    out = dict(data)
    for k in ("x1", "y1", "x2", "y2"):
        if isinstance(out.get(k), (int, float)):
            out[k] = int(round(out[k] / factor))
    return out


def write_profile(path: Path, target: str, source: str, rgb, tol: int, box, note: str,
                  evidence: str, frames: list):
    lines = [
        "; Generated by ai_advisor.py - AI-proposed, MEASURED tolerance.",
        "; Target: " + target,
        "; Source image: " + source,
        ";",
        "; The model proposed the colour and the region. The tolerance written here is",
        "; measured from the frame, not the number the model guessed.",
        "; " + evidence,
        "; Verified against: " + ", ".join(frames),
        ";",
        "; lib/nm_verify.ahk reads this file by its presence. Delete it and the",
        "; built-in constants apply again. Re-run `ai_advisor.py diagnose` on a failed",
        "; frame if detection starts missing.",
        "",
        "[detect]",
        "rgb=" + ",".join(str(int(v)) for v in rgb),
        "tolerance=" + str(int(tol)),
        "x1=" + str(int(box[0])),
        "y1=" + str(int(box[1])),
        "x2=" + str(int(box[2])),
        "y2=" + str(int(box[3])),
    ]
    if note:
        lines.append("; the model's own caveat, kept so the number is not treated as gospel")
        lines.append("note=" + note.replace("\n", " "))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# -------------------------------------------------------------------- commands

def _ask_or_report(image, prompt, only, quiet=False):
    text, who, report = ask(image, prompt, only=only)
    if not quiet:
        for name, status in report:
            print(f"  endpoint {name:<12} {status}")
    if text is None:
        print("\n  NO ENDPOINT COULD ANSWER. The model was never asked anything, so")
        print("  there is no proposal to judge - this is a connection problem, not a")
        print("  bad calibration. Try `ai_advisor.py check`.")
        return None
    print(f"  answered by: {who}")
    return text


def cmd_check(args):
    """Really ask each endpoint, so 'configured' is never mistaken for 'working'."""
    from PIL import Image
    probe = PROFILES.parent / "_probe.png"
    Image.new("RGB", (64, 64), (10, 20, 30)).save(probe)
    rc = 0
    try:
        for ep in ENDPOINTS:
            # 300, not 10: a reasoning model spends the budget thinking first and
            # returns nothing when it runs out, which reads as a dead endpoint.
            text, who, report = ask(probe, "Reply with the single word: ready",
                                    max_tokens=300, endpoints=[ep])
            status = report[0][1]
            if text:
                print(f"  {ep['name']:<12} OK      {ep['model']}")
                print(f"  {'':<12} replied: {text.strip()[:60]!r}")
            else:
                rc = 1
                print(f"  {ep['name']:<12} FAILED  {status}")
    finally:
        probe.unlink(missing_ok=True)
    return rc


def cmd_suggest(args):
    target = args.target
    from PIL import Image
    im = Image.open(args.image)
    W, H = im.size
    (SW, SH), factor = send_size((W, H))
    print(f"  frame       : {args.image.name}  {W}x{H}")
    if factor != 1.0:
        print(f"  sending     : downscaled to {SW}x{SH} (the endpoint drops larger "
              f"images); the model is told THESE dimensions and the answer is scaled back")

    prompt = SUGGEST_PROMPT.format(target=target, w=SW, h=SH)
    text = _ask_or_report(args.image, prompt, args.endpoint)
    if text is None:
        return 1
    data = _extract_json(text)
    if not data:
        print("\n  the model did not return usable JSON. Raw reply:\n")
        print("  " + text.strip()[:900])
        return 1

    data = scale_box(data, factor)
    if factor != 1.0:
        print(f"  note        : coordinates scaled by 1/{factor:.3f} back to the original")

    print(f"  target      : {target}")
    print(f"  found       : {data.get('found')}")
    if data.get("found"):
        print(f"  region      : ({data.get('x1')},{data.get('y1')}) - "
              f"({data.get('x2')},{data.get('y2')})")
    rgb = data.get("rgb")
    print(f"  rgb         : {rgb}")
    print(f"  tolerance   : {data.get('tolerance')}  (the model's guess - not used as-is)")
    print(f"  note        : {data.get('note','')}")

    if not data.get("found") or not rgb:
        print("\n  NOT written to a profile: the model could not sample the target.")
        print("  A threshold guessed from an image where the target is absent is a")
        print("  fabricated constant, which is the failure this whole design avoids.")
        return 1

    ok, detail, _ = verify_proposal(args.image, data)
    print(f"  VERIFY      : {'PASS' if ok else 'FAIL'} - {detail}")
    if not ok:
        # The region is wrong. The COLOUR may still be right - and on this frame it
        # was: the model named [0,255,65] for a [0,255,51] marker, and put the box
        # 1000 pixels away from it. Throwing the whole proposal away because the
        # geometry is wrong discards a correct colour, so the colour is kept and
        # the region is measured instead.
        # Search WIDE. The model's tolerance is a guess about its own guess, and
        # measured: it named [51,255,51] for a (0,255,51) marker - 51 levels out in
        # one channel - so a search at its own tolerance of 30 found nothing at all.
        # A broad window is safe here because precision is not the point of this
        # step: the cluster's MEAN colour and a measured tolerance are taken next,
        # and the localisation guards reject anything that is not a compact feature.
        search_tol = max(int(data.get("tolerance", 40)), 80)
        loc = locate_colour(args.image, data.get("rgb"), search_tol)
        if loc is None:
            print("  NOT saved. The proposal did not survive testing against the frame")
            print("  it came from, and the colour it named does not appear as a compact")
            print("  feature anywhere in that frame. Try a tighter --target description,")
            print("  or a frame where the target is larger and unobstructed.")
            return 1
        bx = loc["box"]
        print(f"  RECOVERED   : the region was wrong, but the colour is real - it is at "
              f"({bx[0]},{bx[1]})-({bx[2]},{bx[3]}), filling {loc['coverage'] * 100:.0f}% "
              f"of that box")
        print(f"  RECOVERED   : measured mean colour {list(loc['mean_rgb'])} "
              f"(the model said {data.get('rgb')})")
        data = dict(data, rgb=list(loc["mean_rgb"]),
                    x1=bx[0], y1=bx[1], x2=bx[2], y2=bx[3])
        ok, detail, _ = verify_proposal(args.image, data)
        print(f"  VERIFY      : {'PASS' if ok else 'FAIL'} - {detail} (on the measured region)")
        if not ok:
            print("  NOT saved: the measured region still does not hold the colour, so")
            print("  something about this frame is not what the model described.")
            return 1

    rgb = data.get("rgb")
    box = (data.get("x1", 0), data.get("y1", 0), data.get("x2", 0), data.get("y2", 0))
    m = measure_tolerance(args.image, box, rgb)
    print(f"  MEASURED    : {m['detail']}")
    if m["verdict"] == "no_separation":
        print("  NOT saved. There is no empty band between the target and what")
        print("  surrounds it, so any tolerance is a coin flip. A detector that")
        print("  cannot be separated from its background is worse than none.")
        return 1
    if m["verdict"] != "ok":
        print("  NOT saved. " + m["detail"])
        return 1

    tol = m["tolerance"]

    # Every extra frame must still find the target. A threshold fitted to one
    # frame is a threshold that fails on the next one.
    frames_checked = [args.image.name]
    for extra in (args.also or []):
        ex = Path(extra)
        if not ex.exists():
            print(f"  ALSO        : {extra} not found - refusing to claim it was checked")
            return 1
        ok2, detail2, _ = verify_proposal(ex, data, trial_tol=tol)
        print(f"  ALSO {ex.name:<14} {'PASS' if ok2 else 'FAIL'} - {detail2}")
        if not ok2:
            print("  NOT saved: the proposal does not hold on this frame, so it is")
            print("  fitted to one screenshot rather than to the game.")
            return 1
        frames_checked.append(ex.name)

    out = PROFILES / f"{args.name}.ini"
    write_profile(out, target, args.image.name, rgb, tol, box,
                  str(data.get("note", "")), m["detail"], frames_checked)
    print(f"\n  profile written: {out}")
    if m.get("margin") is None:
        print(f"  measured tolerance {tol} (model guessed {data.get('tolerance')}), "
              f"margin NOT measurable on this frame - the region holds only the target, "
              f"so the threshold is a floor rather than a measurement. Re-run with a "
              f"wider region if you want the margin.")
    else:
        print(f"  measured tolerance {tol} (model guessed {data.get('tolerance')}), "
              f"margin {m['margin']} levels")
    print("  lib/nm_verify.ahk picks it up by presence; reload the macro to apply.")
    return 0


def cmd_diagnose(args):
    prof = ""
    if args.profile and args.profile.exists():
        prof = args.profile.read_text(encoding="utf-8").strip()
    else:
        print("  (no profile read - diagnosing against the description alone)")
    prompt = DIAGNOSE_PROMPT.format(expected=args.expected, profile=prof or "(none)")
    text = _ask_or_report(args.image, prompt, args.endpoint)
    if text is None:
        return 1
    data = _extract_json(text)
    if not data:
        print("\n  no usable JSON. Raw reply:\n")
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


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("suggest", help="propose thresholds from a screenshot")
    s.add_argument("--image", type=Path, required=True)
    s.add_argument("--target", required=True, help="what to find, in words")
    s.add_argument("--name", default="marker", help="profile name to write")
    s.add_argument("--also", action="append", metavar="FRAME",
                   help="another frame the proposal must also pass (repeatable)")
    s.add_argument("--endpoint", help="force one endpoint by name")
    s.set_defaults(fn=cmd_suggest)

    d = sub.add_parser("diagnose", help="why did detection fail on this frame")
    d.add_argument("--image", type=Path, required=True)
    d.add_argument("--expected", required=True)
    d.add_argument("--profile", type=Path)
    d.add_argument("--endpoint")
    d.set_defaults(fn=cmd_diagnose)

    c = sub.add_parser("check", help="which endpoints can actually answer")
    c.set_defaults(fn=cmd_check)

    args = ap.parse_args()
    if hasattr(args, "image") and not args.image.exists():
        print(f"  image not found: {args.image}")
        return 2
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
