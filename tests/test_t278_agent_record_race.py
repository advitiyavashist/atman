"""T-278: agent record writes are unlocked read-modify-write.

cmd_limit and checkin both do rec=_agent_rec(); rec[...]=...; write-whole-file.
os.replace makes each write atomic, but nothing serialises the READ against the
other writer, so a heartbeat that reads before cmd_limit's write and writes
after it silently discards the limit -- and both commands print success.

The interleave tests below drive the REAL checkin() and REAL cmd_limit(); the
only instrumentation is a gate on the read boundary, used as a scheduler. A
test that called the two sequentially would pass against the broken code and
prove nothing, which is the trap this ticket names explicitly.

The last two tests are diagnostic, not regression: they pin the finding that
T-228 and T-244 are NOT caused by this race. Both reproduce single-threaded.
"""
import importlib.util
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from test_wakeup import board, run  # noqa: F401

TOOL = Path(__file__).resolve().parents[1] / "tickets.py"
STOP_HOOK_CAP_PROBE = 40


def load_tool():
    """Import tickets.py as a module (it guards main() behind __main__)."""
    spec = importlib.util.spec_from_file_location("tickets_tool_t278", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Args:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def interleave(mod, board_dir, owner, writer):
    """Run `writer()` while a real checkin() is paused between its read and its write.

    Returns after both have finished. The heartbeat thread holds whatever the
    implementation holds at read time -- nothing today, the agent lock once
    fixed -- so against the fixed code `writer` simply blocks until the
    heartbeat commits, which is the point.
    """
    b_read = threading.Event()
    writer_done = threading.Event()
    real_rec = mod._agent_rec

    def gated_rec(bd, own):
        rec = real_rec(bd, own)
        if threading.current_thread().name == "heartbeat" and not b_read.is_set():
            b_read.set()
            # Bounded: against the FIXED code the writer is blocked on the lock
            # we are holding and can never set this, so we must time out and
            # commit rather than deadlock the suite.
            writer_done.wait(1.5)
        return rec

    mod._agent_rec = gated_rec
    try:
        t = threading.Thread(target=lambda: mod.checkin(board_dir, owner), name="heartbeat")
        t.start()
        assert b_read.wait(5), "heartbeat never reached its read"
        writer()
        writer_done.set()
        t.join(10)
        assert not t.is_alive(), "heartbeat thread hung"
    finally:
        mod._agent_rec = real_rec


def _rec(board_dir, owner):
    return json.loads((Path(board_dir) / "agents" / (owner + ".json")).read_text())


# ---- the reported bug ---------------------------------------------------

def test_limit_survives_a_heartbeat_that_straddles_it(board, monkeypatch):
    """The 03:36Z incident: `tickets limit` printed success and the record was
    gone three minutes later, because a watch-loop check-in had read the record
    before the limit landed and wrote it back afterwards."""
    monkeypatch.setenv("TICKET_AGENT", "gpt-codex")
    mod = load_tool()
    b = str(board)
    mod.checkin(b, "gpt-codex")

    interleave(mod, b, "gpt-codex", lambda: mod.cmd_limit(
        Args(agent="gpt-codex", clear=False, until="2026-09-12 19:41", note="credits out"), b))

    rec = _rec(b, "gpt-codex")
    assert "limit" in rec, "the heartbeat silently discarded the limit (both printed success)"
    assert rec["limit"]["until"] == "2026-09-12 19:41"
    assert rec.get("seen"), "the heartbeat's own write must not be lost either"


def test_clearing_a_limit_survives_a_straddling_heartbeat(board, monkeypatch):
    """The inverse: `tickets limit --clear` must not be undone either, or a
    recovered agent stays marked dead and nothing routes to it."""
    monkeypatch.setenv("TICKET_AGENT", "gpt-codex")
    mod = load_tool()
    b = str(board)
    mod.checkin(b, "gpt-codex")
    mod.cmd_limit(Args(agent="gpt-codex", clear=False, until="2026-10-05", note="x"), b)
    assert "limit" in _rec(b, "gpt-codex")

    interleave(mod, b, "gpt-codex", lambda: mod.cmd_limit(
        Args(agent="gpt-codex", clear=True, until="", note=""), b))

    assert "limit" not in _rec(b, "gpt-codex"), "a heartbeat resurrected a cleared limit"


# ---- blast radius: every other field in the same record -----------------

def test_inbox_seen_survives_a_straddling_heartbeat(board, monkeypatch):
    """inbox_seen lives in the same record behind the same read-modify-write.
    Losing it re-delivers already-read mail on every poll."""
    monkeypatch.setenv("TICKET_AGENT", "bob")
    mod = load_tool()
    b = str(board)
    mod.checkin(b, "bob")

    interleave(mod, b, "bob", lambda: mod._mark_inbox_read(b, "bob"))

    assert _rec(b, "bob").get("inbox_seen"), "the heartbeat discarded inbox_seen"


def test_stop_blocks_survive_a_straddling_heartbeat(board, monkeypatch):
    """stop_blocks is the continuation rate limit. Losing an append hands the
    agent more continuations than the cap allows."""
    monkeypatch.setenv("TICKET_AGENT", "bob")
    mod = load_tool()
    b = str(board)
    mod.checkin(b, "bob")

    interleave(mod, b, "bob", lambda: mod._record_stop_block(b, "bob"))

    assert _rec(b, "bob").get("stop_blocks"), "the heartbeat discarded the stop-block stamp"


# ---- end to end, real processes, real flock ------------------------------

def test_concurrent_cli_writers_do_not_corrupt_the_record(board):
    """Cross-process smoke test through the real CLI, NOT the race proof.

    Honest about its own strength: this test PASSES against the unfixed code
    (verified against origin/main 69ad5da), because the read-to-write window in
    a real `tickets` invocation is sub-millisecond and 6 concurrent check-ins
    do not reliably land inside it. It is kept for what it does establish and
    the interleave tests cannot: that a blocking flock taken by separate
    processes through the real CLI neither deadlocks nor corrupts the record,
    and that `tickets limit` still exits 0 under contention.

    The regression proof for the race itself is the interleave tests above,
    which fail against the unfixed code."""
    run(board, "join", "gpt-codex", agent="gpt-codex")
    env = dict(os.environ, TICKETS_DIR=str(board), TICKET_AGENT="gpt-codex",
               HOME=str(Path(board).parent.parent / "home"))
    env.pop("TICKETS_STOP_HOOK", None)
    cwd = str(Path(board).parent)

    for attempt in range(5):
        (Path(board) / "agents" / "gpt-codex.json").write_text(json.dumps({"owner": "gpt-codex"}))
        beats = [subprocess.Popen([sys.executable, str(TOOL), "here"], env=env, cwd=cwd,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                 for _ in range(6)]
        lim = subprocess.run([sys.executable, str(TOOL), "limit", "gpt-codex",
                              "--until", "2026-09-12 19:41", "--note", "credits out"],
                             env=env, cwd=cwd, capture_output=True, text=True)
        for p in beats:
            p.wait(30)
        assert lim.returncode == 0, lim.stderr
        rec = _rec(board, "gpt-codex")
        assert "limit" in rec, (
            "attempt %d: `tickets limit` reported success but a concurrent "
            "check-in dropped the record" % attempt)


# ---- diagnostic: T-228 and T-244 are NOT this race -----------------------

def test_t228_same_second_message_is_a_boundary_bug_not_a_race(board):
    """Single process, no concurrency, nothing to race against. now() is
    second-resolution and unread() filters `at > since`, so a message posted in
    the same second as the inbox check has at == inbox_seen and is excluded
    permanently. Pins the finding that T-278's lock does not fix T-228."""
    run(board, "join", "alice", agent="alice")
    run(board, "join", "bob", agent="bob")
    ts = "2026-05-01T12:00:00Z"
    p = Path(board) / "agents" / "bob.json"
    rec = json.loads(p.read_text())
    rec["inbox_seen"] = ts
    p.write_text(json.dumps(rec))
    with open(Path(board) / "messages.jsonl", "w") as f:
        f.write(json.dumps({"at": ts, "from": "alice", "to": "bob", "re": "",
                            "text": "SAME-SECOND-FOR-BOB"}) + "\n")
        f.write(json.dumps({"at": "2026-05-01T12:00:01Z", "from": "alice", "to": "bob",
                            "re": "", "text": "ONE-SECOND-LATER"}) + "\n")
    out = run(board, "inbox", "--limit", "500", agent="bob").stdout
    assert "ONE-SECOND-LATER" in out, "control: a later message must be visible"
    if "SAME-SECOND-FOR-BOB" not in out:
        pytest.xfail("T-228 reproduces single-threaded: boundary bug, not this race")


def test_t244_never_read_inbox_is_a_sentinel_bug_not_a_race(board):
    """Single process, no concurrency. An agent that has never run `inbox` has
    no inbox_seen key at all, so since == '' short-circuits the
    `if since and ...` archive guard and archives are never consulted. Pins the
    finding that T-278's lock does not fix T-244 either."""
    run(board, "join", "alice", agent="alice")
    run(board, "join", "carol", agent="carol")
    if "inbox_seen" in json.loads((Path(board) / "agents" / "carol.json").read_text()):
        # T-244's fix stamps inbox_seen at first check-in, so the empty-sentinel
        # state this test diagnoses no longer exists. Skip rather than fail:
        # this test documents a finding about the UNFIXED code, and once T-244
        # lands it has served its purpose and must not become a false alarm on
        # somebody else's merge.
        pytest.skip("T-244's fix is present: no never-read agent to diagnose")
    with open(Path(board) / "messages.2026-01-01.jsonl", "w") as f:
        f.write(json.dumps({"at": "2026-01-01T00:00:00Z", "from": "alice", "to": "carol",
                            "re": "", "text": "ARCHIVED-FOR-CAROL"}) + "\n")
    with open(Path(board) / "messages.jsonl", "w") as f:
        f.write(json.dumps({"at": "2026-01-03T00:00:00Z", "from": "alice", "to": "all",
                            "re": "", "text": "live-filler"}) + "\n")
    never_read = run(board, "inbox", "--limit", "500", agent="carol").stdout

    # control: identical data, but with an old inbox_seen the archive IS read.
    p = Path(board) / "agents" / "carol.json"
    rec = json.loads(p.read_text())
    rec["inbox_seen"] = "2025-12-31T00:00:00Z"
    p.write_text(json.dumps(rec))
    with_seen = run(board, "inbox", "--limit", "500", agent="carol").stdout
    assert "ARCHIVED-FOR-CAROL" in with_seen, "control: an old inbox_seen must reach archives"
    if "ARCHIVED-FOR-CAROL" not in never_read:
        pytest.xfail("T-244 reproduces single-threaded: sentinel bug, not this race")
