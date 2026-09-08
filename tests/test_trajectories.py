"""T-311: the trajectory log.

Runs the real CLI as a subprocess against a throwaway board, so the tests see
exactly what a writer writes and a reader reads. No network, no model.

The load-bearing claims under test are the ones a metrics layer would be
silently wrong about: that a field nobody measured is ABSENT rather than
zeroed, that one run's harness usage is never billed to another, that backfill
is idempotent and never double-counts the instrumented era, and that no note or
message body ever reaches the log.
"""

import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from test_wakeup import board, run  # noqa: F401

TOOL = Path(__file__).resolve().parents[1] / "tickets.py"


def events(board, **kw):
    path = board / "trajectories.jsonl"
    if not path.exists():
        return []
    out = []
    for ln in path.read_text().splitlines():
        ln = ln.strip()
        if ln:
            out.append(json.loads(ln))
    for k, v in kw.items():
        out = [e for e in out if e.get(k) == v]
    return out


def kinds(board):
    return [e["kind"] for e in events(board)]


def commit(repo, msg="work"):
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", msg],
                   check=True, env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                                        GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"))


@pytest.fixture
def worked(board):
    """A board with one agent, one ticket, and a git repo clean enough that
    `review`/`done` do not trip their own worktree rules."""
    repo = board.parent
    (repo / ".gitignore").write_text(".tickets/\n")
    subprocess.run(["git", "-C", str(repo), "add", ".gitignore"], check=True)
    commit(repo, "ignore")
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", "alice/work"], check=True)
    run(board, "join", "alice", "--roles", "backend", "--tool", "claude", "--model", "opus",
        agent="alice", cwd=repo)
    run(board, "create", "Ship it", "--role", "backend", cwd=repo)
    return board


# ---- writers -------------------------------------------------------------

def test_claim_update_review_done_are_all_recorded(worked):
    b = worked
    repo = b.parent
    r = run(b, "next", "--role", "backend", agent="alice", cwd=repo)
    assert r.returncode == 0, r.stdout + r.stderr
    tid = "T-002"  # T-001 is the fixture's docs ticket
    run(b, "update", tid, "halfway there", agent="alice", cwd=repo)
    commit(repo)
    r = run(b, "review", tid, "--notes", "paths and tests", agent="alice", cwd=repo)
    assert r.returncode == 0, r.stdout + r.stderr
    r = run(b, "done", tid, "--notes", "landed", agent="alice", cwd=repo)
    assert r.returncode == 0, r.stdout + r.stderr

    got = [e["kind"] for e in events(b, ticket=tid)]
    # the `msg` is cmd_review notifying the master: a real event on this
    # ticket, and it carries the notification's LENGTH, never its text.
    assert got == ["claim", "update", "review", "msg", "done"], got
    assert "paths and tests" not in (b / "trajectories.jsonl").read_text()
    ev = {e["kind"]: e for e in events(b, ticket=tid)}
    assert ev["claim"]["state_before"] == "open" and ev["claim"]["state_after"] == "claimed"
    assert ev["review"]["outcome"] == "review" and ev["review"]["pin"].startswith("alice/work@")
    assert ev["done"]["outcome"] == "done"
    # harness/model come from what the agent registered with `tickets join`
    assert ev["claim"]["harness"] == "claude" and ev["claim"]["model"] == "opus"


def test_block_and_reopen_record_the_state_they_came_from(worked):
    b = worked
    repo = b.parent
    run(b, "next", "--role", "backend", agent="alice", cwd=repo)
    run(b, "block", "T-002", "--reason", "need a decision", agent="alice", cwd=repo)
    run(b, "reopen", "T-002", "--notes", "unblocked", agent="carol", cwd=repo)
    ev = {e["kind"]: e for e in events(b, ticket="T-002")}
    assert ev["block"]["state_before"] == "claimed" and ev["block"]["outcome"] == "blocked"
    assert ev["reopen"]["state_before"] == "blocked" and ev["reopen"]["outcome"] == "reopened"
    assert ev["reopen"]["agent"] == "carol" and ev["reopen"]["prev_owner"] == "alice"


