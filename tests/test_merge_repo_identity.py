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
import time

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


def test_repin_corrects_a_stale_pin_after_a_followup_commit_in_the_same_repo(board):
    """T-285's actual trigger, reproduced honestly: T-184's pin was CORRECT at
    write time (right repo, right branch) and went stale because the author
    pushed a follow-up commit (docs-only + a sync of main) while the ticket
    sat IN REVIEW. This is not a cross-repo problem -- `--artifact` here names
    the SAME repo the ticket was already correctly pinned to, just its
    current HEAD instead of the reviewed-time HEAD. Before `repin` existed the
    only channel for this correction was reopening (loses queue position and
    review state, T-215 warns an owner may not get it back cleanly) or a
    free-text note no tool reads -- exactly the defect T-285 files."""
    repo = board.parent
    _ignore_board(repo)

    tid = _create(board, "Correct pin that goes stale in review", role="backend")
    run(board, "claim", tid, agent="alice")
    _git(repo, "checkout", "-q", "-b", "alice/t184-style")
    (repo / "work.txt").write_text("v1")
    _git(repo, "add", "work.txt")
    _git(repo, "commit", "-m", "the reviewed work")
    r = run(board, "review", tid, "--notes", "reviewed at v1", agent="alice", cwd=repo)
    assert r.returncode == 0, r.stderr
    reviewed = _ticket(board, tid)
    stale_commit = reviewed["commit"]
    reviewed_at = reviewed["review_at"]

    # 1.1s, not 0: review_at is second-resolution, so without this a repin
    # that wrongly re-stamps review_at can land in the same second as the
    # original and the equality assertion below passes by timing coincidence
    # rather than because the clock was actually left alone (board convention,
    # see test_rotation_unread.py).
    time.sleep(1.1)

    # A follow-up commit lands on the SAME branch, in the SAME repo, while the
    # ticket is still IN REVIEW -- the recorded pin no longer names trunk-mergeable HEAD.
    (repo / "docs.txt").write_text("docs-only follow-up")
    _git(repo, "add", "docs.txt")
    _git(repo, "commit", "-m", "docs-only follow-up")
    fresh_sha = _git(repo, "rev-parse", "--short", "HEAD")

    r = run(board, "repin", tid, "--artifact", str(repo),
            "--notes", "follow-up commit landed after review", agent="alice", cwd=repo)
    assert r.returncode == 0, r.stderr

    fixed = _ticket(board, tid)
    assert fixed["status"] == "review", "repin must not force a reopen"
    assert fixed["review_at"] == reviewed_at, "repin must not reset the review clock"
    assert fixed["repo"] == reviewed["repo"], "the repo did not change, only the tip did"
    assert fixed["commit"].startswith("alice/t184-style@%s" % fresh_sha), (
        "the pin must follow the branch's real current head: %s" % fixed["commit"]
    )
    assert fixed["commit"] != stale_commit
    assert "REPIN: " in fixed["notes"][-1]["text"]
    assert "was %s" % stale_commit in fixed["notes"][-1]["text"], (
        "must record what it replaced, not just what it became: %s" % fixed["notes"][-1]["text"]
    )


def test_repin_records_who_performed_the_correction_not_the_tickets_owner(board):
    """T-285's SHAPE explicitly requires 'recording who changed it and when' --
    a pin is evidence, and the correcting agent is often not the ticket's
    owner (T-272's own notes: the merger repins on behalf of a hard-limited
    owner like gpt-codex or gpt-cursor who cannot run anything themselves).
    The note must attribute the CALLER, never silently credit the ticket's
    assigned owner for a correction they did not make."""
    repo = board.parent
    _ignore_board(repo)

    tid = _create(board, "Owned by alice, corrected by the merger", role="backend")
    run(board, "claim", tid, agent="alice")
    _git(repo, "checkout", "-q", "-b", "alice/needs-a-repin")
    (repo / "f.txt").write_text("x")
    _git(repo, "add", "f.txt")
    _git(repo, "commit", "-m", "alice's work, wrong tree reviewed")
    r = run(board, "review", tid, "--notes", "submitted", agent="alice", cwd=repo)
    assert r.returncode == 0, r.stderr

    (repo / "f.txt").write_text("y")
    _git(repo, "add", "f.txt")
    _git(repo, "commit", "-m", "the real head")

    r = run(board, "repin", tid, "--artifact", str(repo),
            "--notes", "correcting on alice's behalf", agent="bob", cwd=repo)
    assert r.returncode == 0, r.stderr

    fixed = _ticket(board, tid)
    note = fixed["notes"][-1]
    assert note["by"] == "bob", (
        "the correction must be attributed to whoever ran it, not the ticket owner: %s" % note
    )
    assert note["by"] != fixed.get("owner"), "bob is not this ticket's owner"
    assert "at" in note and note["at"], "when the correction happened must be recorded"


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


