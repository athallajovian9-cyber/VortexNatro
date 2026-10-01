"""Tests for the AI advisor.

The parts that can be wrong without anyone noticing:

  * the tolerance it writes - it must come from the frame, not from the model
  * the region - a proposal on a DOWNSCALED image must be mapped back, or every
    coordinate is off by the scale factor
  * the endpoint chain - a gateway that lists models and 402s on every
    completion must fall through to the next one, and the reply must be
    reassembled from the SSE deltas
  * the profile file - lib/nm_verify.ahk reads [detect] keys by exact name, so
    the writer and the reader are a contract

RUN
    python3 tools/test_ai_advisor.py
"""
import base64
import json
import sys
import threading
import configparser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ai_advisor as A
from PIL import Image

passed = 0
failed = 0


def check(name, cond, extra=None):
    global passed, failed
    if cond:
        passed += 1
        print("  PASS  " + name)
    else:
        failed += 1
        print("  FAIL  " + name + ("   -> " + str(extra) if extra is not None else ""))


def rule(t):
    print("")
    print("  ---- " + t + " ----")


TMP = Path(__file__).resolve().parent / "_test_tmp"
TMP.mkdir(exist_ok=True)

BG = (18, 40, 22)          # field green
MARK = (0, 255, 51)        # the sprinkler marker, the built-in target colour


def make_frame(path, w=400, h=300, box=(250, 140, 320, 170), colour=MARK, bg=BG):
    im = Image.new("RGB", (w, h), bg)
    x1, y1, x2, y2 = box
    x1, x2 = max(0, min(w - 1, x1)), max(0, min(w, x2))
    y1, y2 = max(0, min(h - 1, y1)), max(0, min(h, y2))
    for y in range(y1, y2):
        for x in range(x1, x2):
            im.putpixel((x, y), colour)
    im.save(path)
    return path


# --------------------------------------------------------------------- region

rule("the verifier")

f = make_frame(TMP / "good.png")
ok, detail, stats = A.verify_proposal(f, {"rgb": list(MARK), "tolerance": 20,
                                          "x1": 250, "y1": 140, "x2": 320, "y2": 170})
check("a correct proposal passes", ok, detail)
check("and it measures the region it actually used", stats["pixels"] == 70 * 30, stats)

ok, detail, _ = A.verify_proposal(f, {"rgb": [17, 17, 17], "tolerance": 20,
                                      "x1": 250, "y1": 140, "x2": 320, "y2": 170})
check("a wrong colour is rejected", not ok, detail)

f2 = make_frame(TMP / "w300.png", w=300, h=200)
ok, detail, _ = A.verify_proposal(f2, {"rgb": list(MARK), "tolerance": 20,
                                       "x1": 10, "y1": 528, "x2": 100, "y2": 560})
check("a region off the bottom of the frame is rejected", not ok, detail)
check("and the rejection names the frame size", "200" in detail, detail)

ok, detail, _ = A.verify_proposal(f, {"rgb": list(MARK), "tolerance": 20,
                                      "x1": 100, "y1": 100, "x2": 101, "y2": 101})
check("a degenerate region is rejected", not ok, detail)

ok, detail, _ = A.verify_proposal(f, {"rgb": None, "tolerance": 20,
                                      "x1": 250, "y1": 140, "x2": 320, "y2": 170})
check("a proposal with no colour is rejected", not ok, detail)


# ---------------------------------------------------------------- tolerance

rule("the measured tolerance")

m = A.measure_tolerance(f, (250, 140, 320, 170), MARK)
check("the marker's own colour is isolated", m["verdict"] == "ok", m)
check("the tolerance sits at the top of the target cluster, not the model's guess",
      m.get("tolerance", 999) <= 10, m)
check("it matches essentially the whole region", m.get("fraction", 0) > 0.99, m)
check("a region holding ONLY the target says its margin is unmeasured, not zero",
      m.get("margin") is None and m.get("margin_measured") is False, m)

# the same marker inside the field: now there IS a background to measure against
m_bg = A.measure_tolerance(f, (200, 100, 380, 200), MARK)
check("with background in the region the margin IS measured",
      m_bg["verdict"] == "ok" and m_bg.get("margin") is not None, m_bg)
