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
   window, so a reboot does not cost you the context of a dozen agents.

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

A window already running Claude is never touched.

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

Requires `tmux`, `jq`, Python **3.10+** and PyYAML.

## Layout

```
bin/
  claude-hook-notify   state machine: hook events -> tab colour + state files
  flow-approve         PreToolUse entrypoint (stdin JSON -> allow/ask)
  flow-session-index   durable window -> session index (run from cron)
  flow-restore         put sessions back after a reboot
lib/
  flow_policy.py       deny-list decision engine — pure, no I/O
  flow_sessions.py     index merge/render rules — pure, no I/O
config/
  policy.example.yaml  starting rules; yours live in ~/.config/tmux-core-flow/
  tmux-monitoring.conf fragment to source from your own ~/.tmux.conf
docs/
  state-format.md      the contract: colours, timestamps, session map, index
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
