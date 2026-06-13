"""Thin wrappers over the tmux CLI (read-only for the dashboard)."""
from __future__ import annotations

import subprocess

_FORMAT = (
    "#{session_name}|#{window_index}|#{window_name}|#{pane_current_command}|"
    "#{window_active}|#{pane_current_path}|#{pane_pid}"
)


def run(args: list[str]) -> str:
    """Run `tmux <args>` and return stdout, or "" on failure."""
    try:
        r = subprocess.run(
            ["tmux", *args], capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout if r.returncode == 0 else ""


def list_windows() -> list[dict]:
    """All windows across all sessions, newest tmux fields parsed into dicts."""
    raw = run(["list-windows", "-a", "-F", _FORMAT])
    windows = []
    for line in raw.splitlines():
        parts = line.split("|")
        if len(parts) < 4:
            continue
        windows.append({
            "session": parts[0],
            "index": parts[1],
            "name": parts[2],
            "cmd": parts[3],
            "active": parts[4] if len(parts) > 4 else "0",
            "path": parts[5] if len(parts) > 5 else "",
            "pane_pid": parts[6] if len(parts) > 6 else "",
        })
    return windows


def list_sessions() -> list[str]:
    """Session names. The main "agents" session (if any) is sorted first."""
    raw = run(["list-sessions", "-F", "#{session_name}"])
    names = [s for s in raw.splitlines() if s]
    names.sort(key=lambda s: (s != "agents", s))
    return names


def capture(window: str, lines: int, session: str | None = None) -> str:
    """Capture the last `lines` rows of a pane."""
    target = f"{session}:{window}" if session else window
    return run(["capture-pane", "-p", "-t", target, "-S", f"-{lines}"])
