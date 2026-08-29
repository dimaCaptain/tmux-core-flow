"""What travels to the off-machine repo, and how a restore lands without clobbering.

`flow-session-index` answers "which session ran where". This module answers the
next question: how that answer, and the transcripts it points at, survive the
machine itself — a reinstall, a second laptop, a stolen one.

The premise is narrow and was measured, not assumed: `claude --resume <uuid>`
needs the transcript and nothing else. The directory it lives in must be the
slug of the working directory you resume *from* (`flow_sessions.project_dir`),
and the `cwd` recorded inside the entries is history — it does not have to
match. So a restore is a file copy plus, when the paths differ, a rename.

Two rules shape everything here, and both exist because a sync that is not
paranoid is worse than no sync at all:

1. **A pull never destroys.** Transcripts only ever grow, so between two copies
   of one session the longer *and* newer one wins and the other is left alone.
   When neither dominates — one newer, the other longer — the session was
   resumed in two places and the incoming copy is parked next to the local one
   as `.from-sync` rather than chosen for you.
2. **The index merges, it never replaces.** `flow_sessions.merge` already knows
   how to fold two readings together and retain what is missing from one of
   them; a pull is just another reading, from another machine.

Pure: no filesystem, no network, no clock. `bin/flow-sync` supplies all three.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, replace

import flow_sessions as fs

# Kept in the repo for a month of dailies and half a year of thinning history.
# Transcripts are append-only, so an old snapshot costs the delta and nothing
# more — the reason to keep them is the accident this cannot otherwise survive:
# a local `rm -rf ~/.claude` that a mirror would faithfully replicate.
DEFAULT_RETENTION = ("--keep-daily", "7", "--keep-weekly", "4", "--keep-monthly", "6")

# Never leaves the machine. The transcripts are the context; the credentials are
# the account. Backing up the second buys nothing a re-login does not.
DEFAULT_EXCLUDES = (
    "**/.credentials.json",
    "**/__pycache__",
    "*.tmp",
    "*.tmp.*",
)

# Both resurrect directories are listed because the plugin moved: older
# installs save to ~/.tmux/resurrect, newer ones follow XDG. A path that is not
# there is skipped, so naming both costs nothing and finding neither is the bug.
DEFAULT_EXTRA = (
    "~/.tmux/claude-map",
    "~/.tmux/claude-launcher",
    "~/.tmux/tab-state",
    "~/.tmux/resurrect",
    "~/.local/share/tmux/resurrect",
)

# What a pull decided to do with one incoming file.
COPY = "copy"        # nothing here, or the incoming one is strictly ahead
KEEP = "keep"        # what is here already dominates
SIDECAR = "sidecar"  # both moved apart — park it, do not choose


@dataclass(frozen=True)
class Config:
    """Where the repo is and what goes into it.

    `projects`, `index` and `claude_json` are named rather than folded into
    `extra` because each is reconciled differently on the way back: transcripts
    by file, the index by merge, the config by key.
    """

    repo: str = ""
    password_file: str = ""
    home: str = ""
    projects: str = ""
    index: str = ""
    claude_json: str = ""
    extra: tuple[str, ...] = DEFAULT_EXTRA
    excludes: tuple[str, ...] = DEFAULT_EXCLUDES
    retention: tuple[str, ...] = DEFAULT_RETENTION
    tag: str = "flow-state"

    @property
    def paths(self) -> tuple[str, ...]:
        """Everything that goes into a snapshot, longest-lived first."""
        return (self.projects, self.index, self.claude_json, *self.extra)


@dataclass(frozen=True)
class Stat:
    """The two facts a pull decides on. Size, because transcripts only grow."""

    size: int
    mtime: int


def expand(path: str, home: str) -> str:
    """`~/x` -> `<home>/x`, everything else untouched and made absolute."""
    if path.startswith("~/") or path == "~":
        path = home + path[1:]
    return os.path.normpath(path)


def load_config(data: dict | None, env: dict, home: str) -> Config:
    """Build the config from parsed YAML, with the environment winning.

    Env overrides exist so a one-off restore onto a rescue machine needs no
    config file at all: `FLOW_SYNC_REPO=sftp:host:/path flow-sync pull`.
    """
    data = dict(data or {})
    cfg = Config(
        repo=str(env.get("FLOW_SYNC_REPO") or data.get("repo") or ""),
        password_file=expand(
            str(env.get("FLOW_SYNC_PASSWORD_FILE")
                or data.get("password_file")
                or "~/.config/tmux-core-flow/sync-password"),
            home,
        ),
        home=home,
        projects=expand(
            str(env.get("FLOW_CLAUDE_PROJECTS") or data.get("projects") or "~/.claude/projects"),
            home,
        ),
        index=expand(
            os.path.join(env["FLOW_STATE_DIR"], "claude-sessions.tsv")
            if env.get("FLOW_STATE_DIR")
            else str(data.get("index") or "~/.tmux/claude-sessions.tsv"),
            home,
        ),
        claude_json=expand(str(data.get("claude_json") or "~/.claude.json"), home),
        extra=tuple(expand(str(p), home) for p in data.get("extra", DEFAULT_EXTRA)),
        excludes=tuple(str(p) for p in data.get("excludes", DEFAULT_EXCLUDES)),
        retention=tuple(str(p) for p in data.get("retention", DEFAULT_RETENTION)),
        tag=str(data.get("tag") or "flow-state"),
    )
    return cfg


# --- restic invocation -----------------------------------------------------
#
# restic is the transport and only the transport: it gives encryption at rest,
# deduplication across snapshots, and a repo the server never needs software to
# host (sftp is the ssh daemon that is already there). Everything about *what*
# to send and *how it lands* stays in this file, so swapping the transport is a
# rewrite of six argv builders and nothing else.


def base_argv(cfg: Config) -> list[str]:
    return ["restic", "-r", cfg.repo]


def env_for(cfg: Config, environ: dict) -> dict:
    """Environment for a restic call: the password comes from a file, always."""
    out = dict(environ)
    out["RESTIC_PASSWORD_FILE"] = cfg.password_file
    out.setdefault("RESTIC_PROGRESS_FPS", "0.2")
    return out


def init_argv(cfg: Config) -> list[str]:
    return base_argv(cfg) + ["init"]


def backup_argv(cfg: Config, paths: list[str], dry_run: bool = False) -> list[str]:
    argv = base_argv(cfg) + ["backup", "--tag", cfg.tag]
    for pattern in cfg.excludes:
        argv += ["--exclude", pattern]
    if dry_run:
        argv += ["--dry-run", "--verbose"]
    return argv + list(paths)


def snapshots_argv(cfg: Config, last: bool = False) -> list[str]:
    argv = base_argv(cfg) + ["snapshots", "--tag", cfg.tag, "--json"]
    return argv + ["--latest", "1"] if last else argv


def restore_argv(cfg: Config, snapshot: str, target: str) -> list[str]:
    """`latest` is resolved within our own tag, so a repository shared with
    another backup never answers a pull with somebody else's snapshot."""
    argv = base_argv(cfg) + ["restore", snapshot, "--target", target]
    return argv + ["--tag", cfg.tag] if snapshot == "latest" else argv


