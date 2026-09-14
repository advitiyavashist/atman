#!/bin/zsh
# T-876 reproducer: a REFUSED `tickets join` still stamps this session's seat.
# usage: refused_join_identity.sh <tree-with-.venv>   (cand => leak, base => clean)
# Disposable board only; never point this at a live .tickets.
TREE=${1:?tree}
W=$(mktemp -d)
trap "rm -rf $W" EXIT
mkdir -p $W/home $W/tmp $W/proj
PY=$TREE/.venv/bin/python
run() { env -i PATH="$TREE/.venv/bin:/usr/bin:/bin" HOME=$W/home TMPDIR=$W/tmp \
        LANG=en_US.UTF-8 "$@"; }
cd $W/proj && git init -q . && git commit -q --allow-empty -m init
run TICKET_SESSION_ID=sessA TICKET_AGENT=alpha $PY $TREE/tickets.py init >/dev/null 2>&1
run TICKET_SESSION_ID=sessA TICKET_AGENT=alpha $PY $TREE/tickets.py \
    join alpha --tool claude --roles eng >/dev/null 2>&1
run TICKET_SESSION_ID=sessC TICKET_AGENT=carol $PY $TREE/tickets.py \
    join carol --tool cursor --roles eng >/dev/null 2>&1
run TICKET_SESSION_ID=sessC TICKET_AGENT=carol $PY $TREE/tickets.py \
    msg "carol->alpha: private handover details" --to alpha >/dev/null 2>&1

echo "== session B (TICKET_AGENT=bravo) tries to join as alpha under a 2nd harness =="
run TICKET_SESSION_ID=sessB TICKET_AGENT=bravo $PY $TREE/tickets.py \
    join alpha --tool codex --roles eng 2>&1 | tail -1

echo "== after that REFUSAL, session B's bare inbox =="
OUT=$(run TICKET_SESSION_ID=sessB TICKET_AGENT=bravo $PY $TREE/tickets.py inbox 2>&1)
echo "$OUT" | head -4
echo "== after that REFUSAL, session B's bare msg sender =="
SENT=$(run TICKET_SESSION_ID=sessB TICKET_AGENT=bravo $PY $TREE/tickets.py \
       msg "posted by the refused session" 2>&1 | tail -1)
echo "$SENT"

fail=0
echo "$OUT"  | grep -q "for alpha"                  && { echo "LEAK: B reads alpha's inbox"; fail=1; }
echo "$OUT"  | grep -q "private handover details"   && { echo "LEAK: B consumed alpha's private DM"; fail=1; }
echo "$SENT" | grep -q " alpha: "                   && { echo "LEAK: B posts AS alpha"; fail=1; }
[ $fail -eq 0 ] && echo "CLEAN: refused join left session B as itself"
exit $fail
