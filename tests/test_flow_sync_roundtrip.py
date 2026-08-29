"""End-to-end: push a machine's state, pull it onto a different one.

The unit tests pin the rules; this one proves the thing they exist for. It
builds a throwaway `$HOME`, pushes it into a real restic repository, and pulls
it into a *second* `$HOME` — which is the case that silently produces an
unresumable restore if the encoded directory names are not rewritten.

Skipped where restic is absent, like the tmux test is where tmux is absent.
"""
import json
import os
import shutil
import subprocess
import sys
import time

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "lib"))

import flow_sessions as fs  # noqa: E402

FLOW_SYNC = os.path.join(_ROOT, "bin", "flow-sync")
# Real time, not a pinned epoch: the index retains 30 days, so a row stamped in
# 2023 is correctly dropped on the way back and would test the wrong thing.
NOW = int(time.time())
UUID = "11111111-2222-3333-4444-555555555555"

pytestmark = pytest.mark.skipif(shutil.which("restic") is None, reason="restic not installed")


def run(home, repo, password, *args, expect=0):
    env = dict(os.environ, HOME=str(home), FLOW_SYNC_REPO=repo,
               FLOW_SYNC_PASSWORD_FILE=str(password),
               FLOW_SYNC_CONFIG=str(home / "no-such-config.yaml"))
    proc = subprocess.run([sys.executable, FLOW_SYNC, *args],
                          capture_output=True, text=True, env=env, timeout=180)
    assert proc.returncode == expect, proc.stdout + proc.stderr
    return proc.stdout


@pytest.fixture
def machine(tmp_path):
    """A $HOME with one session in it: transcript, index row, pane map, config."""
    home = tmp_path / "src-home"
    cwd = home / "work" / "proj"
    cwd.mkdir(parents=True)

    projects = home / ".claude" / "projects" / fs.project_dir(str(cwd))
    projects.mkdir(parents=True)
    (projects / f"{UUID}.jsonl").write_text('{"type":"user","cwd":"%s"}\n' % cwd)

    state = home / ".tmux"
    (state / "claude-map").mkdir(parents=True)
    (state / "claude-map" / f"{fs.encode_cwd(str(cwd))}__claude").write_text(UUID)
    (state / "claude-sessions.tsv").write_text(
        fs.format_index([fs.Row("main:1", "claude", str(cwd), UUID, NOW, NOW)]))
    (home / ".claude.json").write_text(json.dumps(
        {"numStartups": 41, "projects": {str(cwd): {"hasTrustDialogAccepted": True}}}))

    password = tmp_path / "pw"
    password.write_text("test-password\n")
    repo = f"{tmp_path}/repo"
    run(home, repo, password, "init")
    run(home, repo, password, "push")
    return home, repo, password, cwd


def test_pull_onto_the_same_home_is_a_no_op(machine, tmp_path):
    home, repo, password, cwd = machine
    out = run(home, repo, password, "pull")
    assert "remapping" not in out
    assert "already current" in out


def test_pull_onto_a_different_home_lands_where_resume_will_look(machine, tmp_path):
    home, repo, password, cwd = machine
    dst = tmp_path / "dst-home"
    dst.mkdir()

    out = run(dst, repo, password, "pull")
    assert f"{home} -> {dst}" in out

    new_cwd = dst / "work" / "proj"
    transcript = dst / ".claude" / "projects" / fs.project_dir(str(new_cwd)) / f"{UUID}.jsonl"
    assert transcript.exists(), "a transcript under the old slug is one --resume never finds"

    rows = fs.parse_index((dst / ".tmux" / "claude-sessions.tsv").read_text())
    assert [r.cwd for r in rows] == [str(new_cwd)], "flow-restore would cd nowhere"

    assert (dst / ".tmux" / "claude-map" /
            f"{fs.encode_cwd(str(new_cwd))}__claude").exists()

    config = json.loads((dst / ".claude.json").read_text())
    assert str(new_cwd) in config["projects"]
    assert "numStartups" not in config, "machine-local state must not travel"


def test_pull_keeps_a_longer_local_transcript(machine, tmp_path):
    """The machine resumed the session after the snapshot was taken. A pull is
    not allowed to roll that back."""
    home, repo, password, cwd = machine
    transcript = (home / ".claude" / "projects" / fs.project_dir(str(cwd)) / f"{UUID}.jsonl")
    grown = transcript.read_text() + '{"type":"assistant","text":"newer"}\n'
    transcript.write_text(grown)

    run(home, repo, password, "pull")
    assert transcript.read_text() == grown
    assert not (transcript.parent / f"{UUID}.jsonl.from-sync").exists()


def test_divergence_is_parked_beside_the_local_copy(machine, tmp_path):
    """Older but longer here, newer but shorter there: two machines resumed the
    same session. Neither copy may be discarded."""
    home, repo, password, cwd = machine
    transcript = (home / ".claude" / "projects" / fs.project_dir(str(cwd)) / f"{UUID}.jsonl")
    local = transcript.read_text() + "x" * 500
    transcript.write_text(local)
    os.utime(transcript, (NOW - 3600, NOW - 3600))  # older than the snapshot, and longer

    run(home, repo, password, "pull")
    assert transcript.read_text() == local
    assert (transcript.parent / f"{UUID}.jsonl.from-sync").exists()


def test_a_staging_directory_with_something_in_it_is_refused(machine, tmp_path):
    """`--staging` names a directory this would otherwise empty. Somebody's
    working tree is not a scratch area, and finding that out afterwards is too
    late."""
    home, repo, password, cwd = machine
    occupied = tmp_path / "not-scratch"
    occupied.mkdir()
    (occupied / "important.txt").write_text("mine")

    out = run(home, repo, password, "pull", "--staging", str(occupied), expect=2)
    assert (occupied / "important.txt").read_text() == "mine"


def test_dry_run_changes_nothing(machine, tmp_path):
    home, repo, password, cwd = machine
    dst = tmp_path / "dry-home"
    dst.mkdir()
    out = run(dst, repo, password, "pull", "--dry-run")
    assert "would restore" in out
    assert not (dst / ".claude").exists()
