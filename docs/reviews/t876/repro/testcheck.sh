#!/bin/zsh
# Prove the suggested regression tests are real: they must FAIL on the
# candidate as-is and PASS once write_identity moves below the guards.
# Runs only after every measured suite is finished; the cand tree is patched
# in place and then restored to f3289b2.
SP=/private/tmp/claude-502/-Users-kavana-Downloads-atman--worktrees-atman-verify-t876-opus-0914/5b5d252c-25d6-4f12-aa89-09c3c0279e44/scratchpad/t876
while [ ! -f $SP/DONE-REPRO ]; do sleep 20; done
T=$SP/cand
F=$T/tests/test_t876_refused_join.py
{ head -55 $T/tests/test_identity_session_scope.py; sed -n '56,80p' $T/tests/test_identity_session_scope.py; } > /dev/null
# Build a standalone module: reuse the helpers by importing them.
cat > $F <<'PYEOF'
from test_identity_session_scope import run, board, seat_of  # noqa: F401
PYEOF
cat $SP/suggested_test.py >> $F
HOMEDIR=$SP/home-tc; TMP=$SP/tmp-tc
rm -rf $HOMEDIR $TMP; mkdir -p $HOMEDIR $TMP
cat > $HOMEDIR/.gitconfig <<'GC'
[user]
	name = t876 verifier
	email = t876@example.invalid
[init]
	defaultBranch = main
GC
runpy() {
  cd $T
  env -i PATH="$T/.venv/bin:/usr/bin:/bin:/usr/sbin:/sbin" HOME="$HOMEDIR" TMPDIR="$TMP" \
    LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8 TICKET_BOARD_CONTRACTS_REQUIRED=1 \
    "$T/.venv/bin/python" -m pytest -q -p no:cacheprovider tests/test_t876_refused_join.py
}
echo "===== unpatched candidate f3289b2 (expect FAIL) =====" > $SP/testcheck.log
runpy >> $SP/testcheck.log 2>&1
echo "exit=$?" >> $SP/testcheck.log

# apply the proposed fix: move write_identity below the guards in tickets.py
$T/.venv/bin/python - <<'PYEOF'
import re
p = "tickets.py"
s = open(p).read()
i = s.index("def cmd_join(a, board):")
j = s.index("    first_join = not _agent_rec(board, owner)", i)
body = s[i:j]
assert body.count("    write_identity(board, owner)\n") == 1, "unexpected join body"
body2 = body.replace("    write_identity(board, owner)\n", "", 1)
anchor = "        alias=(getattr(a, \"alias\", \"\") or \"\").strip())\n"
assert anchor in body2
body2 = body2.replace(anchor, anchor + "    write_identity(board, owner)\n", 1)
open(p, "w").write(s[:i] + body2 + s[j:])
print("patched tickets.py: write_identity moved below _guard_seat_identity")
PYEOF
cd $T && $T/.venv/bin/python - <<'PYEOF'
import subprocess, sys
print(subprocess.run([sys.executable, "-c", "import ast;ast.parse(open('tickets.py').read())"]).returncode)
PYEOF
echo "===== candidate + proposed fix (expect PASS) =====" >> $SP/testcheck.log
runpy >> $SP/testcheck.log 2>&1
echo "exit=$?" >> $SP/testcheck.log
# also re-run the identity/precedence modules on the patched tree: the move
# must not break the precedence the PR exists to establish.
cd $T
env -i PATH="$T/.venv/bin:/usr/bin:/bin:/usr/sbin:/sbin" HOME="$HOMEDIR" TMPDIR="$TMP" \
  LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8 TICKET_BOARD_CONTRACTS_REQUIRED=1 \
  "$T/.venv/bin/python" -m pytest -q -p no:cacheprovider \
  tests/test_identity_precedence.py tests/test_identity_session_scope.py \
  tests/test_t839_launch_identity.py tests/test_byoa.py \
  tests/test_t327_join_inbox_watermark.py >> $SP/testcheck.log 2>&1
echo "patched-identity-modules exit=$?" >> $SP/testcheck.log
cd $T && git checkout -q f3289b2 -- tickets.py && rm -f $F
git -C $T status --porcelain >> $SP/testcheck.log
touch $SP/DONE-TESTCHECK
