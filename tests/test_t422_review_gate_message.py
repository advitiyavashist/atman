"""T-422: `tickets review`'s behind-trunk refusal must name the tree it measured.

The gate itself is correct and is T-272 working as designed: it computes the
trunk with `_trunk(cwd=art)`, i.e. against the ARTIFACT tree. The text it
printed did not say so -- it said a bare "Run `tickets sync`", and `cmd_sync`
without `--artifact` operates on the process cwd. On a cross-repo ticket those
are DIFFERENT REPOS, so the observed sequence closes into a loop with no exit:

    tickets review ... --artifact B   -> "your branch is behind main"
    tickets sync                      -> "<branch> already contains main; nothing to do"
    tickets review ... --artifact B   -> "your branch is behind main"

Both messages are true and they are about different repositories. The agent
that hit this escaped only by reading the release source, and burned two
merges in the wrong repo first -- so the failure mode this defends against is
not a wasted turn, it is a merge into the wrong repo's main.

These tests assert the message names the artifact DIRECTORY, its resolved repo
identity, and the repo the agent is standing in as the wrong place to sync --
not merely that some message is non-empty.
"""
import os
import subprocess
import sys
from pathlib import Path

from test_wakeup import board, run  # noqa: F401
from test_merge_repo_identity import (  # noqa: F401
    _artifact_repo, _create, _git, _ignore_board, _ticket,
)

ROOT = Path(__file__).resolve().parents[1]


def _run_pkg_cli(board, *args, agent="", cwd=None):
    """Drive the packaged entry point (pyproject: tickets = ticket_board.cli:main)."""
    e = dict(os.environ, TICKETS_DIR=str(board), TICKET_AGENT=agent or "",
             HOME=str(board.parent.parent / "home"))
    e.pop("TICKETS_STOP_HOOK", None)
    # cli.py still imports top-level ticket_coordination from the repo root.
    e["PYTHONPATH"] = os.pathsep.join([str(ROOT / "src"), str(ROOT)])
    where = cwd or board.parent
    return subprocess.run(
        [sys.executable, "-m", "ticket_board.cli", *args],
        capture_output=True, text=True, env=e, cwd=str(where),
    )


def _commit(repo, name, text="x"):
    (repo / name).write_text(text)
    _git(repo, "add", name)
    _git(repo, "commit", "-m", name)


def _behind_trunk_branch(repo, branch):
    """Leave `repo` checked out on `branch`, which does NOT contain trunk:
    trunk moved on after the branch was cut. This is the real shape -- another
    agent's merge landed on main while this branch was being worked."""
    _git(repo, "checkout", "-q", "-b", branch)
    _commit(repo, "work.txt", "branch work")
    trunk = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")  # placeholder, replaced below
    del trunk
    _git(repo, "checkout", "-q", "main")
    _commit(repo, "trunk.txt", "someone else's merge")
    _git(repo, "checkout", "-q", branch)
    # Sanity: the gate's own probe must genuinely fail here.
    r = subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", "main", "HEAD"],
                       capture_output=True)
    assert r.returncode != 0, "setup did not actually leave the branch behind trunk"


