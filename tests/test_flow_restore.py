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
    # @dataclass resolves its module through sys.modules, so register before exec.
    sys.modules["flow_restore"] = mod
    spec.loader.exec_module(mod)
    return mod


restore = _load()

UUID = "11111111-2222-3333-4444-555555555555"


def row(target="main:1", cwd="/home/u/proj", window="claude", activity=100,
        launcher="claude"):
    return fs.Row(target, window, cwd, UUID, activity, activity, launcher)


def pane(pane_id="%1", command="zsh", path="/home/u/proj", active=True,
         width=100, height=40, target="main:1", pid="1"):
    return restore.Pane(target, pane_id, pid, command, path, active, width, height)


@pytest.fixture(autouse=True)
def bare_shells(monkeypatch):
    """By default every pane process looks like an idle shell."""
    monkeypatch.setattr(restore, "_cmdline", lambda pid: ["/usr/bin/zsh"])


# --- is_bare_shell: the check that keeps us out of busy panes ---

def test_plain_shell_is_bare(monkeypatch):
    monkeypatch.setattr(restore, "_cmdline", lambda pid: ["/usr/bin/zsh"])
    assert restore.is_bare_shell("1")


def test_login_shell_flags_are_still_bare(monkeypatch):
    monkeypatch.setattr(restore, "_cmdline", lambda pid: ["-zsh", "-l"])
    assert restore.is_bare_shell("1")


def test_shell_running_a_script_is_not_bare(monkeypatch):
    """The dashboard case: reports `bash`, but is running a program."""
    monkeypatch.setattr(restore, "_cmdline",
                        lambda pid: ["/bin/bash", "/usr/local/bin/some-dashboard", "--compact"])
    assert not restore.is_bare_shell("1")


def test_non_shell_process_is_not_bare(monkeypatch):
    monkeypatch.setattr(restore, "_cmdline", lambda pid: ["/usr/bin/vim"])
    assert not restore.is_bare_shell("1")


def test_unreadable_process_is_not_bare(monkeypatch):
    """Unknown means unsafe — leave the pane alone rather than guess."""
    monkeypatch.setattr(restore, "_cmdline", lambda pid: [])
    assert not restore.is_bare_shell("1")


# --- pick_pane ---

def test_focused_shell_wins():
    chosen = restore.pick_pane([
        pane(pane_id="%1", active=False, width=200),
        pane(pane_id="%2", active=True, width=30),
    ])
    assert chosen.pane_id == "%2"


def test_largest_shell_when_focus_is_elsewhere():
    """The narrow side column must never win over the working area."""
    chosen = restore.pick_pane([
        pane(pane_id="%1", active=False, width=200, height=40),
        pane(pane_id="%2", active=False, width=30, height=40),
    ])
    assert chosen.pane_id == "%1"


def test_pane_running_a_program_is_skipped():
    chosen = restore.pick_pane([
        pane(pane_id="%1", command="vim", active=True),
        pane(pane_id="%2", command="zsh", active=False),
    ])
    assert chosen.pane_id == "%2"


def test_pane_running_a_script_is_skipped(monkeypatch):
    monkeypatch.setattr(
        restore, "_cmdline",
        lambda pid: ["/bin/bash", "/x/dashboard"] if pid == "dash" else ["/usr/bin/zsh"])
    chosen = restore.pick_pane([
        pane(pane_id="%1", command="bash", pid="dash", active=True),
        pane(pane_id="%2", command="zsh", pid="ok", active=False),
    ])
    assert chosen.pane_id == "%2"


def test_no_usable_pane_returns_none():
    assert restore.pick_pane([pane(command="vim"), pane(command="claude")]) is None


# --- classify ---

def test_window_running_claude_is_live():
    assert restore.classify([row()], {"main:1": [pane(command="claude")]})[0][1] == restore.LIVE


def test_claude_in_an_unfocused_pane_still_counts_as_live():
    """Otherwise we would start a second copy of a session already running."""
    pairs = restore.classify([row()], {"main:1": [
        pane(pane_id="%1", command="zsh", active=True),
        pane(pane_id="%2", command="claude", active=False),
    ]})
    assert pairs[0][1] == restore.LIVE


def test_window_at_a_shell_is_restorable():
    assert restore.classify([row()], {"main:1": [pane()]})[0][1] == restore.RESTORABLE


