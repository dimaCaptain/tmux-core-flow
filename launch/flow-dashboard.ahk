; tmux-core-flow — compact dashboard hotkey (standalone).
;
; Ctrl+\  toggles a pinned, borderless, always-on-top "Agents Dashboard"
; widget that runs `flow-dashboard --compact` in WSL.
;
; Install: run this file with AutoHotkey v2, or #Include it from your main
; AHK script. install.sh copies it (and the .ps1) to C:\Users\user\tools\.
;
; Requires: launch-flow-dashboard.ps1 in the same folder as referenced below.

#Requires AutoHotkey v2.0
#SingleInstance Force

DASHBOARD_POS_FILE := "C:\Users\user\.cache\flow-dashboard-pos.txt"
PS1 := "C:\Users\user\tools\launch-flow-dashboard.ps1"
TITLE := "Agents Dashboard"
global LastDashboardState := ""

; --- persist window position/size while open ---
PollDashboardSize() {
    global LastDashboardState, DASHBOARD_POS_FILE, TITLE
    if !WinExist(TITLE)
        return
    WinGetPos(&wx, &wy, &ww, &wh, TITLE)
    state := wx "|" wy "|" ww "|" wh
    if (state != LastDashboardState) {
        LastDashboardState := state
        try {
            dir := "C:\Users\user\.cache"
            if !DirExist(dir)
                DirCreate(dir)
            if FileExist(DASHBOARD_POS_FILE)
                FileDelete(DASHBOARD_POS_FILE)
            FileAppend(state, DASHBOARD_POS_FILE)
        }
    }
}
SetTimer PollDashboardSize, 2000

GetDashboardPos() {
    global DASHBOARD_POS_FILE
    if !FileExist(DASHBOARD_POS_FILE)
        return [40, 40, 575, 425]
    try {
        parts := StrSplit(Trim(FileRead(DASHBOARD_POS_FILE)), "|")
        if (parts.Length >= 4)
            return [Integer(parts[1]), Integer(parts[2]), Integer(parts[3]), Integer(parts[4])]
    }
    return [40, 40, 575, 425]
}

; --- Ctrl+\ : toggle ---
^vkDC:: ToggleDashboard()

ToggleDashboard(*) {
    global PS1, TITLE
    if WinExist(TITLE) {
        Run 'wsl.exe bash -lc "pkill -f flow-dashboard"',, "Hide"
        Sleep 200
        WinClose(TITLE)
        return
    }
    Run 'conhost.exe powershell.exe -NoProfile -ExecutionPolicy Bypass -File "' PS1 '"'
    if WinWait(TITLE,, 5) {
        WinSetAlwaysOnTop(1, TITLE)
        WinSetStyle("-0xC00000", TITLE)        ; borderless
        pos := GetDashboardPos()
        WinMove(pos[1], pos[2], pos[3], pos[4], TITLE)
        WinSetTransparent(230, TITLE)          ; ~90% opacity
        hwnd := WinExist(TITLE)
        DetectHiddenWindows(true)
        WinHide("ahk_id " hwnd)
        WinSetExStyle("-0x40000", "ahk_id " hwnd)  ; remove WS_EX_APPWINDOW
        WinSetExStyle("+0x80", "ahk_id " hwnd)     ; add WS_EX_TOOLWINDOW (hide from taskbar)
        DllCall("SetWindowPos", "ptr", hwnd, "ptr", 0, "int", 0, "int", 0, "int", 0, "int", 0, "uint", 0x37)
        WinShow("ahk_id " hwnd)
        DetectHiddenWindows(false)
        WinSetAlwaysOnTop(1, "ahk_id " hwnd)
    }
}

; --- Alt+LButton drag to move the borderless widget ---
#HotIf WinActive("Agents Dashboard")
!LButton:: {
    CoordMode "Mouse", "Screen"
    MouseGetPos &mx1, &my1
    WinGetPos &wx, &wy, , , "Agents Dashboard"
    offx := mx1 - wx, offy := my1 - wy
    while GetKeyState("LButton", "P") {
        MouseGetPos &mx, &my
        WinMove(mx - offx, my - offy, , , "Agents Dashboard")
        Sleep 10
    }
}
#HotIf
