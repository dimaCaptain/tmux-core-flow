# Compact tmux-core-flow dashboard launcher (Windows side).
# Sizes the console small (<= buffer, no scrollbar), enables VT processing,
# then runs the WSL dashboard. Invoked by flow-dashboard.ahk on Ctrl+\.

# Window first (must be <= buffer), then buffer — removes scrollbar.
# ~10 rows for up to ~9 tmux windows (bump both equally if you have more).
$Host.UI.RawUI.WindowSize = New-Object Management.Automation.Host.Size(32, 12)
$Host.UI.RawUI.BufferSize = New-Object Management.Automation.Host.Size(32, 12)

# Kill any stale dashboard before starting a fresh one.
wsl.exe bash -lc "pkill -f 'flow-dashboard' 2>/dev/null; sleep 0.3"

# Enable VT output / disable line-wrap on the input handle.
$sig = @'
[DllImport("kernel32.dll")] public static extern bool SetConsoleMode(IntPtr h, uint m);
[DllImport("kernel32.dll")] public static extern bool GetConsoleMode(IntPtr h, out uint m);
[DllImport("kernel32.dll")] public static extern IntPtr GetStdHandle(int n);
'@
$k = Add-Type -Name K -Namespace W -MemberDefinition $sig -PassThru
$hIn = $k::GetStdHandle(-10)
$m = 0
[void]$k::GetConsoleMode($hIn, [ref]$m)
[void]$k::SetConsoleMode($hIn, ($m -band -bnot 0x40) -bor 0x80)

wsl.exe bash -lic "flow-dashboard --compact"
