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


# ---------------------------------------------------------------------------
# T-272: the recorded repo must be the repo the ARTIFACT is in, not the repo
# the agent happened to run `tickets review` from.
#
# T-215 (above) aimed the guard correctly at a pin that was itself correct.
# The pin was not correct. Every E-010 agent drives the CLI from its steer
# worktree while the deliverable lives in advitiyavashist/tickets, so the guard
# was comparing against the caller's cwd repo -- which fails in BOTH
# directions on the same ticket: it refuses the correct merge, and it permits
# an unrelated merge in the cwd repo that merely contains the recorded sha.
# ---------------------------------------------------------------------------


def _artifact_repo(tmp_root, name, board):
    """A second, genuinely separate repo -- the 'advitiyavashist/tickets' of
    the real setup -- with its own trunk and its own origin URL, containing no
    board of its own."""
    repo = tmp_root / name
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "artifact repo init")
    # A distinct origin URL is what makes repo_identity() stable and distinct
    # across worktrees of the same repo -- the real repos both have one.
    _git(repo, "remote", "add", "origin", "https://example.invalid/%s.git" % name)
    return repo


def test_review_from_repo_a_for_an_artifact_in_repo_b(board):
    """Acceptance (1). Review is run from repo A (where the board lives) for a
    deliverable that lives in repo B, and then:
      (i)  a merge in B CAN close it, and
      (ii) an unrelated merge in A that genuinely contains the recorded sha
           CANNOT.
    """
    repo_a = board.parent
    _ignore_board(repo_a)
    run(board, "master", "take", "--owner", "ceo")

    repo_b = _artifact_repo(board.parent.parent, "artifact_repo", board)

    # The deliverable: a real branch with a real commit, in repo B.
    tid = _create(board, "Work whose artifact lives in another repo", role="backend")
    run(board, "claim", tid, agent="alice")
    _git(repo_b, "checkout", "-q", "-b", "alice/real-work")
    (repo_b / "deliverable.txt").write_text("the actual work")
    _git(repo_b, "add", "deliverable.txt")
    _git(repo_b, "commit", "-m", "the actual work")
    b_sha = _git(repo_b, "rev-parse", "HEAD")

    # Review is run FROM REPO A -- exactly how every E-010 agent runs it --
    # naming repo B as the artifact tree.
    r = run(board, "review", tid, "--notes", "work is in the other repo",
            "--artifact", str(repo_b), agent="alice", cwd=repo_a)
    assert r.returncode == 0, r.stderr

    rec = _ticket(board, tid)
    assert rec["repo"] == "https://example.invalid/artifact_repo.git", (
        "the pin must name the ARTIFACT repo, not the cwd repo: %r" % rec["repo"]
    )
    assert rec["commit"] == "alice/real-work@" + _git(repo_b, "rev-parse", "--short", "HEAD"), (
        "branch and sha must come from the artifact tree too -- a repo field re-aimed "
        "on its own would leave a repo-B claim carrying a repo-A sha: %r" % rec["commit"]
    )
    assert rec["repo_source"] == "artifact"
    assert rec["cwd_repo"] and rec["cwd_repo"] != rec["repo"], (
        "the cwd repo must be kept as provenance, not silently dropped: %r" % rec
    )

    # (ii) An UNRELATED merge in repo A that really does contain the recorded
    # sha must NOT close it. We give repo A the same commit object honestly,
    # the same way the T-215 test does, so this is genuine sha reuse.
    trunk_a = _git(repo_a, "symbolic-ref", "--short", "HEAD")
    _git(repo_a, "fetch", "-q", str(repo_b), b_sha)
    _git(repo_a, "merge", "-q", "--no-edit", "--allow-unrelated-histories", b_sha)
    # _git asserts on a non-zero exit, so this IS the precondition check:
    # repo A's trunk now genuinely contains the recorded sha.
    _git(repo_a, "merge-base", "--is-ancestor", b_sha, "HEAD")
    _git(repo_a, "checkout", "-q", "-b", "someone/unrelated-a-work")
    (repo_a / "unrelated.txt").write_text("nothing to do with the ticket")
    _git(repo_a, "add", "unrelated.txt")
    _git(repo_a, "commit", "-m", "unrelated work in repo A")
    tid_a = _create(board, "Unrelated work in the board's own repo", role="backend")
    run(board, "claim", tid_a, agent="bob")
    r = run(board, "review", tid_a, "--notes", "unrelated", agent="bob", cwd=repo_a)
    assert r.returncode == 0, r.stderr
    _git(repo_a, "checkout", "-q", trunk_a)

    r = run(board, "merge", "someone/unrelated-a-work", "--no-test", agent="ceo", cwd=repo_a)
    assert r.returncode == 0, r.stderr
    assert _status(board, tid_a) == "done", "repo A's own ticket should close in repo A:\n%s" % r.stdout
    assert _status(board, tid) == "review", (
        "an unrelated merge in the board's repo must NOT close a ticket whose artifact "
        "is in another repo, even though that repo's trunk really does contain the "
        "recorded sha:\n%s" % r.stdout
    )

    # (i) The CORRECT merge, run in repo B, closes it. The artifact repo must
    # be on its own trunk, exactly as the board's main checkout must be.
    _git(repo_b, "checkout", "-q", "main")
    r = run(board, "merge", "alice/real-work", "--artifact", str(repo_b),
            "--no-test", agent="ceo", cwd=repo_a)
    assert r.returncode == 0, r.stderr + r.stdout
    assert _status(board, tid) == "done", (
        "the merge in the artifact's own repo must be able to close it:\n%s" % r.stdout
    )


