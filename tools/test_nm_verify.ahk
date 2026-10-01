; =====================================================================
;  test_nm_verify.ahk  -  behavioural tests for the profile-driven detector
; =====================================================================
; The file LOADING is not the same as the file WORKING. Everything here runs
; without the game: a synthetic capture buffer stands in for the screen, so the
; two colour rules in nm_MarkerX can be told apart and the profile reader can be
; tested against a profile the PYTHON writer actually produced.
;
;   tools/test_ai_advisor.py writes profiles\_contract_marker.ini
;   this script reads it back with the same reader the macro uses
;
; That pair is the contract: a profile format the writer emits and the reader
; cannot parse would leave the calibration silently inert.
;
; RUN
;   ..\submacros\AutoHotkey64.exe /ErrorStdOut test_nm_verify.ahk
; Exits 0 on pass, 1 on any failure. Results go to stdout AND to a file, because
; AHK is a GUI-subsystem app and stdout is frequently detached from the caller.
; =====================================================================
#Requires AutoHotkey v2.0
#SingleInstance Off
; Warnings default to a MsgBox. That BLOCKS the process with no output, so a
; compile check looks like a hang rather than a report - which is exactly how a
; genuine defect hid here: the library reads windowWidth without declaring it
; global, and the only signal is this warning. Sent to stdout it is readable,
; and the runner treats any warning as a failure.
#Warn All, StdOut

; The macro declares `paths` at the top level, which makes it a super-global that
; nm_RecoverOrGiveUp reads without redeclaring. Mirror that here, or the engine
; warns about the missing global and "no warnings" stops meaning anything.
global paths := Map()

; ---------------------------------------------------------------------
;  STUBS FOR THE HOST FUNCTIONS
;
;  nm_verify.ahk calls eight functions that live in natro_macro.ahk and
;  Roblox.ahk, not in itself. AHK v2 resolves a direct call at LOAD time, so
;  including the library WITHOUT those functions does not fail at the call -
;  it fails to load at all, with an error dialog that blocks the process. That
;  is why "it loads fine" has to be tested with the host present, and why a
;  bare `AutoHotkey64.exe lib\nm_verify.ahk` check hangs instead of reporting.
;
;  None of these are called by the tests below; they exist so the file can be
;  compiled on its own. GetRobloxClientPos is deliberately a no-op rather than a
;  real screen probe: a test must not touch the screen.
; ---------------------------------------------------------------------
GetRobloxClientPos() {
    ; assigns them, like the real one does in lib\Roblox.ahk - an empty stub
    ; leaves the globals unassigned and the engine warns about it at load
    global windowX, windowY, windowWidth, windowHeight
    windowX := 0
    windowY := 0
    windowWidth := 800
    windowHeight := 600
    return 0
}
nm_Reset(*) {
    return 0
}
nm_convert(*) {
    return 0
}
nm_createPath(*) {
    return 0
}
nm_endWalk(*) {
    return 0
}
nm_gotoField(*) {
    return 0
}
nm_walkFrom(*) {
    return 0
}
nm_setStatus(*) {
    return 0
}

#Include "..\lib\nm_verify.ahk"

global PASSED := 0
global FAILED := 0
global RESFILE := A_ScriptDir . "\_test_nm_verify_results.txt"

say(line) {
    global RESFILE
    try FileAppend(line . "`r`n", RESFILE)
}

check(name, cond, extra := "") {
    global PASSED, FAILED
    if (cond) {
        PASSED += 1
        say("  PASS  " . name)
    } else {
        FAILED += 1
        say("  FAIL  " . name . (extra != "" ? "   -> " . extra : ""))
    }
}

rule(t) {
    say("")
    say("  ---- " . t . " ----")
}

; A synthetic BGRA capture buffer, gw x gh, filled with one colour and with a
; block of another. Same layout nm_MarkerX reads: off=B, off+1=G, off+2=R.
makeBuf(gw, gh, bg, block, bx1, bx2, by1 := -1, by2 := -1) {
    buf := Buffer(gw * gh * 4, 0)
    if (by1 < 0)
        by1 := 0
    if (by2 < 0)
        by2 := gh
    loop gh {
        y := A_Index - 1
        loop gw {
            x := A_Index - 1
            c := (x >= bx1 && x < bx2 && y >= by1 && y < by2) ? block : bg
            off := (y * gw + x) * 4
            NumPut("UChar", c[3], buf, off)
            NumPut("UChar", c[2], buf, off + 1)
            NumPut("UChar", c[1], buf, off + 2)
            NumPut("UChar", 255,  buf, off + 3)
        }
    }
    return buf
}

try FileDelete(RESFILE)

say("  ==== nm_verify :: behavioural tests ====")

GREEN   := [0, 255, 51]        ; the built-in target
MAGENTA := [200, 10, 240]      ; a colour only a profile can name
BGCOL   := [18, 40, 22]        ; field green

GW := 64, GH := 32
REG_L := 440, REG_R := 840, REG_TOP := 260, REG_BOT := 460