# ---------------------------------------------------------------------------
# T-287: --artifact targeting and the T-243 env scrub must be true AT THE SAME
# TIME. Neither branch could carry this test, because each predates the other's
# feature: T-272 threaded cwd= through git() but passed no env=, and an
# inherited GIT_DIR/GIT_COMMON_DIR/GIT_WORK_TREE overrides cwd-based discovery
# inside git ITSELF. So under a leaked env `tickets review --artifact <dir>`
# pinned whatever repo the leak named and exited 0 -- the exact defect the
# --artifact flag exists to fix, reappearing on the command that fixes it.
#
# The leak is not hypothetical on this board: agents run `tickets` from steer
# worktrees while the deliverable lives in advitiyavashist/tickets, and
# sonnet-qa reproduced this against T-272's branch by exporting those three
# variables at a steer worktree.
# ---------------------------------------------------------------------------


def _leak_env(repo):
    """The three variables git consults BEFORE cwd-based discovery."""
    return {
        "GIT_DIR": str(repo / ".git"),
        "GIT_COMMON_DIR": str(repo / ".git"),
        "GIT_WORK_TREE": str(repo),
    }


def test_artifact_pin_survives_a_leaked_git_env(board):
    """Acceptance (2). Same setup as the test above, plus a leaked git env
    aimed at repo A. The pin must still name repo B.

    Asserted on the recorded pin rather than on stderr: the failure mode is
    silent and exit 0, so 'it did not crash' is not evidence."""
    repo_a = board.parent
    _ignore_board(repo_a)
    run(board, "master", "take", "--owner", "ceo")
    # Repo A must NOT be on main. If it is, the never-work-on-main guard
    # rejects the review first and the defect never gets to show itself -- the
    # test would go red for the wrong reason and, worse, would go green against
    # a fix that only made the guard fire. The reported harm is a WRONG PIN
    # WRITTEN WITH EXIT 0, so repo A is put on a feature branch to let the
    # command run all the way to the write.
    _git(repo_a, "checkout", "-q", "-b", "alice/cwd-side-branch")

    repo_b = _artifact_repo(board.parent.parent, "leak_artifact_repo", board)

    tid = _create(board, "Artifact in repo B, reviewed under a leaked env", role="backend")
    run(board, "claim", tid, agent="alice")
    _git(repo_b, "checkout", "-q", "-b", "alice/leak-proof")
    (repo_b / "deliverable.txt").write_text("the actual work")
    _git(repo_b, "add", "deliverable.txt")
    _git(repo_b, "commit", "-m", "the actual work")

    r = run(board, "review", tid, "--notes", "artifact is in repo B",
            "--artifact", str(repo_b), agent="alice", cwd=repo_a,
            env=_leak_env(repo_a))
    assert r.returncode == 0, r.stderr

    rec = _ticket(board, tid)
    assert rec["repo"] == "https://example.invalid/leak_artifact_repo.git", (
        "a leaked GIT_DIR/GIT_WORK_TREE re-pinned the review to the cwd repo. "
        "cwd= alone does not stop git's own env-first discovery; git() must "
        "also scrub the environment (T-243/T-287). got repo=%r" % rec["repo"]
    )
    assert rec["commit"] == "alice/leak-proof@" + _git(repo_b, "rev-parse", "--short", "HEAD"), (
        "branch and sha must come from the ARTIFACT tree under a leaked env too "
        "-- a correct repo field carrying the cwd repo's branch/sha is still an "
        "unmergeable pin: %r" % rec["commit"]
    )
    assert rec["repo_source"] == "artifact"


