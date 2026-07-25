"""Tests for bin/flow-restore.

The property worth protecting is that `--arm` types a command and stops there.
Anything that silently launched agents would recreate the failure this whole
design avoids: every window starting Claude at once.
"""
import importlib.machinery
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import time

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BIN = os.path.join(_ROOT, "bin", "flow-restore")
sys.path.insert(0, os.path.join(_ROOT, "lib"))

import flow_sessions as fs  # noqa: E402


def _load():
    spec = importlib.util.spec_from_loader(
        "flow_restore", importlib.machinery.SourceFileLoader("flow_restore", _BIN)
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


restore = _load()

UUID = "11111111-2222-3333-4444-555555555555"


def row(target="main:1", cwd="/home/u/proj", window="claude", activity=100):
    return fs.Row(target, window, cwd, UUID, activity, activity)


# --- classify ---

def test_window_running_claude_is_live():
    pairs = restore.classify([row()], {"main:1": ("/home/u/proj", "claude")})
    assert pairs[0][1] == restore.LIVE


def test_window_at_a_shell_is_restorable():
    pairs = restore.classify([row()], {"main:1": ("/home/u/proj", "zsh")})
    assert pairs[0][1] == restore.RESTORABLE


def test_missing_window_is_gone():
    assert restore.classify([row()], {})[0][1] == restore.GONE


# --- the command that gets typed ---

def test_no_cd_when_window_is_already_in_place():
    assert restore.resume_command(row(), "/home/u/proj") == f"claude --resume {UUID}"


def test_cd_prepended_when_window_drifted():
    cmd = restore.resume_command(row(cwd="/home/u/proj"), "/somewhere/else")
    assert cmd == f"cd /home/u/proj && claude --resume {UUID}"


def test_cwd_with_spaces_is_quoted():
    cmd = restore.resume_command(row(cwd="/home/u/my proj"), "/elsewhere")
    assert "'/home/u/my proj'" in cmd


# --- selection ---

def test_only_restorable_windows_are_selected():
    pairs = [
        (row(target="a:1"), restore.RESTORABLE),
        (row(target="b:1"), restore.LIVE),
        (row(target="c:1"), restore.GONE),
    ]
    assert [r.target for r, _ in restore.select(pairs, [], want_all=True)] == ["a:1"]


def test_named_target_narrows_selection():
    pairs = [(row(target="a:1"), restore.RESTORABLE), (row(target="b:1"), restore.RESTORABLE)]
    assert [r.target for r, _ in restore.select(pairs, ["b:1"], want_all=False)] == ["b:1"]


def test_live_window_cannot_be_selected_by_name():
    """Naming a window explicitly still must not restart a running agent."""
    pairs = [(row(target="a:1"), restore.LIVE)]
    assert restore.select(pairs, ["a:1"], want_all=False) == []


# --- end to end, against a throwaway tmux server ---

def _tmux_available():
    return shutil.which("tmux") is not None


@pytest.mark.skipif(not _tmux_available(), reason="tmux not installed")
def test_arm_types_the_command_without_running_it():
    tmpdir = tempfile.mkdtemp(prefix="flowtest-")
    sockdir = tempfile.mkdtemp(prefix="flowsock-")
    env = dict(os.environ, TMUX_TMPDIR=sockdir)
    env.pop("TMUX", None)

    state = os.path.join(tmpdir, "state")
    projects = os.path.join(tmpdir, "projects")
    cwd = os.path.join(tmpdir, "work")
    os.makedirs(state)
    os.makedirs(cwd)
    os.makedirs(os.path.join(projects, fs.project_dir(cwd)))
    open(os.path.join(projects, fs.project_dir(cwd), f"{UUID}.jsonl"), "w").close()

    def tmux(*args, **kw):
        return subprocess.run(["tmux", *args], env=env, capture_output=True,
                              text=True, timeout=15, **kw)

    try:
        tmux("-f", "/dev/null", "new-session", "-d", "-s", "t", "-n", "agent", "-c", cwd)
        target = tmux("list-windows", "-a", "-F",
                      "#{session_name}:#{window_index}").stdout.strip().splitlines()[0]

        with open(os.path.join(state, "claude-sessions.tsv"), "w") as f:
            f.write(fs.format_index([fs.Row(target, "agent", cwd, UUID, 100, 100)]))

        env_run = dict(env, FLOW_STATE_DIR=state, FLOW_CLAUDE_PROJECTS=projects)
        out = subprocess.run([sys.executable, _BIN, "--arm", "--all"],
                             env=env_run, capture_output=True, text=True, timeout=30)
        assert out.returncode == 0, out.stderr

        pane = ""
        for _ in range(20):
            pane = tmux("capture-pane", "-p", "-t", target).stdout
            if UUID in pane:
                break
            time.sleep(0.25)

        assert f"claude --resume {UUID}" in pane, f"command was not typed:\n{pane}"

        command = tmux("display-message", "-p", "-t", target,
                       "#{pane_current_command}").stdout.strip()
        assert command != "claude", "--arm must not execute the command"
    finally:
        tmux("kill-server")
        shutil.rmtree(tmpdir, ignore_errors=True)
        shutil.rmtree(sockdir, ignore_errors=True)
