"""D1 isolated: does rotation hide an agent's unread mail? (1s sleep avoids the
separate same-second hole, so this test measures rotation alone.)"""
import json
import time
from test_wakeup import board, run  # noqa: F401
TINY = {"TICKETS_MESSAGES_MAX_BYTES": "2000"}

def test_unread_survives_rotation(board):
    run(board, "join", "alice", agent="alice"); run(board, "join", "bob", agent="bob")
    run(board, "inbox", agent="bob", env=TINY)
    time.sleep(1.1)
    run(board, "msg", "IMPORTANT-FOR-BOB", "--to", "bob", agent="alice", env=TINY)
    for i in range(60):
        run(board, "msg", "filler%03d %s" % (i, "x"*80), agent="alice", env=TINY)
    assert list(board.glob("messages.*.jsonl")), "precondition: must have rotated"
    r = run(board, "inbox", "--limit", "500", agent="bob", env=TINY)
    print("HEADLINE:", r.stdout.splitlines()[0] if r.stdout else "(empty)")
    assert "IMPORTANT-FOR-BOB" in r.stdout


def _write_jsonl(path, records):
    with open(path, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def _set_inbox_seen(board, agent, ts):
    p = board / "agents" / (agent + ".json")
    rec = json.loads(p.read_text())
    rec["inbox_seen"] = ts
    p.write_text(json.dumps(rec))


def test_unread_spans_every_dated_archive_not_just_the_latest(board):
    """Adversarial case: TWO separate dated archives (a real cross-day
    rotation, not one file folded twice in a single run) plus a live file,
    with the message an agent needs buried in the OLDEST archive. If a fix
    only consulted glob's last match, or only the newest archive, this
    would still fail."""
    run(board, "join", "alice", agent="alice")
    run(board, "join", "bob", agent="bob")
    run(board, "join", "carol", agent="carol")

    _write_jsonl(board / "messages.2026-01-01.jsonl", [
        {"at": "2026-01-01T00:00:00Z", "from": "alice", "to": "bob", "re": "", "text": "OLDEST-ARCHIVE-FOR-BOB"},
    ])
    _write_jsonl(board / "messages.2026-01-02.jsonl", [
        {"at": "2026-01-02T00:00:00Z", "from": "alice", "to": "all", "re": "", "text": "middle-archive-filler"},
    ])
    _write_jsonl(board / "messages.jsonl", [
        {"at": "2026-01-03T00:00:00Z", "from": "alice", "to": "all", "re": "", "text": "live-filler"},
    ])

    # bob slept through both rotations: his last read predates every archive.
    _set_inbox_seen(board, "bob", "2025-12-31T00:00:00Z")
    r_bob = run(board, "inbox", "--limit", "500", agent="bob")
    assert "OLDEST-ARCHIVE-FOR-BOB" in r_bob.stdout, r_bob.stdout

    # carol is already caught up past the live file's own content; the
    # since < msgs[0].at guard should not resurrect old, already-read mail
    # for her even though the same archives exist on disk.
    _set_inbox_seen(board, "carol", "2026-01-03T00:00:01Z")
    r_carol = run(board, "inbox", "--limit", "500", agent="carol")
    assert "OLDEST-ARCHIVE-FOR-BOB" not in r_carol.stdout
    assert "middle-archive-filler" not in r_carol.stdout
    assert "live-filler" not in r_carol.stdout
