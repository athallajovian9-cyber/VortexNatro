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

A vision model **proposes** a colour threshold; a verifier **tests the proposal against
the same frame** before anything is applied; the macro executes it every frame.

**Measured:** asked for a `#17263A` button the model answered `#111111` — wrong on every
channel — and a region at `y528` in a **513-pixel-tall** image, entirely off-frame.
Applied blindly it would have shipped a detector that silently never matches. The
verifier rejected it.

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


## Licence

Natro Macro belongs to the **Natro Team** and is **GPL-3.0** — see `LICENSE.md`,
unchanged. Including it makes this repository a GPL-3.0 derivative. The added files are
released under the same terms.

Revolution Macro is **not** included (compiled, no source, no stated licence).
