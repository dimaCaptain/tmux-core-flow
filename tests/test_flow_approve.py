"""Tests for the PreToolUse autoapprove hook (bin/flow-approve)."""
import importlib.util
import json
import os
import subprocess
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_HOOK = os.path.join(_ROOT, "bin", "flow-approve")
_POLICY = os.path.join(_ROOT, "config", "policy.example.yaml")


def _load_hook():
    """bin/flow-approve has no .py suffix — load it by path."""
    spec = importlib.util.spec_from_loader(
        "flow_approve", importlib.machinery.SourceFileLoader("flow_approve", _HOOK)
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


hook = _load_hook()

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
    assert d["permissionDecisionReason"]


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


# --- policy lookup: explicit override, then user config, then the example ---

def test_policy_path_prefers_explicit_override():
    assert hook.policy_path({"FLOW_POLICY": "/tmp/custom.yaml"}) == "/tmp/custom.yaml"


def test_policy_path_falls_back_to_example(monkeypatch):
    # No override and no user config → the shipped example.
    monkeypatch.setattr(hook.os.path, "exists", lambda p: False)
    assert hook.policy_path({}) == hook.EXAMPLE_POLICY


def test_policy_path_prefers_user_config_over_example(monkeypatch):
    monkeypatch.setattr(hook.os.path, "exists", lambda p: p == hook.USER_POLICY)
    assert hook.policy_path({}) == hook.USER_POLICY


def test_user_policy_lives_outside_the_repo():
    # The whole point: your rules must not sit in a public checkout.
    assert not hook.USER_POLICY.startswith(_ROOT)


# --- end-to-end through the script via stdin/stdout ---

def _run(stdin_obj, env_extra, raw=None):
    env = dict(os.environ)
    env.update(env_extra)
    env["FLOW_POLICY"] = _POLICY
    return subprocess.run(
        [sys.executable, _HOOK],
        input=json.dumps(stdin_obj) if raw is None else raw,
        capture_output=True, text=True, env=env,
    )


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
    p = _run(None, {"FLOW_AUTOAPPROVE": "1"}, raw="not json")
    # Must never crash the agent: exit 0, emit nothing (fall back to prompt)
    assert p.returncode == 0
    assert p.stdout.strip() == ""
