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
BIN="${PREFIX:-${HOME}/.local/bin}"
FORCE=0
HOOK=0
while [ $# -gt 0 ]; do
  case "$1" in
    --prefix)
      BIN="$2"
      shift 2
      ;;
    --prefix=*)
      BIN="${1#--prefix=}"
      shift
      ;;
    --force)
      FORCE=1
      shift
      ;;
    --claude-hook)
      HOOK=1
      shift
      ;;
    *)
      echo "install.sh: unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

# Refuse to replace a foreign tickets/atm (T-882) unless it already points here.
for TARGET in "$BIN/tickets" "$BIN/atm"; do
  if [ -e "$TARGET" ] || [ -L "$TARGET" ]; then
    LINKS_HERE=0
    if [ -L "$TARGET" ] && [ "$(readlink "$TARGET")" = "$HERE/tickets.py" ]; then
      LINKS_HERE=1
    fi
    if [ "$LINKS_HERE" -eq 0 ] && [ "$FORCE" -ne 1 ]; then
      if [ -f "$TARGET" ] && grep -q "tickets-releases" "$TARGET" 2>/dev/null \
         && grep -q "execv" "$TARGET" 2>/dev/null; then
        echo "install.sh: refusing to overwrite $TARGET -- it looks like a pinned" >&2
        echo "live-release launcher (see ./install.sh --live-release). Installing" >&2
        echo "here would replace the machine's pinned release." >&2
      else
        echo "install.sh: refusing to overwrite existing $TARGET (it is not a" >&2
        echo "symlink to this checkout's tickets.py -- something else owns it)." >&2
      fi
      echo "Use --prefix DIR (or PREFIX=DIR) for an isolated install, or --force" >&2
      echo "to replace it anyway." >&2
      exit 1
    fi
  fi
done

mkdir -p "$BIN"
chmod +x "$HERE/tickets.py"
ln -sf "$HERE/tickets.py" "$BIN/atm"
ln -sf "$HERE/tickets.py" "$BIN/tickets"
echo "linked $BIN/atm -> $HERE/tickets.py (primary)"
echo "linked $BIN/tickets -> $HERE/tickets.py (compatibility alias)"
case ":$PATH:" in *":$BIN:"*) ;; *) echo "add $BIN to your PATH";; esac

if [ "$HOOK" -eq 1 ]; then
  if [ -z "${TICKET_AGENT:-}" ]; then
    echo "--claude-hook needs TICKET_AGENT set to the identity this hook will own" >&2
    exit 2
  fi
  "$HERE/tickets.py" hooks claude --agent "$TICKET_AGENT"
fi
echo "now: cd <your project> && atm --help   # tickets is the same command"
