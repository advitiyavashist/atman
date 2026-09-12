#!/bin/sh
# Install `atm` on PATH (primary) and `tickets` as the same-file compatibility alias.
# Optionally install the Claude Code SessionStart hook.
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
ln -sf "$HERE/tickets.py" "$BIN/atm"
ln -sf "$HERE/tickets.py" "$BIN/tickets"
echo "linked $BIN/atm -> $HERE/tickets.py (primary)"
echo "linked $BIN/tickets -> $HERE/tickets.py (compatibility alias)"
case ":$PATH:" in *":$BIN:"*) ;; *) echo "add $BIN to your PATH";; esac

if [ "$1" = "--claude-hook" ]; then
  if [ -z "${TICKET_AGENT:-}" ]; then
    echo "--claude-hook needs TICKET_AGENT set to the identity this hook will own" >&2
    exit 2
  fi
  "$HERE/tickets.py" hooks claude --agent "$TICKET_AGENT"
fi
echo "now: cd <your project> && atm --help   # tickets is the same command"
