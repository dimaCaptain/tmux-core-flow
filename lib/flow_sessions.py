"""Durable index of Claude Code sessions, keyed by tmux window.

The problem this solves: tmux survives a reboot (via resurrect/continuum) but
the Claude processes in its windows do not. Everything needed to bring them
back already exists on disk — `~/.tmux/claude-map/` maps a working directory
plus window name to a session UUID, and `~/.claude/projects/<cwd>/<uuid>.jsonl`
is the transcript. What was missing is something that joins the two and keeps
the answer across a reboot.

Two rules make the index durable, and both exist because the mechanism this
replaces failed at exactly these points:

1. **Never derive from live processes.** The old snapshot was rebuilt from
   `~/.claude/sessions/<pid>.json`, which only ever describes running agents.
   After a reboot there are none, so the snapshot rebuilt itself into nothing.
2. **Never shrink destructively.** A refresh merges into what is already there.
   An empty reading — tmux not up yet, windows not restored yet — leaves the
   previous contents alone instead of overwriting them.

This module is pure: no filesystem, no tmux, no clock. `bin/flow-session-index`
supplies all of that. Time is always passed in as an epoch so tests can pin it.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

RETAIN_DAYS = 30

_COLUMNS = ("target", "window", "cwd", "uuid", "activity", "seen")


@dataclass(frozen=True)
class Row:
    """One restorable session.

    target   tmux "session:index", e.g. "pet-projects:3" — where it was
    window   tmux window name, e.g. "claude" — for recognising it in a list
    cwd      absolute working directory the session belongs to
    uuid     Claude Code session id, the argument to `claude --resume`
    activity epoch of the last transcript write — when the session last did work
    seen     epoch when this row was last confirmed against a live tmux window
    """

    target: str
    window: str
    cwd: str
    uuid: str
    activity: int
    seen: int


def encode_cwd(path: str) -> str:
    """Working directory in `~/.tmux/claude-map/` key form.

    `/home/you/work/my_project` -> `home-you-work-my-project`

    Both `/` and `_` collapse to `-`, and the leading `-` is stripped. This
    matches what `claude-hook-notify` writes; see docs/state-format.md.
    """
    return path.replace("/", "-").replace("_", "-").lstrip("-")


def project_dir(cwd: str) -> str:
    """Working directory in `~/.claude/projects/` directory-name form.

    `/home/you/work/my_project` -> `-home-you-work-my-project`

    The same substitution as `encode_cwd` but keeping the leading `-`. Claude
    Code owns this one; we only read it.
    """
    return cwd.replace("/", "-").replace("_", "-")


def _clean(value: str) -> str:
    """Strip characters that would corrupt a TSV row."""
    return str(value).replace("\t", " ").replace("\n", " ").strip()


def format_index(rows: list[Row]) -> str:
    """Render rows as the on-disk TSV, newest activity first."""
    out = ["#" + "\t".join(_COLUMNS)]
    for r in sorted(rows, key=lambda r: (-r.activity, r.target)):
        out.append(
            "\t".join(
                (
                    _clean(r.target),
                    _clean(r.window),
                    _clean(r.cwd),
                    _clean(r.uuid),
                    str(r.activity),
                    str(r.seen),
                )
            )
        )
    return "\n".join(out) + "\n"


def parse_index(text: str) -> list[Row]:
    """Read the on-disk TSV. Unparsable lines are skipped, never fatal.

    A corrupt index must degrade to a partial one — losing a row costs you one
    resume you can still do by hand, while raising here would take out the cron
    job that maintains the whole file.
    """
    rows = []
    for line in (text or "").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) != len(_COLUMNS):
            continue
        target, window, cwd, uuid, activity, seen = parts
        try:
            rows.append(Row(target, window, cwd, uuid, int(activity), int(seen)))
        except ValueError:
            continue
    return rows


def merge(old: list[Row], current: list[Row], now: int, retain_days: int = RETAIN_DAYS) -> list[Row]:
    """Fold a fresh reading into the stored index.

    - a target present in `current` is replaced — that is the live truth
    - a target absent from `current` is kept until it goes `retain_days` stale,
      so a window you closed (or one tmux has not restored yet) stays
      resumable rather than vanishing on the next cron tick
    - rows without a uuid are dropped; they are not restorable

    `current` being empty is not a signal that anything ended — it usually
    means tmux was not running when we looked. Everything is retained.
    """
    cutoff = now - retain_days * 86400
    by_target = {}
    for r in old:
        if r.uuid and r.seen >= cutoff:
            by_target[r.target] = r
    for r in current:
        if r.uuid:
            by_target[r.target] = r
    return list(by_target.values())


def humanize(seconds: int) -> str:
    """Compact age: 45s / 12m / 3h / 8d. Empty for a missing timestamp."""
    if seconds < 0:
        return "?"
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 172800:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def render(rows: list[Row], now: int | None = None) -> str:
    """The human view — what `cat ~/.tmux/claude-sessions.txt` shows.

    Each line is a command you can paste as-is, because the recovery path has
    to work even when everything fancier is broken.
    """
    now = int(time.time()) if now is None else now
    stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(now))
    ordered = sorted(rows, key=lambda r: (-r.activity, r.target))
    if not ordered:
        return f"# Claude sessions — {stamp}\n#\n# (index empty — no sessions recorded yet)\n"

    w_target = max(len(r.target) for r in ordered)
    w_proj = max(len(r.cwd.rstrip("/").split("/")[-1] or "/") for r in ordered)
    lines = [
        f"# Claude sessions — {stamp}  (durable index, survives reboot)",
        "#",
    ]
    for r in ordered:
        project = r.cwd.rstrip("/").split("/")[-1] or "/"
        age = humanize(now - r.activity)
        lines.append(
            f"  {r.target:<{w_target}}  {project:<{w_proj}}  {age:>4} ago  "
            f"claude --resume {r.uuid}"
        )
    return "\n".join(lines) + "\n"