def forget_argv(cfg: Config, prune: bool = True) -> list[str]:
    argv = base_argv(cfg) + ["forget", "--tag", cfg.tag, *cfg.retention]
    return argv + ["--prune"] if prune else argv


# --- staleness -------------------------------------------------------------


def is_stale(last_push: int | None, now: int, max_age: int) -> bool:
    """Whether a push is due. No stamp means overdue, never 'skip'."""
    if last_push is None:
        return True
    return now - last_push >= max_age


# --- path remapping --------------------------------------------------------
#
# The one thing that genuinely breaks a restore onto a different machine: a
# transcript lives in a directory named after the absolute path it belongs to.
# Move `$HOME` and every one of those names is wrong, and `--resume` finds
# nothing. The fix is mechanical — rename by prefix, on both the encoded form
# and the plain one — but it has to happen, and it has to happen to the index
# too or `flow-restore` will `cd` into a directory that does not exist.


def parse_remap(spec: str) -> tuple[str, str]:
    """`/old/home=/new/home` -> the pair. Raises on anything else."""
    old, sep, new = spec.partition("=")
    old, new = old.rstrip("/"), new.rstrip("/")
    if not sep or not old.startswith("/") or not new.startswith("/"):
        raise ValueError(f"remap must be /old/path=/new/path, got {spec!r}")
    return old, new


