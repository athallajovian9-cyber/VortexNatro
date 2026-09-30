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
AI          proposes a colour threshold from a screenshot
VERIFIER    tests the proposal against that same frame
THRESHOLD   executes it every frame
```

**Measured, and why the verifier exists.** Asked for a `#17263A` button, the model
answered `#111111` — wrong on every channel — and gave a region at `y528` in a
**513-pixel-tall** image, entirely off-frame. Applied blindly it would have shipped a
detector that silently never matches. With the verifier it is **rejected, with the
reason shown**. Of the first two real proposals, one was rejected.

## Not done — stated plainly

- **The hooks are NOT wired in.** `natro_macro.ahk` is byte-identical to stock Natro
  (sha256 `0aba1007...`, 23,029 lines, 891,923 bytes). Five one-line insertions into
  `nm_gotoField` / `nm_walkFrom` were attempted by script and produced **four defects in
  four attempts**:
  1. a hook inserted before `nm_createPath`'s **definition**, not a call — dead code
  2. stop hooks landing in two **unrelated** functions
  3. my fix for #2 inserting a **duplicate** start
  4. an arrival check inside `nm_gotoBooster`, whose parameter is **`booster`**, not
     `location` — that would have thrown at runtime the first time a booster route ran

  All reverted. This integration is a **hand-edit job, not a scripted one.**
- **Nothing has been playtested.** Function-level tests pass (13/13) and the advisor's
  verifier is proven to reject bad proposals — but no BSS macro here has run in game.

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
