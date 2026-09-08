"""T-248: cross-agent attack on T-182's master lease and assignment loop.

The four things asserted here are declared limits from the master-loop
review notes; the point of this file is that a declared limit is a place
nobody has executed yet, not a place that has been cleared.

Three of the four hold up under attack and are pinned here so they stay held.
The fourth held, but only for the property it was stated about -- a fifth gate,
which nobody declared, does not hold, and the two `xfail(strict=True)` tests at
the bottom are that finding. They assert the behaviour the frozen contract asks
for, so they turn green the day somebody fixes it rather than freezing the
defect into the suite.
"""

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from ticket_board.orchestrator import Sweeper, eligibility
from ticket_board.server import master as master_ops
from ticket_board.server.auth import in_seconds
from ticket_board.storage.db import write_txn

CHILD = Path(__file__).with_name("t248_child.py")


# --------------------------------------------------------------- helpers

def _run_children(argvs, barrier, timeout=60):
    """Start every child, then drop the barrier so they collide."""
    procs = [subprocess.Popen([sys.executable, str(CHILD)] + [str(a) for a in argv])
             for argv in argvs]
    Path(barrier).write_text("go")
    for proc in procs:
        assert proc.wait(timeout=timeout) == 0, "child exited %s" % proc.returncode


def _results(paths):
    return [json.loads(Path(p).read_text()) for p in paths]


# ============================================================ DECLARED LIMIT 1
# "The competing-master tests race two Sweeper objects against one store -- that
#  is the fence's real failure mode (two live masters), but it is not two OS
#  processes."
#
# So: two OS processes, two SQLite connections, one board file. And a control,
# because a passing race proves nothing on its own unless the same harness can
# be made to fail.

def test_two_os_processes_racing_the_lease_takeover_cannot_both_win(
        store, project, tmp_path):
    """The compare-and-swap, across a process boundary rather than a `Store`."""
    barrier = tmp_path / "barrier"
    outs = [tmp_path / "take-a.json", tmp_path / "take-b.json"]
    db = store.conn.execute("PRAGMA database_list").fetchone()["file"]
    store.conn.commit()

    procs = [subprocess.Popen(
        [sys.executable, str(CHILD), "take", db, project, "0",
         str(barrier), str(out)]) for out in outs]
    barrier.write_text("go")
    for proc in procs:
        assert proc.wait(timeout=60) == 0

    outcomes = sorted(r["outcome"] for r in _results(outs))
    assert outcomes == ["MasterLeaseConflict", "won"], (
        "two OS processes both took the lease from epoch 0: %s" % outcomes)
    assert master_ops.current_epoch(store, project) == 1


def test_a_superseded_master_in_another_os_process_writes_nothing(
        store, project, operator, make_agent, make_ticket, tmp_path):
    """Epoch 1 and epoch 2, live at the same instant, in two interpreters."""
    make_agent("backend-1", role="backend")
    make_agent("backend-2", role="backend")
    make_ticket("one", role="backend")
    make_ticket("two", role="backend")

    master_ops.take_lease(store, project, operator, expected_epoch=0)
    master_ops.take_lease(store, project, operator, expected_epoch=1)
    store.conn.execute(
        "UPDATE master_lease SET next_sweep_at = ? WHERE project_id = ?",
        (in_seconds(-1), project))
    store.conn.commit()
    db = store.conn.execute("PRAGMA database_list").fetchone()["file"]

    barrier = tmp_path / "barrier"
    stale, current = tmp_path / "stale.json", tmp_path / "current.json"
    _run_children([[db, project, 1, barrier, stale, 0.4],
                   [db, project, 2, barrier, current, 0.4]], barrier)

    by_epoch = {r["epoch"]: r for r in _results([stale, current])}
    assert by_epoch[1]["status"] == "superseded", by_epoch[1]
    assert by_epoch[1]["assigned"] == []
    assert by_epoch[2]["status"] == "swept", by_epoch[2]

    queued = master_ops.queue(store, project)
    assert len(queued) == 2, "expected one reservation per ticket, got %s" % [
        (r["ticket_id"], r["agent_id"], r["lease_epoch"]) for r in queued]
    assert {r["lease_epoch"] for r in queued} == {2}


