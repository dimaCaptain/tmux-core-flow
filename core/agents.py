"""Collect normalized agent state across all tmux sessions.

This is the shared model the dashboard renders. It reads tmux + each agent's
Claude transcript directly — it does NOT depend on the old bot's tab-state
files, so it works standalone.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass

from . import model as model_mod
from . import transcript
from . import tmux

# Commands that represent a Claude agent pane.
_AGENT_CMDS = ("claude", "node")

_DOTS = {"yellow": "🟡", "red": "🔴", "gray": "⚪"}
# How long after end_turn until an idle agent is shown as gray.
_GRAY_AFTER = 900


@dataclass
class AgentState:
    session: str
    index: str
    name: str
    is_agent: bool
    color: str          # yellow | red | gray
    dot: str
    model: str          # short label, "" if unknown
    elapsed: str        # human-readable, "—" if unknown


def _elapsed(ts: float | None, now: float) -> str:
    if not ts:
        return "—"
    s = int(now - ts)
    if s <= 0:
        return "—"
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    return f"{s // 3600}h{(s % 3600) // 60}m"


def _read_lines(path: str, max_read: int = 4_000_000) -> list[str]:
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            partial = size > max_read
            f.seek(-max_read, 2) if partial else f.seek(0)
            tail = f.read().decode("utf-8", errors="ignore")
    except OSError:
        return []
    lines = [ln for ln in tail.strip().split("\n") if ln]
    return lines[1:] if partial and len(lines) > 1 else lines


def _window_state(w: dict, now: float) -> AgentState:
    is_agent = w.get("cmd") in _AGENT_CMDS
    color, dot, model, elapsed = "gray", _DOTS["gray"], "", "—"
    if is_agent:
        path = transcript.find_jsonl(w.get("path", ""), w.get("name", ""), w.get("pane_pid", ""))
        lines = _read_lines(path) if path else []
        st = transcript.state_from_lines(lines) if lines else None
        if st:
            state, ts = st
            if state == "red" and ts and (now - ts) > _GRAY_AFTER:
                color = "gray"
            else:
                color = state
            elapsed = _elapsed(ts, now)
        if lines:
            model = model_mod.shorten(model_mod.model_from_lines(lines))
        dot = _DOTS.get(color, _DOTS["gray"])
    return AgentState(
        session=w["session"], index=w["index"], name=w.get("name", ""),
        is_agent=is_agent, color=color, dot=dot, model=model, elapsed=elapsed,
    )


def collect() -> dict[str, list[AgentState]]:
    """Return {session_name: [AgentState, ...]} for all sessions, ordered."""
    now = time.time()
    windows = tmux.list_windows()
    by_session: dict[str, list[AgentState]] = {}
    for w in windows:
        by_session.setdefault(w["session"], []).append(_window_state(w, now))
    ordered = {}
    for sess in tmux.list_sessions():
        if sess in by_session:
            ordered[sess] = by_session[sess]
    # include any session not returned by list_sessions (edge case)
    for sess, items in by_session.items():
        ordered.setdefault(sess, items)
    return ordered