def test_pin_taken_from_cwd_is_labelled_as_such(board):
    """A pin the tool merely inherited from the caller's cwd must be
    distinguishable from one the agent aimed. Without this the merger cannot
    tell a verified pin from a defaulted one, which is how the 9 bad closes in
    T-253 went unnoticed."""
    repo = board.parent
    _ignore_board(repo)
    tid = _create(board, "Ordinary same-repo work", role="backend")
    run(board, "claim", tid, agent="alice")
    _git(repo, "checkout", "-q", "-b", "alice/plain")
    (repo / "f.txt").write_text("x")
    _git(repo, "add", "f.txt")
    _git(repo, "commit", "-m", "work")
    r = run(board, "review", tid, "--notes", "plain", agent="alice", cwd=repo)
    assert r.returncode == 0, r.stderr
    rec = _ticket(board, tid)
    assert rec["repo_source"] == "cwd"
    assert "cwd_repo" not in rec, "provenance is only recorded when it differs from the pin"


def test_repin_corrects_a_queued_ticket_without_reopening_it(board):
    """Acceptance (2): the 11 tickets already queued with a cwd pin need a
    path that does not reset review_at or re-notify the master 11 times."""
    repo_a = board.parent
    _ignore_board(repo_a)
    repo_b = _artifact_repo(board.parent.parent, "artifact_repo2", board)

    tid = _create(board, "Queued with a wrong pin", role="backend")
    run(board, "claim", tid, agent="alice")
    _git(repo_a, "checkout", "-q", "-b", "alice/steer-side")
    (repo_a / "x.txt").write_text("x")
    _git(repo_a, "add", "x.txt")
    _git(repo_a, "commit", "-m", "x")
    # The bad pin: reviewed from repo A while the deliverable is in repo B.
    r = run(board, "review", tid, "--notes", "real work is in the other repo", agent="alice", cwd=repo_a)
    assert r.returncode == 0, r.stderr
    bad = _ticket(board, tid)
    reviewed_at = bad["review_at"]
    notes_before = len(bad["notes"])

    _git(repo_b, "checkout", "-q", "-b", "alice/real")
    (repo_b / "real.txt").write_text("real")
    _git(repo_b, "add", "real.txt")
    _git(repo_b, "commit", "-m", "real work")

    r = run(board, "repin", tid, "--artifact", str(repo_b),
            "--notes", "pin named steer; artifact is in the tickets repo", agent="alice", cwd=repo_a)
    assert r.returncode == 0, r.stderr

    fixed = _ticket(board, tid)
    assert fixed["status"] == "review", "repin must not change status"
    assert fixed["review_at"] == reviewed_at, "repin must not reset the review clock"
    assert fixed["repo"] == "https://example.invalid/artifact_repo2.git"
    assert fixed["branch"] == "alice/real"
    assert fixed["repo_source"] == "artifact"
    assert len(fixed["notes"]) == notes_before + 1
    assert fixed["notes"][-1]["text"].startswith("REPIN: "), fixed["notes"][-1]["text"]
    assert "was alice/steer-side@" in fixed["notes"][-1]["text"], (
        "the correction must record what it replaced, not silently overwrite: %s"
        % fixed["notes"][-1]["text"]
    )