def test_control_sqlite_locking_alone_does_not_stop_two_masters(
        store, project, operator, make_agent, make_ticket, tmp_path):
    """The control that makes the test above mean something.

    Two OS processes at the *same* epoch are two masters the fence cannot tell
    apart -- so if the write lock were what saved us, this would still route
    once. It does not: both processes route the same ticket to the same agent
    and the board ends with duplicate reservations. Serialisation is not
    mutual exclusion, and the epoch is doing the work the docstring credits
    it with.
    """
    make_agent("backend-1", role="backend", max_active=5)
    make_ticket("one", role="backend")

    master_ops.take_lease(store, project, operator, expected_epoch=0)
    store.conn.execute(
        "UPDATE master_lease SET next_sweep_at = ? WHERE project_id = ?",
        (in_seconds(-1), project))
    store.conn.commit()
    db = store.conn.execute("PRAGMA database_list").fetchone()["file"]

    barrier = tmp_path / "barrier"
    outs = [tmp_path / "a.json", tmp_path / "b.json"]
    _run_children([[db, project, 1, barrier, outs[0], 0.4],
                   [db, project, 1, barrier, outs[1], 0.4]], barrier)

    results = _results(outs)
    assert all(r["status"] == "swept" for r in results), results
    queued = master_ops.queue(store, project)
    assert len(queued) == 2, (
        "control failed: SQLite locking alone already prevented the double"
        " write, so the epoch fence is not what the passing race proves")


# ============================================================ DECLARED LIMIT 2
# "ONE MUTATION SURVIVES: removing the is_current() check at the top of
#  run_once changes nothing, because expire_reservations() re-checks the epoch
#  inside its own transaction BEFORE its no-rows early return."
#
# The author verified the zero-reservations path. Below are the other ways out
# of run_once, looking for one where the first write is not expire_reservations.

class _Unfenced(Sweeper):
    """T-182 with the mutation applied: the top-of-pass check deleted."""

    def is_current(self):
        return True


def _superseded(store, project, operator):
    master_ops.take_lease(store, project, operator, expected_epoch=0)
    master_ops.take_lease(store, project, operator, expected_epoch=1)
    store.conn.execute(
        "UPDATE master_lease SET next_sweep_at = ? WHERE project_id = ?",
        (in_seconds(-1), project))
    store.conn.commit()
    return _Unfenced(store, project, operator, 1)


def _write_counts(store, project):
    conn = store.conn
    return {
        "assignments": conn.execute(
            "SELECT COUNT(*) c FROM assignments WHERE project_id = ?",
            (project,)).fetchone()["c"],
        "decisions": conn.execute(
            "SELECT COUNT(*) c FROM master_decisions WHERE project_id = ?",
            (project,)).fetchone()["c"],
        "next_sweep_at": conn.execute(
            "SELECT next_sweep_at n FROM master_lease WHERE project_id = ?",
            (project,)).fetchone()["n"],
    }


@pytest.mark.parametrize("shape", ["empty", "nothing-routable", "routable"])
def test_the_surviving_mutation_still_cannot_write_on_any_shape_of_board(
        store, project, operator, make_agent, make_ticket, shape):
    """Three boards, chosen so the sweep leaves run_once by three routes."""
    if shape == "nothing-routable":
        holder = make_agent("holder", live=False)
        make_ticket("owned", state="claimed", owner=holder)
    elif shape == "routable":
        make_agent("backend-1", role="backend")
        make_ticket("work", role="backend")

    sweeper = _superseded(store, project, operator)
    before = _write_counts(store, project)
    assert sweeper.run_once(force=True).status == "superseded"
    assert _write_counts(store, project) == before, (
        "the mutation is load-bearing on the %s board" % shape)