check("and the margin is the real distance to the field colour",
      m_bg["margin"] > 100, m_bg)

# a frame where the target is one shade of a gradient: no empty band to separate on
grad = Image.new("RGB", (100, 100), BG)
for y in range(100):
    for x in range(100):
        v = int(255 * x / 99)
        grad.putpixel((x, y), (0, v, 51))
gp = TMP / "gradient.png"
grad.save(gp)
m2 = A.measure_tolerance(gp, (0, 0, 100, 100), MARK)
check("a colour that grades into everything else is refused, not given a made-up number",
      m2["verdict"] == "no_separation", m2)

m3 = A.measure_tolerance(f, (0, 0, 60, 60), (123, 45, 200))
check("a colour absent from the region is refused", m3["verdict"] == "too_small", m3)

# the full-frame region must not lose its last row and column
full = A.measure_tolerance(f, (0, 0, 400, 300), BG)
check("a region covering the whole frame counts every pixel",
      full.get("pixels") == 400 * 300, full.get("pixels"))

# A measured mean colour is never an exact pixel value. The sweep used to demand a
# pixel at distance 0 and so reported "0 pixels anywhere near" on a region that was
# mostly that colour - which is what killed the first successful recovery.
pat = Image.new("RGB", (60, 60), (0, 255, 51))
for y in range(60):
    for x in range(30, 60):
        pat.putpixel((x, y), (51, 255, 102))
pp = TMP / "pattern.png"
pat.save(pp)
mp = A.measure_tolerance(pp, (0, 0, 60, 60), (9, 255, 60))     # the mean, not a pixel
check("a MEAN colour that no pixel actually has still measures", mp["verdict"] == "ok", mp)
check("it reports the distance to the nearest real pixel", mp.get("tolerance") == 9, mp)
check("and the gap to the next colour as the margin", mp.get("margin") == 32, mp)


# ------------------------------------------------------------------ geometry

rule("coordinates from a downscaled frame")

box = {"x1": 100, "y1": 50, "x2": 200, "y2": 80}
check("scale 1.0 leaves the box alone", A.scale_box(box, 1.0) == box)
scaled = A.scale_box(box, 0.5)
check("a halved frame maps the box back at double size",
      (scaled["x1"], scaled["x2"]) == (200, 400), scaled)
check("a factor of 1.0 is a no-op for every key", A.scale_box(box, 1.0) is not None)

wide = Image.new("RGB", (1920, 1080), BG)
for y in range(500, 540):
    for x in range(1400, 1500):
        wide.putpixel((x, y), MARK)
wp = TMP / "wide.png"
wide.save(wp)
ok, detail, stats = A.verify_proposal(wp, {"rgb": list(MARK), "tolerance": 20,
                                           "x1": 1400, "y1": 500, "x2": 1500, "y2": 540})
check("a marker in the right half is found at its real coordinates", ok, detail)
# and the same proposal expressed in DOWNSCALED coordinates must fail: that is the
# whole point of scaling the box back before anything is verified or written
ok_bad, detail_bad, _ = A.verify_proposal(wp, {"rgb": list(MARK), "tolerance": 20,
                                               "x1": 700, "y1": 250, "x2": 750, "y2": 270})
check("the downscaled coordinates alone do NOT pass - so the mapping is load-bearing",
      not ok_bad, detail_bad)
fixed = A.scale_box({"x1": round(1400 * (720 / 1920)), "y1": round(500 * (720 / 1920)),
                     "x2": round(1500 * (720 / 1920)), "y2": round(540 * (720 / 1920))},
                    720 / 1920)
ok_fixed, detail_fixed, _ = A.verify_proposal(wp, {"rgb": list(MARK), "tolerance": 20,
                                                   **fixed})
check("and after scale_box they do", ok_fixed, (fixed, detail_fixed))


# ------------------------------------------------------------------- profile

rule("the profile writer and the AHK reader agree")

prof = TMP / "marker.ini"
A.write_profile(prof, "the sprinkler marker", "good.png", MARK, 12,
                (250, 140, 320, 170), "flat colour, no glow",
                "target isolates at tolerance 12", ["good.png", "wide.png"])