def test_leaked_git_env_does_not_change_the_pin_at_all(board):
    """The A/B control. The leak must be a NO-OP, not merely survivable: the
    pin recorded under a leaked env must equal the pin recorded without one.

    Without this, a fix that happened to fail closed -- refusing, or writing
    "?@?" -- would satisfy the assertions above while still not resolving the
    artifact tree. T-259 defect 2 is exactly that failure dressed as a pass."""
    repo_a = board.parent
    _ignore_board(repo_a)
    run(board, "master", "take", "--owner", "ceo")
    _git(repo_a, "checkout", "-q", "-b", "alice/cwd-side-branch")

    repo_b = _artifact_repo(board.parent.parent, "control_artifact_repo", board)
    _git(repo_b, "checkout", "-q", "-b", "alice/control")
    (repo_b / "deliverable.txt").write_text("the actual work")
    _git(repo_b, "add", "deliverable.txt")
    _git(repo_b, "commit", "-m", "the actual work")

    pins = {}
    for label, extra in (("clean", None), ("leaked", _leak_env(repo_a))):
        tid = _create(board, "control %s" % label, role="backend")
        run(board, "claim", tid, agent="alice")
        r = run(board, "review", tid, "--notes", "n", "--artifact", str(repo_b),
                agent="alice", cwd=repo_a, env=extra)
        assert r.returncode == 0, r.stderr
        rec = _ticket(board, tid)
        pins[label] = (rec["repo"], rec["commit"], rec.get("repo_source"))

    assert pins["leaked"] == pins["clean"], (
        "the git env leak changed the recorded pin: clean=%r leaked=%r"
        % (pins["clean"], pins["leaked"]))
    # And the shared answer is the real one, not a shared failure.
    assert pins["clean"][0] == "https://example.invalid/control_artifact_repo.git"
    assert pins["clean"][1].startswith("alice/control@")


# ---------------------------------------------------------------------------
# T-324: sha membership in the merged-pin set is not identity either. T-215
# closed the cross-repo hole (a foreign repo's sha coincidentally reachable
# from this repo's history); this is the same-repo sibling of that bug --
# production incident T-223, whose `commit` field held "cos-opus@af27511", an
# unrelated sync commit that happened to match this repo, resolve, be an
# ancestor, AND be part of that pass's legitimately-merged pin set (because
# SOME OTHER ticket's branch really did integrate that sha). T-223 closed
# alongside it with its own real fix still unmerged.
# ---------------------------------------------------------------------------


