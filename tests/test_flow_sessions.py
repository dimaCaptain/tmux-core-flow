"""Tests for the durable session index (lib/flow_sessions.py).

The interesting behaviour is all in `merge`: the index has to keep describing
what was there when nothing is running, because that is the exact moment you
need it — right after a reboot.
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "lib"))

import flow_sessions as fs  # noqa: E402

DAY = 86400
NOW = 1_700_000_000


def row(target="main:1", uuid="uuid-a", activity=NOW, seen=NOW, cwd="/home/u/proj",
        window="claude", launcher="claude"):
    return fs.Row(target, window, cwd, uuid, activity, seen, launcher)


# --- key encoding: must match what claude-hook-notify writes ---

def test_encode_cwd_collapses_slash_and_underscore():
    assert fs.encode_cwd("/home/you/work/my_project") == "home-you-work-my-project"


def test_encode_cwd_strips_leading_dash():
    assert not fs.encode_cwd("/home/you").startswith("-")


def test_project_dir_keeps_leading_dash():
    assert fs.project_dir("/home/you/work/my_project") == "-home-you-work-my-project"


# --- launcher: how the session has to come back ---

def test_launcher_defaults_to_plain_claude():
    assert row().launcher == "claude"


def test_sanitize_accepts_a_wrapper_and_a_model_flag():
    assert fs.sanitize_launcher("claude-glm") == "claude-glm"
    assert fs.sanitize_launcher("claude --model zhipu,glm-5.2") == "claude --model zhipu,glm-5.2"


def test_sanitize_refuses_anything_the_shell_would_interpret():
    """The value is typed into a prompt. A tampered or corrupt file must
    degrade to plain `claude`, not to something that runs."""
    for bad in ("claude; rm -rf /", "claude && curl x|sh", "claude `id`", "claude $(id)", ""):
        assert fs.sanitize_launcher(bad) == "claude"


def test_render_pastes_the_launcher_not_bare_claude():
    """Every line of the human view is meant to be pasteable as-is, which it
    is not if it names the wrong binary."""
    assert "claude-glm --resume uuid-a" in fs.render([row(launcher="claude-glm")], NOW)


# --- on-disk format ---

def test_an_index_written_before_launchers_still_parses():
    """Six columns is what every existing index on disk has. Rejecting those
    rows would empty the index of exactly the sessions a restore needs."""
    legacy = "#target\twindow\tcwd\tuuid\tactivity\tseen\n" \
             "main:1\tclaude\t/home/u/proj\tuuid-a\t100\t100\n"
    rows = fs.parse_index(legacy)
    assert len(rows) == 1 and rows[0].launcher == "claude"


def test_launcher_survives_the_roundtrip():
    rows = [fs.Row("main:1", "claude", "/home/u/p", "uuid-a", NOW, NOW, "claude-gemini")]
    assert fs.parse_index(fs.format_index(rows))[0].launcher == "claude-gemini"


def test_format_parse_roundtrip():
    rows = [row(target="a:1"), row(target="b:2", uuid="uuid-b")]
    back = fs.parse_index(fs.format_index(rows))
    assert sorted(back, key=lambda r: r.target) == sorted(rows, key=lambda r: r.target)


def test_parse_skips_header_and_junk():
    text = "#target\twindow\tcwd\tuuid\tactivity\tseen\nnot a row\n\n"
    assert fs.parse_index(text) == []


def test_parse_skips_row_with_bad_number_but_keeps_others():
    good = fs.format_index([row(target="a:1")]).splitlines()[1]
    text = "\n".join(["a:1\tw\t/c\tu\tNOT_A_NUMBER\t1", good])
    parsed = fs.parse_index(text)
    assert len(parsed) == 1 and parsed[0].target == "a:1"


def test_format_survives_tab_in_window_name():
    rows = [row(window="we\tird")]
    assert len(fs.parse_index(fs.format_index(rows))) == 1


def test_parse_empty_is_empty():
    assert fs.parse_index("") == []


# --- merge: the durability rules ---

def test_current_reading_replaces_stored_row():
    old = [row(uuid="stale", activity=NOW - DAY)]
    new = [row(uuid="fresh", activity=NOW)]
    merged = fs.merge(old, new, NOW)
    assert [r.uuid for r in merged] == ["fresh"]


def test_empty_reading_keeps_everything():
    """tmux not up yet is not evidence that a session ended."""
    old = [row(target="a:1"), row(target="b:2", uuid="uuid-b")]
    assert len(fs.merge(old, [], NOW)) == 2


def test_window_absent_from_reading_is_retained():
    """A window you closed stays resumable — that is the whole point."""
    old = [row(target="closed:9", uuid="uuid-closed")]
    merged = fs.merge(old, [row(target="open:1")], NOW)
    assert {r.target for r in merged} == {"closed:9", "open:1"}


def test_stale_row_is_pruned():
    old = [row(target="ancient:1", seen=NOW - 31 * DAY)]
    assert fs.merge(old, [], NOW) == []


def test_row_just_inside_retention_is_kept():
    old = [row(target="old:1", seen=NOW - 29 * DAY)]
    assert len(fs.merge(old, [], NOW)) == 1


def test_retention_window_is_configurable():
    old = [row(target="old:1", seen=NOW - 5 * DAY)]
    assert fs.merge(old, [], NOW, retain_days=3) == []


def test_stale_row_revived_by_current_reading():
    """Being seen now beats having been stale."""
    old = [row(target="a:1", seen=NOW - 99 * DAY)]
    merged = fs.merge(old, [row(target="a:1", seen=NOW)], NOW)
    assert len(merged) == 1 and merged[0].seen == NOW


def test_rows_without_uuid_are_dropped():
    assert fs.merge([row(uuid="")], [], NOW) == []
    assert fs.merge([], [row(uuid="")], NOW) == []


# --- rendering ---

def test_humanize_scales():
    assert fs.humanize(30) == "30s"
    assert fs.humanize(600) == "10m"
    assert fs.humanize(7200) == "2h"
    assert fs.humanize(10 * DAY) == "10d"


def test_render_line_is_a_pasteable_command():
    text = fs.render([row(uuid="abc-123")], NOW)
    assert "claude --resume abc-123" in text


def test_render_shows_project_not_full_path():
    text = fs.render([row(cwd="/home/u/work/YasnoPOS")], NOW)
    assert "YasnoPOS" in text


def test_render_empty_says_so_without_crashing():
    assert "empty" in fs.render([], NOW)


def test_render_newest_first():
    rows = [row(target="old:1", activity=NOW - DAY), row(target="new:1", activity=NOW)]
    body = [ln for ln in fs.render(rows, NOW).splitlines() if not ln.startswith("#")]
    assert "new:1" in body[0]
