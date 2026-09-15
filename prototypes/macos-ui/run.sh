#!/bin/sh
# Throwaway native shell around the current local app. Never the live board.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname "$0")/../.." && pwd)
OUT=${OUT:-"$ROOT/prototypes/macos-ui/atman-shell"}
URL=${ATMAN_UI_URL:-http://127.0.0.1:8765}
swiftc -O -o "$OUT" "$ROOT/prototypes/macos-ui/AtmanShell.swift" \
  -framework Cocoa -framework WebKit
echo "binary $OUT ($(wc -c < "$OUT") bytes)"
echo "loads $URL — start the existing UI with: atm ui --host 127.0.0.1 --port 8765"
if [ "${ATMAN_SHELL_LAUNCH:-}" = "1" ]; then
  ATMAN_UI_URL="$URL" "$OUT"
fi
