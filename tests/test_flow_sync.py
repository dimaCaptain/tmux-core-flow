"""Tests for the off-machine copy (lib/flow_sync.py).

Two things here are worth getting wrong only once. `decide` is what stands
between a pull and a destroyed transcript, and `remap_dirname` is what makes a
restore onto a different $HOME resumable at all — a transcript in a directory
named after the old path is a transcript `--resume` will never find.
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "lib"))

import flow_sessions as fs  # noqa: E402
import flow_sync as sync  # noqa: E402

HOME = "/home/u"
NOW = 1_700_000_000
MAP = (("/home/u", "/home/other"),)


def cfg(**kw):
    return sync.load_config(kw.pop("data", None), kw.pop("env", {}), kw.pop("home", HOME))


def stat(size, mtime):
    return sync.Stat(size, mtime)


# --- config ---

def test_defaults_land_under_home():
    c = cfg()
    assert c.projects == "/home/u/.claude/projects"
    assert c.index == "/home/u/.tmux/claude-sessions.tsv"
    assert c.claude_json == "/home/u/.claude.json"


def test_repo_from_yaml_and_env_override():
    assert cfg(data={"repo": "sftp:a:/x"}).repo == "sftp:a:/x"
    assert cfg(data={"repo": "sftp:a:/x"}, env={"FLOW_SYNC_REPO": "sftp:b:/y"}).repo == "sftp:b:/y"


def test_state_dir_env_moves_the_index():
    """The other tools honour $FLOW_STATE_DIR; a sync that did not would push
    one index and restore over another."""
    c = cfg(env={"FLOW_STATE_DIR": "/tmp/state"})
    assert c.index == "/tmp/state/claude-sessions.tsv"


def test_paths_are_everything_that_travels():
    c = cfg()
    assert c.projects in c.paths and c.index in c.paths and c.claude_json in c.paths
    assert "/home/u/.tmux/claude-map" in c.paths


def test_credentials_are_excluded_by_default():
    assert any("credentials" in p for p in cfg().excludes)


# --- restic argv ---

def test_backup_argv_carries_tag_and_excludes():
    c = cfg(data={"repo": "R", "excludes": ["**/.credentials.json"]})
    argv = sync.backup_argv(c, ["/a", "/b"])
    assert argv[:5] == ["restic", "-r", "R", "backup", "--tag"]
    assert "--exclude" in argv and "**/.credentials.json" in argv
    assert argv[-2:] == ["/a", "/b"]


def test_backup_dry_run_asks_restic_not_to_write():
    assert "--dry-run" in sync.backup_argv(cfg(data={"repo": "R"}), ["/a"], dry_run=True)


def test_restore_and_forget_argv():
    c = cfg(data={"repo": "R"})
    assert sync.restore_argv(c, "abc123", "/tmp/s")[3:] == [
        "restore", "abc123", "--target", "/tmp/s"]
    forget = sync.forget_argv(c)
    assert "--keep-daily" in forget and forget[-1] == "--prune"


def test_restore_latest_stays_inside_our_tag():
    """A repository shared with another backup must not answer a pull with
    somebody else's newest snapshot."""
    argv = sync.restore_argv(cfg(data={"repo": "R"}), "latest", "/tmp/s")
    assert argv[-2:] == ["--tag", "flow-state"]


def test_snapshots_latest_narrows_the_query():
    assert sync.snapshots_argv(cfg(data={"repo": "R"}), last=True)[-2:] == ["--latest", "1"]


def test_env_points_restic_at_the_password_file():
    c = cfg(data={"repo": "R"})
    assert sync.env_for(c, {})["RESTIC_PASSWORD_FILE"] == c.password_file


# --- staleness ---

def test_never_pushed_is_always_due():
    assert sync.is_stale(None, NOW, 900)


def test_fresh_push_is_skipped_and_old_one_is_not():
    assert not sync.is_stale(NOW - 10, NOW, 900)
    assert sync.is_stale(NOW - 900, NOW, 900)


# --- remapping ---

def test_parse_remap_pair():
    assert sync.parse_remap("/home/a=/home/b/") == ("/home/a", "/home/b")


def test_parse_remap_rejects_nonsense():
    for bad in ("/home/a", "a=b", "=/home/b"):
        try:
            sync.parse_remap(bad)
        except ValueError:
            continue
        raise AssertionError(f"accepted {bad!r}")


def test_remap_path_rewrites_the_prefix_only():
    assert sync.remap_path("/home/u/work/x", MAP) == "/home/other/work/x"
    assert sync.remap_path("/srv/x", MAP) == "/srv/x"


