# State format

`claude-hook-notify` publishes agent state in four places. All of them are
plain files or plain tmux options — no daemon, no socket, no database. Read them
from any language to build your own status widget.

This document is the contract. If you build on it, these are the guarantees.

## 1. Tab colour — `window-status-style`

The live state of a window, readable straight from tmux:

```bash
tmux show-window-option -t "$session:$window" window-status-style
```

| Colour | Hex | Meaning |
|---|---|---|
| 🟡 yellow | `#f9e2af` | working — the agent is doing something |
| 🟢 green | `#a6e3a1` | needs approval — waiting on you |
| 🔴 red | `#f38ba8` | finished — the turn ended, or 30 min of silence |
| ⚪ unset | — | no agent in this window |

Match on the hex substring, not the whole style string: it also carries
`fg=` and `bold`.

**Red is sticky.** Ambient output does not clear it — only a `permission_prompt`
(green) or a new `UserPromptSubmit` (yellow) does. This is what stops a finished
agent from flickering back to "working" because something printed to its pane.

## 2. Timestamps — `~/.tmux/tab-state/<session>__<window>.txt`

Line-based `key=value`, Unix epoch seconds. Written on every hook event:

```
work=1753436400
wait=1753436712
done=1753436980
activity=1753436975
```

| Key | Set when | Use it for |
|---|---|---|
| `work` | `UserPromptSubmit` | how long the agent has been on the current prompt |
| `wait` | `permission_prompt` | how long it has been blocked on you |
| `done` | `Stop` | how long since it finished |
| `activity` | `PostToolUse` and other notifications | last sign of life; idle detection |

Session names have `/` and space translated to `_`, so `my proj` becomes
`my_proj`. The window is the tmux window **index**, not its name.

Pick the timestamp matching the current colour — that pairing is what gives a
meaningful "elapsed" figure:

| Colour | Read |
|---|---|
| 🟡 yellow | `work` |
| 🟢 green | `wait` |
| 🔴 red | `done` |
| ⚪ unset | `activity`, and only if it is more than 30 min old |

## 3. Session mapping — `~/.tmux/claude-map/`

Maps a tmux pane to the Claude Code session running in it, so you can find its
transcript. Each file contains one session UUID and nothing else.

Two keys are written for every event, because each fails in a different way:

| File | Key | Survives | Breaks on |
|---|---|---|---|
| `<cwd>__<window-name>` | working directory + window name | window restarts, new PIDs | two windows with the same name in the same directory |
| `pid__<pane_pid>` | tmux pane PID | duplicate window names | the pane being replaced |

`<cwd>` has `/` and `_` both translated to `-`, with any leading `-` stripped:
`/home/you/work/proj` becomes `home-you-work-proj`.

Given a UUID you can read the transcript at:

```
~/.claude/projects/<cwd-with-slashes-as-dashes>/<uuid>.jsonl
```

Prefer `pid__*` when you have the pane PID — it is unambiguous. Fall back to the
name key otherwise.

## 4. Launcher — `~/.tmux/claude-launcher/<uuid>`

One line: the command this session was started with, without `--resume <uuid>`.
Keyed by session UUID, because the launcher belongs to the session — move it to
another window and it still has to come back the same way.

It exists because resuming through the wrong entry point looks like it worked.
A session started on GLM and resumed with plain `claude` loads the same
transcript and answers as a different model, and nothing on screen says so.

Two sources, in order:

| Source | When |
|---|---|
| `$FLOW_LAUNCHER` | a wrapper exported it — the hook runs as a child of Claude, so it just reads the variable |
| `--model X` in Claude's argv | no wrapper announced itself, but a model was pinned on the command line; recorded as `claude --model X` |

Neither present means plain `claude`, and nothing is written.

The record is authoritative, not a one-time reading: every `flow-session-index
--update` re-reads it for every row that has one, including rows tmux can no
longer show. That is what makes a launcher recoverable after the fact — a
session that predates the hook, or one the orchestrator started by calling the
binary directly, can have its record written later and the index will pick it
up on the next tick. A row with no record keeps whatever it already had.

If you wrap Claude Code, one line makes your wrapper restorable:

```bash
export FLOW_LAUNCHER=claude-glm     # before exec claude …
```

Consumers must treat the value as untrusted input — it ends up on a command
line. `flow_sessions.sanitize_launcher` allows only what a command and a model
argument need, and returns `claude` for anything else; `flow-restore` also
falls back when the program is not on this machine's `PATH`.

## 5. Session index — `~/.tmux/claude-sessions.tsv`

The three surfaces above describe *now*. This one describes *what was there*,
so it still answers after a reboot — when tmux comes back (via resurrect or
continuum) but every Claude process is gone.

Maintained by `flow-session-index`, which joins live tmux windows against
`claude-map` and the transcripts. Tab-separated, one session per line, newest
activity first:

```
#target	window	cwd	uuid	activity	seen	launcher
pet-projects:3	claude	/home/you/work/proj	3bcad249-…	1784998162	1784998308	claude
```

| Column | Meaning |
|---|---|
| `target` | tmux `session:index` the session ran in |
| `window` | window name, for recognising it in a list |
| `cwd` | working directory — `claude --resume` only resolves from here |
| `uuid` | the argument to `claude --resume` |
| `activity` | epoch of the last transcript write |
| `seen` | epoch this row was last confirmed against a live window |
| `launcher` | command that starts this session, minus `--resume <uuid>` |

An index written before `launcher` existed has six columns and still parses —
those rows resume as plain `claude`.

Two rules make it durable, and both exist because the obvious implementation
fails at exactly these points:

- **Nothing is derived from running processes.** A snapshot built from live
  PIDs rebuilds itself into nothing once those processes are gone — which is
  precisely when you need it.
- **A refresh merges, it never truncates.** An empty reading means "tmux is not
  up yet", not "everything ended". Rows are retained until `seen` goes 30 days
  stale, so a window you closed stays resumable.

A row is dropped when its transcript no longer exists: an entry you cannot
resume is worse than no entry.

`~/.tmux/claude-sessions.txt` is the same data rendered for humans — every line
is a pasteable `claude --resume` command, because the recovery path has to work
when everything fancier is broken.

`flow-restore` consumes the index; `flow-restore --candidates` re-emits it with
a `live` / `restorable` / `gone` verdict per row if you want to build your own
picker.

## Off-machine copies

Everything above is a file, which is what makes `flow-sync` a copy job rather
than an integration: it pushes this state into an encrypted restic repository
and reconciles it back. The one thing a restore cannot take verbatim is a name
— the transcript directory and the `claude-map` keys encode an absolute path,
so a machine with a different `$HOME` gets them rewritten by prefix, along with
the `cwd` column of the index. See [`sync.md`](sync.md).

## Stability

The colours, the four keys, the two map filenames, the launcher file and the
session index columns are the public surface and will not change silently.
Columns are only ever appended, and a reader must accept a row that is short by
the columns it does not know. The rest — the internal
order of checks, how the hook finds its window by walking the PPID chain — is
free to change.
