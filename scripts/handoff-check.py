#!/usr/bin/env python3
"""Verify a board's handoff path is intact BEFORE you clear, compact, or hand over.

Run from a board directory (the one containing .tickets/):

    python3 scripts/handoff-check.py          # exit 0 = safe to clear
    python3 scripts/handoff-check.py --fix-hints

Why this exists, concretely. Two failures happened on one board in one day, and
both were invisible until someone tripped over them:

  1. MASTER.md sat as install-time TEMPLATE text for a whole session. Its
     Mission read "(what we are building, one paragraph)" and its workforce
     table listed "example", while only its decision log had real content. Every
     agent is told to read that file first, so each one read a stub. Nobody
     noticed because the file EXISTED and was non-empty.

  2. `.tickets/` was gitignored wholesale, so MASTER.md was untracked. Writing
     over it destroyed twelve decision-log entries with no recovery path. They
     survived only because one session happened to have read them minutes
     earlier.

Neither is caught by tests, a linter, or code review: they are properties of the
board's documentation, not of any program. Hence a checker.

Exit codes: 0 all clear, 1 one or more FAILs, 2 could not inspect the board.
"""

import argparse
import json
import os
import subprocess
import sys

# Exact strings from tickets.py MASTER_TEMPLATE. If any survives, that section
# was never filled in. Keep in sync with MASTER_TEMPLATE.
TEMPLATE_MARKERS = [
    "(what we are building, one paragraph)",
    "(goals per sprint; `tickets sprint list` has the live numbers)",
    "(append with `tickets master log \"...\"`)",
    "| example | Claude Code | backend | .worktrees/example |",
]

# Docs that must survive a clear. These are hand-written memory, not runtime
# state, so they must be present AND tracked AND committed.
REQUIRED_DOCS = [
    ".tickets/MASTER.md",
    ".tickets/briefs/_shared.md",
]
# Present-and-tracked if they exist, but not mandatory for every board.
OPTIONAL_DOCS = [
    ".tickets/AGENT-HANDLES.md",
    ".tickets/AUTOMATION-BUDGET.md",
    "HANDOFF.md",
]

fails: list[tuple[str, str]] = []
warns: list[tuple[str, str]] = []


def fail(check: str, msg: str) -> None:
    fails.append((check, msg))


def warn(check: str, msg: str) -> None:
    warns.append((check, msg))


def git(*args: str) -> tuple[int, str]:
    p = subprocess.run(["git", *args], capture_output=True, text=True)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def check_docs_exist() -> None:
    for rel in REQUIRED_DOCS:
        if not os.path.exists(rel):
            fail("docs-exist", f"{rel} is missing. A fresh session has nothing to read.")


def check_no_template_text() -> None:
    """The check that would have caught a whole session of agents reading a stub."""
    p = ".tickets/MASTER.md"
    if not os.path.exists(p):
        return
    body = open(p, encoding="utf-8").read()
    # Match a marker only when it is the WHOLE line, as the template writes it.
    # A substring match false-positives on prose that quotes a marker in order to
    # talk about it -- which is exactly what a decision-log entry recording "the
    # Mission was still a placeholder" does, and it made this checker fail on a
    # board that had just been fixed. A checker that cries wolf gets ignored.
    lines = {ln.strip() for ln in body.splitlines()}
    hit = [m for m in TEMPLATE_MARKERS if m in lines]
    if hit:
        fail(
            "master-filled",
            f"{p} still contains {len(hit)} unfilled template section(s): "
            + "; ".join(repr(h[:48]) for h in hit)
            + ". Every agent is told to read this file FIRST, so a placeholder here "
            "is read by all of them. A non-empty file is not a filled-in file.",
        )
    # A decision log with entries but an unfilled Mission is the exact shape of
    # failure (1): rich history, no current state. Call it out specifically.
    if "## Where things" not in body and "- 20" in body:
        warn(
            "master-current-state",
            f"{p} has decision-log entries but no current-state section. An "
            "append-only log accumulates superseded claims; a reader cannot tell "
            "which are live. Add a state section and say it wins over the log.",
        )


