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


def _parse_pattern(pattern: str) -> tuple[str, str] | None:
    """Parse "Tool(cmd)" or unclosed "Tool(cmd " into (tool, cmd_substring).

    Returns None for a bare tool name like "Edit".
    """
    m = _PATTERN_RE.match(pattern)
    if not m:
        return None
    tool, cmd = m.group(1), m.group(2)
    if cmd.endswith(")"):
        cmd = cmd[:-1]
    return tool, cmd.strip()


def _command_of(tool_name: str, tool_input: dict[str, Any]) -> str:
    """The command/argument string a rule matches against.

    Only Bash carries a shell command; for other tools there is no command
    string, so substring rules never match (only bare tool-name rules can).
    """
    if tool_name == "Bash":
        return str(tool_input.get("command", "") or "")
    return ""


def decide(tool_name: str, tool_input: dict[str, Any], rules: dict) -> str:
    """Return ALLOW or ASK for a tool call under the given policy."""
    command = _command_of(tool_name, tool_input)
    for pattern in rules.get("always_ask", []):
        parsed = _parse_pattern(pattern)
        if parsed:
            p_tool, p_cmd = parsed
            if tool_name == p_tool and p_cmd in command:
                return ASK
        elif tool_name == pattern:  # bare tool-name rule
            return ASK
    return ALLOW
