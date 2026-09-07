"""Child process for T-248's two-OS-process race against the master fence.

Run as a script, never imported by the suite. Each invocation is a separate
interpreter with its own SQLite connection to one shared board file, which is
the configuration V1 actually ships in and the one the author's in-process
`Sweeper`-vs-`Sweeper` tests could not reach.
"""

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ticket_board.server import BoardServer                  # noqa: E402
from ticket_board.server import master as master_ops         # noqa: E402
from ticket_board.orchestrator import Sweeper, sweep as sweep_mod  # noqa: E402


def _wait_for_barrier(path):
    """Start both children in the same instant without a shared lock."""
    deadline = time.time() + 30
    while not os.path.exists(path):
        if time.time() > deadline:
            raise SystemExit("barrier never dropped")
        time.sleep(0.002)


def _widen_the_window(seconds):
    """Sleep between the plan and the first write.

    Not a cheat: the sweep really does read a snapshot, decide, and only then
    commit, and the gap is as wide as the board is big. Forcing it to a fixed
    size turns a timing-dependent race into a deterministic experiment.
    """
    original = sweep_mod.eligibility.plan

    def slow_plan(snapshot):
        planned = original(snapshot)
        time.sleep(seconds)
        return planned

    sweep_mod.eligibility.plan = slow_plan


def main():
    db, project_id, epoch, barrier, out, delay = (
        sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4],
        sys.argv[5], float(sys.argv[6]))

    server = BoardServer(db)
    actor = {"type": "master", "id": "mem_operator1",
             "display_name": "Operator", "session_id": "ses_%d" % epoch}
    sweeper = Sweeper(server.store, project_id, actor, epoch)
    if delay:
        _widen_the_window(delay)

    _wait_for_barrier(barrier)
    record = {"pid": os.getpid(), "epoch": epoch}
    try:
        result = sweeper.run_once(force=True)
        record["status"] = result.status
        record["assigned"] = [[d.ticket_id, d.agent_id] for d in result.assigned]
    except Exception as error:                    # noqa: BLE001
        record["status"] = "raised"
        record["error"] = "%s: %s" % (type(error).__name__, error)
        record["assigned"] = []
    finally:
        server.close()
    Path(out).write_text(json.dumps(record))


def take():
    """Second mode: race the compare-and-swap takeover itself."""
    db, project_id, expected, barrier, out = (
        sys.argv[2], sys.argv[3], int(sys.argv[4]), sys.argv[5], sys.argv[6])
    server = BoardServer(db)
    holder = {"type": "master", "id": "mem_operator1",
              "display_name": "Operator", "session_id": "ses_%d" % os.getpid()}
    _wait_for_barrier(barrier)
    record = {"pid": os.getpid()}
    try:
        master_ops.take_lease(server.store, project_id, holder,
                              expected_epoch=expected)
        record["outcome"] = "won"
    except Exception as error:                    # noqa: BLE001
        record["outcome"] = type(error).__name__
    finally:
        server.close()
    Path(out).write_text(json.dumps(record))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "take":
        take()
    else:
        main()
