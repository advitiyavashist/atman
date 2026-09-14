#!/bin/zsh
# Full clean-env suite for one tree. usage: run_suite.sh <cand|base>
SP=/private/tmp/claude-502/-Users-kavana-Downloads-atman--worktrees-atman-verify-t876-opus-0914/5b5d252c-25d6-4f12-aa89-09c3c0279e44/scratchpad/t876
T=$1
TREE=$SP/$T
HOMEDIR=$SP/home-$T
TMP=$SP/tmp-$T
rm -rf $HOMEDIR $TMP
mkdir -p $HOMEDIR $TMP
# minimal git identity inside the isolated HOME (env -i drops the real one)
cat > $HOMEDIR/.gitconfig <<'GC'
[user]
	name = t876 verifier
	email = t876@example.invalid
[init]
	defaultBranch = main
GC
cd $TREE
env -i \
  PATH="$TREE/.venv/bin:/usr/bin:/bin:/usr/sbin:/sbin" \
  HOME="$HOMEDIR" \
  TMPDIR="$TMP" \
  LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8 \
  TICKET_BOARD_CONTRACTS_REQUIRED=1 \
  "$TREE/.venv/bin/python" -m pytest -q -p no:cacheprovider \
    > $SP/suite-$T.log 2>&1
echo "exit=$?" >> $SP/suite-$T.log
