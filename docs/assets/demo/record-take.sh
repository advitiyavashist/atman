#!/bin/zsh
# Record the Atman README demo take against a prepared run directory.
#
#   python3 docs/demo/rehearsal.py setup --mode real --origin <throwaway-repo>   # prints RUN=...
#   python3 docs/assets/demo/split_prompts.py <RUN>
#   asciinema rec -c "zsh docs/assets/demo/record-take.sh <RUN>" demo.cast
#
# Every '$ ' line printed below is the exact command that then executes (run()
# prints its argument and evals it). Full provider output for each agent turn is
# kept in <RUN>/evidence/<beat>.log; the terminal shows only the tail.
set -u
R="${1:?usage: record-take.sh <RUN>}"
[ -f "$R/ceo-plan.prompt" ] || { echo "run split_prompts.py <RUN> first" >&2; exit 2; }
export TICKETS_DIR="$R/repo/.tickets" DEMO_RUN="$R" PATH="$R/bin:$PATH"
mkdir -p "$R/evidence"
say() { printf '\n\033[1;36m  %s\033[0m\n\n' "$1"; sleep 2; }
run() { printf '\033[1;32m$\033[0m %s\n' "$1"; eval "$1"; }
cd "$R/repo/.worktrees/ceo"
clear
printf '\033[1;37m  Atman — your agents, one board, one handoff\033[0m\n'
sleep 3
say "Give a coordinator agent an objective. It plans the work on the board."
run 'codex exec "$(cat $R/ceo-plan.prompt)" --skip-git-repo-check --dangerously-bypass-approvals-and-sandbox > $R/evidence/1-plan.log 2>&1; tail -5 $R/evidence/1-plan.log'
run 'atm list'
say "Two tickets, one real dependency: B waits for A."
sleep 2
say "A worker agent claims A in its own worktree, tests it, opens a PR."
cd "$R/repo/.worktrees/codex-worker"
run 'codex exec "$(cat $R/codex-worker.prompt)" --skip-git-repo-check --dangerously-bypass-approvals-and-sandbox > $R/evidence/2-worker.log 2>&1; tail -6 $R/evidence/2-worker.log'
cd "$R/repo/.worktrees/ceo"
run 'atm list'
say "The coordinator reviews the work itself — then accepts it against the exact commit."
run 'codex exec "$(cat $R/ceo-accept.prompt)" --skip-git-repo-check --dangerously-bypass-approvals-and-sandbox > $R/evidence/3-accept.log 2>&1; tail -5 $R/evidence/3-accept.log'
run 'atm show T-001 | grep -i -m1 accept | cut -c1-140'
say "A is accepted and done. B unblocks, carrying A's accepted commit in its handoff."
run 'atm list'
say "A Cursor agent — different vendor — picks up B. Nobody retypes what A did."
cd "$R/repo/.worktrees/cursor-worker"
run 'agent -p --output-format text --force "$(cat $R/cursor-worker.prompt)" > $R/evidence/4-cursor.log 2>&1; tail -6 $R/evidence/4-cursor.log'
say "B built on A's accepted commit. Real code, real tests."
run 'python3 -m unittest discover -s tests 2>&1 | tail -3'
run 'python3 -m saleskit.cli report data/sales.csv'
run 'git push -q origin HEAD && git merge-base --is-ancestor $(atm show T-001 | grep -oE "accept [0-9a-f]{40}" | head -1 | cut -d" " -f2) HEAD && echo "A accepted commit is an ancestor of B: yes"'
printf '\n\033[1;37m  github.com/advitiyavashist/atman\033[0m\n\n'
sleep 4