def remap_path(path: str, mapping: tuple[tuple[str, str], ...]) -> str:
    """Rewrite an absolute path by the first matching prefix."""
    for old, new in mapping:
        if path == old or path.startswith(old + "/"):
            return new + path[len(old):]
    return path


def remap_dirname(name: str, mapping: tuple[tuple[str, str], ...]) -> str:
    """Rewrite an encoded directory name — the slug, not a path.

    Both encodings are handled, because two directories carry them: the
    transcript store keeps the leading dash (`-home-you-work-x`) and
    `~/.tmux/claude-map/` strips it (`home-you-work-x`). The substitution is
    lossy in one direction only — you cannot decode a slug back into a path,
    since `_` and `/` both became `-` — so the rename works on encoded prefixes
    and never tries to reverse them.
    """
    for old, new in mapping:
        for encode in (fs.project_dir, fs.encode_cwd):
            old_s, new_s = encode(old), encode(new)
            if name == old_s or name.startswith(old_s + "-"):
                return new_s + name[len(old_s):]
    return name


def remap_rows(rows: list[fs.Row], mapping: tuple[tuple[str, str], ...]) -> list[fs.Row]:
    """The index, with every `cwd` pointed at where the files actually are."""
    if not mapping:
        return rows
    return [replace(r, cwd=remap_path(r.cwd, mapping)) for r in rows]


def detect_remap(snapshot_home: str, home: str) -> tuple[tuple[str, str], ...]:
    """The mapping a restore needs, inferred from the two home directories.

    A pull onto the same machine — the common case, a reboot or a reinstall
    into the same layout — infers nothing and touches no name.
    """
    a, b = snapshot_home.rstrip("/"), home.rstrip("/")
    return () if a == b or not a else ((a, b),)


# --- reconciliation --------------------------------------------------------


def decide(local: Stat | None, remote: Stat, force: bool = False) -> str:
    """What to do with one incoming file.

    Transcripts grow and never shrink, so "newer and at least as large" is a
    superset and is safe to take. The reverse is safe to ignore. What is left —
    one copy newer, the other larger — is a session that was resumed on two
    machines, and merging those is not a thing a backup tool gets to guess.
    """
    if local is None or force:
        return COPY
    if remote.size == local.size and remote.mtime == local.mtime:
        return KEEP
    if remote.mtime >= local.mtime and remote.size >= local.size:
        return COPY
    if local.mtime >= remote.mtime and local.size >= remote.size:
        return KEEP
    return SIDECAR


def sidecar_path(path: str) -> str:
    return path + ".from-sync"


def merge_claude_json(
    local: dict, remote: dict, mapping: tuple[tuple[str, str], ...] = ()
) -> tuple[dict, list[str]]:
    """Fold the incoming per-project settings into the local config.

    Only `projects` travels, and only keys that are missing here. That section
    holds the trust decision, the allowed tools and the MCP servers for a
    working directory — without it a restored machine re-asks for every one of
    them. The rest of the file describes *this* installation (startup counts,
    machine id, onboarding) and would be wrong to import.
    """
    out = dict(local)
    projects = dict(out.get("projects") or {})
    added = []
    for key, value in (remote.get("projects") or {}).items():
        moved = remap_path(key, mapping) if mapping else key
        if moved not in projects:
            projects[moved] = value
            added.append(moved)
    out["projects"] = projects
    return out, sorted(added)


def merge_index(local_text: str, remote_text: str, now: int,
                mapping: tuple[tuple[str, str], ...] = ()) -> str:
    """The index after a pull: local truth, plus rows only the snapshot has.

    Argument order matters. `fs.merge(old, current, now)` lets `current` win per
    target, and here the local index is the current reading — the snapshot is
    by definition older than the machine that is running.
    """
    remote_rows = remap_rows(fs.parse_index(remote_text), mapping)
    local_rows = fs.parse_index(local_text)
    return fs.format_index(fs.merge(remote_rows, local_rows, now))
