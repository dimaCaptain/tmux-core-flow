"""Derive an agent's state from its Claude Code JSONL transcript.

State (ported from agent-control's bot.py):
  "red"    — last significant event is an assistant turn that ended
             (stop_reason == "end_turn"): the agent is idle, waiting on you.
  "yellow" — work is in progress (model streaming / tools running).
  None     — no transcript found.

The timestamp returned is when that state began, used by the dashboard to show
elapsed time.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

# System "user" entries that are not a human prompt.
_SYS_TAGS = ("<command-name>", "<command-message>", "<local-command-",
             "<command-stderr>", "<command-stdout>")
_SYS_PREFIXES = ("This session is being continued", "Caveat: The messages below")


def _is_system_cmd(text: str) -> bool:
    t = text.lstrip()
    return any(tag in text for tag in _SYS_TAGS) or \
        any(t.startswith(p) for p in _SYS_PREFIXES)


def _parse_ts(ts_str: str) -> float:
    dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _is_human_prompt(obj: dict) -> bool:
    if obj.get("type") != "user":
        return False
    content = (obj.get("message") or {}).get("content")
    if isinstance(content, str):
        return not _is_system_cmd(content)
    if isinstance(content, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return False
        all_text = " ".join(b.get("text", "") for b in content
                            if isinstance(b, dict) and b.get("type") == "text")
        return not _is_system_cmd(all_text)
    return False


def state_from_lines(lines: list[str]) -> tuple[str, float | None] | None:
    """Pure core: derive (state, ts) from JSONL transcript lines (oldest→newest)."""
    newest = None
    newest_ts = None
    last_human_prompt_ts = None
    for ln in reversed(lines):
        if not ln.strip():
            continue
        try:
            obj = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if obj.get("type") not in ("user", "assistant"):
            continue
        if obj.get("type") == "user":
            content = (obj.get("message") or {}).get("content")
            if isinstance(content, list):
                if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
                    continue
                all_text = " ".join(b.get("text", "") for b in content
                                    if isinstance(b, dict) and b.get("type") == "text")
                if _is_system_cmd(all_text):
                    continue
            elif isinstance(content, str) and _is_system_cmd(content):
                continue
        ts_str = obj.get("timestamp")
        if not ts_str:
            continue
        try:
            ts = _parse_ts(ts_str)
        except Exception:
            continue
        if newest is None:
            newest = obj
            newest_ts = ts
        if last_human_prompt_ts is None and _is_human_prompt(obj):
            last_human_prompt_ts = ts
            break

    if newest is None:
        return None
    msg = newest.get("message") or {}
    stop_reason = msg.get("stop_reason") if isinstance(msg, dict) else None
    if newest.get("type") == "assistant" and stop_reason == "end_turn":
        return ("red", newest_ts)
    return ("yellow", last_human_prompt_ts)


def find_jsonl(cwd: str, win_name: str, pane_pid: str = "") -> str | None:
    """Map a tmux pane to its Claude transcript via ~/.tmux/claude-map/ markers."""
    if not cwd:
        return None
    map_dir = os.path.expanduser("~/.tmux/claude-map")
    encoded = cwd.replace("/", "-").replace("_", "-").replace(".", "-")
    projects_dir = os.path.expanduser(f"~/.claude/projects/{encoded}")

    def _try(map_path: str) -> str | None:
        try:
            with open(map_path) as f:
                sid = f.read().strip()
        except OSError:
            return None
        if not sid:
            return None
        p = os.path.join(projects_dir, f"{sid}.jsonl")
        return p if os.path.exists(p) else None

    if win_name:
        cwd_key = cwd.replace("/", "-").replace("_", "-").lstrip("-")
        name_key = win_name.replace("/", "-")
        r = _try(os.path.join(map_dir, f"{cwd_key}__{name_key}"))
        if r:
            return r
    if pane_pid:
        r = _try(os.path.join(map_dir, f"pid__{pane_pid}"))
        if r:
            return r
    return None


def agent_state(cwd: str, win_name: str, pane_pid: str = "",
                max_read: int = 4_000_000) -> tuple[str, float | None] | None:
    """Read the transcript for a pane and derive its state."""
    path = find_jsonl(cwd, win_name, pane_pid)
    if not path:
        return None
    try:
        fsize = os.path.getsize(path)
        with open(path, "rb") as f:
            partial = fsize > max_read
            f.seek(-max_read, 2) if partial else f.seek(0)
            tail = f.read().decode("utf-8", errors="ignore")
    except OSError:
        return None
    lines = [ln for ln in tail.strip().split("\n") if ln]
    if partial and len(lines) > 1:
        lines = lines[1:]  # drop possibly-truncated first line
    return state_from_lines(lines)