def test_ticket_does_not_close_on_a_coincidentally_matching_sha_from_another_branch(board):
    """A ticket whose recorded `commit` sha matches something genuinely merged
    this pass, but whose own recorded `branch` was NOT one of the branches
    integrated, must not be closed by that coincidence -- reproduces T-223."""
    repo = board.parent
    trunk = _git(repo, "symbolic-ref", "--short", "HEAD")
    _ignore_board(repo)
    run(board, "master", "take", "--owner", "ceo")

    # The real, legitimately merged ticket.
    tid_real = _create(board, "Real work, really merged", role="backend")
    run(board, "claim", tid_real, agent="alice")
    _git(repo, "checkout", "-b", "alice/real-work")
    (repo / "real.txt").write_text("real")
    _git(repo, "add", "real.txt")
    _git(repo, "commit", "-m", "real work")
    real_sha = _git(repo, "rev-parse", "HEAD")
    r = run(board, "review", tid_real, "--notes", "did the real thing", agent="alice", cwd=repo)
    assert r.returncode == 0, r.stderr
    _git(repo, "checkout", trunk)

    # The impostor: a DIFFERENT ticket, never touching `alice/real-work`, whose
    # `commit` field is corrupted (by whatever upstream bug T-223 hit) to hold
    # that exact same sha, on a branch of its own that this merge never sees.
    tid_impostor = _create(board, "Unrelated ticket with a corrupted pin", role="backend")
    run(board, "claim", tid_impostor, agent="bob")
    rec = _ticket(board, tid_impostor)
    rec["status"] = "review"
    rec["branch"] = "bob/still-open-elsewhere"
    rec["commit"] = "bob/still-open-elsewhere@" + real_sha
    rec["repo"] = _ticket(board, tid_real)["repo"]  # same repo -- not a cross-repo case
    _write_ticket(board, tid_impostor, rec)

    r = run(board, "merge", "alice/real-work", "--no-test", agent="ceo", cwd=repo)
    assert r.returncode == 0, r.stderr

    assert _status(board, tid_real) == "done", "the real ticket should close normally:\n%s" % r.stdout
    assert _status(board, tid_impostor) == "review", (
        "a ticket must not close just because its recorded sha matches a commit "
        "some OTHER branch legitimately merged this pass:\n%s" % r.stdout
    )
    assert "sha coincidence" in r.stdout or "was not one of the branches merged" in r.stdout, (
        "the refusal must be reported, not silent: %s" % r.stdout
    )


# ---------------------------------------------------------------------------
# T-389: T-324's branch-membership guard still lets a trunk-snapshot ticket on
# a bare agent-name branch auto-close when a merge of that branch integrates
# another ticket's real work -- cmd_merge's idempotency path folds the trunk
# snapshot into merged_shas/pin_full without integrating that ticket's own pin.
# ---------------------------------------------------------------------------


def test_trunk_snapshot_on_bare_agent_branch_does_not_close_when_sibling_merges(board):
    """A ticket pinned to the trunk snapshot on a bare agent branch must not
    auto-close when the merge integrates a different ticket's real work on
    that same branch name."""
    repo = board.parent
    trunk = _git(repo, "symbolic-ref", "--short", "HEAD")
    _ignore_board(repo)
    run(board, "master", "take", "--owner", "ceo")

    agent_branch = "sonnet-sdk"

    # Ticket A: reviewed at trunk tip on the bare agent branch (no unique work).
    tid_snapshot = _create(board, "Trunk snapshot on bare agent branch", role="backend")
    run(board, "claim", tid_snapshot, agent="sonnet-sdk")
    _git(repo, "checkout", "-b", agent_branch)
    r = run(board, "review", tid_snapshot, "--notes", "trunk snapshot only", agent="sonnet-sdk", cwd=repo)
    assert r.returncode == 0, r.stderr

    # Ticket B: real work on the same bare agent branch, on top of the snapshot.
    tid_real = _create(board, "Real work on bare agent branch", role="backend")
    run(board, "claim", tid_real, agent="sonnet-sdk")
    (repo / "real.txt").write_text("real")
    _git(repo, "add", "real.txt")
    _git(repo, "commit", "-m", "real work")
    r = run(board, "review", tid_real, "--notes", "real work", agent="sonnet-sdk", cwd=repo)
    assert r.returncode == 0, r.stderr
    _git(repo, "checkout", trunk)

    rec_snapshot = _ticket(board, tid_snapshot)
    assert rec_snapshot["branch"] == agent_branch
    assert rec_snapshot["commit"].startswith("%s@" % agent_branch)

    r = run(board, "merge", agent_branch, "--no-test", agent="ceo", cwd=repo)
    assert r.returncode == 0, r.stderr

    assert _status(board, tid_real) == "done", "the real ticket should close normally:\n%s" % r.stdout
    assert _status(board, tid_snapshot) == "review", (
        "a trunk-snapshot ticket must not close just because a sibling on the same "
        "bare agent branch had real work integrated this pass:\n%s" % r.stdout
    )
    assert "contributed nothing" in r.stdout, (
        "the refusal must be reported, not silent: %s" % r.stdout
    )
