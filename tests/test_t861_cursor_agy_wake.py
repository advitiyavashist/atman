"""T-861: Cursor and Agy recipient adapters for any-to-any board delivery.

Evidence behind these tests (cursor-agent 2026.09.10, agy 1.2.2) is written up
in docs/wake-recipients.md. Two rules are under test:

  * a Cursor wake goes to the *managed* `agent persist` tmux server and nowhere
    else, and only says "woken" when the injected line became a turn;
  * an Agy seat is supervised, and says so, instead of reporting a fake wake.
"""

import json
import os
import sqlite3
import stat
import time
from pathlib import Path

import pytest

from test_t683_session_adapters import _adapters, _run, board, cache_dir  # noqa: F401

CHAT_ID = "8f6c2d1e-2f5a-4a1b-9c33-0a1b2c3d4e5f"
WS_HASH = "01b01c4c40e904765016307f543b0588"
SESSION = "persist-grok"

FAKE_TMUX = '''#!/usr/bin/env python3
"""Stand-in for the cursor-agent tmux server: logs argv, lists sessions,
and (when a turn is meant to start) writes the typed line into the chat
store the way a submitted prompt does."""
import json, os, sqlite3, sys, uuid

LOG = %(log)r
SESSIONS = %(sessions)r
STORE = %(store)r
TURN_STARTS = %(turn)r

argv = sys.argv[1:]
with open(LOG, "a") as fh:
    fh.write(json.dumps(argv) + "\\n")
rest = argv[argv.index("-f") + 2:] if "-f" in argv else argv
if rest[:1] == ["list-sessions"]:
    if not SESSIONS:
        sys.stderr.write("no server running on /tmp/tmux-0/cursor-agent\\n")
        sys.exit(1)
    sys.stdout.write(SESSIONS)
    sys.exit(0)
if rest[:1] == ["send-keys"]:
    if "-l" in rest:
        with open(LOG + ".typed", "w") as fh:
            fh.write(rest[rest.index("-l") + 1])
        sys.exit(0)
    if "Enter" in rest and TURN_STARTS and os.path.exists(LOG + ".typed"):
        with open(LOG + ".typed") as fh:
            text = fh.read()
        con = sqlite3.connect(STORE)
        con.execute("insert into blobs values (?, ?)",
                    (uuid.uuid4().hex,
                     json.dumps({"role": "user", "content": text}).encode()))
        con.commit()
        con.close()
    sys.exit(0)
sys.exit(0)
'''


def _session_row(name=SESSION, managed="1", version="1", chat_id=CHAT_ID,
                 ws_hash=WS_HASH, attached="0"):
    return "\t".join([name, attached, managed, ws_hash, version, chat_id]) + "\n"


@pytest.fixture
def cursor_seat(tmp_path, monkeypatch, cache_dir):
    """A fake managed persist session plus the chat store it writes to."""
    monkeypatch.setenv("TICKETS_CACHE_DIR", cache_dir)
    monkeypatch.delenv("CURSOR_ACP_CONTROL_SOCK", raising=False)
    monkeypatch.delenv("CURSOR_PERSIST_SESSION", raising=False)
    monkeypatch.delenv("CURSOR_AGENT_PERSIST_SESSION", raising=False)
    chats = tmp_path / "chats" / WS_HASH / CHAT_ID
    chats.mkdir(parents=True)
    store = chats / "store.db"
    con = sqlite3.connect(str(store))
    con.execute("create table blobs (id TEXT PRIMARY KEY, data BLOB)")
    con.commit()
    con.close()
    monkeypatch.setenv("CURSOR_CHATS_DIR", str(tmp_path / "chats"))
    log = tmp_path / "tmux.log"

    def install(sessions=None, turn_starts=True):
        binary = tmp_path / "fake-tmux"
        binary.write_text(FAKE_TMUX % {
            "log": str(log), "store": str(store), "turn": bool(turn_starts),
            "sessions": _session_row() if sessions is None else sessions})
        binary.chmod(binary.stat().st_mode | stat.S_IEXEC)
        monkeypatch.setenv("CURSOR_AGENT_TMUX_PATH", str(binary))
        return binary

    def calls():
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text().splitlines() if line.strip()]

    return {"install": install, "calls": calls, "store": str(store), "log": log}


def _endpoint(sa, board, seat="grok-worker", **extra):
    record = {"seat": seat, "provider": "cursor", "mode": "native",
              "session_id": CHAT_ID, "chat_id": CHAT_ID, "workspace_hash": WS_HASH,
              "persist_session": SESSION, "pid": os.getpid(), "at": "now",
              "heartbeat_epoch": time.time()}
    record.update(extra)
    sa.write_endpoint(str(board), seat, record)
    return record