def test_messages_record_ids_and_lengths_only(worked):
    b = worked
    run(b, "join", "bob", "--roles", "backend", agent="bob")
    run(b, "msg", "SECRET-PAYLOAD-DO-NOT-LOG", "--to", "bob", agent="alice")
    m = [e for e in events(b, kind="msg") if e.get("to") == "bob"]
    assert m, kinds(b)
    assert m[-1]["text_len"] == len("SECRET-PAYLOAD-DO-NOT-LOG")
    assert "SECRET-PAYLOAD-DO-NOT-LOG" not in (b / "trajectories.jsonl").read_text()


def test_no_note_or_prompt_text_ever_reaches_the_log(worked):
    b = worked
    repo = b.parent
    run(b, "next", "--role", "backend", agent="alice", cwd=repo)
    run(b, "update", "T-002", "CONFIDENTIAL-NOTE-BODY", agent="alice", cwd=repo)
    run(b, "block", "T-002", "--reason", "CONFIDENTIAL-BLOCK-REASON", agent="alice", cwd=repo)
    raw = (b / "trajectories.jsonl").read_text()
    assert "CONFIDENTIAL-NOTE-BODY" not in raw
    assert "CONFIDENTIAL-BLOCK-REASON" not in raw
    upd = events(b, kind="update", ticket="T-002")[-1]
    assert upd["notes_len"] == len("CONFIDENTIAL-NOTE-BODY")


def test_instrumentation_cannot_fail_a_command(worked):
    """A trajectory write that cannot land must not take a ticket transition
    down with it: the transition has already happened on disk by then."""
    b = worked
    repo = b.parent
    run(b, "next", "--role", "backend", agent="alice", cwd=repo)
    # make the log path unwritable in the most total way available
    (b / "trajectories.jsonl").unlink(missing_ok=True)
    (b / "trajectories.jsonl").mkdir()
    r = run(b, "update", "T-002", "still works", agent="alice", cwd=repo)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "recorded" in r.stdout
    t = json.loads((b / "T-002.json").read_text())
    assert any(n["text"] == "still works" for n in t["notes"])


# ---- watch runs ----------------------------------------------------------

def _fake_harness(tmp, body):
    p = tmp / "fake.sh"
    p.write_text("#!/bin/sh\n" + body)
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return p


RESULT_JSON = ('{"type":"result","num_turns":7,"total_cost_usd":0.4213,'
               '"duration_ms":8123,"usage":{"input_tokens":12,"output_tokens":333,'
               '"cache_read_input_tokens":4400,"cache_creation_input_tokens":900}}')


def test_watch_records_run_start_and_run_end_with_reported_usage(worked, tmp_path):
    b = worked
    repo = b.parent
    fake = _fake_harness(tmp_path, "echo chatter\necho '%s'\n" % RESULT_JSON)
    run(b, "watch", "--agent", "alice", "--once", "--exec", str(fake), "--cwd", str(repo),
        agent="alice", cwd=repo)
    start = events(b, kind="run_start")
    end = events(b, kind="run_end")
    assert len(start) == 1 and len(end) == 1, kinds(b)
    assert start[0]["run_no"] == 1 and start[0]["trigger"] == ["ready_in_my_lane"]
    e = end[0]
    assert e["exit"] == 0 and e["timed_out"] is False
    assert e["tokens_in"] == 12 and e["tokens_out"] == 333
    assert e["tokens_cache_read"] == 4400 and e["tokens_cache_write"] == 900
    assert e["cost_usd"] == 0.4213 and e["turns"] == 7
    assert e["harness_duration_ms"] == 8123


def test_a_harness_that_reports_no_usage_yields_no_usage_fields(worked, tmp_path):
    """The default watch command does not ask for JSON output. A zero would be
    a lie; absence is the truth, and the two must not be confused."""
    b = worked
    repo = b.parent
    fake = _fake_harness(tmp_path, "echo 'just some prose, no json here'\n")
    run(b, "watch", "--agent", "alice", "--once", "--exec", str(fake), "--cwd", str(repo),
        agent="alice", cwd=repo)
    e = events(b, kind="run_end")[-1]
    for k in ("tokens_in", "tokens_out", "cost_usd", "turns", "harness_duration_ms"):
        assert k not in e, "%s must be absent, not defaulted: %r" % (k, e)
    assert e["exit"] == 0  # exit 0 IS recorded: zero is meaningful there


