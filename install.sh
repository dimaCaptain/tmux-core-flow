#!/usr/bin/env bash
#
# install.sh — wire tmux-core-flow into the environment. Idempotent.
#
#   ./install.sh             apply
#   ./install.sh --dry-run   preview, change nothing
#   ./install.sh --windows   also copy launch/*.ps1 + *.ahk to C:\Users\user\tools
#
set -euo pipefail

DRY=0; WINDOWS=0
for a in "$@"; do
  case "$a" in
    --dry-run) DRY=1 ;;
    --windows) WINDOWS=1 ;;
  esac
done

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL_BIN="$HOME/.local/bin"
SETTINGS="$HOME/.claude/settings.json"
WIN_TOOLS="/mnt/c/Users/user/tools"

say() { printf '%s\n' "$*"; }
run() { if [ "$DRY" = 1 ]; then say "  [dry-run] $*"; else eval "$*"; fi; }

say "tmux-core-flow install (repo: $ROOT)$([ "$DRY" = 1 ] && echo ' [DRY-RUN]')"

# ---------------------------------------------------------------------------
# 1. PATH entrypoints
# ---------------------------------------------------------------------------
say "[1/3] PATH symlinks"
[ "$DRY" = 1 ] || mkdir -p "$LOCAL_BIN"
declare -A LINKS=(
  [flow-dashboard]="$ROOT/dashboard/dashboard.py"
  [flow-approve]="$ROOT/autoapprove/hook.py"
)
for name in "${!LINKS[@]}"; do
  tgt="${LINKS[$name]}"
  [ "$DRY" = 1 ] || chmod +x "$tgt"
  lp="$LOCAL_BIN/$name"
  if [ -L "$lp" ] && [ "$(readlink "$lp")" = "$tgt" ]; then
    say "  ok   $name"
  else
    run "ln -sfn '$tgt' '$lp'"; say "  link $name -> $tgt"
  fi
done

# ---------------------------------------------------------------------------
# 2. Register PreToolUse autoapprove hook in ~/.claude/settings.json
# ---------------------------------------------------------------------------
say "[2/3] PreToolUse hook in $SETTINGS"
if [ "$DRY" = 1 ]; then
  say "  [dry-run] would merge PreToolUse -> flow-approve (backup first)"
else
  HOOK_CMD="flow-approve" python3 - "$SETTINGS" <<'PY'
import json, os, sys, shutil
path = sys.argv[1]
cmd = os.environ["HOOK_CMD"]
os.makedirs(os.path.dirname(path), exist_ok=True)
data = {}
if os.path.exists(path):
    shutil.copy2(path, path + ".pre-flow.bak")
    with open(path) as f:
        try: data = json.load(f)
        except Exception: data = {}
hooks = data.setdefault("hooks", {})
pre = hooks.setdefault("PreToolUse", [])
# already present?
present = any(
    h.get("command") == cmd
    for block in pre if isinstance(block, dict)
    for h in block.get("hooks", []) if isinstance(h, dict)
)
if present:
    print("  ok   hook already registered")
else:
    pre.append({"matcher": "*", "hooks": [{"type": "command", "command": cmd}]})
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    print("  added PreToolUse -> " + cmd + " (backup: settings.json.pre-flow.bak)")
PY
fi

# ---------------------------------------------------------------------------
# 3. Windows launch scripts (opt-in)
# ---------------------------------------------------------------------------
say "[3/3] Windows launch scripts"
if [ "$WINDOWS" = 1 ]; then
  if [ -d "$WIN_TOOLS" ]; then
    run "cp '$ROOT/launch/launch-flow-dashboard.ps1' '$WIN_TOOLS/'"
    run "cp '$ROOT/launch/flow-dashboard.ahk' '$WIN_TOOLS/'"
    say "  copied ps1 + ahk to $WIN_TOOLS (run the .ahk with AutoHotkey v2)"
  else
    say "  SKIP: $WIN_TOOLS not found"
  fi
else
  say "  skipped (pass --windows to copy ps1/ahk to C:\\Users\\user\\tools)"
fi

say ""
say "done.$([ "$DRY" = 1 ] && echo ' (dry-run)')"
say "NOTE: autoapprove is active in any tmux window (\$TMUX). Dangerous calls still"
say "      prompt via always_ask. Set FLOW_AUTOAPPROVE=0 to opt a session out."