def test_window_with_nothing_typeable_is_blocked():
    pairs = restore.classify([row()], {"main:1": [pane(command="vim")]})
    assert pairs[0][1] == restore.BLOCKED


def test_missing_window_is_gone():
    assert restore.classify([row()], {})[0][1] == restore.GONE


# --- the command that gets typed ---

def test_no_cd_when_window_is_already_in_place():
    assert restore.resume_command(row(), "/home/u/proj") == f"claude --resume {UUID}"


def test_cd_prepended_when_window_drifted():
    cmd = restore.resume_command(row(cwd="/home/u/proj"), "/somewhere/else")
    assert cmd == f"cd /home/u/proj && claude --resume {UUID}"


def test_a_wrapper_that_exists_is_what_gets_typed(monkeypatch):
    """A session started through claude-glm must come back through it, or it
    resumes the same transcript on a different model.

    `which` is stubbed rather than trusted: whether a wrapper happens to be
    installed is a property of the machine running the suite, not of the
    behaviour under test."""
    monkeypatch.setattr(restore.shutil, "which", lambda p: f"/usr/local/bin/{p}")
    cmd = restore.resume_command(row(launcher="claude-glm"), "/home/u/proj")
    assert cmd == f"claude-glm --resume {UUID}"


def test_a_wrapper_missing_from_this_machine_falls_back(monkeypatch):
    """Restoring onto a machine without your wrappers must still restore."""
    monkeypatch.setattr(restore.shutil, "which", lambda p: None)
    assert restore.resume_command(row(launcher="claude-glm"), "/home/u/proj") == \
        f"claude --resume {UUID}"


def test_the_model_flag_survives_into_the_command(monkeypatch):
    monkeypatch.setattr(restore.shutil, "which", lambda p: "/usr/bin/claude")
    cmd = restore.resume_command(row(launcher="claude --model zhipu,glm-5.2"), "/home/u/proj")
    assert cmd == f"claude --model zhipu,glm-5.2 --resume {UUID}"


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
        tmux("-f", "/dev/null", "new-session", "-d", "-s", "t", "-n", "agent", "-c", cwd,
             "-x", "200", "-y", "50")
        target = tmux("list-windows", "-a", "-F",
                      "#{session_name}:#{window_index}").stdout.strip().splitlines()[0]
        shell_pane = tmux("list-panes", "-t", target, "-F", "#{pane_id}").stdout.strip()

        # A side column running a script, and focused — the shape that made an
        # earlier version type its resume command straight into a dashboard.
        # `read` is a builtin, so pane_current_command stays "bash" throughout,
        # which is exactly what makes this pane indistinguishable by command name.
        tmux("split-window", "-h", "-t", target, "-c", cwd,
             "bash", "--norc", "-c", "read -r _")
        busy_pane = [p for p in tmux("list-panes", "-t", target, "-F", "#{pane_id}")
                     .stdout.split() if p != shell_pane][0]
        assert tmux("display-message", "-p", "-t", target,
                    "#{pane_id}").stdout.strip() == busy_pane, "busy pane should be focused"

        with open(os.path.join(state, "claude-sessions.tsv"), "w") as f:
            f.write(fs.format_index([fs.Row(target, "agent", cwd, UUID, 100, 100)]))

        env_run = dict(env, FLOW_STATE_DIR=state, FLOW_CLAUDE_PROJECTS=projects)
        out = subprocess.run([sys.executable, _BIN, "--arm", "--all"],
                             env=env_run, capture_output=True, text=True, timeout=30)
        assert out.returncode == 0, out.stderr

        # -J joins wrapped lines; also drop newlines, since a narrow pane can
        # split the command mid-token and that is not a failure.
        def flat(pane_id):
            return tmux("capture-pane", "-p", "-J", "-t", pane_id).stdout.replace("\n", "")

        content = ""
        for _ in range(20):
            content = flat(shell_pane)
            if UUID in content:
                break
            time.sleep(0.25)

        assert f"claude --resume {UUID}" in content, f"command was not typed:\n{content}"
        assert UUID not in flat(busy_pane), "typed into the pane running a script"

        command = tmux("display-message", "-p", "-t", shell_pane,
                       "#{pane_current_command}").stdout.strip()
        assert command != "claude", "--arm must not execute the command"
    finally:
        tmux("kill-server")
        shutil.rmtree(tmpdir, ignore_errors=True)
        shutil.rmtree(sockdir, ignore_errors=True)