; ---------------------------------------------------------------------
rule("the built-in rule, with no profile in force")

nm_ProfileReset()
greenBuf := makeBuf(GW, GH, BGCOL, GREEN, 20, 28)
xGreen := nm_MarkerX(greenBuf, GW, GH, REG_L, REG_TOP, REG_R, REG_BOT)
check("the built-in window finds a neon-green marker", xGreen >= 0, xGreen)
check("and puts it inside the region it was given",
      xGreen >= REG_L && xGreen <= REG_R, xGreen)

magentaBuf := makeBuf(GW, GH, BGCOL, MAGENTA, 20, 28)
xMag := nm_MarkerX(magentaBuf, GW, GH, REG_L, REG_TOP, REG_R, REG_BOT)
check("the built-in window does NOT find magenta", xMag = -1, xMag)

blank := makeBuf(GW, GH, BGCOL, BGCOL, 0, 0)
check("a frame with no marker returns -1, not a guess",
      nm_MarkerX(blank, GW, GH, REG_L, REG_TOP, REG_R, REG_BOT) = -1)

; ---------------------------------------------------------------------
rule("the profile, written by ai_advisor.py, read by the macro")

profPath := A_ScriptDir . "\..\profiles\_contract_marker.ini"
check("the python writer produced the fixture", FileExist(profPath), profPath)

if FileExist(profPath) {
    check("nm_ProfileLoad accepts it", nm_ProfileLoad("_contract_marker"))
    check("and it parsed a colour", NMV_PROFILE.Has("rgb"),
          nm_ProfileDescribe())
    check("the rgb matches what the writer put in the file",
          NMV_PROFILE["rgb"][1] = 200 && NMV_PROFILE["rgb"][2] = 10
          && NMV_PROFILE["rgb"][3] = 240, nm_ProfileDescribe())
    check("the tolerance came through", NMV_PROFILE["tolerance"] = 12,
          NMV_PROFILE["tolerance"])
    check("the region came through",
          NMV_PROFILE.Has("x1") && NMV_PROFILE["x1"] = 100
          && NMV_PROFILE["y2"] = 90, nm_ProfileDescribe())
    check("the describe line names the file, so a miss can say which rule ran",
          InStr(nm_ProfileDescribe(), "_contract_marker") > 0, nm_ProfileDescribe())

    ; with that profile in force the rules must SWAP
    xMag2 := nm_MarkerX(magentaBuf, GW, GH, REG_L, REG_TOP, REG_R, REG_BOT)
    check("with the profile loaded, magenta IS found", xMag2 >= 0, xMag2)
    xGreen2 := nm_MarkerX(greenBuf, GW, GH, REG_L, REG_TOP, REG_R, REG_BOT)
    check("and the built-in green is NOT - the profile replaced the rule",
          xGreen2 = -1, xGreen2)
}

; ---------------------------------------------------------------------
rule("a bad profile must fall back, never break the detector")

nm_ProfileReset()
badPath := A_ScriptDir . "\..\profiles\_bad_marker.ini"
if FileExist(badPath)
    FileDelete(badPath)

FileAppend("; junk`r`n[detect]`r`nrgb=1,2`r`ntolerance=10`r`n", badPath)
check("rgb with two components is refused", !nm_ProfileLoad("_bad_marker"))

if FileExist(badPath)
    FileDelete(badPath)
FileAppend("; junk`r`n[detect]`r`nrgb=1,2,3`r`ntolerance=notanumber`r`n", badPath)
check("a non-numeric tolerance is refused", !nm_ProfileLoad("_bad_marker"))

if FileExist(badPath)
    FileDelete(badPath)
FileAppend("; junk`r`n[detect]`r`nrgb=300,0,0`r`ntolerance=10`r`n", badPath)
check("an out-of-range channel is refused", !nm_ProfileLoad("_bad_marker"))

if FileExist(badPath)
    FileDelete(badPath)
FileAppend("; junk`r`n[detect]`r`nrgb=200,10,240`r`ntolerance=12`r`nx1=500`r`n", badPath)
check("a PARTIAL region is refused - the colour still loads",
      nm_ProfileLoad("_bad_marker") && !NMV_PROFILE.Has("x1"),
      nm_ProfileDescribe())

check("a missing profile file is refused, not fatal", !nm_ProfileLoad("_nope_missing"))
if FileExist(badPath)
    FileDelete(badPath)

; after a refusal the built-in rule must be back
nm_ProfileReset()
check("after a refusal the built-in rule is in force again",
      nm_MarkerX(greenBuf, GW, GH, REG_L, REG_TOP, REG_R, REG_BOT) >= 0)

; ---------------------------------------------------------------------
say("")
say("  RESULT  passes=" . PASSED . "  fails=" . FAILED)
say("")
try FileAppend("  RESULT  passes=" . PASSED . "  fails=" . FAILED . "`r`n", "*")
catch
ExitApp(FAILED = 0 ? 0 : 1)