def test_a_recommender_that_raises_cannot_reach_a_write_either(
        store, project, operator, make_agent, make_ticket):
    """The one input to the loop nobody reviewed, on the mutated build."""
    make_agent("backend-1", role="backend")
    make_ticket("work", role="backend")
    master_ops.take_lease(store, project, operator, expected_epoch=0)
    master_ops.take_lease(store, project, operator, expected_epoch=1)
    store.conn.execute(
        "UPDATE master_lease SET routing_mode = 'deterministic_plus_suggestions',"
        " next_sweep_at = ? WHERE project_id = ?", (in_seconds(-1), project))
    store.conn.commit()

    def explodes(ticket, candidates):
        raise RuntimeError("recommender is down")

    sweeper = _Unfenced(store, project, operator, 1, recommender=explodes)
    before = _write_counts(store, project)
    assert sweeper.run_once(force=True).status == "superseded"
    assert _write_counts(store, project) == before


def test_the_mutation_is_silent_on_writes_but_not_on_what_it_reports(
        store, project, operator):
    """What the author's mutation check could not see.

    `is_current()` is not only a write guard, it is also what names the
    failure. Delete it and a superseded master on a paused or not-yet-due
    board answers "paused" / "not_due" -- a schedule, something an operator
    waits out -- instead of "superseded", which is a thing to act on. No write
    is lost, so the claim "changes nothing" is right about the fence and wrong
    about the report. Recorded, not filed: it needs the mutation to be real.
    """
    sweeper = _superseded(store, project, operator)
    store.conn.execute("UPDATE master_lease SET paused = 1 WHERE project_id = ?",
                       (project,))
    store.conn.commit()
    assert sweeper.run_once(force=True).status == "paused"

    store.conn.execute(
        "UPDATE master_lease SET paused = 0, next_sweep_at = ?"
        " WHERE project_id = ?", (in_seconds(3600), project))
    store.conn.commit()
    assert sweeper.run_once().status == "not_due"

    unmutated = Sweeper(store, project, operator, 1)
    assert unmutated.run_once(force=True).status == "superseded"


# ============================================================ DECLARED LIMIT 3
# "No autonomous merge or silent worktree takeover: enforced by absence, not by
#  a flag." Their test compares ticket state/owner/version before and after.
# A before/after comparison cannot see a write that happens to be a no-op on
# the fixture board, so this watches every statement the sweep executes.

FORBIDDEN = ("tickets", "ticket_dependencies", "reviews")


def _statements(conn, work):
    seen = []
    conn.set_trace_callback(seen.append)
    try:
        work()
    finally:
        conn.set_trace_callback(None)
    return seen


def test_no_statement_a_sweep_executes_writes_to_a_ticket(
        store, project, operator, lease, make_agent, make_ticket):
    """Absence, proved by execution instead of by reading."""
    make_agent("backend-1", role="backend", max_active=3)
    make_agent("backend-2", role="review")
    ghost = make_agent("ghost", live=False)
    make_ticket("routable", role="backend")
    make_ticket("blocked", role="backend", dependencies=["DEMO-9"])
    make_ticket("owned but open", role="backend", owner=ghost)
    make_ticket("in flight", role="backend", state="claimed", owner=ghost)
    make_ticket("no taker", role="nobody-has-this")

    sweeper = Sweeper(store, project, operator, lease)
    statements = _statements(store.conn, lambda: sweeper.run_once(force=True))

    assert statements, "trace callback captured nothing; the test proves nothing"
    offenders = []
    for sql in statements:
        head = sql.strip().split()
        if not head or head[0].upper() not in ("INSERT", "UPDATE", "DELETE"):
            continue
        target = " ".join(head[:3]).lower()
        if any(table in target for table in FORBIDDEN):
            offenders.append(sql.strip())
    assert offenders == [], (
        "the sweep wrote to a ticket table: %s" % offenders)