def test_one_runs_usage_is_never_billed_to_the_next(worked, tmp_path):
    """Both runs append to the same watch.log. If run 2's parse read the whole
    log instead of its own slice, run 1's result object would be re-counted --
    a fleet-wide cost overstatement that nothing downstream could detect."""
    b = worked
    repo = b.parent
    counter = tmp_path / "n"
    fake = _fake_harness(tmp_path, (
        "n=$(cat %s 2>/dev/null || echo 0); n=$((n+1)); echo $n > %s\n"
        "if [ \"$n\" = \"1\" ]; then echo '%s'; else echo 'quiet run, nothing reported'; fi\n"
    ) % (counter, counter, RESULT_JSON))
    for _ in range(2):
        run(b, "watch", "--agent", "alice", "--once", "--exec", str(fake), "--cwd", str(repo),
            agent="alice", cwd=repo)
    ends = events(b, kind="run_end")
    assert len(ends) == 2, kinds(b)
    assert ends[0]["turns"] == 7 and ends[0]["cost_usd"] == 0.4213
    assert "turns" not in ends[1] and "cost_usd" not in ends[1] and "tokens_in" not in ends[1]


def test_run_end_flags_a_run_the_cli_refused_to_start(worked, tmp_path):
    b = worked
    repo = b.parent
    fake = _fake_harness(tmp_path, "echo 'Claude usage limit reached'\nexit 1\n")
    run(b, "watch", "--agent", "alice", "--once", "--exec", str(fake), "--cwd", str(repo),
        agent="alice", cwd=repo)
    e = events(b, kind="run_end")[-1]
    assert e["exit"] == 1 and e["outcome"] == "limit"


# ---- reader --------------------------------------------------------------

def test_filters_and_json_and_export(worked, tmp_path):
    b = worked
    repo = b.parent
    run(b, "next", "--role", "backend", agent="alice", cwd=repo)
    run(b, "update", "T-002", "note", agent="alice", cwd=repo)
    r = run(b, "trajectories", "--ticket", "T-002", "--kind", "claim", "--json",
            agent="alice", cwd=repo)
    got = json.loads(r.stdout)
    assert len(got) == 1 and got[0]["kind"] == "claim"

    r = run(b, "trajectories", "--agent", "nobody", "--json", agent="alice", cwd=repo)
    assert json.loads(r.stdout) == []

    out = tmp_path / "out.jsonl"
    r = run(b, "trajectories", "--ticket", "T-002", "export", "--out", str(out),
            agent="alice", cwd=repo)
    assert r.returncode == 0, r.stdout + r.stderr
    lines = [json.loads(x) for x in out.read_text().splitlines() if x.strip()]
    assert lines and all(e["ticket"] == "T-002" for e in lines)


def test_since_and_until_bound_the_window(worked):
    b = worked
    repo = b.parent
    run(b, "next", "--role", "backend", agent="alice", cwd=repo)
    first = events(b, kind="claim")[0]["at"]
    r = run(b, "trajectories", "--since", "2999-01-01T00:00:00Z", "--json", agent="alice", cwd=repo)
    assert json.loads(r.stdout) == []
    r = run(b, "trajectories", "--until", first, "--kind", "claim", "--json", agent="alice", cwd=repo)
    assert len(json.loads(r.stdout)) == 1


def test_summary_counts_runs_updates_and_reopens(worked, tmp_path):
    b = worked
    repo = b.parent
    fake = _fake_harness(tmp_path, "echo hi\n")
    run(b, "next", "--role", "backend", agent="alice", cwd=repo)
    run(b, "update", "T-002", "one", agent="alice", cwd=repo)
    run(b, "update", "T-002", "two", agent="alice", cwd=repo)
    run(b, "watch", "--agent", "alice", "--once", "--exec", str(fake), "--cwd", str(repo),
        agent="alice", cwd=repo)
    run(b, "reopen", "T-002", "--notes", "back", agent="carol", cwd=repo)
    r = run(b, "trajectories", "--ticket", "T-002", "--summary", agent="alice", cwd=repo)
    line = [x for x in r.stdout.splitlines() if x.startswith("T-002")]
    assert line, r.stdout
    cols = line[-1].split()
    # ticket runs turns upd msgs reopens ...
    assert cols[0] == "T-002" and cols[1] == "1" and cols[2] == "-"
    assert cols[3] == "2" and cols[5] == "1"


