"""Runner lease fencing under real process contention."""

import multiprocessing
import sys
from pathlib import Path

SRC = str(Path(__file__).resolve().parents[2] / "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from ticket_board.storage import BoardStore  # noqa: E402
from ticket_board.storage.errors import BoardError  # noqa: E402


def _acquire_runner(args):
    db, project_id, agent_id, runner_id = args
    store = BoardStore(db)
    try:
        lease = store.acquire_runner_lease(
            project_id, runner_id, agent_id, "2099-01-01T00:00:00Z",
            allowlisted_worktree="/repos/demo/.worktrees/backend-1",
        )
        return ("won", runner_id, lease["epoch"])
    except BoardError as exc:
        return ("lost", runner_id, exc.code)
    except Exception as exc:  # noqa: BLE001 - surfaced as evidence in the assertion
        return ("crash", runner_id, "{}: {}".format(type(exc).__name__, exc))
    finally:
        store.close()


def test_eight_processes_race_runner_lease_exactly_one_holder(db_path):
    store = BoardStore(db_path)
    project = store.create_project("Race")
    agent = store.create_agent(project["id"], "backend-1")
    store.close()

    runners = ["rnr_runner{:02d}".format(i) for i in range(8)]
    args = [(str(db_path), project["id"], agent["id"], runner) for runner in runners]
    ctx = multiprocessing.get_context("spawn")
    with ctx.Pool(8) as pool:
        results = pool.map(_acquire_runner, args)

    winners = [x for x in results if x[0] == "won"]
    losers = [x for x in results if x[0] == "lost"]
    crashes = [x for x in results if x[0] == "crash"]

    assert not crashes, crashes
    assert len(winners) == 1, results
    assert len(losers) == 7
    assert {x[2] for x in losers} == {"run_already_active"}

    store = BoardStore(db_path)
    try:
        lease = store.get_runner_lease(project["id"], agent["id"])
        assert lease["runner_id"] == winners[0][1]
        assert lease["epoch"] == 1
        audits = [
            e for e in store.audit_trail(project["id"], limit=10000)
            if e["action"] == "runner_lease.acquire"
        ]
        assert len(audits) == 1
    finally:
        store.close()


def test_runner_lease_heartbeat_and_expiry_are_fenced(store, project, agent):
    lease = store.acquire_runner_lease(
        project["id"], "rnr_runner01", agent["id"], "2026-09-06T14:40:00Z",
        allowlisted_worktree="/repos/demo/.worktrees/backend-1",
        at="2026-09-06T14:32:00Z",
    )
    refreshed = store.heartbeat_runner_lease(
        project["id"], lease["runner_id"], agent["id"],
        "2026-09-06T14:45:00Z", expected_epoch=lease["epoch"],
        at="2026-09-06T14:35:00Z",
    )
    assert refreshed["expires_at"] == "2026-09-06T14:45:00Z"

    expired = store.expire_runner_lease(
        project["id"], lease["runner_id"], agent["id"],
        expected_epoch=lease["epoch"], at="2026-09-06T14:36:00Z",
    )
    assert expired["expires_at"] == "2026-09-06T14:36:00Z"

    next_lease = store.acquire_runner_lease(
        project["id"], "rnr_runner02", agent["id"], "2026-09-06T15:00:00Z",
        allowlisted_worktree="/repos/demo/.worktrees/backend-1",
        at="2026-09-06T14:37:00Z",
    )
    assert next_lease["runner_id"] == "rnr_runner02"
    assert next_lease["epoch"] == lease["epoch"] + 1
