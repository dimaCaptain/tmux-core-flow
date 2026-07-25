"""Autoapprove policy engine — deny-list matching.

A tool call is auto-approved unless it matches an `always_ask` rule, in which
case it falls back to a normal permission prompt ("ask"), never "deny".

This is the deterministic replacement for the old tmux screen-scraper: instead
of parsing terminal text, the PreToolUse hook hands us the structured
`tool_name` and `tool_input`, and we decide here.
"""
from __future__ import annotations

import re
from typing import Any

import yaml

ALLOW = "allow"
ASK = "ask"

_PATTERN_RE = re.compile(r"(\w+)\((.*)")  # "Bash(rm " -> ("Bash", "rm ")


def load(path: str) -> dict:
    """Load a policy file. Returns a dict with at least an 'always_ask' list."""
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    data.setdefault("always_ask", [])
    return data


def _parse_pattern(pattern: str) -> tuple[str, str, bool] | None:
    """Parse "Tool(cmd)" into (tool, cmd_substring, word_boundary).

    - "Bash(rm "     -> ("Bash", "rm ", False)  — plain substring
    - "Bash(!rm "    -> ("Bash", "rm ", True)   — whole-word match only
    - "Edit"         -> bare tool name, parsed as such in decide()
    """
    m = _PATTERN_RE.match(pattern)
    if not m:
        return None
    tool, cmd = m.group(1), m.group(2)
    if cmd.endswith(")"):
        cmd = cmd[:-1]
    cmd = cmd.strip()
    word_boundary = False
    if cmd.startswith("!"):
        word_boundary = True
        cmd = cmd[1:]
    return tool, cmd, word_boundary


def _command_of(tool_name: str, tool_input: dict[str, Any]) -> str:
    """The command/argument string a rule matches against."""
    if tool_name == "Bash":
        return str(tool_input.get("command", "") or "")
    return ""


def _word_match(needle: str, haystack: str) -> bool:
    """True if needle appears as a whole word in haystack.

    'rm ' matches 'rm -rf /' but NOT 'term ' or 'xrm '.
    """
    idx = haystack.find(needle)
    if idx == -1:
        return False
    # Check left boundary: start of string or preceded by non-word char
    left_ok = (idx == 0) or (not haystack[idx - 1].isalnum() and haystack[idx - 1] not in '_')
    # Check right boundary: end of string or followed by non-word char
    end = idx + len(needle)
    right_ok = (end >= len(haystack)) or (not haystack[end].isalnum() and haystack[end] not in '_')
    return left_ok and right_ok


def decide(tool_name: str, tool_input: dict[str, Any], rules: dict) -> str:
    """Return ALLOW or ASK for a tool call under the given policy."""
    command = _command_of(tool_name, tool_input)
    for pattern in rules.get("always_ask", []):
        parsed = _parse_pattern(pattern)
        if parsed:
            p_tool, p_cmd, word_boundary = parsed
            if tool_name != p_tool:
                continue
            if word_boundary:
                if _word_match(p_cmd, command):
                    return ASK
            else:
                if p_cmd in command:
                    return ASK
        elif tool_name == pattern:  # bare tool-name rule
            return ASK
    return ALLOW
