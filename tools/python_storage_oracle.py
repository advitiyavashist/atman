#!/usr/bin/env python3
"""Black-box bridge from the portable conformance protocol to Python BoardStore.

This file is an oracle adapter, not an API that the replacement core must copy.
Other implementations expose the same scenario names and JSON results through
their own adapter command.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from ticket_board.storage import BoardStore, RunAlreadyActive  # noqa: E402


def duplicate_delivery(db: Path) -> dict:
    store = BoardStore(db)
    try:
        project = store.create_project("Conformance")
        agent = store.create_agent(project["id"], "worker")
        operator = store.create_member(
            project["id"], "human", "Operator", "owner", member_id="mem_operator1"
        )
        worker = store.create_member(
            project["id"], "agent", "worker", "member", agent_id=agent["id"],
            member_id="mem_worker001",
        )
        channel = store.create_channel(
            project["id"], "work", "public", channel_id="chn_work0001"
        )
        store.add_channel_member(project["id"], channel["id"], operator["id"])
        actor = {
            "type": "human", "id": operator["id"], "display_name": "Operator",
            "session_id": None,
        }
        first = store.send_message(
            project["id"], channel["id"], actor, "Take the task",
            mentions=[worker["id"]], ticket_id="CONF-1",
            message_id="msg_conform001", request_id="11111111-2222-4333-8444-555555555555",
        )
        replay = store.send_message(
            project["id"], channel["id"], actor, "Take the task",
            mentions=[worker["id"]], ticket_id="CONF-1",
            message_id="msg_conform001", request_id="11111111-2222-4333-8444-555555555555",
        )
        delivery = first["deliveries"][0]
        wake1 = store.create_wake_job(
            project["id"], first["message"]["id"], agent["id"], delivery["id"],
            ticket_id="CONF-1", wake_job_id="wjb_conform001",
        )
        wake2 = store.create_wake_job(
            project["id"], first["message"]["id"], agent["id"], delivery["id"],
            ticket_id="CONF-1",
        )
        return {
            "request_replay_equal": replay == first,
            "delivery_count": len(
                store.list_deliveries(project["id"], first["message"]["id"])["items"]
            ),
            "wake_job_count": len(store.list_wake_jobs(project["id"])["items"]),
            "wake_deduplicated": wake1["id"] == wake2["id"],
            "intent": first["message"]["intent"],
        }
    finally:
        store.close()


def lease_fencing(db: Path) -> dict:
    store = BoardStore(db)
    try:
        project = store.create_project("Conformance")
        agent = store.create_agent(project["id"], "worker")
        first = store.acquire_runner_lease(
            project["id"], "rnr_first001", agent["id"], "2026-09-09T10:05:00Z",
            allowlisted_worktree="/repo/.worktrees/worker",
            at="2026-09-09T10:00:00Z",
        )
        conflict_code = None
        try:
            store.acquire_runner_lease(
                project["id"], "rnr_second01", agent["id"], "2026-09-09T10:06:00Z",
                allowlisted_worktree="/repo/.worktrees/worker",
                at="2026-09-09T10:01:00Z",
            )
        except RunAlreadyActive as exc:
            conflict_code = exc.code
        second = store.acquire_runner_lease(
            project["id"], "rnr_second01", agent["id"], "2026-09-09T10:15:00Z",
            allowlisted_worktree="/repo/.worktrees/worker",
            at="2026-09-09T10:06:00Z",
        )
        stale_code = None
        try:
            store.heartbeat_runner_lease(
                project["id"], first["runner_id"], agent["id"],
                "2026-09-09T10:20:00Z", expected_epoch=first["epoch"],
                at="2026-09-09T10:07:00Z",
            )
        except RunAlreadyActive as exc:
            stale_code = exc.code
        return {
            "first_epoch": first["epoch"],
            "live_conflict": conflict_code,
            "takeover_epoch": second["epoch"],
            "stale_heartbeat": stale_code,
            "holder": second["runner_id"],
        }
    finally:
        store.close()


SCENARIOS = {
    "duplicate-delivery": duplicate_delivery,
    "lease-fencing": lease_fencing,
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario", choices=sorted(SCENARIOS))
    parser.add_argument("--db", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(SCENARIOS[args.scenario](args.db), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