def check_tracked_and_committed() -> None:
    """The check that would have prevented the destroyed log.

    An untracked doc has no recovery path from an overwrite, and an uncommitted
    change to a tracked doc is lost the same way if the tree is reset.
    """
    rc, _ = git("rev-parse", "--is-inside-work-tree")
    if rc != 0:
        warn("git", "Board is not a git repo, so no doc here is recoverable from an "
                    "overwrite. `git init` and commit the docs.")
        return

    for rel in REQUIRED_DOCS + OPTIONAL_DOCS:
        if not os.path.exists(rel):
            continue
        rc, out = git("ls-files", "--error-unmatch", rel)
        if rc != 0:
            fail(
                "tracked",
                f"{rel} exists but is NOT git-tracked, so overwriting it is "
                "unrecoverable. If .gitignore covers the board dir, note that a "
                "trailing-slash pattern (`.tickets/`) stops git descending and "
                "negations are never reconsidered -- use `.tickets/*` plus "
                f"`!{rel}`.",
            )
            continue
        rc, out = git("status", "--porcelain", "--", rel)
        if out.strip():
            fail(
                "committed",
                f"{rel} has uncommitted changes ({out.strip().split()[0]}). "
                "Commit before clearing; a tracked-but-uncommitted doc is exactly "
                "as lossy as an untracked one.",
            )


def check_objective() -> None:
    p = ".tickets/objective.json"
    if not os.path.exists(p):
        warn("objective", f"{p} missing; a fresh master has no stated goal.")
        return
    try:
        obj = json.load(open(p, encoding="utf-8"))
    except (OSError, ValueError) as e:
        fail("objective", f"{p} is unreadable ({type(e).__name__}).")
        return
    text = (obj.get("text") or "").strip()
    if not text:
        fail("objective", f"{p} has no text.")
    elif len(text) < 40:
        warn("objective", f"{p} text is {len(text)} chars; too terse to orient anyone.")


def check_agent_handles() -> None:
    """Every active claim should have a resumable id recorded.

    An unrecorded agent id is the one thing genuinely lost when the coordinator's
    context goes: the agent's accumulated context becomes unreachable and the only
    recovery is a fresh spawn that re-reads every brief.
    """
    handles_p = ".tickets/AGENT-HANDLES.md"
    if not os.path.exists(handles_p):
        return
    handles = open(handles_p, encoding="utf-8").read()

    active: list[tuple[str, str]] = []
    for name in os.listdir(".tickets"):
        if not (name.startswith("T-") and name.endswith(".json")):
            continue
        try:
            t = json.load(open(os.path.join(".tickets", name), encoding="utf-8"))
        except (OSError, ValueError):
            continue
        owner = t.get("owner") or ""
        status = (t.get("status") or "").lower()
        if owner and status in ("in_progress", "in progress", "claimed"):
            active.append((name[:-5], owner))

    for tid, owner in sorted(active):
        if owner.startswith("master"):
            continue  # the coordinator is not a resumable background agent
        if owner not in handles:
            warn(
                "agent-handles",
                f"{tid} is claimed by {owner!r} but {owner!r} has no row in "
                f"{handles_p}. Record the agent id now -- without it that agent's "
                "context is unreachable and the only recovery is a fresh spawn.",
            )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--board", default=".", help="board directory (contains .tickets/)")
    args = ap.parse_args()

    os.chdir(args.board)
    if not os.path.isdir(".tickets"):
        print(f"handoff-check: no .tickets/ in {os.getcwd()} -- not a board", file=sys.stderr)
        return 2

    check_docs_exist()
    check_no_template_text()
    check_tracked_and_committed()
    check_objective()
    check_agent_handles()

    for check, msg in fails:
        print(f"FAIL [{check}] {msg}\n")
    for check, msg in warns:
        print(f"WARN [{check}] {msg}\n")

    if fails:
        print(f"handoff-check: {len(fails)} FAIL, {len(warns)} WARN — DO NOT CLEAR YET")
        return 1
    if warns:
        print(f"handoff-check: 0 FAIL, {len(warns)} WARN — safe to clear, but read the warnings")
        return 0
    print("handoff-check: all clear — safe to clear/compact/hand over")
    return 0


if __name__ == "__main__":
    sys.exit(main())
