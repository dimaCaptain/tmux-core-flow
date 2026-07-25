#!/usr/bin/env bash
#
# install.sh — wire tmux-core-flow into your environment. Idempotent.
#
#   ./install.sh             apply
#   ./install.sh --dry-run   preview, change nothing
#
# Creates:
#   ~/.local/bin/{flow-approve,claude-hook-notify,flow-session-index,flow-restore}
#   ~/.config/tmux-core-flow/policy.yaml            -> copy of the example, yours to edit
#   ~/.claude/settings.json                          -> hooks registered
#
# It never edits your ~/.tmux.conf or your crontab: both lines are printed at
# the end for you to add, so your own config stays yours.
#
set -euo pipefail

DRY=0
[ "${1:-}" = "--dry-run" ] && DRY=1

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL_BIN="$HOME/.local/bin"
CFG_DIR="$HOME/.config/tmux-core-flow"
SETTINGS="$HOME/.claude/settings.json"

say() { printf '%s\n' "$*"; }
run() { if [ "$DRY" = 1 ]; then say "  [dry-run] $*"; else eval "$*"; fi; }

say "tmux-core-flow install (repo: $ROOT)$([ "$DRY" = 1 ] && echo ' [DRY-RUN]')"

# ---------------------------------------------------------------------------
say "[1/4] dependencies"
missing=""
for c in tmux jq python3; do command -v "$c" >/dev/null || missing="$missing $c"; done
python3 -c 'import yaml' 2>/dev/null || missing="$missing python3-yaml(PyYAML)"
if [ -n "$missing" ]; then
  say "  MISSING:$missing"
  say "  install them first — jq is needed by claude-hook-notify, PyYAML by flow-approve"
else
  say "  ok   tmux, jq, python3, PyYAML"
fi

# ---------------------------------------------------------------------------
say "[2/4] PATH symlinks in $LOCAL_BIN"
[ "$DRY" = 1 ] || mkdir -p "$LOCAL_BIN"
for name in flow-approve claude-hook-notify flow-session-index flow-restore; do
  tgt="$ROOT/bin/$name"
  lp="$LOCAL_BIN/$name"
  [ "$DRY" = 1 ] || chmod +x "$tgt"
  if [ -L "$lp" ] && [ "$(readlink "$lp")" = "$tgt" ]; then
    say "  ok   $name"
  else
    [ -e "$lp" ] && [ ! -L "$lp" ] && run "mv '$lp' '$lp.pre-flow.bak'"
    run "ln -sfn '$tgt' '$lp'"
    say "  link $name"
  fi
done

# ---------------------------------------------------------------------------
# Your rules live outside the repo, so this checkout stays publishable and
# `git pull` never fights your edits.
say "[3/4] policy in $CFG_DIR"
if [ -f "$CFG_DIR/policy.yaml" ]; then
  say "  keep policy.yaml (yours — the example is never copied over it)"
else
  run "mkdir -p '$CFG_DIR'"
  run "cp '$ROOT/config/policy.example.yaml' '$CFG_DIR/policy.yaml'"
  say "  created policy.yaml from the example — edit it to taste"
fi

# ---------------------------------------------------------------------------
say "[4/4] hooks in $SETTINGS"
if [ "$DRY" = 1 ]; then
  say "  [dry-run] would register PreToolUse -> flow-approve"
  say "  [dry-run] would register Notification/UserPromptSubmit/Stop/PostToolUse -> claude-hook-notify"
else
  python3 - "$SETTINGS" <<'PY'
import json, os, shutil, sys
path = sys.argv[1]
os.makedirs(os.path.dirname(path), exist_ok=True)
data = {}
if os.path.exists(path):
    with open(path) as f:
        try: data = json.load(f)
        except Exception: data = {}
hooks = data.setdefault("hooks", {})
wanted = {
    "PreToolUse": "flow-approve",
    "Notification": "claude-hook-notify",
    "UserPromptSubmit": "claude-hook-notify",
    "Stop": "claude-hook-notify",
    "PostToolUse": "claude-hook-notify",
}
changed = []
for event, cmd in wanted.items():
    blocks = hooks.setdefault(event, [])
    present = any(
        h.get("command") == cmd
        for b in blocks if isinstance(b, dict)
        for h in b.get("hooks", []) if isinstance(h, dict)
    )
    if present:
        print(f"  ok   {event} -> {cmd}")
    else:
        blocks.append({"matcher": "*", "hooks": [{"type": "command", "command": cmd}]})
        changed.append(f"{event} -> {cmd}")
if changed:
    if os.path.exists(path):
        shutil.copy2(path, path + ".pre-flow.bak")
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    for c in changed:
        print(f"  added {c}")
    print("  (backup: settings.json.pre-flow.bak)")
PY
fi

say ""
say "done.$([ "$DRY" = 1 ] && echo ' (dry-run)')"
say ""
say "Two manual steps, so nothing of yours is edited behind your back."
say ""
say "1. Add to ~/.tmux.conf, then \`tmux source-file ~/.tmux.conf\`:"
say ""
say "       source-file $ROOT/config/tmux-monitoring.conf"
say ""
if crontab -l 2>/dev/null | grep -q 'flow-session-index'; then
  say "2. Index refresh: already in your crontab. ok"
else
  say "2. Keep the session index fresh — \`crontab -e\` and add:"
  say ""
  say "       */15 * * * * $LOCAL_BIN/flow-session-index --update"
  say ""
  say "   Without it the index still works, but only reflects the last refresh."
fi
say ""
say "Autoapprove is then active in every tmux window; dangerous calls still"
say "prompt via always_ask. Set FLOW_AUTOAPPROVE=0 to opt a session out."
say "After a reboot, \`flow-restore\` shows what can be brought back."
