# tmux-core-flow

Minimal successor to `agent-control` — only the two pieces actually used:

1. **Compact dashboard** — a pinned, always-on-top widget showing each tmux
   agent's state (🟡 working / 🔴 idle-waiting / ⚪ inactive), model and
   elapsed time. Launched by `Ctrl+\`.
2. **Autoapprove** — a deterministic Claude Code **PreToolUse hook** that
   auto-approves agent tool calls except those matching `config/policy.yaml`.

No Telegram bot, no screen-scraping, no key injection.

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

**Scoping safeguard:** auto-approval only happens when `FLOW_AUTOAPPROVE=1` is in
the environment (set by the agent launcher in agent panes). In your own
interactive session the variable is absent and the hook stays transparent.

## Install

```bash
./install.sh            # symlinks flow-dashboard/flow-approve, registers the hook
./install.sh --windows  # also copy launch/*.ps1 + *.ahk to C:\Users\user\tools
./install.sh --dry-run  # preview
```

Then, in the agent launcher, export `FLOW_AUTOAPPROVE=1` into agent panes so the
hook activates for them (and only them).

## Test

```bash
python3 -m pytest tests/ -q
```