def test_remap_path_does_not_match_a_longer_sibling():
    assert sync.remap_path("/home/username/x", MAP) == "/home/username/x"


def test_remap_dirname_handles_both_encodings():
    """The transcript store keeps the leading dash, claude-map strips it."""
    assert sync.remap_dirname("-home-u-work-proj", MAP) == "-home-other-work-proj"
    assert sync.remap_dirname("home-u-work-proj__claude", MAP) == "home-other-work-proj__claude"


def test_remap_dirname_leaves_unrelated_names_alone():
    assert sync.remap_dirname("pid__12345", MAP) == "pid__12345"
    assert sync.remap_dirname("main__3.txt", MAP) == "main__3.txt"


def test_remap_dirname_survives_an_underscore_in_the_path():
    """`_` and `/` both encode to `-`, so the rename works on encoded prefixes
    and never tries to reverse one."""
    mapping = (("/home/u/my_project", "/opt/mp"),)
    assert sync.remap_dirname("-home-u-my-project-sub", mapping) == "-opt-mp-sub"


def test_remap_rows_moves_the_index_cwd():
    rows = [fs.Row("main:1", "claude", "/home/u/work/x", "uuid", NOW, NOW)]
    assert sync.remap_rows(rows, MAP)[0].cwd == "/home/other/work/x"


def test_same_home_infers_no_remapping():
    assert sync.detect_remap("/home/u", "/home/u") == ()
    assert sync.detect_remap("/home/u", "/home/other") == (("/home/u", "/home/other"),)


# --- what a pull does with one file ---

def test_absent_locally_is_taken():
    assert sync.decide(None, stat(10, NOW)) == sync.COPY


def test_identical_is_left_alone():
    assert sync.decide(stat(10, NOW), stat(10, NOW)) == sync.KEEP


def test_longer_and_newer_snapshot_wins():
    """A transcript only grows, so this is the same session with more in it."""
    assert sync.decide(stat(10, NOW - 60), stat(20, NOW)) == sync.COPY


def test_local_ahead_is_kept():
    assert sync.decide(stat(20, NOW), stat(10, NOW - 60)) == sync.KEEP


def test_divergence_is_parked_not_resolved():
    """Newer but shorter: the session was resumed in two places. Choosing here
    would silently drop one side's work."""
    assert sync.decide(stat(20, NOW - 60), stat(10, NOW)) == sync.SIDECAR
    assert sync.sidecar_path("/a/b.jsonl") == "/a/b.jsonl.from-sync"


def test_force_overrides_every_rule():
    assert sync.decide(stat(999, NOW), stat(1, 0), force=True) == sync.COPY


# --- merges ---

def test_claude_json_takes_only_missing_projects():
    local = {"projects": {"/home/u/a": {"allowedTools": ["local"]}}, "numStartups": 7}
    remote = {"projects": {"/home/u/a": {"allowedTools": ["remote"]}, "/home/u/b": {}},
              "numStartups": 999}
    merged, added = sync.merge_claude_json(local, remote)
    assert added == ["/home/u/b"]
    assert merged["projects"]["/home/u/a"]["allowedTools"] == ["local"]
    assert merged["numStartups"] == 7, "machine-local counters must not travel"


def test_claude_json_project_keys_are_remapped():
    merged, added = sync.merge_claude_json({}, {"projects": {"/home/u/a": {}}}, MAP)
    assert added == ["/home/other/a"]
    assert "/home/other/a" in merged["projects"]


def test_merge_index_adds_rows_the_machine_never_had():
    local = fs.format_index([fs.Row("main:1", "claude", "/home/u/a", "uuid-a", NOW, NOW)])
    remote = fs.format_index([fs.Row("other:2", "claude", "/home/u/b", "uuid-b", NOW, NOW)])
    rows = fs.parse_index(sync.merge_index(local, remote, NOW))
    assert {r.target for r in rows} == {"main:1", "other:2"}


def test_merge_index_lets_the_machine_win_on_a_shared_window():
    """The snapshot is older than the machine running the pull, by definition."""
    local = fs.format_index([fs.Row("main:1", "claude", "/home/u/a", "live", NOW, NOW)])
    remote = fs.format_index([fs.Row("main:1", "claude", "/home/u/a", "stale", NOW - 99, NOW - 99)])
    rows = fs.parse_index(sync.merge_index(local, remote, NOW))
    assert [r.uuid for r in rows] == ["live"]


def test_merge_index_remaps_incoming_rows():
    remote = fs.format_index([fs.Row("main:9", "claude", "/home/u/b", "uuid-b", NOW, NOW)])
    rows = fs.parse_index(sync.merge_index("", remote, NOW, MAP))
    assert rows[0].cwd == "/home/other/b"