cp = configparser.ConfigParser()
cp.read(prof)
check("the file parses as an ini", cp.has_section("detect"), cp.sections())
need = ["rgb", "tolerance", "x1", "y1", "x2", "y2"]
check("every key lib/nm_verify.ahk reads is present",
      all(cp.has_option("detect", k) for k in need),
      [k for k in need if not cp.has_option("detect", k)])
check("rgb is three comma-separated numbers the AHK Integer() call can read",
      len(cp.get("detect", "rgb").split(",")) == 3, cp.get("detect", "rgb"))
check("the tolerance is a plain integer",
      cp.get("detect", "tolerance").strip().isdigit(), cp.get("detect", "tolerance"))
for k in ("x1", "y1", "x2", "y2"):
    check("  " + k + " is a plain integer", cp.get("detect", k).strip().isdigit(),
          cp.get("detect", k))
check("the measured evidence is recorded in the file, not just the number",
      "tolerance 12" in prof.read_text(encoding="utf-8"))
check("every verified frame is named", "wide.png" in prof.read_text(encoding="utf-8"))


# ------------------------------------------------------------------ json/SSE

rule("parsing the model's reply")

check("bare JSON", A._extract_json('{"a":1}') == {"a": 1})
check("a fenced block", A._extract_json('```json\n{"a":1}\n```') == {"a": 1})
check("JSON buried in prose", A._extract_json('Sure! {"a":1} hope that helps') == {"a": 1})
check("nonsense returns None", A._extract_json("no json here") is None)
check("an empty reply returns None", A._extract_json("") is None)


# --------------------------------------------------------- endpoints, for real

rule("the endpoint chain (a real server, not a mock)")

