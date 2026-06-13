"""Tests for the PreToolUse autoapprove hook."""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autoapprove import hook  # noqa: E402

RULES = {"always_ask": ["Bash(rm ", "Bash(sudo)"]}


def out(event, rules, enabled):
    return hook.evaluate(event, rules, enabled)


# --- scoping safeguard: transparent unless explicitly enabled ---

def test_disabled_is_transparent():
    ev = {"tool_name": "Bash", "tool_input": {"command": "ls"}}
    assert out(ev, RULES, enabled=False) is None


def test_disabled_transparent_even_for_dangerous():
    ev = {"tool_name": "Bash", "tool_input": {"command": "rm -rf /"}}
    assert out(ev, RULES, enabled=False) is None


# --- enabled: decisions ---

def test_enabled_allows_plain():
    ev = {"tool_name": "Bash", "tool_input": {"command": "ls -la"}}
    d = out(ev, RULES, enabled=True)
    assert d["hookEventName"] == "PreToolUse"
    assert d["permissionDecision"] == "allow"


def test_enabled_asks_dangerous():
    ev = {"tool_name": "Bash", "tool_input": {"command": "rm -rf build"}}
    d = out(ev, RULES, enabled=True)
    assert d["permissionDecision"] == "ask"
    assert "reason" in d["permissionDecisionReason"].lower() or d["permissionDecisionReason"]


def test_enabled_allows_other_tools():
    ev = {"tool_name": "Read", "tool_input": {"file_path": "/x"}}
    assert out(ev, RULES, enabled=True)["permissionDecision"] == "allow"


def test_missing_tool_name_is_transparent():
    assert out({}, RULES, enabled=True) is None


# --- activation scoping: any tmux agent, with explicit overrides ---

def test_enabled_inside_tmux_by_default():
    assert hook.enabled_for({"TMUX": "/tmp/tmux-1000/default,123,0"}) is True


def test_disabled_outside_tmux_by_default():
    assert hook.enabled_for({}) is False


def test_force_on_overrides_no_tmux():
    assert hook.enabled_for({"FLOW_AUTOAPPROVE": "1"}) is True


def test_force_off_overrides_tmux():
    assert hook.enabled_for({"FLOW_AUTOAPPROVE": "0", "TMUX": "x"}) is False


# --- end-to-end through the script via stdin/stdout ---

def _run(stdin_obj, env_extra):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ)
    env.update(env_extra)
    env["FLOW_POLICY"] = os.path.join(root, "config", "policy.yaml")
    p = subprocess.run(
        [sys.executable, os.path.join(root, "autoapprove", "hook.py")],
        input=json.dumps(stdin_obj),
        capture_output=True, text=True, env=env,
    )
    return p


def test_cli_enabled_allow():
    p = _run({"tool_name": "Bash", "tool_input": {"command": "echo hi"}},
             {"FLOW_AUTOAPPROVE": "1"})
    assert p.returncode == 0
    body = json.loads(p.stdout)
    assert body["hookSpecificOutput"]["permissionDecision"] == "allow"


def test_cli_enabled_ask_on_rm():
    p = _run({"tool_name": "Bash", "tool_input": {"command": "rm -rf x"}},
             {"FLOW_AUTOAPPROVE": "1"})
    body = json.loads(p.stdout)
    assert body["hookSpecificOutput"]["permissionDecision"] == "ask"


def test_cli_disabled_no_output():
    p = _run({"tool_name": "Bash", "tool_input": {"command": "rm -rf x"}},
             {"FLOW_AUTOAPPROVE": "0"})
    assert p.returncode == 0
    assert p.stdout.strip() == ""


def test_cli_malformed_stdin_is_safe():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ, FLOW_AUTOAPPROVE="1",
               FLOW_POLICY=os.path.join(root, "config", "policy.yaml"))
    p = subprocess.run(
        [sys.executable, os.path.join(root, "autoapprove", "hook.py")],
        input="not json", capture_output=True, text=True, env=env,
    )
    # Must never crash the agent: exit 0, emit nothing (fall back to prompt)
    assert p.returncode == 0
    assert p.stdout.strip() == ""
