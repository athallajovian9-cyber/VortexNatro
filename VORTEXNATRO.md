# VortexNatro — what this adds to Natro Macro

Natro's own `README.md` and `LICENSE.md` are unchanged. This file documents the
**addition**: a verification layer, because verification is the one thing Natro and
Revolution Macro both lack.

## The gap

Neither macro ever asks *"did the character arrive?"* Natro integrates a **measured
speed** over time — a closed loop on speed, but **not on position**. So a lag spike, a
bee knockback, a collision or a hive wedge all give the same silent result: the macro
gathers on empty ground for the full duration and reports nothing wrong.

`lib/nm_verify.ahk` closes it with three checks:

| check | signal | why it survives reality |
|---|---|---|
| **Arrival** | flower mass — saturated red/blue pixels | a stored screenshot CANNOT work: the game regenerates a field's flower layout on every server join, so a reference image reports NOT ON FIELD everywhere except the server it came from |
| **Progress** | screen frozen 3 readings in a row (~6 s) | Natro's main macro *blocks* on `KeyWait "F14"`, so this is a **timer** that interrupts the wait, not a hook in the blocked loop |
| **Drift** | the sprinkler's neon-green marker `#00FF33`–`#33FF66` | a known colour at a known place; correction is **proportional** to the pixel offset, and it closes the loop on position, so it holds under any speed change — buffs, beequips, frame drops |

**Three answers, never two:** `1` good, `0` bad, `-1` **cannot tell**. `-1` must never
be read as a pass — a check that cannot fail is worse than none, because it teaches you
to trust an unverified run.

## The AI advisor

`tools/ai_advisor.py` uses a vision model as a **suggestor**, never as a runtime
detector. Detection in a macro loop is a threshold problem: one blit, every frame,
offline, identical answer every time. A model call costs hundreds of milliseconds and
varies between identical frames.

```
AI          names the colour, from a screenshot
VERIFIER    tests the proposal against that same frame
MEASURED    the tolerance comes from the frame, not from the model
THRESHOLD   executes it every frame
```

**The model's number is not trusted, and neither is its geometry.**

*Tolerance.* The value written is measured: per-channel distance from the proposed
colour is histogrammed over the region, the tolerance goes at the top of the near
cluster, and the gap to the next colour is reported as the **margin** — how far a
future frame can drift before the detector starts catching background. A colour that
grades into its surroundings has no gap, and is **refused** rather than given a
made-up number.

*Region.* Measured on a real frame: asked for the sprinkler marker, the model
answered a region at `(832,736)-(917,843)`. The marker is at `(600,300)-(660,340)`.
Its **colour** was right — within a few levels — and its **location** was 1000
pixels out. Rejecting the whole proposal would throw away a correct colour, so the
colour is kept and the region is **measured**: a per-channel mask, bounding box, and
the mean of the pixels inside it. That run recovered `(600,300)-(661,341)` exactly,
measured the mean colour as `[9,255,60]`, and wrote a tolerance of **9 with a margin
of 32 levels**. The colour in the profile is measured too, so the model only has to
be roughly right about what it is looking for.

*More than one frame.* `--also frame2.png` requires the result to hold on every
frame given. A threshold fitted to one screenshot is a threshold that fails on the
next one, and the tool says so rather than saving it.

*Endpoints.* Tried in order, each verified live with `check`, and the reply says
which one answered and why the others did not. Two things had to be fixed to make
the chain real, both measured rather than guessed:

- urllib's default User-Agent is on Cloudflare's bot list, and one endpoint answers
  it with `HTTP 403 / error code: 1010` — a working endpoint that looks dead.
- `"stream": true` is a request, not a guarantee: one gateway ignores it and answers
  with a single JSON body, which an SSE-only reader sees as *zero content*.
- A reasoning model spends its token budget thinking first. Asked for one word with
  `max_tokens=10`, it produced 9 reasoning tokens and no answer at all — which reads
  as a dead endpoint unless the usage block is read.

## What the profile is for

