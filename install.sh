#!/bin/sh
# Install `tickets` on PATH and (optionally) the Claude Code SessionStart hook.
set -e
# Production delivery uses immutable snapshots; the legacy mode below is for development.
if [ "${1:-}" = "--live-release" ]; then
  shift
  exec python3 "$(dirname "$0")/scripts/install_live.py" "$@"
fi
echo "Development install: use --live-release --ref <sha> for the shared live CLI."
HERE=$(cd "$(dirname "$0")" && pwd)
BIN="${HOME}/.local/bin"
mkdir -p "$BIN"
chmod +x "$HERE/tickets.py"
ln -sf "$HERE/tickets.py" "$BIN/tickets"
echo "linked $BIN/tickets -> $HERE/tickets.py"
case ":$PATH:" in *":$BIN:"*) ;; *) echo "add $BIN to your PATH";; esac

if [ "$1" = "--claude-hook" ]; then
  SETTINGS="${HOME}/.claude/settings.json"
  python3 - "$SETTINGS" "$HERE/tickets.py" <<'PY'
import json, sys, os
path, script = sys.argv[1], sys.argv[2]
s = json.load(open(path)) if os.path.exists(path) else {}
hooks = s.setdefault("hooks", {}).setdefault("SessionStart", [])
hooks[:] = [h for h in hooks if "tickets" not in json.dumps(h)]
hooks.append({"matcher": "startup|resume|clear|compact",
              "hooks": [{"type": "command", "command": "%s board" % script, "timeout": 10}]})
allow = s.setdefault("permissions", {}).setdefault("allow", [])
for p in ("Bash(tickets:*)", "Bash(%s:*)" % script):
    if p not in allow:
        allow.append(p)
json.dump(s, open(path, "w"), indent=2)
print("Claude Code SessionStart hook installed in", path)
PY
fi
echo "now: cd <your project> && tickets init"