def test_the_sweep_never_bumps_a_ticket_version_even_indirectly(
        store, project, operator, lease, make_agent, make_ticket):
    """The version column is the CAS everything else in the board depends on."""
    make_agent("backend-1", role="backend", max_active=3)
    make_ticket("routable", role="backend")
    make_ticket("expired reservation", role="backend")

    # A reservation the sweep will expire -- the one path that writes an audit
    # row and a decision row in the same transaction as a real actor.
    master_ops.create_assignment(
        store, project, operator, ticket_id="DEMO-2",
        agent_id=store.conn.execute("SELECT id FROM agents").fetchone()["id"],
        reason="planted", lease_epoch=lease, expected_version=1)
    with write_txn(store.conn) as conn:
        conn.execute("UPDATE assignments SET expires_at = ? WHERE project_id = ?",
                     (in_seconds(-60), project))

    before = {r["id"]: dict(r) for r in store.conn.execute(
        "SELECT * FROM tickets WHERE project_id = ?", (project,))}
    result = Sweeper(store, project, operator, lease).run_once(force=True)
    assert result.expired == 1
    after = {r["id"]: dict(r) for r in store.conn.execute(
        "SELECT * FROM tickets WHERE project_id = ?", (project,))}
    assert before == after


# ============================================================ DECLARED LIMIT 4
# "Ties break on agent id after least-loaded, so the same board yields the same
#  plan -- asserted over 5 repeat runs."
#
# Five runs in one interpreter share one hash seed and one set of dict orders.
# These run the plan in fresh interpreters under hostile PYTHONHASHSEEDs, on
# ties that are NOT on load.

PLAN_SCRIPT = textwrap.dedent("""
    import json, sys
    sys.path.insert(0, %r)
    from ticket_board.server import BoardServer
    from ticket_board.orchestrator import eligibility
    server = BoardServer(sys.argv[1])
    snapshot = eligibility.Snapshot(server.store, sys.argv[2])
    print(json.dumps([list(d) for d in eligibility.plan(snapshot)]))
    server.close()
""")


def _plan_in_a_fresh_interpreter(db, project, seed, tmp_path):
    script = tmp_path / ("plan_%s.py" % seed)
    script.write_text(PLAN_SCRIPT % str(
        Path(__file__).resolve().parents[2] / "src"))
    out = subprocess.run(
        [sys.executable, str(script), str(db), project],
        capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin", "PYTHONHASHSEED": str(seed),
             "HOME": str(tmp_path)})
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_a_tie_that_is_not_on_load_is_stable_across_fresh_interpreters(
        store, project, make_agent, make_ticket, tmp_path):
    """Equal load, equal capability, equal capacity -- differing insert order.

    Four agents enrolled in an order that deliberately disagrees with their
    generated ids, so anything that leaked row order or dict order into the
    tie-break would show up as a different winner under a different hash seed.
    """
    for name in ("zeta", "alpha", "mike", "bravo"):
        make_agent(name, role="backend", max_active=2)
    for n in range(4):
        make_ticket("tie %d" % n, role="backend")
    store.conn.commit()
    db = store.conn.execute("PRAGMA database_list").fetchone()["file"]

    plans = [_plan_in_a_fresh_interpreter(db, project, seed, tmp_path)
             for seed in (0, 1, 7, 4242, 99991)]
    assert all(plan == plans[0] for plan in plans), (
        "the plan moved between interpreters: %s" % plans)
    assert [d[1] for d in plans[0]] == ["assigned"] * 4