def test_empty_log_says_so_instead_of_printing_nothing(board):
    r = run(board, "trajectories", agent="alice")
    assert r.returncode == 0
    assert "no trajectory events yet" in r.stdout
    assert "backfill" in r.stdout


# ---- backfill ------------------------------------------------------------

def _legacy_ticket(board, tid, **fields):
    """A ticket as it exists on a board that predates this log."""
    rec = {"id": tid, "title": "old work", "body": "", "role": "backend", "deps": [],
           "priority": 2, "epic": "", "sprint": "", "needs": [], "status": "done",
           "owner": "alice", "created": "2026-01-01T00:00:00Z",
           "claimed_at": "2026-01-01T01:00:00Z", "review_at": "2026-01-01T05:00:00Z",
           "done_at": "2026-01-01T06:00:00Z", "updated": "2026-01-01T06:00:00Z",
           "commit": "alice/old@abc1234", "repo": "/tmp/x/.git",
           "notes": [{"by": "alice", "at": "2026-01-01T02:00:00Z", "text": "PRIVATE-PROGRESS"},
                     {"by": "bob", "at": "2026-01-01T03:00:00Z", "text": "second lane note"},
                     {"by": "alice", "at": "2026-01-01T05:00:00Z", "text": "REVIEW: branch@sha -- x"}]}
    rec.update(fields)
    (board / (tid + ".json")).write_text(json.dumps(rec, indent=2))
    return rec


def test_backfill_synthesises_history_and_dates_it_when_it_happened(worked):
    b = worked
    _legacy_ticket(b, "T-900")
    r = run(b, "trajectories", "backfill", agent="alice")
    assert r.returncode == 0, r.stdout + r.stderr
    ev = events(b, ticket="T-900")
    assert [e["kind"] for e in ev] == ["claim", "update", "update", "review", "done"], ev
    assert all(e["src"] == "backfill" for e in ev)
    assert ev[0]["at"] == "2026-01-01T01:00:00Z"  # not the wall clock
    assert ev[-1]["at"] == "2026-01-01T06:00:00Z"
    assert all(e.get("backfilled_at", "").startswith("20") for e in ev)
    # the REVIEW: stamp is not a progress update
    assert [e["at"] for e in ev if e["kind"] == "update"] == [
        "2026-01-01T02:00:00Z", "2026-01-01T03:00:00Z"]
    # per-note authorship survives: a second lane is visible (T-238)
    assert [e["agent"] for e in ev if e["kind"] == "update"] == ["alice", "bob"]
    # and no note body came with it
    assert "PRIVATE-PROGRESS" not in (b / "trajectories.jsonl").read_text()


def test_backfill_invents_no_runs_tokens_or_cost(worked):
    b = worked
    _legacy_ticket(b, "T-900")
    run(b, "trajectories", "backfill", agent="alice")
    for e in events(b, ticket="T-900"):
        for k in ("run_no", "exit", "tokens_in", "tokens_out", "cost_usd", "turns"):
            assert k not in e, "%s in a synthesised event: %r" % (k, e)


def test_backfill_is_idempotent(worked):
    b = worked
    _legacy_ticket(b, "T-900")
    run(b, "trajectories", "backfill", agent="alice")
    n = len(events(b))
    r = run(b, "trajectories", "backfill", agent="alice")
    assert "wrote 0 event" in r.stdout
    assert len(events(b)) == n


def test_backfill_dry_run_writes_nothing(worked):
    b = worked
    _legacy_ticket(b, "T-900")
    r = run(b, "trajectories", "backfill", "--dry-run", agent="alice")
    assert "would write" in r.stdout and "T-900" in r.stdout
    assert events(b, ticket="T-900") == []


