; =====================================================================
;  nm_humanize  -  timing variance.  Same actions, same order, same routes.
;  AutoHotkey v2.0
;
;  WHAT THIS DOES AND DOES NOT DO
;    It does NOT hide the macro, and it cannot make automation allowed - Roblox's
;    ToS prohibits it either way, and nothing here changes that.
;
;    What it does is remove the cheapest signal there is: MACHINE-PRECISE
;    REPEATABILITY. A human holding W for 3.19 seconds five hundred times in a row
;    is not a human. Every duration being bit-identical, forever, is a signature
;    that costs nothing to look for.
;
;    Same route. Same order. Same actions. Only the timing stops being exact.
;
;  WHAT ACTUALLY MATTERS MORE (in order)
;    1. Do not touch the client. No executors, no injection, no modified files.
;       That is the category that gets accounts terminated, and an OS-level input
;       macro is not in it.
;    2. Do not run impossible uptime. 24/7 identical loops are obvious from the
;       server side and no client-side change hides them. Take breaks.
;    3. Do not stack a second input tool. Two injectors fighting is also how you
;       nearly died in BSS - it is a safety problem before it is a detection one.
;    4. Avoid rapid-fire input. A burst of keypresses with no gaps reads as a bot;
;       humans have gaps.
;
;  This module addresses 4 and softens the timing half of 2. It cannot help with 1.
; =====================================================================

; Random percentage variance on a duration. Default +/-8%: large enough to break
; exact repetition, small enough that a route still lands on the same field.
nm_Jitter(ms, pct := 8) {
    if (ms <= 0)
        return ms
    d := Round(ms * pct / 100)
    if (d < 1)
        return ms
    return ms + Random(-d, d)
}

; Same, for a stud distance.
nm_JitterStuds(studs, pct := 8) {
    if (studs <= 0)
        return studs
    d := studs * pct / 100
    if (d <= 0)
        return studs
    return studs + Random(-d, d)
}

; An occasional short idle. Default: a 3% chance of a 1-4 second pause per call.
; A macro that never pauses for hours is itself the signal.
nm_HumanPause(chancePct := 3, minS := 1, maxS := 4) {
    if (Random(1, 100) > chancePct)
        return 0
    s := Random(minS, maxS)
    nm_LogVerify("humanize", "idle " . s . "s")
    Sleep(s * 1000)
    return s
}
