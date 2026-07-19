#!/usr/bin/env python3
"""Compact agents dashboard — a small always-on-top widget.

Renders one line per tmux window grouped by session:

    ── Agents Corp 1 ──
     1. 🔴 stew          opus    3m
     2. 🟡 dev-main      v4-pro  12s

Launched by hotkey via launch/launch-flow-dashboard.ps1 (Windows) → wsl →
flow-dashboard. Refreshes in place without flicker; `--once` prints one frame.
"""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core import agents  # noqa: E402

# ANSI
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
MAGENTA = "\033[35m"
COLORS = {"yellow": "\033[33m", "red": "\033[31m", "gray": "\033[90m"}

REFRESH = float(os.environ.get("FLOW_REFRESH", "2"))


def render() -> str:
    by_session = agents.collect()
    if not by_session:
        return f"{COLORS['red']}no tmux sessions{RESET}\n"
    out = []
    for sess, items in by_session.items():
        out.append(f"{DIM}── {RESET}{BOLD}{MAGENTA}{sess}{RESET}{DIM} ──{RESET}")
        for a in items:
            color = COLORS.get(a.color, COLORS["gray"])
            name = a.name[:14]
            out.append(f"{color}{a.index:>2}. {a.dot} {name:<14} "
                       f"{a.model:<10} {a.elapsed:<6}{RESET}")
    out.append(f"{DIM}[Ctrl+C=quit]{RESET}")
    return "\n".join(out) + "\n"


def _set_title():
    # OSC 0 — lets the AHK launcher find/pin the "Agents Dashboard" window.
    sys.stdout.write("\033]0;Agents Dashboard\007")


def main(argv: list[str]) -> int:
    once = "--once" in argv
    _set_title()
    if once:
        sys.stdout.write("\033[H\033[2J" + render())
        sys.stdout.flush()
        return 0
    try:
        import time
        while True:
            sys.stdout.write("\033[H" + render() + "\033[0J")
            sys.stdout.flush()
            time.sleep(REFRESH)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
