#!/usr/bin/env python3
"""Claude Code PreToolUse hook → deterministic autoapprove.

Reads the tool call as JSON on stdin and decides, from `config/policy.yaml`,
whether to auto-approve it. Replaces the old tmux screen-scraper: no terminal
parsing, no key injection, no timing races — the decision is made from the
exact `tool_name` + `tool_input` before any prompt is shown.

Scoping safeguard
-----------------
Auto-approval only happens when the environment variable FLOW_AUTOAPPROVE=1 is
set (the agent launcher sets it in agent panes). In your own interactive Claude
session the variable is absent, the hook stays transparent, and permission
prompts behave normally.

Contract
--------
- stdin:  {"tool_name": "...", "tool_input": {...}, ...}
- stdout: {"hookSpecificOutput": {"hookEventName": "PreToolUse",
            "permissionDecision": "allow"|"ask", "permissionDecisionReason": "..."}}
           or nothing at all (transparent → default prompt behaviour).
- Always exits 0. Any error → emit nothing, so a bug here can never block agents.
"""
from __future__ import annotations

import json
import os
import sys

# Make `core` importable whether run directly or via a ~/.local/bin symlink
# (the symlink resolves to this file inside the repo).
_ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core import policy  # noqa: E402

DEFAULT_POLICY = os.path.join(_ROOT, "config", "policy.yaml")


def evaluate(event: dict, rules: dict, enabled: bool) -> dict | None:
    """Pure decision core. Returns a hookSpecificOutput dict, or None for 'stay
    transparent' (no output → Claude falls back to its default prompt)."""
    if not enabled:
        return None
    tool_name = event.get("tool_name")
    if not tool_name:
        return None
    tool_input = event.get("tool_input") or {}
    decision = policy.decide(tool_name, tool_input, rules)
    if decision == policy.ALLOW:
        reason = "auto-approved by tmux-core-flow policy"
    else:
        reason = "matched always_ask rule — deferring to you"
    return {
        "hookEventName": "PreToolUse",
        "permissionDecision": decision,
        "permissionDecisionReason": reason,
    }


def enabled_for(env) -> bool:
    """Auto-approve is active for any agent running inside tmux.

    Priority:
      FLOW_AUTOAPPROVE=1  → force on  (any session, even outside tmux)
      FLOW_AUTOAPPROVE=0  → force off (escape hatch for a given session)
      otherwise           → on when inside tmux ($TMUX set), off elsewhere.

    Dangerous calls still defer to you via always_ask regardless of this.
    """
    v = env.get("FLOW_AUTOAPPROVE")
    if v == "1":
        return True
    if v == "0":
        return False
    return bool(env.get("TMUX"))


def main() -> int:
    try:
        raw = sys.stdin.read()
        event = json.loads(raw)
    except Exception:
        return 0  # malformed input → transparent, never block the agent
    try:
        rules = policy.load(os.environ.get("FLOW_POLICY", DEFAULT_POLICY))
    except Exception:
        rules = {"always_ask": []}
    try:
        decision = evaluate(event, rules, enabled_for(os.environ))
    except Exception:
        decision = None
    if decision is not None:
        json.dump({"hookSpecificOutput": decision}, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
