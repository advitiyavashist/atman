#!/bin/zsh
SP=/private/tmp/claude-502/-Users-kavana-Downloads-atman--worktrees-atman-verify-t876-opus-0914/5b5d252c-25d6-4f12-aa89-09c3c0279e44/scratchpad/t876
L=$SP/final.log
: > $L
echo "################ 5. clean-venv wheel smoke (build backend now present)" >> $L
rm -rf $SP/wheel $SP/reprotmp; mkdir -p $SP/wheel $SP/reprotmp
cd $SP/cand
$SP/cand/.venv/bin/python -m build --wheel --outdir $SP/wheel >> $L 2>&1
echo "build exit=$?" >> $L
ls -la $SP/wheel >> $L 2>&1
uv venv -q -p 3.11 $SP/wheel/venv >> $L 2>&1
WHL=$(ls $SP/wheel/*.whl 2>/dev/null | head -1)
echo "WHEEL=$WHL" >> $L
$SP/wheel/venv/bin/python -m ensurepip >> $L 2>&1
$SP/wheel/venv/bin/python -m pip install -q "$WHL" >> $L 2>&1
echo "install exit=$?" >> $L
$SP/wheel/venv/bin/tickets --version >> $L 2>&1
echo "tickets --version exit=$?" >> $L
# provenance: the console script must resolve into site-packages, not the source tree
$SP/wheel/venv/bin/python -c "import ticket_board,sys;print('ticket_board from',ticket_board.__file__)" >> $L 2>&1

echo "################ 6. T-844 root/wheel/spawn probe against the installed wheel" >> $L
export T876_CAND=$SP/cand T876_BASE=$SP/base T876_PRIOR=$SP/prior
export T876_SCRATCH=$SP/reprotmp T876_WHEEL=$SP/wheel/venv/bin/tickets
$SP/cand/.venv/bin/python $SP/repro/t844_probe_t876.py >> $L 2>&1
echo "t844_probe exit=$?" >> $L

echo "################ 7. negative control: PR tests vs main production code" >> $L
# Revert ONLY the production modules to main, keeping the PR's new session_boundary
# module so the test imports still resolve. The PR's behavioural tests must fail.
rm -rf $SP/neg2; cp -R $SP/cand $SP/neg2
cd $SP/neg2
git checkout -q 9b2057d -- tickets.py src/ticket_board/cli.py src/ticket_board/agent_checkin.py
echo "reverted to main:" >> $L; git status --porcelain >> $L
H=$SP/home-neg2 M=$SP/tmp-neg2; rm -rf $H $M; mkdir -p $H $M
cat > $H/.gitconfig <<'GC'
[user]
	name = t876 verifier
	email = t876@example.invalid
[init]
	defaultBranch = main
GC
env -i PATH="$SP/neg2/.venv/bin:/usr/bin:/bin:/usr/sbin:/sbin" HOME="$H" TMPDIR="$M" \
  LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8 TICKET_BOARD_CONTRACTS_REQUIRED=1 \
  "$SP/neg2/.venv/bin/python" -m pytest -q -p no:cacheprovider \
  tests/test_identity_precedence.py tests/test_identity_session_scope.py \
  tests/test_t839_launch_identity.py >> $L 2>&1
echo "neg-control exit=$? (NONZERO is the expected, correct result)" >> $L
touch $SP/DONE-FINAL
