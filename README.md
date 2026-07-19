# tmux-core-flow

> Deterministic monitoring and permission control for teams of
> [Claude Code](https://docs.anthropic.com/en/docs/claude-code) agents running in tmux.

[![CI](https://github.com/dimaCaptain/tmux-core-flow/actions/workflows/ci.yml/badge.svg)](https://github.com/dimaCaptain/tmux-core-flow/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)

Running several Claude Code agents at once, you need two things: **to see** what
each one is doing, and **not to babysit** a permission prompt on every safe tool
call. tmux-core-flow does exactly that — and nothing else:

1. **Compact dashboard** — a pinned, always-on-top widget showing each tmux
   agent's state (🟡 working / 🔴 idle-waiting / ⚪ inactive), current model and
   elapsed time.
2. **Autoapprove** — a deterministic Claude Code **PreToolUse hook** that
   auto-approves tool calls except those matching a `always_ask` deny-list.

The design is deliberately boring: no background daemon, no Telegram bot, no
terminal screen-scraping, no simulated keypresses. State is read straight from
each agent's transcript, and the approve/ask decision is a pure function of the
structured tool call — so it's testable and race-free.

## Architecture

```
core/                shared, dependency-light
  tmux.py            list windows / sessions / capture
  transcript.py      agent state from Claude JSONL  (red/yellow/None)
  model.py           resolve + shorten model name
  agents.py          AgentState collection across sessions
  policy.py          deny-list decision engine
autoapprove/hook.py  PreToolUse entrypoint  (stdin JSON -> allow/ask)
dashboard/dashboard.py  compact TUI renderer over core.agents
config/policy.yaml   always_ask rules (single source of truth)
launch/              Windows hotkey: flow-dashboard.ahk + launch ps1
```

The dashboard reads tmux + each agent's `~/.claude/projects/.../*.jsonl`
transcript directly (via `~/.tmux/claude-map/` markers) — it does **not** depend
on any background daemon.

## Autoapprove: how it decides

The hook receives the exact `tool_name` + `tool_input` *before* any prompt:

- default → `permissionDecision: "allow"` (runs silently)
- matches an `always_ask` rule (`rm`, `sudo`, `git push`, …) → `"ask"` (prompts you)

**Activation:** auto-approval is active for **any agent running inside tmux**
(detected via `$TMUX`), so it works in every tmux window without per-launcher
wiring. Overrides:

| Env | Effect |
|---|---|
| _(inside tmux)_ | on |
| _(outside tmux)_ | off (transparent) |
| `FLOW_AUTOAPPROVE=1` | force on (even outside tmux) |
| `FLOW_AUTOAPPROVE=0` | force off (escape hatch for one session) |

This includes your own interactive session if it runs in tmux — dangerous calls
still prompt via `always_ask`. Set `FLOW_AUTOAPPROVE=0` to opt a session out.

## Requirements

- Python **3.10+** (uses `X | None` type syntax)
- `tmux` (dashboard only)
- [PyYAML](https://pypi.org/project/PyYAML/) — `pip install -r requirements.txt`
- Claude Code, for the transcripts and the PreToolUse hook

The Windows hotkey launcher (`launch/`) is optional and only needed if you run
Claude Code under WSL and want the dashboard pinned as a native window.

## Install

```bash
pip install -r requirements.txt

./install.sh            # symlink flow-dashboard/flow-approve, register the hook
./install.sh --windows  # also copy launch/*.ps1 + *.ahk to C:\Users\user\tools
./install.sh --dry-run  # preview, change nothing
```

`install.sh` is idempotent and backs up `~/.claude/settings.json` before adding
the hook. Auto-approval then works in every tmux window automatically (see
Activation above) — no agent-launcher changes needed.

## Development

```bash
pip install -r requirements-dev.txt
pytest -q
```

The `core/` package has no I/O in its decision paths — `policy.decide`,
`transcript.state_from_lines` and `model.*` are pure functions over their
inputs, which is what the test suite exercises. CI runs it on Python 3.10–3.13.
