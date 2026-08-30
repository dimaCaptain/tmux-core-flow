"""Tests for bin/flow-session-index.

The property worth protecting: a launcher recorded on disk reaches the index
even for a window tmux can no longer show us. Retained rows are the ones most
in need of it — they are precisely the sessions you are about to restore.
"""
import importlib.machinery
import importlib.util
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BIN = os.path.join(_ROOT, "bin", "flow-session-index")
sys.path.insert(0, os.path.join(_ROOT, "lib"))

import flow_sessions as fs  # noqa: E402

UUID = "11111111-2222-3333-4444-555555555555"


def _load(monkeypatch, state_dir):
    # Scoped rather than assigned: the module reads FLOW_STATE_DIR at import
    # time, and leaking it would move every later test's state directory.
    monkeypatch.setenv("FLOW_STATE_DIR", str(state_dir))
    spec = importlib.util.spec_from_loader(
        "flow_session_index",
        importlib.machinery.SourceFileLoader("flow_session_index", _BIN),
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["flow_session_index"] = mod
    spec.loader.exec_module(mod)
    return mod


def _row(launcher="claude"):
    return fs.Row("main:1", "claude", "/home/you", UUID, 100, 200, launcher)


def test_a_recorded_launcher_reaches_a_row_that_still_says_claude(monkeypatch, tmp_path):
    mod = _load(monkeypatch, tmp_path)
    os.makedirs(mod.LAUNCHER_DIR, exist_ok=True)
    with open(os.path.join(mod.LAUNCHER_DIR, UUID), "w") as fh:
        fh.write("claude-v4\n")

    assert mod.refresh_launchers([_row()])[0].launcher == "claude-v4"


def test_no_record_leaves_the_stored_launcher_alone(monkeypatch, tmp_path):
    # The lookup answers `claude` for a missing file. Applying that blindly
    # would erase a launcher the hook had recorded before the file was lost.
    mod = _load(monkeypatch, tmp_path)
    assert mod.refresh_launchers([_row("claude-glm")])[0].launcher == "claude-glm"
