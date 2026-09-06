#!/usr/bin/env python3
"""Generate the anonymised sample legacy board used by the import tests.

Run: `python3 scripts/make_sample_legacy_board.py` (writes tests/data/legacy_board/).

Why this is synthesised rather than scrubbed from the real board:

The import tests used to `skipif` on `/Users/kavana/Downloads/steer/.tickets`
existing, so on every other machine the only real-board assertion silently
vanished and the suite still reported green. The fix needs a board that is
*committed*. Deriving one by scrubbing the live board would put a scrubber
between the fixture and the reviewer -- and a scrubber that misses one handle
leaks it into git history permanently. Synthesising instead means the fixture
provably contains no real name, handle or absolute path, because none was ever
read.

What it must keep is the real board's *shape*, including the awkward parts,
since those are what the importer gets wrong. Every edge case below was
observed on the real 133-ticket board or is a limit the frozen T-178 contract
imposes:

- 22 legacy keys, and one key (`squad`) the importer has never heard of
- every legacy status word, plus one that maps to nothing
- a dependency on a ticket that is not on the board, a duplicate edge, and a
  two-ticket cycle
- two notes sharing an `at` (59 such pairs on the real board), a note with an
  extra `kind` key (6), a note timestamped `+00:00` with fractional seconds (2)
- a note over the contract's 4000-char update body (2), a title over 200 and a
  body over 4000
- 106 of 106 real `commit` values are `branch@shortsha`, so most tickets here
  are too -- plus exactly one full 40-hex sha with an https PR url, which is
  the only shape `GitEvidence` actually accepts
- a file that is not valid JSON, board-level files that are not tickets, and a
  `.lock` file that must be ignored rather than archived
"""

import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tests" / "data" / "legacy_board"

AGENTS = ["agent-alpha", "agent-bravo", "agent-charlie", "agent-delta"]

LONG_NOTE = ("A very long handover note. " * 200)          # 5400 chars > 4000
LONG_TITLE = "Rework the ingest pipeline " * 10             # 270 chars > 200
LONG_BODY = ("Background paragraph explaining the change. " * 120)  # 5280 > 4000


def ticket(number, **fields):
    base = {
        "title": fields.pop("title", "Sample ticket {}".format(number)),
        "body": fields.pop("body", "What this ticket is for."),
        "role": fields.pop("role", "backend"),
        "status": fields.pop("status", "open"),
        "deps": fields.pop("deps", []),
        "priority": fields.pop("priority", 2),
        "epic": fields.pop("epic", "E-001"),
        "sprint": fields.pop("sprint", "S-01"),
        "needs": fields.pop("needs", []),
        "owner": fields.pop("owner", ""),
        "created": fields.pop("created", "2026-01-02T09:00:00Z"),
        "updated": fields.pop("updated", "2026-01-02T09:00:00Z"),
        "notes": fields.pop("notes", []),
        "id": "T-{:03d}".format(number),
    }
    base.update(fields)
    return base


def note(by, at, text, **extra):
    payload = {"by": by, "at": at, "text": text}
    payload.update(extra)
    return payload


