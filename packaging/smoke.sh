#!/bin/sh
# packaging/smoke.sh -- first-win acceptance for a fresh machine (T-866).
#
#   sh packaging/smoke.sh checkout   ./install.sh into an isolated prefix, then
#                                    atm join and atm ui answering /board.json
#   sh packaging/smoke.sh wheel      build the wheel, install it into a fresh
#                                    venv, atm + tickets join (packaged subset)
#   sh packaging/smoke.sh pipx       pipx install this checkout, atm join
#
# Everything lands under $SMOKE_WORK (default: a fresh mktemp dir): its own
# HOME, its own board (TICKETS_DIR), its own bin/. It never reads the caller's
# board, hooks or ~/.cache. POSIX sh + python3 only, so the same file runs in
# a clean Ubuntu container, on ubuntu-latest and on macos-latest.
set -eu

MODE=${1:-checkout}
HERE=$(cd "$(dirname "$0")/.." && pwd)
WORK=${SMOKE_WORK:-$(mktemp -d "${TMPDIR:-/tmp}/atman-smoke-XXXXXX")}
mkdir -p "$WORK/home" "$WORK/project"
export HOME="$WORK/home"
export TICKETS_DIR="$WORK/project/.tickets"
export TICKET_AGENT=""
unset TICKETS_STOP_HOOK TICKETS_LIVE_SHIM TICKETS_CACHE_DIR 2>/dev/null || true
PY=${PYTHON:-python3}

say() { printf '%s\n' "smoke[$MODE]: $*"; }
fail() { printf '%s\n' "smoke[$MODE]: FAIL: $*" >&2; exit 1; }

# A board wants a git repo around it (board-resolution reports the worktree).
git -C "$WORK/project" init -q -b main 2>/dev/null || git -C "$WORK/project" init -q

join_and_list() {
    # $1 = atm path, $2 = tickets path
    out=$(cd "$WORK/project" && "$1" join smoke-seat --roles backend 2>&1) \
        || fail "atm join exited non-zero: $out"
    printf '%s\n' "$out" | grep -q "joined as smoke-seat" || fail "atm join did not report the seat: $out"
    [ -d "$TICKETS_DIR" ] || fail "join created no board at $TICKETS_DIR"
    who=$(cd "$WORK/project" && "$2" who 2>&1) || fail "tickets who exited non-zero: $who"
    printf '%s\n' "$who" | grep -q "smoke-seat" || fail "tickets who does not list the seat: $who"
    say "atm join + tickets who: seat smoke-seat is on the board at $TICKETS_DIR"
}

ui_first_win() {
    # $1 = atm path. The first win: the local app answers /board.json.
    PORT=$("$PY" -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')
    (cd "$WORK/project" && "$1" ui --port "$PORT" --host 127.0.0.1 > "$WORK/ui.log" 2>&1) &
    UI_PID=$!
    trap 'kill $UI_PID 2>/dev/null || true' EXIT
    "$PY" - "$PORT" <<'PYEOF' || { cat "$WORK/ui.log" >&2; fail "atm ui never answered /board.json"; }
import json, sys, time, urllib.request
port = sys.argv[1]
deadline = time.time() + 20
while time.time() < deadline:
    try:
        with urllib.request.urlopen("http://127.0.0.1:%s/board.json" % port, timeout=1) as r:
            body = json.loads(r.read())
            assert r.status == 200 and "counts" in body, body
            print("smoke: atm ui answered /board.json on 127.0.0.1:%s (counts=%s)" % (port, body["counts"]))
            sys.exit(0)
    except Exception:
        time.sleep(0.25)
sys.exit(1)
PYEOF
    kill "$UI_PID" 2>/dev/null || true
    trap - EXIT
    say "FIRST WIN: install -> atm join -> atm ui health, all on 127.0.0.1:$PORT"
}

case "$MODE" in
  checkout)
    "$HERE/install.sh" --prefix "$WORK/bin" >/dev/null
    export PATH="$WORK/bin:$PATH"
    [ "$(readlink "$WORK/bin/atm")" = "$HERE/tickets.py" ] || fail "atm is not a symlink to this checkout"
    atm --help >/dev/null || fail "atm --help failed"
    tickets --help >/dev/null || fail "tickets --help failed"
    join_and_list "$WORK/bin/atm" "$WORK/bin/tickets"
    ui_first_win "$WORK/bin/atm"
    ;;
  wheel)
    "$PY" -m pip wheel "$HERE" --no-deps -q -w "$WORK/dist" || fail "pip wheel failed"
    WHEEL=$(ls "$WORK"/dist/ticket_board-*.whl | head -1)
    [ -n "$WHEEL" ] || fail "no ticket_board wheel built"
    "$PY" -m venv "$WORK/venv"
    "$WORK/venv/bin/pip" install -q --no-deps "$WHEEL" || fail "wheel install failed"
    "$WORK/venv/bin/atm" --help | grep -q join || fail "packaged atm --help lacks join"
    "$WORK/venv/bin/tickets" --help | grep -q join || fail "packaged tickets --help lacks join"
    join_and_list "$WORK/venv/bin/atm" "$WORK/venv/bin/tickets"
    say "packaged CLI (wheel): join + who pass; ui/--version/hooks/watch are checkout-only (E-016 subset)"
    ;;
  pipx)
    command -v pipx >/dev/null 2>&1 || fail "pipx is not installed"
    export PIPX_HOME="$WORK/pipx" PIPX_BIN_DIR="$WORK/pipxbin"
    pipx install "$HERE" >/dev/null 2>&1 || pipx install "$HERE" || fail "pipx install failed"
    "$WORK/pipxbin/atm" --help | grep -q join || fail "pipx atm --help lacks join"
    join_and_list "$WORK/pipxbin/atm" "$WORK/pipxbin/tickets"
    say "packaged CLI (pipx): join + who pass; ui/--version/hooks/watch are checkout-only (E-016 subset)"
    ;;
  *)
    fail "unknown mode '$MODE' (checkout | wheel | pipx)"
    ;;
esac
say "OK (work dir $WORK)"
