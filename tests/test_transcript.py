"""Tests for the transcript state machine (synthetic JSONL)."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import transcript  # noqa: E402

T1 = "2026-06-13T10:00:00Z"
T2 = "2026-06-13T10:05:00Z"
T3 = "2026-06-13T10:06:00Z"


def human(text, ts):
    return json.dumps({"type": "user", "timestamp": ts,
                       "message": {"content": text}})


def assistant(ts, stop_reason):
    return json.dumps({"type": "assistant", "timestamp": ts,
                       "message": {"stop_reason": stop_reason, "content": []}})


def tool_result(ts):
    return json.dumps({"type": "user", "timestamp": ts,
                       "message": {"content": [{"type": "tool_result", "content": "ok"}]}})


def test_empty_returns_none():
    assert transcript.state_from_lines([]) is None


def test_ended_turn_is_red():
    lines = [human("do X", T1), assistant(T2, "end_turn")]
    state, ts = transcript.state_from_lines(lines)
    assert state == "red"
    assert ts == transcript._parse_ts(T2)


def test_working_is_yellow_with_human_start_ts():
    # last event is assistant still working (tool_use), not end_turn
    lines = [human("do X", T1), assistant(T2, "tool_use")]
    state, ts = transcript.state_from_lines(lines)
    assert state == "yellow"
    assert ts == transcript._parse_ts(T1)  # timer anchored at human prompt


def test_tool_result_is_not_human_prompt():
    # newest significant = assistant working; the trailing tool_result must be skipped
    lines = [human("do X", T1), assistant(T2, "tool_use"), tool_result(T3)]
    state, ts = transcript.state_from_lines(lines)
    assert state == "yellow"
    assert ts == transcript._parse_ts(T1)


def test_system_command_not_treated_as_human():
    lines = [
        human("real prompt", T1),
        assistant(T2, "end_turn"),
        json.dumps({"type": "user", "timestamp": T3,
                    "message": {"content": "<command-name>/compact</command-name>"}}),
    ]
    # newest significant non-system event is the end_turn assistant → red
    state, ts = transcript.state_from_lines(lines)
    assert state == "red"
    assert ts == transcript._parse_ts(T2)


def test_garbage_lines_ignored():
    lines = ["not json", human("hi", T1), "", assistant(T2, "end_turn")]
    state, _ = transcript.state_from_lines(lines)
    assert state == "red"