`profiles/marker.ini` is read by `lib/nm_verify.ahk` **by its presence**. Drop one in
and the drift detector uses the calibrated colour, tolerance and region; delete it and
the built-in constants apply again. No setting to enable, no config to edit, and a
malformed profile is ignored rather than fatal.

Before this, nothing read those files. The detector used constants compiled into
`nm_verify.ahk`, so a calibrated threshold had no effect at all — the advisor's
"AI proposes, verifier tests, the macro executes" claim was missing its last link.

## Two load-time defects found and fixed

Both were invisible to function-level tests, and both stopped the macro dead.

**`lib/nm_verify.ahk` did not compile.** Four lines used AHK **v1** command syntax —
`SplitPath, x, , dir`, `FileCreateDir, %dir%`, `FileAppend, %frac%`n, %path%`,
`Loop, Read, %path%` — in a v2 script. AHK reports
`Function calls require a space or "(" ` at the first one. Because `natro_macro.ahk`
`#Include`s this file at line 10597, **one bad line in a library stopped the whole
macro from starting**, and no function-level test can see that: they extract
functions and run them in isolation, never compiling the file they came from.

**The sample-file path contained two control bytes.** `"..\verify\flower_samples.txt"`
had been written through a string that turned `\v` and `\f` into a vertical tab and a
form feed. AHK accepts that silently, so the check passed and the file was simply
never found: the self-calibration could never read or write a single sample. The path
now derives from `NMV_DIR`.

Verified by loading the file with the bundled engine (`AutoHotkey64.exe`, reported
version 2.0.12) and checking for an error line — not by grepping, and not by
compiling, which hangs on a warning dialog.

## Integration status

| what | state |
|---|---|
| arrival + progress hooks in `nm_gotoField` / `nm_walkFrom` | **wired in**, 4 call sites |
| `#Include "nm_verify.ahk"` at line 10597 | **in** |
| profile reader + profile-driven colour/tolerance/region | **in**, picks up by file presence |
| `nm_HexLockCorrect` | **no caller in the macro.** The drift detector and its calibrated profile exist and are testable, but nothing calls it from a run yet |

**Why that include is not automatic:** Natro writes `#Include "%A_ScriptDir%\..\lib"`,
and per the AHK docs a `#Include` naming a **directory is a chdir, not a glob** — it
"changes the working directory used by all subsequent occurrences of #Include". Nothing
in `lib/` is pulled in automatically. Assuming otherwise would have left the call sites
undefined at load.

## Still not done

**Nothing has been playtested.** The advisor's own tests pass (59/59), the AHK library
loads cleanly, and both vision endpoints answer live — but no BMS macro here has run
in game. `nm_HexLockCorrect` in particular has never been called by the macro: it is
written, calibrated and dry-run capable, and the first thing a playtest should do is
call it with `dryRun := true` and read `verify.log` to see what the marker detector
measures on a real field.


## Revolution Macro — what was and was not taken

**Not included, and cannot be merged:** its engine ships as a **compiled executable**,
no source, no stated licence. There is nothing to redistribute and nothing to merge
against.

**What was taken is design knowledge:** Revolution's patterns are parametric Lua
exposing `Walk` / `WalkAsync` / `SleepStuds` / `Key.Down` / `Pattern.*` — a cleaner API
surface than AHK command syntax, and it shaped the interface used here. Also worth
knowing, since it appears in circulating "limitation" lists: Revolution has **no AI
vision model** and **no macOS support** (it is a Windows binary), and **Natro already
implements buff reactivity** — `GatherFieldBoostedStart` with 900-second windows, plus
`PFieldBoostExtend` and `PFieldGuidExtend`. Building those would duplicate working code.

## Licence

**Natro Macro belongs to its own authors and is GPL-3.0** (see `LICENSE.md`, unchanged).
Including it makes this repository a GPL-3.0 derivative. The additions in
`lib/nm_verify.ahk` and `tools/ai_advisor.py` are released under the same terms.

Not affiliated with Natro Macro or Revolution Macro.
