# State format

`claude-hook-notify` publishes agent state in three places. All of them are
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

## 4. Session index — `~/.tmux/claude-sessions.tsv`

The three surfaces above describe *now*. This one describes *what was there*,
so it still answers after a reboot — when tmux comes back (via resurrect or
continuum) but every Claude process is gone.

Maintained by `flow-session-index`, which joins live tmux windows against
`claude-map` and the transcripts. Tab-separated, one session per line, newest
activity first:

```
#target	window	cwd	uuid	activity	seen
pet-projects:3	claude	/home/you/work/proj	3bcad249-…	1784998162	1784998308
```

| Column | Meaning |
|---|---|
| `target` | tmux `session:index` the session ran in |
| `window` | window name, for recognising it in a list |
| `cwd` | working directory — `claude --resume` only resolves from here |
| `uuid` | the argument to `claude --resume` |
| `activity` | epoch of the last transcript write |
| `seen` | epoch this row was last confirmed against a live window |

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

## Stability

The colours, the four keys, the two map filenames, and the session index columns
are the public surface and will not change silently. The rest — the internal
order of checks, how the hook finds its window by walking the PPID chain — is
free to change.
