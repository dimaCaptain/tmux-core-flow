"""Tests for the autoapprove policy engine (deny-list matching)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import policy  # noqa: E402

# Policy mirroring config/policy.yaml
RULES = {
    "always_ask": [
        "Bash(rm ",
        "Bash(git push)",
        "Bash(sudo)",
        "Bash(DROP TABLE)",
    ]
}


def decide(tool, cmd):
    return policy.decide(tool, {"command": cmd} if cmd is not None else {}, RULES)


# --- allow: ordinary calls auto-approve ---

def test_plain_bash_allowed():
    assert decide("Bash", "ls -la") == "allow"


def test_read_tool_allowed():
    assert policy.decide("Read", {"file_path": "/x"}, RULES) == "allow"


def test_edit_tool_allowed():
    assert policy.decide("Edit", {"file_path": "/x", "old_string": "a"}, RULES) == "allow"


# --- ask: dangerous substrings fall back to a prompt ---

def test_rm_asks():
    assert decide("Bash", "rm -rf /tmp/x") == "ask"


def test_sudo_asks():
    assert decide("Bash", "sudo apt update") == "ask"


def test_git_push_asks():
    assert decide("Bash", "git push origin main") == "ask"


def test_substring_match_anywhere_in_command():
    # "rm " appears mid-command (e.g. chained) — still asks
    assert decide("Bash", "cd /tmp && rm -rf build") == "ask"


def test_drop_table_asks():
    assert decide("Bash", "psql -c 'DROP TABLE users'") == "ask"


# --- rule does not leak across tools ---

def test_rm_pattern_does_not_match_non_bash():
    # "rm " substring in an Edit payload must NOT trigger the Bash(rm rule
    assert policy.decide("Edit", {"command": "rm "}, RULES) == "allow"


# --- bare tool-name rule blocks the whole tool ---

def test_bare_tool_name_rule_asks():
    rules = {"always_ask": ["WebFetch"]}
    assert policy.decide("WebFetch", {"url": "http://x"}, rules) == "ask"
    assert policy.decide("Read", {}, rules) == "allow"


# --- empty / missing input is safe ---

def test_missing_command_allows():
    assert policy.decide("Bash", {}, RULES) == "allow"


def test_empty_policy_allows_everything():
    assert policy.decide("Bash", {"command": "rm -rf /"}, {"always_ask": []}) == "allow"


# --- loader reads the real YAML ---

def test_load_real_policy_file():
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "config", "policy.yaml",
    )
    rules = policy.load(path)
    assert "Bash(rm " in rules["always_ask"]
    assert policy.decide("Bash", {"command": "rm -rf x"}, rules) == "ask"
    assert policy.decide("Bash", {"command": "echo hi"}, rules) == "allow"