def test_repin_refuses_a_closed_ticket(board):
    """A done ticket's record is history. Repin exists to correct a pin still
    awaiting merge, not to rewrite what was already closed."""
    repo = board.parent
    _ignore_board(repo)
    repo_b = _artifact_repo(board.parent.parent, "artifact_repo3", board)
    tid = _create(board, "Already closed", role="backend")
    run(board, "claim", tid, agent="alice")
    _git(repo, "checkout", "-q", "-b", "alice/closed")
    (repo / "c.txt").write_text("c")
    _git(repo, "add", "c.txt")
    _git(repo, "commit", "-m", "c")
    r = run(board, "done", tid, "--notes", "closed it", agent="alice", cwd=repo)
    assert r.returncode == 0, r.stderr
    r = run(board, "repin", tid, "--artifact", str(repo_b), agent="alice", cwd=repo)
    assert r.returncode != 0
    assert "IN REVIEW" in r.stdout + r.stderr


def test_done_can_be_aimed_at_the_artifact_repo(board):
    """T-254's guard hard-exits when the recorded repo differs from the current
    one and explicitly refuses --force as an override. Re-aiming `review`
    without re-aiming `done` would make every correctly-pinned cross-repo
    ticket permanently unclosable."""
    repo_a = board.parent
    _ignore_board(repo_a)
    repo_b = _artifact_repo(board.parent.parent, "artifact_repo4", board)

    tid = _create(board, "Closed from the artifact repo", role="backend")
    run(board, "claim", tid, agent="alice")
    _git(repo_b, "checkout", "-q", "-b", "alice/b-work")
    (repo_b / "b.txt").write_text("b")
    _git(repo_b, "add", "b.txt")
    _git(repo_b, "commit", "-m", "b work")
    r = run(board, "review", tid, "--notes", "in repo B", "--artifact", str(repo_b),
            agent="alice", cwd=repo_a)
    assert r.returncode == 0, r.stderr

    # Without --artifact, done is run in repo A and must still refuse.
    r = run(board, "done", tid, "--notes", "closing", agent="ceo", cwd=repo_a)
    assert r.returncode != 0, "closing from the wrong repo must still be refused"
    assert "does not match" in r.stdout + r.stderr

    r = run(board, "done", tid, "--notes", "closing", "--artifact", str(repo_b),
            agent="ceo", cwd=repo_a)
    assert r.returncode == 0, r.stderr + r.stdout
    assert _status(board, tid) == "done"


def test_merge_refuses_a_repo_parked_off_trunk_instead_of_closing_nothing(board):
    """The fast-forward targets the checked-out branch while the close loop
    tests ancestry against trunk. Off trunk those diverge and every ticket is
    skipped with no reason printed -- which reads exactly like the repo guard
    rejecting them. It must refuse out loud instead."""
    repo_a = board.parent
    _ignore_board(repo_a)
    run(board, "master", "take", "--owner", "ceo")
    repo_b = _artifact_repo(board.parent.parent, "artifact_repo5", board)

    tid = _create(board, "Artifact repo parked off trunk", role="backend")
    run(board, "claim", tid, agent="alice")
    _git(repo_b, "checkout", "-q", "-b", "alice/off-trunk")
    (repo_b / "w.txt").write_text("w")
    _git(repo_b, "add", "w.txt")
    _git(repo_b, "commit", "-m", "w")
    r = run(board, "review", tid, "--notes", "in repo B", "--artifact", str(repo_b),
            agent="alice", cwd=repo_a)
    assert r.returncode == 0, r.stderr

    # repo_b is still on alice/off-trunk, not main.
    r = run(board, "merge", "alice/off-trunk", "--artifact", str(repo_b),
            "--no-test", agent="ceo", cwd=repo_a)
    assert r.returncode != 0, "must refuse, not merge into the wrong branch"
    out = r.stdout + r.stderr
    assert "not main" in out and "checkout" in out, out
    assert _status(board, tid) == "review"
