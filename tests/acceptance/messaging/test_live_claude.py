"""The one proof that spends a model call: a real, disposable, managed Claude.

    T190_LIVE_CLAUDE=1 python3 -m pytest tests/acceptance/messaging/test_live_claude.py -s

Everything else in this package substitutes the `claude` child. This module
does not: the supervisor spawns the installed Claude Code (`claude -p
--session-id <uuid>`) in a throwaway worktree, the task text goes down its
stdin, and the process's own output is what proves that a model turn happened.

Disposable means: a fresh session UUID minted by the supervisor, a worktree
under pytest's tmp dir, `TICKET_AGENT` stripped from the child (so the Stop
hook on this machine cannot post to the live steer board as somebody else),
and no `~/.claude` mutation by this test. The transcript Claude Code writes
for the session is the only artifact left behind, under its own project dir.

A JSON receipt of the run is written to `$T190_LIVE_RECEIPT` when set, so the
proof can be attached to the review rather than described.
"""

from __future__ import annotations

import json
import os
import time
import uuid

import pytest

from ticket_board.runners.launcher import ClaudeLauncher, preflight

LIVE = os.environ.get("T190_LIVE_CLAUDE") == "1"
pytestmark = pytest.mark.skipif(
    not LIVE, reason="set T190_LIVE_CLAUDE=1 to spend one real claude -p call")


class RecordingLauncher(ClaudeLauncher):
    """The real launcher, plus a copy of what the child said.

    The supervisor discards the child's stdout (F-2); this test needs it to
    prove a model turn actually happened. Recording is done on the handle the
    real launcher returns, so the spawn itself is untouched.
    """

    def __init__(self):
        super().__init__()
        self.specs = []
        self.results = []

    def start(self, spec):
        self.specs.append(spec)
        handle = super().start(spec)
        original_wait = handle.wait

        def wait(timeout=None):
            result = original_wait(timeout=timeout)
            self.results.append(result)
            return result
        handle.wait = wait
        return handle


def test_a_real_disposable_claude_is_woken_by_a_dm_task_claims_and_runs(board, tmp_path):
    report = preflight()
    if report["error"]:
        pytest.skip(report["error"])

    nonce = "PROOF-" + uuid.uuid4().hex[:8]
    agent = board.enroll("claude-live")
    me = board.operator_member()
    dm = board.dm(me["id"], agent.member_id)
    source = board.say(board.operator, dm["id"], "one quick job").body["message"]
    outcome = ("Reply with exactly this token and nothing else, no tools, no "
               "file edits, no explanation: {}".format(nonce))
    task = board.task(board.operator, source["id"], agent.agent_id, outcome=outcome)
    assert task.status == 201, task.body
    task = task.body

    worktree = tmp_path / "disposable-worktree"
    worktree.mkdir()
    launcher = RecordingLauncher()
    sup = board.supervisor(agent, launcher=launcher, worktree=worktree,
                           budget={"max_seconds": 180})
    began = time.monotonic()
    outcomes = sup.run_forever(wait_seconds=2, max_polls=1)
    elapsed = time.monotonic() - began

    assert len(outcomes) == 1, outcomes
    out = outcomes[0]
    run = board.run(out.run_id)
    result = launcher.results[0]
    spec = launcher.specs[0]

    # The child that ran was the installed claude, with a minted UUID session,
    # never a resume, TICKET_AGENT absent from its environment.
    assert spec.resume is False
    uuid.UUID(spec.session_id)
    assert "TICKET_AGENT" not in launcher.env
    assert "Wake job {}".format(task["wake_job"]["id"]) in spec.prompt
    assert nonce in spec.prompt

    # A model turn happened: the token came back on the child's own stdout.
    assert result.returncode == 0, (result.returncode, result.stderr[-2000:])
    assert nonce in result.stdout, result.stdout[-2000:]

    # And the board saw the chain: wake -> claim -> started -> responded.
    assert out.state == "responded", out
    assert run["state"] == "responded"
    assert run["ticket_claim"] == task["ticket"]["id"]
    detail = agent.http.get("/tickets/" + task["ticket"]["id"]).body["ticket"]
    assert detail["state"] == "claimed" and detail["owner"] == agent.agent_id
    assert board.wake_job(task["wake_job"]["id"])["state"] == "completed"
    assert board.deliveries(task["message"]["id"])[0]["state"] == "delivered"

    # What the board did NOT see, stated as fact for the receipt: the reply.
    # The child has no board credential and its stdout is discarded upstream
    # (F-2), so the DM thread holds the source and the task and nothing more.
    dm_messages = board.messages(dm["id"])["items"]
    authors = [m["author"]["id"] for m in dm_messages]
    reply_on_board = agent.agent_id in authors

    receipt = {
        "ticket": "T-190",
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "claude": report["version"],
        "binary": report["binary"],
        "argv_shape": ["claude", "-p", "--session-id", "<uuid>"],
        "session_uuid": spec.session_id,
        "board_session_id": run.get("session_id"),
        "nonce": nonce,
        "nonce_in_child_stdout": nonce in result.stdout,
        "child_stdout": result.stdout[-500:],
        "spawn_seconds": out.spawn_seconds,
        "wall_seconds": round(elapsed, 2),
        "run": {k: run.get(k) for k in ("id", "state", "wake_job_id", "ticket_claim",
                                        "started_at", "ended_at", "budget")},
        "ticket": {"id": detail["id"], "state": detail["state"], "owner": detail["owner"]},
        "delivery_state": board.deliveries(task["message"]["id"])[0]["state"],
        "reply_posted_to_board": reply_on_board,
        "dm_message_intents": [m["intent"] for m in dm_messages],
    }
    print("\nT-190 live receipt:\n" + json.dumps(receipt, indent=2))
    target = os.environ.get("T190_LIVE_RECEIPT")
    if target:
        with open(target, "w") as fh:
            json.dump(receipt, fh, indent=2)
            fh.write("\n")

    # The design's <=5s "healthy idle start" target, measured here rather than
    # inherited: spawn_seconds is the supervisor's own Popen timing.
    assert out.spawn_seconds is not None and out.spawn_seconds <= 5
    # F-2 observed live, not only with the fake. Asserted as the current fact
    # (the strict xfail for the design's expectation lives in
    # test_dispatch_end_to_end.py) so this one model call is not spent twice.
    assert reply_on_board is False, "F-2 is fixed; promote the reply check to a pass"