def tickets():
    return [
        # 1: the fully-populated done ticket, short-sha commit like the real board
        ticket(
            1, title="Define the storage schema", body="Tables, indexes, triggers.",
            status="done", priority=1, epic="E-001", sprint="S-01",
            owner="agent-alpha", created="2026-01-02T09:00:00Z",
            updated="2026-01-03T17:20:00Z", claimed_at="2026-01-02T10:05:00Z",
            done_at="2026-01-03T17:20:00Z", review_at="2026-01-03T16:00:00Z",
            commit="agent-alpha/schema@a1b2c3d", branch="agent-alpha/schema",
            pr="12", suggested="agent-alpha",
            notes=[
                note("agent-alpha", "2026-01-02T10:05:00Z", "claimed"),
                # Two notes on the same second: source order is the only way to
                # tell them apart, so the importer must record an ordinal.
                note("agent-bravo", "2026-01-03T16:00:00Z", "review requested"),
                note("agent-alpha", "2026-01-03T16:00:00Z", "handing to review"),
                note("agent-bravo", "2026-01-03T17:20:00Z", "accepted",
                     kind="decision"),
            ],
        ),
        # 2: the only ticket whose evidence the contract can actually hold
        ticket(
            2, title="Publish the API contract", status="done", priority=1,
            owner="agent-bravo", created="2026-01-02T09:30:00Z",
            updated="2026-01-04T11:00:00Z", claimed_at="2026-01-02T11:00:00Z",
            done_at="2026-01-04T11:00:00Z", review_at="2026-01-04T10:00:00Z",
            commit="agent-bravo/contract@"
                   "0f1e2d3c4b5a69788796a5b4c3d2e1f00f1e2d3c",
            branch="agent-bravo/contract",
            pr="https://example.invalid/example/board/pull/13",
            notes=[note("agent-bravo", "2026-01-04T10:00:00Z", "contract frozen")],
        ),
        # 3: normal dependency edge onto a done ticket
        ticket(3, title="Build the board API", status="in progress",
               deps=["T-001", "T-002"], priority=1, owner="agent-charlie",
               role="backend", created="2026-01-03T09:00:00Z",
               updated="2026-01-05T09:00:00Z", claimed_at="2026-01-05T08:00:00Z",
               needs=["own-machine"],
               notes=[note("agent-charlie", "2026-01-05T08:00:00Z", "claimed")]),
        # 4: a dependency on a ticket that is not on this board
        ticket(4, title="Wire the dashboard", status="open", deps=["T-999"],
               role="console", epic="E-002", sprint="S-02"),
        # 5: the same edge listed twice
        ticket(5, title="Verify recovery", status="open",
               deps=["T-001", "T-001"], role="verification"),
        # 6 and 7: a two-ticket cycle. Real boards contain these; one bad pair
        # must not cost the other 18 tickets their import.
        ticket(6, title="Cycle head", status="open", deps=["T-007"]),
        ticket(7, title="Cycle tail", status="open", deps=["T-006"]),
        # 8: blocked, with the legacy status words the CLI actually writes
        ticket(8, title="Deploy to staging", status="blocked", role="infra",
               owner="agent-delta", needs=["docker", "network"],
               notes=[note("agent-delta", "2026-01-06T12:00:00Z",
                           "blocked on credentials")]),
        # 9: 'todo' and 'to do' both mean open
        ticket(9, title="Write the runbook", status="to do", role="docs"),
        ticket(10, title="Rotate the log files", status="todo", role="backend"),
        # 11: a status that maps to nothing -- imported open and counted
        ticket(11, title="Speculative cleanup", status="percolating"),
        # 12: 'in review'
        ticket(12, title="Message delivery API", status="in review",
               owner="agent-bravo", role="backend",
               claimed_at="2026-01-07T09:00:00Z",
               review_at="2026-01-08T09:00:00Z",
               commit="agent-bravo/messages@9f8e7d6", branch="agent-bravo/messages",
               notes=[note("agent-bravo", "2026-01-08T09:00:00Z", "submitted")]),
        # 13: a note past the contract's 4000-char update body
        ticket(13, title="Long handover", status="claimed", owner="agent-alpha",
               claimed_at="2026-01-09T09:00:00Z",
               notes=[note("agent-alpha", "2026-01-09T10:00:00Z", LONG_NOTE)]),
        # 14: a title past the contract's 200
        ticket(14, title=LONG_TITLE, status="open"),
        # 15: a body past the contract's 4000
        ticket(15, title="Large body", body=LONG_BODY, status="open"),
        # 16: a note timestamped with a numeric zero offset and fractional
        # seconds -- valid RFC 3339, and it is UTC
        ticket(16, title="Fractional timestamp", status="open",
               notes=[note("agent-charlie", "2026-01-10T11:22:33.456789+00:00",
                           "released the auto-claim")]),
        # 17: a key this importer has never seen
        ticket(17, title="Future field", status="open", squad="platform"),
        # 18: empty body and no role at all
        ticket(18, title="Sparse ticket", body="", role="", status="open"),
        # 19: an owner with no agents/ record -- the agent row still has to exist
        # or tickets.owner cannot be set, and the board shows an unowned claim
        ticket(19, title="Owner with no runtime record", status="claimed",
               owner="agent-echo", claimed_at="2026-01-11T09:00:00Z"),
        # 20: per-ticket messages key, empty, exactly as the CLI writes it
        ticket(20, title="Trailing ticket", status="open", messages=[]),
    ]


