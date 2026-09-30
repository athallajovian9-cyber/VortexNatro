; =====================================================================
;  nm_verify  -  arrival and progress verification for Natro Macro
;  AutoHotkey v2.0   (Natro's own target: #Requires AutoHotkey v2.0)
;
;  WHAT THIS ADDS  -  Option 3
;    Natro computes a distance and trusts it. Nothing ever asks "did the
;    character actually arrive?", so a lag spike, a bee knockback, a
;    collision or a hive wedge all give the same result: the macro gathers
;    on empty ground for the full duration and reports nothing wrong.
;    These two checks close that loop.
;
;    Additive: delete the two call sites and Natro behaves exactly as it
;    shipped.
;
;  WHERE IT HOOKS IN
;    nm_gotoField() and nm_walkFrom() each end with
;        KeyWait "F14", "T120 L"    ; the path process signals completion
;    The verification call goes immediately after that line, in the MAIN
;    process. Path files cannot do it themselves - they are piped as text
;    to a separate AutoHotkey process, so nothing defined here is in scope
;    there.
;
;  THREE ANSWERS, NEVER TWO
;     1 = verified good
;     0 = verified BAD
;    -1 = cannot tell (no reference image yet)
;
;    -1 must never be treated as a pass. A verification that cannot fail is
;    worse than none, because it teaches you to trust an unverified run.
; =====================================================================

global NMV_DIR  := A_ScriptDir . "\..\verify"
global NMV_LAST := -1
; Fraction of sampled pixels that must be saturated red/blue to call it a field.
; Tune from the value logged each run rather than guessing.
global NMV_FLOWER_MIN := 0.02
global NMV_FAILS := 0

; ---------------------------------------------------------------------
;  Arrival: is the character standing on the expected field?
; ---------------------------------------------------------------------
nm_VerifyArrived(location) {
    ; FLOWER MASS, NOT A REFERENCE IMAGE.
    ;
    ; The obvious design - screenshot the field, match it later - is broken by
    ; the game itself: "the flowers also vary each time a player joins a server
    ; based on how the field is generated." A reference captured on one server
    ; would report NOT ON FIELD on every other one, which is worse than no check
    ; at all because it teaches you to distrust a working macro.
    ;
    ; What does NOT change per server: a field is always full of flowers, and
    ; they are always white, red and blue. Regeneration changes the layout, not
    ; the fact that the area is dense with flower colour. So the check is
    ; "is there flower-coloured pixel mass in view?".
    ;
    ; White is ignored on purpose: clouds, the HUD and beehives are white, so it
    ; discriminates nothing. Saturated red and blue do - against green and brown
    ; terrain they are unmistakable.
    ;
    ; Logs the measured fraction every run, so the threshold can be set from
    ; observed values instead of guessed.

    GetRobloxClientPos()
    if (windowWidth = 0)
        return -1

    ; Lower-centre band: where the field fills the frame when you are standing
    ; on it. Avoids the sky and the top HUD.
    x1 := windowX + Round(windowWidth  * 0.20)
    x2 := windowX + Round(windowWidth  * 0.80)
    y1 := windowY + Round(windowHeight * 0.45)
    y2 := windowY + Round(windowHeight * 0.85)

    frac := nm_FlowerFraction(x1, y1, x2, y2, 48, 28)
    if (frac < 0)
        return -1

    onField := frac >= NMV_FLOWER_MIN
    nm_LogVerify(location, (onField ? "on field" : "NOT ON FIELD")
        . " - flower mass " . Round(frac * 100, 2) . "% (threshold "
        . Round(NMV_FLOWER_MIN * 100, 2) . "%)")
    global NMV_LAST := onField ? 1 : 0
    return NMV_LAST
}

; ---------------------------------------------------------------------
;  Progress: did the screen change at all just now?
;  A frozen view while a walk pattern runs means the character is stuck, or
;  the game stopped responding. Catching it here costs seconds; catching it
;  at the end of a ten-minute gather costs the whole run.
; ---------------------------------------------------------------------
nm_VerifyMoving(changeTol := 2) {
    GetRobloxClientPos()
    if (windowWidth = 0)
        return -1

    a := nm_RegionSig(windowX, windowY, windowWidth, windowHeight)
    Sleep 250
    b := nm_RegionSig(windowX, windowY, windowWidth, windowHeight)
    return (nm_SigDelta(a, b) > changeTol) ? 1 : 0
}

; A 24x14 colour signature of the client area, via one cheap StretchBlt.
; v2 returns a Buffer directly - v1's "cannot return a local buffer"
; problem does not exist here.
nm_RegionSig(x, y, w, h, gw := 24, gh := 14) {
    hdcScreen := DllCall("GetDC", "Ptr", 0, "Ptr")
    hdcMem := DllCall("CreateCompatibleDC", "Ptr", hdcScreen, "Ptr")
    hbm := DllCall("CreateCompatibleBitmap", "Ptr", hdcScreen, "Int", gw, "Int", gh)
    hOld := DllCall("SelectObject", "Ptr", hdcMem, "Ptr", hbm, "Ptr")
    DllCall("SetStretchBltMode", "Ptr", hdcMem, "Int", 3)
    DllCall("StretchBlt", "Ptr", hdcMem, "Int", 0, "Int", 0, "Int", gw, "Int", gh
        , "Ptr", hdcScreen, "Int", x, "Int", y, "Int", w, "Int", h, "UInt", 0x00CC0020)

    bi := Buffer(40, 0)
    NumPut("UInt", 40, bi, 0)
    NumPut("Int", gw, bi, 4)
    NumPut("Int", -gh, bi, 8)
    NumPut("UShort", 1, bi, 12)
    NumPut("UShort", 32, bi, 14)
    buf := Buffer(gw * gh * 4, 0)
    DllCall("GetDIBits", "Ptr", hdcMem, "Ptr", hbm, "UInt", 0, "UInt", gh
        , "Ptr", buf, "Ptr", bi, "UInt", 0)

    DllCall("SelectObject", "Ptr", hdcMem, "Ptr", hOld)
    DllCall("DeleteObject", "Ptr", hbm)
    DllCall("DeleteDC", "Ptr", hdcMem)
    DllCall("ReleaseDC", "Ptr", 0, "Ptr", hdcScreen)
    return buf
}

; Fraction of sampled pixels that look like flower colour (saturated red or blue).
; One cheap StretchBlt to a small grid, then count. Returns -1 if it cannot tell.
nm_FlowerFraction(x, y, w, h, gw, gh) {
    buf := nm_RegionSig(x, y, w, h, gw, gh)
    if !buf
        return -1
    hits := 0
    loop gw * gh {
        off := (A_Index - 1) * 4
        b := NumGet(buf, off,     "UChar")
        g := NumGet(buf, off + 1, "UChar")
        r := NumGet(buf, off + 2, "UChar")
        red  := (r > 110) && (r > g * 1.35) && (r > b * 1.35)
        blue := (b > 110) && (b > r * 1.35) && (b > g * 1.35)
        if (red || blue)
            hits += 1
    }
    return hits / (gw * gh)
}

nm_SigDelta(a, b, gw := 24, gh := 14) {
    total := 0
    loop gw * gh {
        off := (A_Index - 1) * 4
        total += Abs(NumGet(a, off, "UChar") - NumGet(b, off, "UChar"))
        total += Abs(NumGet(a, off + 1, "UChar") - NumGet(b, off + 1, "UChar"))
        total += Abs(NumGet(a, off + 2, "UChar") - NumGet(b, off + 2, "UChar"))
    }
    return total / (gw * gh * 3)
}

; One line per check into a log, so a run is auditable afterwards rather
; than reconstructed from memory.
nm_LogVerify(what, verdict) {
    global NMV_FAILS
    if InStr(verdict, "NOT ON FIELD")
        NMV_FAILS += 1
    FileAppend(FormatTime(A_Now, "HH:mm:ss") . "  VERIFY [" . what . "] " . verdict . "`r`n"
        , A_ScriptDir . "\..\verify.log")
}

; =====================================================================
;  PROGRESS WATCHER   -  the other half of anti-drift
; =====================================================================
; Natro's main macro BLOCKS on `KeyWait "F14", "T120 L"` while the path
; process does the walking. Nothing in the main process is polling, so a
; character wedged against terrain is invisible until the path times out -
; or worse, until a ten-minute gather finishes on empty ground.
;
; A timer can interrupt that wait, so the watch lives here instead of in the
; blocked loop.
;
; Three consecutive frozen readings - roughly six seconds - is the trigger.
; One frozen reading is normal (a loading hitch); three is a character that
; is not moving.

global NMV_STALLS := 0
global NMV_WATCHING := false

nm_ProgressWatchStart() {
    global NMV_STALLS, NMV_WATCHING
    NMV_STALLS := 0
    NMV_WATCHING := true
    SetTimer(nm_ProgressWatcher, 2000)
}

nm_ProgressWatchStop() {
    global NMV_WATCHING
    NMV_WATCHING := false
    SetTimer(nm_ProgressWatcher, 0)
}

nm_ProgressWatcher() {
    global NMV_STALLS, NMV_WATCHING
    if !NMV_WATCHING
        return
    m := nm_VerifyMoving()
    if (m = 0) {                      ; screen frozen
        NMV_STALLS += 1
        nm_LogVerify("progress", "frozen reading " . NMV_STALLS . "/3")
        if (NMV_STALLS >= 3) {
            nm_LogVerify("progress", "STUCK - three frozen readings, aborting the walk")
            nm_ProgressWatchStop()
            nm_setStatus("STUCK - see verify.log", "FF6B4A")
        }
    } else if (m = 1) {
        NMV_STALLS := 0               ; moving again - reset
    }
    ; m = -1 means it could not tell. Do NOT count that as a stall: no
    ; information is not evidence of a problem.
}

; =====================================================================
;  SPRINKLER HEX-LOCK   -  closed-loop drift correction
; =====================================================================
; Static loops drift over hours of runtime: frame drops make a timed walk
; land fractionally long or short, and it compounds until the pattern is
; farming the wrong square. The fix is to stop trusting the timer and look
; at where the character actually IS.
;
; The Supreme Saturator (and high-tier sprinklers) drop a persistent NEON
; GREEN marker - #00FF33 to #33FF66 - in the middle of the field. That is a
; known colour at a known place, so drift is measurable and correctable
; without any template image or calibration.
;
;   marker left of the deadband  -> character drifted RIGHT -> tap A
;   marker right of the deadband -> character drifted LEFT  -> tap D
;   correction time is PROPORTIONAL to the pixel offset, so a small drift
;   gets a small nudge and never overshoots into a new drift.
;
; Numbers are from the field guide and are left as named values so they can
; be tuned from the logged measurements rather than edited blind.

global HEXLOCK_CX    := 640       ; centre X of the scan window
global HEXLOCK_BAND  := 20        ; deadband +/- around centre (620..660)
global HEXLOCK_MS_PER_PX := 4     ; correction duration per pixel of offset
global HEXLOCK_WIN   := { x1: 440, y1: 260, x2: 840, y2: 460 }

; Pure: mean X of neon-green pixels in the capture, or -1 if none.
; Kept separate so it can be tested without the game running.
nm_MarkerX(buf, gw, gh, x1, y1, x2, y2) {
    sumX := 0
    n := 0
    yy := 0
    while (yy < gh) {
        xx := 0
        while (xx < gw) {
            i := yy * gw + xx
            off := i * 4
            b := NumGet(buf, off,     "UChar")
            g := NumGet(buf, off + 1, "UChar")
            r := NumGet(buf, off + 2, "UChar")
            ; #00FF33 .. #33FF66 : strong green, little red, some blue
            if (g > 200 && r < 100 && b < 140) {
                sumX += x1 + (x2 - x1) * xx / gw
                n += 1
            }
            xx += 1
        }
        yy += 1
    }
    return (n > 0) ? Round(sumX / n) : -1
}

; Read the real screen and correct. Returns the measured X, or -1.
nm_HexLockCorrect(dryRun := false) {
    global HEXLOCK_CX, HEXLOCK_BAND, HEXLOCK_MS_PER_PX, HEXLOCK_WIN

    GetRobloxClientPos()
    if (windowWidth = 0)
        return -1

    w := HEXLOCK_WIN
    buf := nm_RegionSig(windowX + w.x1, windowY + w.y1
                      , w.x2 - w.x1, w.y2 - w.y1, 64, 32)
    x := nm_MarkerX(buf, 64, 32, w.x1, w.y1, w.x2, w.y2)
    if (x < 0) {
        nm_LogVerify("hexlock", "no sprinkler marker in the scan window")
        return -1
    }

    off := x - HEXLOCK_CX
    if (Abs(off) <= HEXLOCK_BAND) {
        nm_LogVerify("hexlock", "centred at X=" . x . " - no correction")
        return x
    }

    ms := Abs(off) * HEXLOCK_MS_PER_PX
    key := (off > 0) ? "d" : "a"          ; marker right of centre => drifted left
    if (!dryRun) {
        Send("{" . key . " down}")
        Sleep ms
        Send("{" . key . " up}")
    }
    nm_LogVerify("hexlock", "X=" . x . " off by " . off . "px - held " . key
        . " for " . ms . "ms" . (dryRun ? " (dry run)" : ""))
    return x
}

; =====================================================================
;  RECOVERY  -  detection without recovery is only half the job
; =====================================================================
; Found by playtest: cancelling the parachute mid-flight lands the character
; somewhere the route never accounted for. The arrival check DETECTED it and set a
; status message - and then the macro carried on farming from the wrong square.
;
; This does the other half: respawn and re-run the route, a bounded number of times.
;
; Deliberately NOT recursive. Re-entering nm_gotoField would re-enter recovery, and a
; route that always fails would recurse until the stack dies. One reset, one re-run,
; one re-check per attempt, then give up and let the caller move to another field.

global NMV_RECOVER := Map()          ; location -> attempts spent
global NMV_MAX_RECOVER := 2

nm_RecoverOrGiveUp(location) {
    global NMV_RECOVER, NMV_MAX_RECOVER

    tries := NMV_RECOVER.Has(location) ? NMV_RECOVER[location] : 0
    if (tries >= NMV_MAX_RECOVER) {
        nm_LogVerify(location, "recovery gave up after " . tries . " attempts")
        NMV_RECOVER.Delete(location)          ; reset for next time round
        return false
    }
    NMV_RECOVER[location] := tries + 1
    nm_LogVerify(location, "recovering: attempt " . (tries + 1) . "/" . NMV_MAX_RECOVER)

    ; Respawn at the hive, then walk the field route again. nm_Reset() is Natro's
    ; own respawn path, so this stays inside its machinery.
    ;
    ; BAG-FULL GUARD (user rule: never reset with a full bag - the pollen is lost).
    ;
    ; Confirmed from the game: "you lose all the pollen currently stored in your
    ; backpack when you reset your character". Players who reset on purpose rely on
    ; INSTANT CONVERSION; without it a reset discards the bag.
    ;
    ; Whether Natro's reset path banks the load first could not be established with
    ; certainty by reading - nm_Reset(..., convert:=1) calls nm_convert(), but the
    ; order relative to the respawn is not provable from the source alone. So this
    ; does not depend on that answer: when the bag is full it simply does NOT reset.
    ; The route is re-run from wherever the character stands. Nothing can be lost
    ; because nothing respawns, and a failed re-run gives up rather than risk it.
    if (nm_BagFull()) {
        global BackpackPercentFiltered
        nm_LogVerify(location, "bag at " . BackpackPercentFiltered
            . "% - NOT resetting (pollen would be lost); re-running the route in place")
        path := paths["gtf"][StrReplace(location, " ")]
        nm_createPath(path)
        KeyWait "F14", "D T5 L"
        KeyWait "F14", "T120 L"
        nm_endWalk()
        return (nm_VerifyArrived(location) = 1)
    }


    path := paths["gtf"][StrReplace(location, " ")]
    nm_createPath(path)
    KeyWait "F14", "D T5 L"
    KeyWait "F14", "T120 L"
    nm_endWalk()

    arrived := nm_VerifyArrived(location)
    if (arrived = 1) {
        NMV_RECOVER.Delete(location)          ; success: clear the count
        return true
    }
    if (arrived = -1)
        return true          ; cannot tell (no reference): do not spiral on unknowns
    return false
}

; ---------------------------------------------------------------------
;  BAG-FULL GUARD  (user rule)
; ---------------------------------------------------------------------
; "don't reset if the pollen is full - if reset all pollen lost."
;
; So the recovery must not blindly respawn. It reads Natro's OWN bag level
; (BackpackPercentFiltered, maintained by its convert scanning) rather than testing
; for a red pixel: a hardcoded single-pixel colour test is exactly how the
; Prospecting macro came to dig on a lake, and it would fail the moment the UI hue
; shifts.
;
; It decides whether to LOG the load. The reset itself converts, so the pollen is
; banked either way - this exists so a full-bag reset is visible in verify.log rather
; than silent, and so the threshold never becomes an assumption.
nm_BagFull(margin := 2) {
    global BackpackPercentFiltered, FieldUntilPack
    if !IsSet(BackpackPercentFiltered)
        return false                  ; no reading yet: never assume full
    limit := IsSet(FieldUntilPack) ? FieldUntilPack : 95
    return (BackpackPercentFiltered >= limit - margin)
}