def test_cursor_wake_targets_the_managed_tmux_server(board, cursor_seat):
    sa = _adapters()
    cursor_seat["install"]()
    _endpoint(sa, board)
    label = sa.wake_seat(str(board), "grok-worker", "wake now", harness="cursor")
    assert label == "woken", label
    calls = cursor_seat["calls"]()
    # Every call carries the managed-server flags; the default tmux server (and
    # whatever session of the operator's happens to share the name) is never
    # addressed.
    for argv in calls:
        assert argv[:5] == ["-u", "-L", "cursor-agent", "-f", "/dev/null"], argv
    typed = [a for a in calls if "send-keys" in a and "-l" in a][0]
    assert typed[5:9] == ["send-keys", "-t", SESSION + ":", "-l"]
    assert typed[9] == "wake now"
    assert [a for a in calls if a[-1] == "Enter"], calls


def test_cursor_wake_without_turn_evidence_is_not_woken(board, cursor_seat):
    """send-keys exits 0 into a dead pane or a modal too. No turn, no wake.

    The keys did land in the pane, so this is T-857's `delivered-unconfirmed`,
    not a failure: no fabricated `woken`, and no heartbeat bump, because
    nothing proved a turn started. The message id is still recorded as
    delivered so a re-poke is deduped -- Cursor has no receiver-side dedupe
    the way Claude Code does, so resending would type the line into the pane a
    second time. Mail is not at risk either way: the board message is durable
    in messages.jsonl and `has_live_native_session` stays False here, so the
    watch fallback still reaches the seat.
    """
    sa = _adapters()
    cursor_seat["install"](turn_starts=False)
    _endpoint(sa, board)
    before = sa.read_endpoint(str(board), "grok-worker")["heartbeat_epoch"]
    label = sa.wake_seat(str(board), "grok-worker", "wake now", harness="cursor",
                         message_id="m-1")
    assert label == "delivered-unconfirmed", label
    after = sa.read_endpoint(str(board), "grok-worker")
    assert after["heartbeat_epoch"] == before
    assert after.get("last_delivery_status") == "delivered-unconfirmed"
    assert after.get("last_delivery_id") == "m-1"


def test_cursor_unconfirmed_delivery_is_not_retyped_into_the_pane(board, cursor_seat):
    """The same message id must never be typed twice into a Cursor pane."""
    sa = _adapters()
    cursor_seat["install"](turn_starts=False)
    _endpoint(sa, board)
    sa.wake_seat(str(board), "grok-worker", "wake now", harness="cursor",
                 message_id="m-1")
    typed_once = [a for a in cursor_seat["calls"]() if "send-keys" in a and "-l" in a]
    assert typed_once, "first wake should have typed the line"
    label = sa.wake_seat(str(board), "grok-worker", "wake now", harness="cursor",
                         message_id="m-1")
    assert label == "deduped", label
    typed_twice = [a for a in cursor_seat["calls"]() if "send-keys" in a and "-l" in a]
    assert typed_twice == typed_once, typed_twice


def test_cursor_never_types_into_an_unmanaged_session(board, cursor_seat):
    """A same-named session that cursor-agent does not own is not a seat."""
    sa = _adapters()
    cursor_seat["install"](sessions=_session_row(managed=""))
    _endpoint(sa, board)
    label = sa.wake_seat(str(board), "grok-worker", "wake now", harness="cursor")
    assert label.startswith("supervised"), label
    assert not [a for a in cursor_seat["calls"]() if "send-keys" in a]


def test_cursor_payload_is_typed_as_one_line(board, cursor_seat):
    """A literal newline in the payload would submit half a prompt."""
    sa = _adapters()
    cursor_seat["install"]()
    _endpoint(sa, board)
    payload = sa.wake_payload(lambda m: m, "board mail")
    assert "\n" in payload
    label = sa.wake_seat(str(board), "grok-worker", payload, harness="cursor")
    assert label == "woken", label
    typed = [a for a in cursor_seat["calls"]() if "-l" in a][0]
    text = typed[typed.index("-l") + 1]
    assert "\n" not in text
    assert "board mail" in text and "atm inbox" in text


def test_cursor_gone_persist_session_is_not_reachable(board, cursor_seat):
    sa = _adapters()
    cursor_seat["install"](sessions="")
    _endpoint(sa, board)
    assert sa.has_live_native_session(str(board), "grok-worker") is False
    ep, _ = sa.live_endpoint(str(board), "grok-worker")
    assert ep["mode"] == "supervised"
    label = sa.wake_seat(str(board), "grok-worker", "wake now", harness="cursor")
    assert label.startswith("supervised"), label


def test_cursor_has_no_default_acp_control_socket(monkeypatch):
    """The old default path was invented; `agent acp` is a stdio server."""
    monkeypatch.delenv("CURSOR_ACP_CONTROL_SOCK", raising=False)
    sa = _adapters()
    assert sa._cursor_acp_sock() == ""
    assert sa.default_cursor_acp_socket() == ""


