"""T-215: `tickets merge` must never close a review ticket whose pin belongs
to a repo other than the one being merged -- ancestry alone is not identity.

T-214's real shape: a review ticket is pinned with a genuine, real commit sha
from its own (correct) repo. An unrelated merge in a DIFFERENT repo closes it
anyway because that same sha object also happens to be reachable from the
unrelated repo's new trunk (a real coincidence in production: the shared
board's main checkout can be parked on a branch that a different ticket's
pin also names). We reproduce that honestly here by fetching one real
commit -- and everything it needs -- from one repo into a second,
otherwise-unrelated one via a one-off filesystem fetch (no permanent remote
registered). This is the one way to make two independent repos agree on a
sha without a hash search, and it is exactly the shape "ancestry alone is
not identity" describes: the fetched commit is a real object in both repos'
git object databases.

Reader-level, against the real CLI: every pin under test (except the one
explicitly modelling a pre-T-215 legacy record, which by definition cannot be
produced by the current CLI) is recorded by actually running `tickets
review` from the repo it claims to be in.
"""
import json
import subprocess

from test_wakeup import board, run  # noqa: F401


def _git(repo, *args):
    r = subprocess.run(["git", "-C", str(repo)] + list(args), capture_output=True, text=True,
                       env=dict(__import__("os").environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                                GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"))
    assert r.returncode == 0, "git %s failed: %s" % (list(args), r.stderr)
    return r.stdout.strip()


def _fetch_commit(src_repo, dst_repo, sha):
    """Pull one real commit (and everything it needs -- tree, blobs, its own
    ancestry) from src_repo into dst_repo via a one-off filesystem fetch, no
    permanent remote required. Both repos then honestly agree on the same
    sha -- this is the one way to make two independent repos share a real
    commit object without a hash search, modelling genuine cross-repo sha
    reuse rather than a fabricated field."""
    _git(dst_repo, "fetch", str(src_repo), sha)
    _git(dst_repo, "branch", "-f", "main", "FETCH_HEAD")


def _create(board, title, **kw):
    args = ["create", title]
    for k, v in kw.items():
        args += ["--%s" % k.replace("_", "-"), str(v)]
    r = run(board, *args)
    assert r.returncode == 0, r.stderr
    return r.stdout.split()[1]


def _status(board, tid):
    return json.loads((board / (tid + ".json")).read_text())["status"]


def _ticket(board, tid):
    return json.loads((board / (tid + ".json")).read_text())


def _write_ticket(board, tid, rec):
    (board / (tid + ".json")).write_text(json.dumps(rec))


def _ignore_board(repo):
    """The board lives inside the repo under test; untracked board files
    would otherwise trip `tickets review`'s dirty-tree guard on their own."""
    (repo / ".gitignore").write_text(".tickets/\n")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-m", "ignore board")


def test_cross_repo_pin_is_not_closed_and_same_repo_pin_still_closes(board):
    """Acceptance (1)+(2)+(3): a genuinely cross-repo pin must survive an
    otherwise-successful merge that legitimately closes a same-repo ticket
    sharing that merge's pin set."""
    repo = board.parent
    trunk = _git(repo, "symbolic-ref", "--short", "HEAD")
    _ignore_board(repo)
    run(board, "master", "take", "--owner", "ceo")

    # Ticket A: real work, reviewed from THIS repo -- a legitimate pin.
    tid_a = _create(board, "Real same-repo work", role="backend")
    run(board, "claim", tid_a, agent="alice")
    _git(repo, "checkout", "-b", "alice/work")
    (repo / "feature.txt").write_text("x")
    _git(repo, "add", "feature.txt")
    _git(repo, "commit", "-m", "feature")
    a_tip = _git(repo, "rev-parse", "HEAD")
    r = run(board, "review", tid_a, "--notes", "did the thing", agent="alice", cwd=repo)
    assert r.returncode == 0, r.stderr
    _git(repo, "checkout", trunk)

    # Ticket B: a DIFFERENT deliverable that happens to record the exact same
    # commit object -- reviewed for real from a second, unrelated repo.
    tid_b = _create(board, "Different deliverable, different repo", role="backend")
    run(board, "claim", tid_b, agent="bob")
    other_repo = board.parent.parent / "other_repo"
    other_repo.mkdir()
    _git(other_repo, "init", "-q", "-b", "main")
    _fetch_commit(repo, other_repo, a_tip)
    _git(other_repo, "checkout", "main")
    _git(other_repo, "checkout", "-b", "bob/other-repo-work")
    r = run(board, "review", tid_b, "--notes", "unrelated work, unrelated repo", agent="bob", cwd=other_repo)
    assert r.returncode == 0, r.stderr
    rec_b = _ticket(board, tid_b)
    assert rec_b["repo"] and rec_b["repo"] != _ticket(board, tid_a)["repo"], (
        "precondition: the two tickets' recorded repos must actually differ"
    )

    r = run(board, "merge", "alice/work", "--no-test", agent="ceo", cwd=repo)
    assert r.returncode == 0, r.stderr

    assert _status(board, tid_a) == "done", "same-repo ticket should close normally:\n%s" % r.stdout
    assert _status(board, tid_b) == "review", (
        "cross-repo pin must NOT be closed just because its sha is a real ancestor "
        "of this repo's new trunk:\n%s" % r.stdout
    )
    assert "does not match" in r.stdout or "cross-repo" in r.stdout, (
        "the skip must be reported, not silent: %s" % r.stdout
    )


def test_legacy_pin_with_no_recorded_repo_is_not_eligible_for_auto_close(board):
    """A pin recorded before T-215 shipped has no `repo` field at all. Absence
    must never be read as 'matches everything' -- it must be just as
    ineligible as an explicit mismatch."""
    repo = board.parent
    trunk = _git(repo, "symbolic-ref", "--short", "HEAD")
    _ignore_board(repo)
    run(board, "master", "take", "--owner", "ceo")

    tid = _create(board, "Legacy pin, pre-T-215", role="backend")
    run(board, "claim", tid, agent="carol")
    _git(repo, "checkout", "-b", "carol/legacy")
    (repo / "legacy.txt").write_text("x")
    _git(repo, "add", "legacy.txt")
    _git(repo, "commit", "-m", "legacy work")
    r = run(board, "review", tid, "--notes", "legacy work", agent="carol", cwd=repo)
    assert r.returncode == 0, r.stderr
    _git(repo, "checkout", trunk)

    rec = _ticket(board, tid)
    assert "repo" in rec, "precondition: a real review records a repo field"
    del rec["repo"]
    _write_ticket(board, tid, rec)

    r = run(board, "merge", "carol/legacy", "--no-test", agent="ceo", cwd=repo)
    assert r.returncode == 0, r.stderr
    assert _status(board, tid) == "review", (
        "a pin with no recorded repo (pre-T-215) must not be auto-closeable:\n%s" % r.stdout
    )
    assert "no repo recorded" in r.stdout, "the skip reason must say why: %s" % r.stdout


def test_in_progress_ticket_sharing_a_branch_name_is_untouched_by_merge(board):
    """Acceptance (4)+(5): an in-progress (never reviewed) ticket must be
    untouchable by a merge, even when it shares a branch name with a ticket
    that IS in review and IS legitimately merged this pass."""
    repo = board.parent
    trunk = _git(repo, "symbolic-ref", "--short", "HEAD")
    _ignore_board(repo)
    run(board, "master", "take", "--owner", "ceo")

    tid_reviewed = _create(board, "Reviewed work on the shared branch name", role="backend")
    run(board, "claim", tid_reviewed, agent="dave")
    _git(repo, "checkout", "-b", "shared-branch-name")
    (repo / "reviewed.txt").write_text("x")
    _git(repo, "add", "reviewed.txt")
    _git(repo, "commit", "-m", "reviewed work")
    r = run(board, "review", tid_reviewed, "--notes", "reviewed work", agent="dave", cwd=repo)
    assert r.returncode == 0, r.stderr
    _git(repo, "checkout", trunk)

    # A second, unrelated ticket that merely CLAIMS the identical branch name
    # in its own record and was never submitted for review at all.
    tid_inprogress = _create(board, "Different in-progress ticket, same branch name", role="backend")
    run(board, "claim", tid_inprogress, agent="eve")
    rec = _ticket(board, tid_inprogress)
    rec["branch"] = "shared-branch-name"
    _write_ticket(board, tid_inprogress, rec)
    assert rec["status"] == "claimed"

    r = run(board, "merge", "shared-branch-name", "--no-test", agent="ceo", cwd=repo)
    assert r.returncode == 0, r.stderr

    assert _status(board, tid_reviewed) == "done", "the actually-reviewed ticket should close:\n%s" % r.stdout
    assert _status(board, tid_inprogress) == "claimed", (
        "a ticket that was never submitted for review must be untouched by any merge, "
        "branch name shared or not:\n%s" % r.stdout
    )