def write():
    if OUT.exists():
        shutil.rmtree(OUT)
    for sub in ("", "agents", "epics", "sprints", "briefs", "coordination"):
        (OUT / sub).mkdir(parents=True, exist_ok=True)

    for item in tickets():
        (OUT / "{}.json".format(item["id"])).write_text(
            json.dumps(item, indent=2) + "\n")

    # Not a ticket, and not an error: board-level state the CLI keeps here.
    (OUT / "master.json").write_text(json.dumps(
        {"owner": "agent-bravo", "since": "2026-01-02T08:00:00Z"}, indent=2) + "\n")
    (OUT / "roles.json").write_text(json.dumps(
        {"agent-alpha": ["backend"], "agent-bravo": ["backend", "review"],
         "agent-charlie": ["console"], "agent-delta": ["infra"]}, indent=2) + "\n")
    (OUT / "CONTEXT.md").write_text(
        "# Sample board\n\nSynthetic. No real names, handles or paths.\n")

    # Deliberately not valid JSON: the importer must report it, not skip it in
    # silence, and must not count it as a ticket.
    (OUT / "T-021.json").write_text('{"title": "truncated write"')

    for index, name in enumerate(AGENTS):
        (OUT / "agents" / "{}.json".format(name)).write_text(json.dumps({
            "owner": name,
            "cwd": "/work/board/.worktrees/{}".format(name),
            "worktree": "/work/board/.worktrees/{}".format(name),
            "branch": "{}/lane".format(name),
            "sha": "abc{:04d}".format(index),
            "dirty": 0,
            "ticket": "T-003" if name == "agent-charlie" else "",
            "note": "",
            "seen": "2026-01-11T12:0{}:00Z".format(index),
        }, indent=2) + "\n")
    # A lock held by a running CLI. Transient, not board content.
    (OUT / "agents" / "agent-alpha.lock").write_text("locked\n")

    for eid, title in (("E-001", "Board core"), ("E-002", "Dashboard")):
        (OUT / "epics" / "{}.json".format(eid)).write_text(json.dumps({
            "title": title, "body": "Epic body for {}.".format(eid),
            "status": "open", "created": "2026-01-01T08:00:00Z",
            "updated": "2026-01-01T08:00:00Z", "id": eid}, indent=2) + "\n")
    for sid, goal, status in (("S-01", "Land storage", "done"),
                              ("S-02", "Land the dashboard", "open")):
        (OUT / "sprints" / "{}.json".format(sid)).write_text(json.dumps({
            "goal": goal, "status": status, "start": "2026-01-01T08:00:00Z",
            "end": "2026-01-06T08:00:00Z", "created": "2026-01-01T08:00:00Z",
            "updated": "2026-01-06T08:00:00Z", "id": sid}, indent=2) + "\n")
    (OUT / "briefs" / "agent-alpha.md").write_text(
        "# Brief for agent-alpha\n\nOwn the storage lane.\n")
    (OUT / "coordination" / "state.json").write_text(json.dumps(
        {"last_sweep": "2026-01-11T12:00:00Z"}, indent=2) + "\n")
    (OUT / "coordination" / "state.lock").write_text("locked\n")

    messages = [
        {"at": "2026-01-02T08:00:00Z", "from": "agent-alpha", "to": "", "re": "",
         "text": "joined the board"},
        {"at": "2026-01-02T10:05:00Z", "from": "agent-alpha", "to": "agent-bravo",
         "re": "T-001", "text": "claimed the schema ticket"},
        {"at": "2026-01-05T08:00:00Z", "from": "agent-charlie", "to": "",
         "re": "T-003", "text": "starting the API"},
        {"at": "2026-01-06T12:00:00Z", "from": "agent-delta", "to": "agent-bravo",
         "re": "T-008", "text": "stuck: no credentials"},
        {"at": "2026-01-11T12:00:00Z", "from": "agent-bravo", "to": "",
         "re": "T-999", "text": "note about a ticket that is not on the board"},
    ]
    lines = [json.dumps(m) for m in messages]
    lines.insert(3, "{not json at all")   # one bad line, reported not skipped
    (OUT / "messages.jsonl").write_text("\n".join(lines) + "\n")

    print("wrote {} ({} files)".format(
        OUT, sum(1 for _ in OUT.rglob("*") if _.is_file())))


if __name__ == "__main__":
    write()
