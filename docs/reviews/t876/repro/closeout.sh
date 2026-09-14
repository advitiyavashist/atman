#!/bin/zsh
SP=/private/tmp/claude-502/-Users-kavana-Downloads-atman--worktrees-atman-verify-t876-opus-0914/5b5d252c-25d6-4f12-aa89-09c3c0279e44/scratchpad/t876
L=$SP/closeout.log
: > $L
runin() {  # runin <tree> <homedir> <tmpdir> <pytest args...>
  local T=$1 H=$2 M=$3; shift 3
  rm -rf $H $M; mkdir -p $H $M
  cat > $H/.gitconfig <<'GC'
[user]
	name = t876 verifier
	email = t876@example.invalid
[init]
	defaultBranch = main
GC
  cd $T
  env -i PATH="$T/.venv/bin:/usr/bin:/bin:/usr/sbin:/sbin" HOME="$H" TMPDIR="$M" \
    LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8 TICKET_BOARD_CONTRACTS_REQUIRED=1 \
    "$T/.venv/bin/python" -m pytest -q -p no:cacheprovider "$@"
}

echo "################ 1. t683 dedupe: is the candidate-only failure real or flake?" >> $L
echo "# session_adapters.py is byte-identical base vs cand; the test loads it by file path." >> $L
for tree in cand base; do
  for i in 1 2 3 4 5; do
    echo "--- $tree run $i ---" >> $L
    runin $SP/$tree $SP/home-co $SP/tmp-co \
      tests/test_t683_session_adapters.py::test_wake_delivery_is_deduped_by_message_id >> $L 2>&1
    echo "exit=$?" >> $L
  done
done

echo "################ 2. seed the build backend into BOTH venvs (identically)" >> $L
for tree in cand base; do
  echo "--- $tree ---" >> $L
  $SP/$tree/.venv/bin/python -m ensurepip -q >> $L 2>&1
  $SP/$tree/.venv/bin/python -m pip install -q setuptools wheel build >> $L 2>&1
  echo "pip-seed exit=$?" >> $L
  $SP/$tree/.venv/bin/python -m pip list 2>/dev/null | grep -iE "^(setuptools|wheel|build) " >> $L
done

echo "################ 3. the PR's own t839_boundary_adapters, now that the backend exists" >> $L
runin $SP/cand $SP/home-co $SP/tmp-co tests/test_t839_boundary_adapters.py >> $L 2>&1
echo "cand t839_boundary_adapters exit=$?" >> $L
echo "# (base has no such file: it is added by the PR)" >> $L

echo "################ 4. t707 wheel/console-script on BOTH trees with the backend seeded" >> $L
for tree in cand base; do
  echo "--- $tree ---" >> $L
  runin $SP/$tree $SP/home-co $SP/tmp-co tests/test_t707_docker_packaging.py >> $L 2>&1
  echo "$tree t707 exit=$?" >> $L
done
touch $SP/DONE-CLOSEOUT