def test_a_tie_broken_by_worktree_and_file_occupancy_is_also_stable(
        store, project, make_agent, make_ticket, tmp_path):
    """The other dictionaries in the pass: occupied_files, occupied_worktrees.

    Both are built with `setdefault`, so whichever ticket is seen first wins the
    key and lands in an operator-visible reason string. Ordered inputs should
    make that stable; two hash seeds and two interpreters say whether it is.
    """
    make_agent("backend-1", role="backend", worktree="/w/a")
    make_agent("backend-2", role="backend", worktree="/w/b")
    ghost = make_agent("ghost", live=False)
    for n, path in enumerate(("src/a.py", "src/b.py", "src/a.py")):
        make_ticket("held %d" % n, role="backend", files=[path],
                    state="claimed", owner=ghost, worktree="/w/a")
    make_ticket("wants a", role="backend", files=["src/a.py"])
    make_ticket("wants b", role="backend", files=["src/b.py"], worktree="/w/b")
    store.conn.commit()
    db = store.conn.execute("PRAGMA database_list").fetchone()["file"]

    plans = [_plan_in_a_fresh_interpreter(db, project, seed, tmp_path)
             for seed in (0, 3, 12345)]
    assert all(plan == plans[0] for plan in plans), (
        "a rejection reason changed with the hash seed: %s" % plans)


# =============================================================== THE FINDING
# Not one of the four. Gate 4 of the frozen contract -- "file/worktree
# conflicts" -- was enforced against the snapshot only, and `plan()` decremented
# capacity as it went but did not reserve files or worktrees as it went. So the
# gate held between sweeps and collapsed inside one.
#
# The author guarded exactly this interference for capacity
# (`test_one_sweep_cannot_overfill_an_agent`); T-258 closes the same hazard for
# files and worktrees by tracking `claimed_files` / `claimed_worktrees` across
# a pass the same way `consumed` already tracks capacity. These two tests were
# xfail(strict=True) findings under T-248; they are plain assertions now that
# T-258 has landed.

def test_one_sweep_does_not_route_two_open_tickets_that_touch_one_file(
        store, project, operator, lease, make_agent, make_ticket):
    make_agent("backend-1", role="backend")
    make_agent("backend-2", role="backend")
    make_ticket("first", role="backend", files=["src/app.py"])
    make_ticket("second", role="backend", files=["src/app.py"])

    result = Sweeper(store, project, operator, lease).run_once(force=True)
    assigned = [(d.ticket_id, d.agent_id) for d in result.assigned]
    assert len(assigned) == 1, (
        "both tickets on src/app.py were reserved in one pass: %s" % assigned)


def test_one_sweep_does_not_reserve_two_agents_into_one_worktree(
        store, project, operator, lease, make_agent, make_ticket):
    make_agent("backend-1", role="backend")
    make_agent("backend-2", role="backend")
    make_ticket("first", role="backend", worktree="/w/shared")
    make_ticket("second", role="backend", worktree="/w/shared")

    result = Sweeper(store, project, operator, lease).run_once(force=True)
    worktrees = [store.conn.execute(
        "SELECT worktree FROM tickets WHERE project_id = ? AND id = ?",
        (project, d.ticket_id)).fetchone()["worktree"] for d in result.assigned]
    assert len(set(worktrees)) == len(worktrees), (
        "one sweep put two agents in %s" % worktrees)


def test_the_second_sweep_does_close_the_gate(
        store, project, operator, lease, make_agent, make_ticket):
    """Scope of the finding, stated precisely rather than dramatically.

    The gate is not missing -- it reads reservations, so once pass one has
    queued something the file is occupied and pass two respects it. The defect
    is bounded to tickets routed inside a single pass.
    """
    make_agent("backend-1", role="backend")
    make_ticket("first", role="backend", files=["src/app.py"])
    sweeper = Sweeper(store, project, operator, lease)
    assert len(sweeper.run_once(force=True).assigned) == 1

    make_ticket("second", role="backend", files=["src/app.py"])
    make_agent("backend-2", role="backend")
    second = sweeper.run_once(force=True)
    assert second.assigned == []
    reasons = " ".join(d.reason for d in second.decisions)
    assert "src/app.py" in reasons, reasons