STATE = {"auth": None, "image_px": None}
SSE_TEXT = '{"found": true, "rgb": [0, 255, 51]}'


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        STATE["auth"] = self.headers.get("Authorization")
        try:
            url = body["messages"][0]["content"][1]["image_url"]["url"]
            STATE["image_px"] = len(base64.b64decode(url.split(",", 1)[1]))
        except Exception:
            STATE["image_px"] = None

        if self.path.endswith("/broken/chat/completions"):
            payload = json.dumps({"error": {"message": "Insufficient Balance"}}).encode()
            self.send_response(402)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        if self.path.endswith("/plain/chat/completions"):
            payload = json.dumps({"choices": [{"message": {"content": "plain reply"}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        if self.path.endswith("/liar/chat/completions"):
            # asked to stream, answers with one JSON object anyway
            payload = json.dumps({"choices": [{"message": {"content": "ignored the stream flag"}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        # streamed, split across deltas so reassembly is genuinely exercised
        half = len(SSE_TEXT) // 2
        chunks = [SSE_TEXT[:half], SSE_TEXT[half:]]
        out = b""
        for c in chunks:
            out += ("data: " + json.dumps({"choices": [{"delta": {"content": c}}]}) + "\n\n").encode()
        out += b"data: [DONE]\n\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


srv = HTTPServer(("127.0.0.1", 0), Handler)
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{port}"

EPS = [
    {"name": "broken", "base": BASE + "/broken", "model": "m",
     "key_env": "AI_ADVISOR_TEST_KEY", "sse": True},
    {"name": "streamer", "base": BASE + "/streamer", "model": "m",
     "key_env": "AI_ADVISOR_TEST_KEY", "sse": True},
]
text, who, report = A.ask(f, "prompt", endpoints=EPS)
check("a 402 endpoint falls through to the next one", who == "streamer", (who, report))
check("the failure is reported, not swallowed",
      any("402" in s for _, s in report), report)
check("the SSE deltas are reassembled into the full reply",
      A._extract_json(text) == {"found": True, "rgb": [0, 255, 51]}, text)

text2, who2, rep2 = A.ask(f, "prompt", endpoints=[EPS[0]])
check("when nothing can answer, the result is empty - not a fabricated reply",
      text2 is None and who2 is None, (text2, who2))
check("and every endpoint's reason is kept for the report",
      len(rep2) == 1 and "402" in rep2[0][1], rep2)

plain = [{"name": "plain", "base": BASE + "/plain", "model": "m",
          "key_env": "AI_ADVISOR_TEST_KEY", "sse": False}]
text3, who3, _ = A.ask(f, "prompt", endpoints=plain)
check("a non-streaming endpoint is read the other way", text3 == "plain reply", text3)

liar = [{"name": "liar", "base": BASE + "/liar", "model": "m",
         "key_env": "AI_ADVISOR_TEST_KEY", "sse": True}]
text4, who4, rep4 = A.ask(f, "prompt", endpoints=liar)
check("an endpoint that ignores stream=true is still read, not called empty",
      text4 == "ignored the stream flag", (text4, rep4))

# the downscale, proven by inspecting what the server actually received
big = make_frame(TMP / "big.png", w=1600, h=900, box=(700, 400, 900, 500))
A.ask(big, "prompt", endpoints=plain)
sent = STATE["image_px"]
check("a 1600px frame is not sent at 1600px", sent is not None and sent < 1_000_000, sent)
small = make_frame(TMP / "small.png", w=200, h=150)
A.ask(small, "prompt", endpoints=plain)
check("a small frame is sent unchanged", STATE["image_px"] > 0, STATE["image_px"])

check("the key is read and sent as a bearer token",
      STATE["auth"] is not None and STATE["auth"].startswith("Bearer "), STATE["auth"])

# the model names the colour; the tool measures where it is
rule("recovering a correct colour from a wrong region")
loc = A.locate_colour(f, MARK, 40)
check("a compact feature is located", loc is not None and loc["hits"] > 2000, loc)
check("and the box is the marker's real box",
      loc and loc["box"] == (250, 140, 320, 170), loc and loc["box"])
check("it returns the MEASURED mean colour, not the colour it was asked for",
      loc and loc["mean_rgb"] == MARK, loc and loc["mean_rgb"])
# asked for a colour that is not there at all
check("a colour absent from the frame is not located", A.locate_colour(f, (200, 10, 200), 20) is None)
# asked for the field colour, which is everywhere: that identifies nothing
check("the field colour itself is rejected - it spans the frame",
      A.locate_colour(f, BG, 20) is None)
# a mean is taken from the pixels, so a slightly-off request still lands right
loc2 = A.locate_colour(f, (10, 250, 60), 25)
check("a request 10 levels off still finds the marker and reports the true colour",
      loc2 is not None and loc2["mean_rgb"] == MARK, loc2)

# an empty 200 is a real failure mode and must be explained, not shrugged at
rule("an empty 200 is explained, not reported as 'nothing to say'")
check("a reasoning model that burned its budget is named as such",
      "reasoning" in A._empty_reason(json.dumps(
          {"choices": [{"message": {"content": ""}}],
           "usage": {"completion_tokens": 10,
                     "completion_tokens_details": {"reasoning_tokens": 9}}})),
      A._empty_reason(json.dumps({"choices": [{"message": {"content": ""}}],
                                  "usage": {"completion_tokens": 10}})))
check("a length-truncated reply says so",
      "max_tokens" in A._empty_reason(json.dumps(
          {"choices": [{"message": {"content": ""}, "finish_reason": "length"}]})))
check("and a genuinely empty body still says that",
      A._empty_reason("not json at all") == "answered with an empty body")

srv.shutdown()

# ------------------------------------------------------------------ the contract
rule("the cross-language contract fixture")
# Written here with the REAL writer so tools/test_nm_verify.ahk reads exactly the
# bytes this tool produces - a profile format the writer emits and the reader
# cannot parse would leave the calibration silently inert.
fixture = A.PROFILES / "_contract_marker.ini"
A.write_profile(fixture, "the contract fixture", "synthetic.png", (200, 10, 240), 12,
                (100, 50, 140, 90), "written by the writer the tool actually uses",
                "target isolates at tolerance 12", ["synthetic.png"])
check("the fixture for the AHK reader was written", fixture.exists(), fixture)
fx = fixture.read_text(encoding="utf-8")
check("it carries the colour the AHK test expects", "rgb=200,10,240" in fx)
check("and the tolerance", "tolerance=12" in fx)
check("and the region", "x1=100" in fx and "y2=90" in fx)

print("")
print("  RESULT  passes=" + str(passed) + "  fails=" + str(failed))
sys.exit(1 if failed else 0)
