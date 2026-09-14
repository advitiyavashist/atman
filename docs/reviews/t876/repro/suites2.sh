#!/bin/zsh
SP=/private/tmp/claude-502/-Users-kavana-Downloads-atman--worktrees-atman-verify-t876-opus-0914/5b5d252c-25d6-4f12-aa89-09c3c0279e44/scratchpad/t876
mkgit() { mkdir -p $1; cat > $1/.gitconfig <<'GC'
[user]
	name = t876 verifier
	email = t876@example.invalid
[init]
	defaultBranch = main
GC
}
run_tree() {  # run_tree <tree> <outlog>
  local T=$SP/$1 H=$SP/home2-$1 M=$SP/tmp2-$1 OUT=$2
  rm -rf $H $M; mkgit $H; mkdir -p $M
  cd $T
  env -i PATH="$T/.venv/bin:/usr/bin:/bin:/usr/sbin:/sbin" HOME="$H" TMPDIR="$M" \
    LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8 TICKET_BOARD_CONTRACTS_REQUIRED=1 \
    "$T/.venv/bin/python" -m pytest -q -p no:cacheprovider > $OUT 2>&1
  echo "exit=$?" >> $OUT
}
run_tree cand $SP/suite2-cand.log
touch $SP/DONE2-cand
run_tree base $SP/suite2-base.log
touch $SP/DONE2-base
touch $SP/DONE2