def test_backfill_never_double_counts_the_instrumented_era(worked):
    """The live writers already recorded this ticket. Backfill must not
    synthesise a second claim from claimed_at, or every ticket worked after
    this landed reads as having been claimed twice."""
    b = worked
    repo = b.parent
    run(b, "next", "--role", "backend", agent="alice", cwd=repo)
    run(b, "update", "T-002", "live note", agent="alice", cwd=repo)
    before = events(b, ticket="T-002")
    assert [e["kind"] for e in before] == ["claim", "update"]
    run(b, "trajectories", "backfill", agent="alice", cwd=repo)
    after = events(b, ticket="T-002")
    assert [e["kind"] for e in after] == ["claim", "update"], after
    assert not any(e.get("src") == "backfill" for e in after)


def test_backfill_fills_the_pre_instrumentation_half_of_a_mixed_ticket(worked):
    """A ticket claimed before this log existed and updated after it must get
    its old half synthesised and its new half left alone."""
    b = worked
    repo = b.parent
    run(b, "next", "--role", "backend", agent="alice", cwd=repo)
    run(b, "update", "T-002", "live note", agent="alice", cwd=repo)
    t = json.loads((b / "T-002.json").read_text())
    t["claimed_at"] = "2026-01-01T01:00:00Z"
    t["notes"].insert(0, {"by": "alice", "at": "2026-01-01T02:00:00Z", "text": "ancient"})
    (b / "T-002.json").write_text(json.dumps(t, indent=2))
    # the live claim event is still in the log, so claim must NOT be re-synthesised
    run(b, "trajectories", "backfill", agent="alice", cwd=repo)
    ev = events(b, ticket="T-002")
    assert [e["kind"] for e in ev].count("claim") == 1
    bf = [e for e in ev if e.get("src") == "backfill"]
    assert [e["at"] for e in bf] == ["2026-01-01T02:00:00Z"], bf


# ---- rotation ------------------------------------------------------------

def test_rotation_archives_and_readers_still_see_the_history(worked):
    b = worked
    repo = b.parent
    tiny = {"TICKETS_TRAJECTORIES_MAX_BYTES": "1200"}
    run(b, "next", "--role", "backend", agent="alice", cwd=repo, env=tiny)
    for i in range(40):
        run(b, "update", "T-002", "n%02d" % i, agent="alice", cwd=repo, env=tiny)
    assert list(b.glob("trajectories.*.jsonl")), "precondition: must have rotated"
    r = run(b, "trajectories", "--ticket", "T-002", "--kind", "claim", "--json",
            agent="alice", cwd=repo, env=tiny)
    got = json.loads(r.stdout)
    assert len(got) == 1, "the claim is in an archive; readers must include archives"


def test_backfill_dedup_survives_rotation(worked):
    b = worked
    tiny = {"TICKETS_TRAJECTORIES_MAX_BYTES": "1200"}
    for n in range(900, 912):
        _legacy_ticket(b, "T-%d" % n)
    run(b, "trajectories", "backfill", agent="alice", env=tiny)
    assert list(b.glob("trajectories.*.jsonl")), "precondition: must have rotated"
    r = run(b, "trajectories", "backfill", agent="alice", env=tiny)
    assert "wrote 0 event" in r.stdout, r.stdout


# ---- objective id --------------------------------------------------------

def test_objective_id_is_stable_and_changes_with_the_objective(worked):
    b = worked
    repo = b.parent
    run(b, "objective", "ship the thing", agent="alice", cwd=repo)
    run(b, "next", "--role", "backend", agent="alice", cwd=repo)
    run(b, "update", "T-002", "a", agent="alice", cwd=repo)
    ids = {e["objective_id"] for e in events(b, ticket="T-002")}
    assert len(ids) == 1 and next(iter(ids)).startswith("obj-")
    first = next(iter(ids))
    time.sleep(1.1)
    run(b, "objective", "ship a different thing", agent="alice", cwd=repo)
    run(b, "update", "T-002", "b", agent="alice", cwd=repo)
    assert events(b, ticket="T-002")[-1]["objective_id"] != first
