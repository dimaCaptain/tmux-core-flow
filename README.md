# tmux-core-flow

> Deterministic state tracking and permission control for
> [Claude Code](https://docs.anthropic.com/en/docs/claude-code) agents running in tmux.

[![CI](https://github.com/dimaCaptain/tmux-core-flow/actions/workflows/ci.yml/badge.svg)](https://github.com/dimaCaptain/tmux-core-flow/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)

Running several Claude Code agents at once, two things get tedious: knowing
which one needs you, and approving a permission prompt for every safe tool call.
This does both from Claude Code's own hooks — no screen scraping, no simulated
keypresses, no background daemon.

1. **State** — `claude-hook-notify` turns hook events into a colour on the tmux
   tab: 🟡 working, 🟢 waiting for your approval, 🔴 finished. It also writes
   timestamps and a pane→session map to disk.
2. **Permissions** — `flow-approve` is a `PreToolUse` hook that auto-approves
   tool calls except those matching an `always_ask` deny-list.
3. **Survival** — `flow-session-index` records which session ran in which
   window, so a reboot does not cost you the context of a dozen agents, and
   `flow-sync` keeps an encrypted copy of that state off the machine entirely.

## No dashboard here — on purpose

You get the state, not a widget. The hard part is the state machine: knowing
that a finished agent must *stay* red when its pane keeps printing, that green
clears when a tool runs because that means you approved. Rendering is the easy
part, and it is where everyone's needs differ — your columns are not mine.

So the tab colour is already a usable display on its own, and
[`docs/state-format.md`](docs/state-format.md) documents every file this writes
so you can build exactly the widget you want, in any language. That contract is
stable and versioned; a dashboard shipped here would only rot.

## How the state machine works

Each hook event resolves to a tmux window by walking the process tree, then sets
that window's `window-status-style`:

| Event | Result |
|---|---|
| `UserPromptSubmit` | 🟡 you gave it work |
| `permission_prompt` | 🟢 it needs your approval |
| `PostToolUse` | 🟡 a tool ran, so you approved — back to working |
| `Stop` | 🔴 the turn ended |
| 30 min of silence | 🔴 idle (tmux `monitor-silence`) |

**Red is sticky.** Ambient output never clears it; only a permission prompt or a
new user prompt does. Without that rule a finished agent flickers back to
"working" every time something writes to its pane — which is exactly the failure
that makes naive terminal-watching approaches useless.

## How autoapprove decides

The hook receives the exact `tool_name` + `tool_input` *before* any prompt:

- default → `permissionDecision: "allow"` (runs silently)
- matches an `always_ask` rule (`rm`, `git push`, …) → `"ask"` (prompts you)

It never denies — a matched rule just gives you back the normal prompt. Any
error inside the hook emits nothing and exits 0, so a bug here can never block
an agent.

Rule forms in `policy.yaml`:

| Rule | Matches |
|---|---|
| `Bash(git push)` | substring anywhere in the command |
| `Bash(!rm ` | whole word only — `rm ` fires, `term ` does not |
| `Edit` | every call to that tool |

**Activation:** auto-approval is on for **any agent inside tmux** (detected via
`$TMUX`), so it works in every window without per-launcher wiring.

| Env | Effect |
|---|---|
| _(inside tmux)_ | on |
| _(outside tmux)_ | off (transparent) |
| `FLOW_AUTOAPPROVE=1` | force on (even outside tmux) |
| `FLOW_AUTOAPPROVE=0` | force off (escape hatch for one session) |

This includes your own interactive session if it runs in tmux — dangerous calls
still prompt via `always_ask`.

## Surviving a reboot

tmux comes back — [resurrect](https://github.com/tmux-plugins/tmux-resurrect)
and [continuum](https://github.com/tmux-plugins/tmux-continuum) restore your
windows. The Claude processes in them do not, and with twenty agents that is a
lot of context to rebuild by hand.

Everything needed to bring them back is already on disk: `claude-hook-notify`
recorded which session UUID ran in which window, and the transcript is still
there. `flow-session-index` joins the two into
[`~/.tmux/claude-sessions.tsv`](docs/state-format.md#4-session-index--tmuxclaude-sessionstsv)
and keeps it correct across a reboot, because it derives nothing from running
processes and a refresh merges rather than truncates.

Then, once tmux is back:

```console
$ flow-restore
9 session(s) can be restored:

  main:6      Irbi        8h ago   claude --resume 82e05302-…
  yasno:2     stew       10d ago   claude --resume a7094bbe-…
  …
4 window(s) already running Claude, left alone.
```

```bash
flow-restore --arm --all     # type the command into each window, don't run it
flow-restore --run --all     # actually start them, 3 at a time
flow-restore --arm yasno:2   # just this one
```

**`--arm` is the default habit worth forming.** It types the resume command and
leaves the cursor there, so you press Enter only in the windows you actually
open. Nothing starts on its own — resuming twenty agents simultaneously will
put the machine into swap, which is why restoring is a choice here and not a
boot-time side effect. `--run` batches for the same reason.

Two things it will not do. A window where Claude runs in **any** pane is left
alone, so you never get a second copy of a live session. And within a window it
types into a specific pane — the one you left focused, or the largest idle
shell — chosen by checking that the pane's process really is a bare shell:

```
/usr/bin/zsh                       idle shell — safe to type into
/bin/bash /path/to/some-widget     a script — skipped
```

That check matters because a pane running a long-lived script reports its
`pane_current_command` as `bash`, exactly like an idle shell. Without looking at
the actual argv, a resume command lands in your status widget instead of your
shell. A window with no idle pane is reported as blocked rather than guessed at.

What gets typed is the command the session was **started** with, not always
plain `claude`:

```
main:3    agent-control   4d ago   claude-glm --resume eb5fbcfd-…
kvant:2   finance         9d ago   claude --resume a6966a0e-…
```

Resuming a session through the wrong entry point looks like it worked — the
transcript loads, and only the answers tell you a different model is behind
them. So the hook records the launcher, keyed by session
([`docs/state-format.md`](docs/state-format.md#4-launcher--tmuxclaude-launcheruuid)):
a wrapper that exports `FLOW_LAUNCHER` is remembered by name, a bare
`--model` on the command line is remembered as itself, and everything else is
plain `claude`. A launcher that is not on this machine's `PATH` falls back to
`claude` rather than typing a command that cannot run.

## Surviving the machine

A reboot is the cheap case — the disk is still there. `flow-sync` covers the
expensive ones: a reinstall, a second laptop, a stolen one. It pushes the
transcripts, the index and the pane map into an encrypted
[restic](https://restic.net) repository over ssh, and pulls them back onto a
machine that has nothing.

```bash
flow-sync push            # or from cron: push --if-stale 900 --quiet
flow-sync pull            # newest snapshot, then flow-restore as usual
```

The premise was measured, not assumed: `claude --resume` needs the transcript
and nothing else — a transcript copied into a directory it never belonged to
resumes with its full history. What it *does* need is the directory name to be
the slug of the working directory you resume from, which is exactly what breaks
on a machine where `$HOME` is different. So a pull reads the snapshot's own home,
compares it with yours, and renames as it restores:

```console
$ flow-sync pull
remapping paths: /home/you -> /Users/you
restored: 166 file(s), 0 already current
index: +57 session(s)
```

A pull never destroys. Transcripts only ever grow, so between two copies of one
session the longer *and* newer one wins and the other is left alone; when
neither dominates — the session was resumed on two machines — the incoming copy
is parked as `.from-sync` rather than chosen for you. The index and
`~/.claude.json` merge instead of being overwritten, because both describe this
machine as well as the snapshot's. Credentials never leave the machine at all.

Full setup, the conflict rules and the remapping in
[`docs/sync.md`](docs/sync.md). Needs `restic`; everything else here works
without it.

## Install

```bash
git clone https://github.com/dimaCaptain/tmux-core-flow
cd tmux-core-flow
./install.sh            # symlinks, policy file, hook registration
./install.sh --dry-run  # preview, change nothing
```

Then add one line to your `~/.tmux.conf` and reload it:

```tmux
source-file /path/to/tmux-core-flow/config/tmux-monitoring.conf
```

`install.sh` is idempotent and backs up `~/.claude/settings.json` before
touching it. Your rules are copied to `~/.config/tmux-core-flow/policy.yaml` on
first run and never overwritten after — so `git pull` never fights your edits.

Requires `tmux`, `jq`, Python **3.10+** and PyYAML. `flow-sync` also needs
`restic`; without it everything else is unaffected.

## Layout

```
bin/
  claude-hook-notify   state machine: hook events -> tab colour + state files
  flow-approve         PreToolUse entrypoint (stdin JSON -> allow/ask)
  flow-session-index   durable window -> session index (run from cron)
  flow-restore         put sessions back after a reboot
  flow-sync            push/pull that state off the machine (restic)
lib/
  flow_policy.py       deny-list decision engine — pure, no I/O
  flow_sessions.py     index merge/render rules — pure, no I/O
  flow_sync.py         what travels, and how a restore lands — pure, no I/O
config/
  policy.example.yaml  starting rules; yours live in ~/.config/tmux-core-flow/
  sync.example.yaml    repository, paths and retention for flow-sync
  tmux-monitoring.conf fragment to source from your own ~/.tmux.conf
docs/
  state-format.md      the contract: colours, timestamps, session map, index
  sync.md              off-machine copies: setup, conflicts, path remapping
```

## Development

```bash
pip install -r requirements-dev.txt
pytest -q
```

`flow_policy.decide`, the hook's `evaluate`/`enabled_for`, and the index merge
rules are pure functions over their inputs, which is what the suite exercises —
including that a malformed event can never crash the agent, and that an empty
reading never empties the session index. One test drives a throwaway tmux server
to prove `--arm` types its command without executing it; it skips where tmux is
absent. CI runs the suite on Python 3.10–3.13.