def _ensure_origin_fetchable(repo):
    """T-423: `tickets sync` refuses without origin. Point origin at this repo
    so `git fetch origin main` is local and a branch that contains main prints
    'already contains origin/main; nothing to do'."""
    r = subprocess.run(["git", "-C", str(repo), "remote", "get-url", "origin"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        _git(repo, "remote", "add", "origin", str(repo.resolve()))
    _git(repo, "fetch", "-q", "origin", "main")


def _cwd_repo_on_its_own_branch(repo):
    """Repo A: the tree the agent is STANDING in. Up to date with its own
    trunk and on its own branch, so `tickets sync` there succeeds trivially --
    which is precisely what makes the reported loop closed rather than obvious."""
    _ignore_board(repo)
    _ensure_origin_fetchable(repo)
    _git(repo, "checkout", "-q", "-b", "alice/cwd-work")
    _commit(repo, "cwd.txt", "cwd work")


def _artifact_repo_fetchable(tmp_root, name, board):
    """Same identity URL as `_artifact_repo`, but T-423 sync can `git fetch origin`."""
    repo = _artifact_repo(tmp_root, name, board)
    url = "https://example.invalid/%s.git" % name
    _git(repo, "config", "url.%s.insteadOf" % str(repo.resolve()), url)
    return repo


def test_cross_repo_behind_trunk_names_the_artifact_tree_and_the_wrong_repo(board):
    """Acceptance (1)+(2): the real failure -- artifact repo behind ITS trunk,
    cwd repo up to date -- and the message must carry the artifact path."""
    repo_a = board.parent
    _cwd_repo_on_its_own_branch(repo_a)
    repo_b = _artifact_repo_fetchable(board.parent.parent, "artifact_repo_t422", board)
    _behind_trunk_branch(repo_b, "alice/b-work")

    tid = _create(board, "Cross-repo deliverable behind its own trunk", role="backend")
    run(board, "claim", tid, agent="alice")

    r = run(board, "review", tid, "--notes", "n", "--artifact", str(repo_b),
            agent="alice", cwd=repo_a)
    assert r.returncode != 0, "gate must still refuse; T-422 changes the text, not the measurement"
    out = r.stdout + r.stderr

    # The tree that was actually measured, by path and by resolved identity.
    assert str(repo_b) in out, "message must name the artifact DIRECTORY: %r" % out
    assert "example.invalid/artifact_repo_t422" in out, \
        "message must name the artifact repo's resolved identity: %r" % out

    # The repo the agent is standing in, named as the wrong place to sync.
    assert str(repo_a) in out, "message must name the cwd repo as the wrong place: %r" % out

    # A command that can actually clear the refusal.
    assert "--artifact %s" % repo_b in out, \
        "message must give a sync command aimed at the artifact tree: %r" % out

    # And the loop the agent actually hit: syncing the cwd repo says "nothing
    # to do" and the resubmit is refused again. The message above is the only
    # thing standing between an agent and merging in the wrong repo.
    s = run(board, "sync", agent="alice", cwd=repo_a)
    assert s.returncode == 0 and "nothing to do" in s.stdout + s.stderr
    r2 = run(board, "review", tid, "--notes", "n", "--artifact", str(repo_b),
             agent="alice", cwd=repo_a)
    assert r2.returncode != 0

    # The command the message names must be the one that works.
    s2 = run(board, "sync", "--artifact", str(repo_b), agent="alice", cwd=repo_a)
    assert s2.returncode == 0, s2.stdout + s2.stderr
    r3 = run(board, "review", tid, "--notes", "n", "--artifact", str(repo_b),
             agent="alice", cwd=repo_a)
    assert r3.returncode == 0, "after the prescribed sync the submit must pass: %s" % (r3.stdout + r3.stderr)


def test_same_repo_behind_trunk_keeps_the_original_wording(board):
    """Acceptance (3): no --artifact, no cross-repo problem, no new noise."""
    repo_a = board.parent
    _ignore_board(repo_a)
    _behind_trunk_branch(repo_a, "alice/same-repo")

    tid = _create(board, "Same-repo deliverable behind trunk", role="backend")
    run(board, "claim", tid, agent="alice")
    r = run(board, "review", tid, "--notes", "n", agent="alice", cwd=repo_a)
    assert r.returncode != 0
    out = (r.stdout + r.stderr).strip()
    assert out == (
        "RULE: your branch is behind main. Run `tickets sync` (merges main in, so conflicts "
        "are yours to fix now, not the master's later), then submit again."
    ), "common-path wording must be byte-identical: %r" % out


def test_artifact_pointing_at_the_cwd_tree_gets_the_common_path_wording(board):
    """`--artifact .` is the common path wearing a flag. Splitting the wording
    on "was --artifact passed" rather than "are these actually different trees"
    would put cross-repo noise on it."""
    repo_a = board.parent
    _ignore_board(repo_a)
    _behind_trunk_branch(repo_a, "alice/flagged-same")

    tid = _create(board, "Artifact flag aimed at the cwd tree", role="backend")
    run(board, "claim", tid, agent="alice")
    r = run(board, "review", tid, "--notes", "n", "--artifact", str(repo_a),
            agent="alice", cwd=repo_a)
    assert r.returncode != 0
    out = (r.stdout + r.stderr).strip()
    assert out == (
        "RULE: your branch is behind main. Run `tickets sync` (merges main in, so conflicts "
        "are yours to fix now, not the master's later), then submit again."
    ), "same tree must not get the cross-repo wording: %r" % out


def test_second_worktree_of_the_same_repo_is_also_a_wrong_place_to_sync(board):
    """The case a repo-identity comparison would miss: the artifact tree is
    another WORKTREE of the same repo, so `repo_identity` is identical on both
    sides and only the checked-out tree differs. `tickets sync` in the cwd
    worktree merges the cwd BRANCH and still cannot clear this refusal, so the
    message has to send the agent to the artifact tree here too."""
    repo_a = board.parent
    _cwd_repo_on_its_own_branch(repo_a)
    _git(repo_a, "branch", "-f", "main", "HEAD")  # a trunk both worktrees share
    wt = board.parent.parent / "second_worktree"
    _git(repo_a, "worktree", "add", "-q", "-b", "alice/wt-work", str(wt), "main")
    _commit(wt, "wt.txt", "worktree work")
    _git(repo_a, "checkout", "-q", "main")
    _commit(repo_a, "trunk2.txt", "trunk moved on")
    _git(repo_a, "checkout", "-q", "alice/cwd-work")
    _git(repo_a, "merge", "-q", "main", "-m", "cwd is up to date")

    tid = _create(board, "Deliverable in a sibling worktree", role="backend")
    run(board, "claim", tid, agent="alice")
    r = run(board, "review", tid, "--notes", "n", "--artifact", str(wt),
            agent="alice", cwd=repo_a)
    assert r.returncode != 0, "the sibling worktree is genuinely behind trunk"
    out = r.stdout + r.stderr
    assert str(wt) in out, "must name the worktree that was measured: %r" % out
    assert "--artifact %s" % wt in out, "must aim the sync at that worktree: %r" % out


def test_cli_py_review_refuses_without_minting_a_repo_none_pin(board):
    """FIX-FIRST (acceptance 4): python -m ticket_board.cli, not the root tickets.py.

    The partial --artifact port on this copy minted a pin with repo=None that
    T-215's merge guard cannot see (F2) and printed `tickets sync --artifact`
    which this copy cannot parse (F1). Review --artifact is deleted here on
    purpose -- the real cli.py artifact port is T-272. This test is the
    regression that the root-only suite could not see.
    """
    repo_a = board.parent
    _cwd_repo_on_its_own_branch(repo_a)
    repo_b = _artifact_repo(board.parent.parent, "artifact_repo_t422_cli", board)
    _behind_trunk_branch(repo_b, "alice/b-work")

    tid = _create(board, "cli.py review must not take --artifact", role="backend")
    run(board, "claim", tid, agent="alice")

    # F1: --artifact is not a flag this copy accepts. Argparse must fail
    # before cmd_review, so no pin is written at all.
    r = _run_pkg_cli(board, "review", tid, "--notes", "n", "--artifact", str(repo_b),
                     agent="alice", cwd=repo_a)
    assert r.returncode != 0, r.stdout + r.stderr
    err = r.stdout + r.stderr
    assert "unrecognized arguments" in err, err
    rec = _ticket(board, tid)
    assert rec["status"] == "claimed"
    assert not rec.get("commit"), rec
    assert rec.get("repo") is None
    assert not (rec.get("commit") and rec.get("repo") is None)

    # Behind-trunk refusal on this copy (cwd measured; art is always None).
    # Message must be the common-path line -- flags cli.py actually accepts.
    _behind_trunk_branch(repo_a, "alice/cli-behind")
    tid2 = _create(board, "cli.py behind-trunk wording", role="backend")
    run(board, "claim", tid2, agent="alice")
    r2 = _run_pkg_cli(board, "review", tid2, "--notes", "n",
                      agent="alice", cwd=repo_a)
    assert r2.returncode != 0
    out = (r2.stdout + r2.stderr).strip()
    assert out == (
        "RULE: your branch is behind main. Run `tickets sync` (merges main in, so conflicts "
        "are yours to fix now, not the master's later), then submit again."
    ), "cli.py refusal must name only flags this copy accepts: %r" % out
    rec2 = _ticket(board, tid2)
    assert rec2["status"] == "claimed"
    assert not rec2.get("commit"), rec2
    assert not (rec2.get("commit") and rec2.get("repo") is None)
