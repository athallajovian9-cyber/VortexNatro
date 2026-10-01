> # ⚠️ UNOFFICIAL — MODIFIED COPY OF NATRO MACRO
>
> **This is NOT Natro Macro.** It is a **modified copy of v1.1.2** with additions made
> by someone else, published here as a personal build.
>
> * **Not affiliated with, endorsed by, or supported by the Natro Team.**
> * **Do not report bugs here.** The Natro Team cannot help with a build they did not
>   make, and this copy has been changed.
> * **Get the official macro instead:**
>   <https://github.com/NatroTeam/NatroMacro/releases> · <https://discord.gg/natromacro>
> * Natro's own README warns: *"Make sure you are only downloading from an official
>   source!"* — this is **not** one. Use it at your own risk.
>
> If you want Natro Macro, use the links above. This repository exists only to document
> and share the verification layer described below.

---

# VortexNatro — unofficial build notes

## What is changed vs the official v1.1.2

| File | Status |
|---|---|
| `submacros/natro_macro.ahk` | **one edit** — a fallback in `RunWith32()` (see below). Otherwise stock. |
| `lib/`, `paths/`, `patterns/`, `nm_image_assets/`, `LICENSE.md` | **unchanged** |
| `lib/nm_verify.ahk` | **ADDED** — verification module, not the Natro Team's work |
| `tools/ai_advisor.py` | **ADDED** — AI threshold advisor, not the Natro Team's work |
| `README.md` | replaced with this warning. Natro's original is kept as `NATRO_OFFICIAL_README.md` |

## Why the additions exist

Neither Natro nor Revolution Macro ever asks *"did the character arrive?"* Natro
integrates a **measured speed** over time — a closed loop on speed, but **not on
position**. So a lag spike, a bee knockback, a collision or a hive wedge all produce the
same silent result: the macro gathers on empty ground for the full duration and reports
nothing wrong.

Three checks close that:

| check | signal | why it survives reality |
|---|---|---|
| **Arrival** | flower mass — saturated red/blue pixels | a stored screenshot cannot work: the game **regenerates a field's flower layout on every server join**, so a reference image reports NOT ON FIELD everywhere but the server it came from |
| **Progress** | screen frozen 3 readings in a row (~6 s) | Natro's main macro **blocks** on `KeyWait "F14"`, so this is a **timer** that interrupts the wait |
| **Drift** | the sprinkler's neon-green marker `#00FF33`–`#33FF66` | known colour, proportional correction on **observed position** — holds under any speed change |

Answers are `1` good, `0` bad, `-1` **cannot tell**. `-1` is never a pass: a check that
cannot fail is worse than none.

## The AI advisor

A vision model **names the colour**; a verifier **tests the proposal against the same
frame**; the tolerance actually written is **measured from that frame**, not taken from
the model. The macro executes it every frame.

**Measured, and why nothing numeric is trusted.** Asked for a `#17263A` button the
model answered `#111111` — wrong on every channel — and gave a region at `y528` in a
**513-pixel-tall** image, entirely off-frame. On a later frame it named the right colour
and put the region 1000 pixels away from it. Neither is usable as-is, and rejecting the
whole proposal would throw away a correct colour — so the colour is kept and the region
is **measured** (mask, bounding box, mean colour). That run recovered the marker's box
exactly and wrote a tolerance of 9 with a margin of 32 levels.

`--also frame2.png` requires the result to hold on a second frame: a threshold fitted to
one screenshot is a threshold that fails on the next one.

`profiles/*.ini` is read by `lib/nm_verify.ahk` by its presence. Delete the file and the
built-in constants apply again. See `VORTEXNATRO.md` for the two load-time defects this
work also fixed.

## Integration status

| what | state |
|---|---|
| arrival + progress hooks in `nm_gotoField` / `nm_walkFrom` | **wired in**, 4 call sites |
| `#Include "nm_verify.ahk"` at line 10597 | **in** |
| profile reader + profile-driven colour/tolerance/region | **in**, picks up by file presence |
| `nm_HexLockCorrect` (drift) | **no caller in the macro yet** — written, calibrated, dry-run capable; nothing calls it from a run |

**Because it is included, a syntax error in `lib/nm_verify.ahk` stops the whole macro.**
That is not hypothetical: four AHK v1 command lines in this file meant the macro could
not start at all until they were fixed, and function-level tests could not see it. See
`VORTEXNATRO.md`.

**Why the include is not automatic:** Natro writes `#Include "%A_ScriptDir%\..\lib"`,
and per the AHK docs a `#Include` naming a **directory is a chdir, not a glob** — it
"changes the working directory used by all subsequent occurrences of #Include". Nothing
in `lib/` is pulled in automatically. Assuming otherwise would have left the call sites
undefined at load.

## Still not done

**Nothing has been playtested.** The advisor's tests pass (59/59) and the AHK library
loads cleanly, but no BMS macro here has run in game. The first thing a playtest should
do is call `nm_HexLockCorrect(true)` - dry run, sends no input - and read `verify.log` to
see what the marker detector measures on a real field.


## Licence

Natro Macro belongs to the **Natro Team** and is **GPL-3.0** — see `LICENSE.md`,
unchanged. Including it makes this repository a GPL-3.0 derivative. The added files are
released under the same terms.

Revolution Macro is **not** included (compiled, no source, no stated licence).
