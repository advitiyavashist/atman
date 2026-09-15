#!/bin/zsh
# Corrected replay of the command sequence used for the published take.
# The original used a temporary absolute path and lost its run directory. This
# version takes a prepared disposable run as an argument. It does not claim to
# recreate the original provider outputs byte for byte.
set -euo pipefail
R="${1:?usage: record-take.sh <RUN>}"
for prompt in ceo-plan.prompt codex-worker.prompt ceo-accept.prompt cursor-worker.prompt; do
  [ -f "$R/$prompt" ] || { echo "missing $R/$prompt; run prepare-replay.py first" >&2; exit 2; }
done
export TICKETS_DIR="$R/repo/.tickets"
export DEMO_RUN="$R"
export PATH="$R/bin:$PATH"
cd $R/repo/.worktrees/ceo
say() { printf '\n\033[1;36m  %s\033[0m\n\n' "$1"; sleep 2; }
run() { printf '\033[1;32m$\033[0m %s\n' "$1"; eval "$1"; }
clear
printf '\033[1;37m  Atman — your agents, one board, one handoff\033[0m\n'
sleep 3
say "Give a coordinator agent an objective. It plans the work on the board."
run 'codex exec "$(cat $R/ceo-plan.prompt)" --skip-git-repo-check --dangerously-bypass-approvals-and-sandbox 2>&1 | tail -5'
run 'atm list'
say "Two tickets, one real dependency: B waits for A."
sleep 2
say "A worker agent claims A in its own worktree, tests it, opens a PR."
cd $R/repo/.worktrees/codex-worker
run 'codex exec "$(cat $R/codex-worker.prompt)" --skip-git-repo-check --dangerously-bypass-approvals-and-sandbox 2>&1 | tail -6'
cd $R/repo/.worktrees/ceo
run 'atm list'
say "The coordinator reviews the work itself — then accepts it against the exact commit."
run 'codex exec "$(cat $R/ceo-accept.prompt)" --skip-git-repo-check --dangerously-bypass-approvals-and-sandbox 2>&1 | tail -5'
run 'atm show T-001 | grep -i -m1 accept | cut -c1-140'
say "A is accepted and done. B unblocks, carrying A's accepted commit in its handoff."
run 'atm list'
say "A Cursor agent — different vendor — picks up B. Nobody retypes what A did."
cd $R/repo/.worktrees/cursor-worker
run 'agent -p --output-format text --force "$(cat $R/cursor-worker.prompt)" 2>&1 | tail -6'
say "B built on A's accepted commit. Real code, real tests."
run 'python3 -m unittest discover -s tests 2>&1 | tail -3'
run 'python3 -m saleskit.cli report data/sales.csv'
printf '\n\033[1;37m  github.com/advitiyavashist/atman\033[0m\n\n'
sleep 4
