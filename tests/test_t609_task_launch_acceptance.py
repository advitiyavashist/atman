"""T-609: local V1 task delivery starts one bounded worker run end to end."""

import json
import re
import shlex
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from test_wakeup import board, pending, run  # noqa: F401
from ui_server_harness import make_ui_server_fixture


TOOL = Path(__file__).resolve().parents[1] / "tickets.py"
ui_server = make_ui_server_fixture("t609-launch")


def _same_origin_task(server, payload):
    origin = "http://127.0.0.1:%d" % server.port
    req = urllib.request.Request(
        origin + "/msg",
        data=json.dumps(payload).encode(),
        method="POST",
        headers={"Content-Type": "application/json", "Origin": origin},
    )
    with urllib.request.urlopen(req, timeout=5) as response:
        return response.status, json.loads(response.read())


def _message(snapshot, text):
    return next(m for m in snapshot["messages"] if m.get("text") == text)


def test_dashboard_task_wakes_bounded_worker_and_receipts_update(board, ui_server, tmp_path):
    run(board, "join", "boss", "--roles", "backend", agent="boss")
    run(board, "join", "worker", "--roles", "backend", agent="worker")
    created = run(board, "create", "Run the launch acceptance", "--role", "launch", agent="boss")
    assert created.returncode == 0, created.stderr
    ticket_id = re.search(r"T-\d+", created.stdout).group(0)

    # Agent join receipts use second-resolution watermarks. Keep the task in a
    # later second so this test exercises delivery instead of the legacy tie.
    time.sleep(1.1)
    task_text = "Start %s and acknowledge when the run begins" % ticket_id
    status, posted = _same_origin_task(ui_server, {
        "from": "boss", "to": "worker", "re": ticket_id,
        "kind": "task", "text": task_text,
    })
    assert status == 200 and posted["ok"] is True

    before = ui_server.get()
    task_before = _message(before, task_text)
    assert task_before["kind"] == "task"
    assert task_before["delivery"]["acks"] == [{"agent": "worker", "acked": False}]
    rc, queued = pending(board, "worker")
    assert rc == 0 and queued["pending"] is True
    assert queued["wake_reason"] == "task_messages"
    assert any(task_text in item for item in queued["task_messages"])

    # Assignment claims the ticket. The deterministic harness then represents
    # one real model turn: it reads the task, records a bound work update, and
    # posts an acknowledgement before the one-shot watcher exits.
    assigned = run(board, "assign", ticket_id, "--owner", "worker", agent="boss")
    assert assigned.returncode == 0, assigned.stderr
    helper = tmp_path / "bounded_worker.py"
    helper.write_text(
        "import subprocess, sys\n"
        "tool, ticket = sys.argv[1:]\n"
        "base = [sys.executable, tool]\n"
        "subprocess.run(base + ['inbox'], check=True)\n"
        "subprocess.run(base + ['update', ticket, 'started from dashboard task'], check=True)\n"
        "subprocess.run(base + ['msg', 'ACK started ' + ticket, '--to', 'boss', '--re', ticket], check=True)\n"
    )
    watched = run(
        board, "watch", "--agent", "worker", "--once",
        "--exec", shlex.join([sys.executable, str(helper), str(TOOL), ticket_id]),
        agent="worker",
    )
    assert watched.returncode == 0, watched.stderr + watched.stdout
    assert "run 1 finished exit=0" in watched.stdout
    assert "max-runs reached" in watched.stdout

    ticket = json.loads((board / (ticket_id + ".json")).read_text())
    assert ticket["status"] == "claimed" and ticket["owner"] == "worker"
    assert any("started from dashboard task" in n.get("text", "") for n in ticket["notes"])

    after_worker = ui_server.get()
    task_after = _message(after_worker, task_text)
    assert task_after["delivery"]["acks"] == [{"agent": "worker", "acked": True}]
    ack_text = "ACK started " + ticket_id
    ack = _message(after_worker, ack_text)
    assert ack["from"] == "worker" and ack["to"] == "boss" and ack["re"] == ticket_id
    assert ack["delivery"]["acks"] == [{"agent": "boss", "acked": False}]

    run(board, "inbox", agent="boss")
    final = ui_server.get()
    assert _message(final, ack_text)["delivery"]["acks"] == [{"agent": "boss", "acked": True}]

    events = [json.loads(line) for line in (board / "trajectories.jsonl").read_text().splitlines()]
    starts = [e for e in events if e.get("kind") == "run_start" and e.get("agent") == "worker"]
    ends = [e for e in events if e.get("kind") == "run_end" and e.get("agent") == "worker"]
    assert len(starts) == 1 and starts[0].get("ticket") == ticket_id
    assert len(ends) == 1 and ends[0].get("exit") == 0 and ends[0].get("bound_write") is True