def test_cursor_wake_is_the_same_from_a_codex_and_a_claude_sender(board, cursor_seat,
                                                                  cache_dir, tmp_path):
    """Any-to-any: the receipt is the recipient's transport, not the sender's."""
    sa = _adapters()
    cursor_seat["install"]()
    env = {"TICKETS_CACHE_DIR": cache_dir,
           "CURSOR_CHATS_DIR": str(tmp_path / "chats"),
           "CURSOR_AGENT_TMUX_PATH": os.environ["CURSOR_AGENT_TMUX_PATH"]}
    _run(board, "join", "cursor-recipient", "--roles", "backend", "--harness", "cursor",
         agent="cursor-recipient", env=env)
    receipts = {}
    for sender, harness in (("codex-sender", "codex"), ("claude-sender", "claude")):
        _run(board, "join", sender, "--roles", "backend", "--harness", harness,
             agent=sender, env=env)
        _endpoint(sa, board, seat="cursor-recipient")
        r = _run(board, "msg", "ping %s" % sender, "--to", "cursor-recipient", "--task",
                 agent=sender, env=env)
        line = [x for x in r.stdout.splitlines() if x.startswith("wake: cursor-recipient")]
        assert line, r.stdout + r.stderr
        receipts[sender] = line[0]
    assert receipts["codex-sender"] == receipts["claude-sender"], receipts
    assert receipts["codex-sender"].endswith("-> woken"), receipts


def test_cursor_wake_is_the_same_into_a_busy_and_an_idle_pane(board, cursor_seat):
    """The other axis of any-to-any: a recipient mid-turn gets the same receipt.

    `session_attached` is the only busy/idle signal tmux offers, and
    `cursor_persist_sessions` does parse it, into `row["attached_clients"]`.
    Nothing ever reads it back, so an attached pane (agent mid-turn, operator
    watching) and a detached idle one cannot diverge. That is worth pinning
    rather than assuming: it is what makes the live idle/busy cells
    interchangeable, and a future caller that started gating a wake on
    "looks busy" would silently reintroduce a dropped wake.
    """
    sa = _adapters()
    receipts, typed, seen = {}, {}, {}
    for state, attached, mid in (("idle", "0", "m-idle"), ("busy", "3", "m-busy")):
        cursor_seat["install"](sessions=_session_row(attached=attached))
        # Also refreshes the TTL cache, so the second pass cannot read the
        # first pass's rows back out of it.
        rows = sa.cursor_persist_sessions(refresh=True)
        seen[state] = [r["attached_clients"] for r in rows]
        _endpoint(sa, board)
        before = len(cursor_seat["calls"]())
        receipts[state] = sa.wake_seat(str(board), "grok-worker", "wake now",
                                       harness="cursor", message_id=mid)
        fresh = cursor_seat["calls"]()[before:]
        typed[state] = [a for a in fresh if "send-keys" in a and "-l" in a]
    # The difference really is visible to the adapter ...
    assert seen == {"idle": [0], "busy": [3]}, seen
    # ... and it changes neither the receipt nor a single keystroke.
    assert receipts["idle"] == receipts["busy"] == "woken", receipts
    assert typed["idle"], "the idle pass should have typed the line"
    assert typed["idle"] == typed["busy"], typed


# ---- Agy: supervised by design (no live-session injection exists) ----------

def test_agy_wake_is_supervised_not_woken(board, cache_dir, monkeypatch):
    monkeypatch.setenv("TICKETS_CACHE_DIR", cache_dir)
    sa = _adapters()
    label = sa.wake_seat(str(board), "agy-worker", "hello", harness="agy")
    assert label.startswith("supervised (agy"), label
    assert "no live-session injection" in label


def test_agy_alias_antigravity_is_supervised(board, cache_dir, monkeypatch):
    monkeypatch.setenv("TICKETS_CACHE_DIR", cache_dir)
    sa = _adapters()
    label = sa.wake_seat(str(board), "agy-worker", "hello", harness="antigravity")
    assert label.startswith("supervised (antigravity"), label


def test_agy_register_persistent_explains_the_supervised_seat(board, cache_dir,
                                                              monkeypatch):
    monkeypatch.setenv("TICKETS_CACHE_DIR", cache_dir)
    sa = _adapters()
    reg = sa.register_persistent(str(board), "agy-worker", "agy", "now")
    assert reg.get("ok") is False
    reason = reg.get("reason", "")
    assert "supervised seat" in reason
    assert "remote-control" in reason and "hooks" in reason
    assert sa.read_endpoint(str(board), "agy-worker") is None


def test_agy_probe_reports_a_transport_instead_of_no_adapter():
    sa = _adapters()
    probe = sa.probe_provider("agy")
    assert probe.get("ok") is True
    caps = probe["capabilities"]
    assert caps["native_inject"] is False
    assert caps["supervised"] is True
    assert "hooks" in caps["transport"]
