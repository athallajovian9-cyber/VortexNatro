#!/usr/bin/env python3
"""
candidates.py - measure the frame in CODE, so the model only has to CHOOSE.

WHY THIS EXISTS (from a measurement, not a theory)
    Asked to name the colour and bounding box of a #17263A button, the vision model
    answered #111111 - wrong on every channel - and gave a region at y528 in a
    513-pixel-tall image, entirely off-frame.

    That is not a reasoning failure, so a smarter model would not fix it. Reading
    exact pixel values and coordinates off an image is a SENSOR task, and sensor
    accuracy does not improve with reasoning depth. Asking a language model to do
    it is asking the wrong component for the wrong thing.

    So: this counts the pixels and hands over exact numbers. The model's job reduces
    to picking a row, which is what language models are genuinely good at.

USAGE
    python candidates.py --image frame.png
    python candidates.py --image frame.png --target "the dark START button"

    Prints a measured table (area, bounding box, sampled colour, solidity) and the
    ready-to-paste CHOOSE prompt. Paste the table to any model and ask for an index.
    Every number in it was counted, not estimated.
"""
import argparse
import sys
from pathlib import Path

QUANT = 32          # 8 buckets per channel: groups near-identical pixels together


def _key(r: int, g: int, b: int):
    return (r // QUANT, g // QUANT, b // QUANT)


def extract(image: Path, top_n: int = 12, min_area: int = 40):
    """Colour clusters with bounding boxes and areas, largest first.

    Returns (rows, (W, H)). Pure image work - no model, no guessing.
    """
    try:
        from PIL import Image
    except ImportError:
        sys.exit("  Pillow is required: pip install pillow")

    im = Image.open(image).convert("RGB")
    W, H = im.size
    px = im.load()

    # count, x1, y1, x2, y2, first-seen rgb for the bucket
    stats = {}
    for y in range(H):
        for x in range(W):
            r, g, b = px[x, y]
            k = _key(r, g, b)
            s = stats.get(k)
            if s is None:
                # keep a per-bucket tally of exact colours: the reported colour must
                # be the MOST COMMON one in the bucket, not the first pixel seen.
                # Measured failure: the light bucket reported #F3F3F3 when the
                # dominant colour was #EDF5FF (82% of the frame).
                stats[k] = [1, x, y, x, y, r, g, b, {(r, g, b): 1}]
            else:
                s[0] += 1
                s[8][(r, g, b)] = s[8].get((r, g, b), 0) + 1
                if x < s[1]:
                    s[1] = x
                if y < s[2]:
                    s[2] = y
                if x > s[3]:
                    s[3] = x
                if y > s[4]:
                    s[4] = y

    rows = []
    for k, v in sorted(stats.items(), key=lambda kv: -kv[1][0]):
        cnt, x1, y1, x2, y2, r, g, b, tally = v
        if cnt < min_area:
            continue
        r, g, b = max(tally.items(), key=lambda kv: kv[1])[0]   # truly modal
        w, h = x2 - x1 + 1, y2 - y1 + 1
        # solidity: what fraction of the bounding box the cluster actually fills.
        # A UI button is near 1.0; scattered scenery across the whole frame is not.
        rows.append({
            "area": cnt,
            "pct": round(100.0 * cnt / (W * H), 2),
            "bbox": [x1, y1, x2, y2],
            "w": w,
            "h": h,
            "rgb": [r, g, b],
            "hex": "#{:02X}{:02X}{:02X}".format(r, g, b),
            "solid": round(cnt / max(1, w * h), 2),
        })
        if len(rows) >= top_n:
            break
    return rows, (W, H)


def print_table(rows, size):
    W, H = size
    print(f"  frame: {W}x{H}   clusters found: {len(rows)}  (measured, not estimated)\n")
    print(f"  {'#':>3}  {'colour':<9} {'area':>7} {'bbox':<26} {'size':>10} {'solid':>6}")
    for i, c in enumerate(rows, 1):
        bbox = ",".join(str(v) for v in c["bbox"])
        print(f"  {i:>3}  {c['hex']:<9} {c['pct']:>6}%  {bbox:<26} "
              f"{c['w']:>4}x{c['h']:<5} {c['solid']:>6}")


def print_prompt(rows, size, target):
    W, H = size
    lines = "\n".join(
        f"  [{i}] rgb={c['rgb']} hex={c['hex']} area={c['pct']}% "
        f"bbox={c['bbox']} size={c['w']}x{c['h']} solid={c['solid']}"
        for i, c in enumerate(rows, 1))
    print("\n" + "-" * 72)
    print(f"""Below is a measured list of colour clusters from a game screenshot
({W}x{H}). Every number was counted from the actual pixels.

Which ONE cluster best matches: {target}

{lines}

Reply with the index number only, or -1 if none match.
A solid cluster (solid near 1.0) with a rectangular box is more likely a UI element
than one spread across the whole frame.""")
    print("-" * 72)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--image", type=Path, required=True)
    ap.add_argument("--target", default="", help="what you are looking for")
    ap.add_argument("--top", type=int, default=12)
    ap.add_argument("--min-area", type=int, default=40,
                    help="ignore clusters smaller than this many pixels")
    a = ap.parse_args()

    if not a.image.exists():
        sys.exit(f"  image not found: {a.image}")
    rows, size = extract(a.image, a.top, a.min_area)
    if not rows:
        print("  no clusters large enough - lower --min-area")
        return 1
    print_table(rows, size)
    if a.target:
        print_prompt(rows, size, a.target)
    return 0


if __name__ == "__main__":
    sys.exit(main())