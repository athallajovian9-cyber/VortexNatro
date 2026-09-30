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

## Integration status — WIRED IN (and how it was done)

The four hooks are in, placed **by hand** after four scripted attempts produced four
defects. What worked:

| function | hooks | arrival check? |
|---|---|---|
| `nm_gotoField` | start + stop | **yes** — it goes to a field |
| `nm_walkFrom` | start + stop | **no** — it goes to the HIVE, so a field signature would test the wrong thing |

Plus one `#Include "nm_verify.ahk"` at line 10597.

**Why that include is not automatic:** Natro writes `#Include "%A_ScriptDir%\..\lib"`,
and per the AHK docs a `#Include` naming a **directory is a chdir, not a glob** — it
"changes the working directory used by all subsequent occurrences of #Include". Nothing
in `lib/` is pulled in automatically. Assuming otherwise would have left the call sites
undefined at load.

Verified after the edit: one include directive, all three called functions resolve to
definitions, and both functions have a matched start/stop pair.

## Still not done

**Nothing has been playtested.** Function-level tests pass (13/13) and the advisor's
verifier is proven to reject bad proposals, but no BMS macro here has run in game.


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
