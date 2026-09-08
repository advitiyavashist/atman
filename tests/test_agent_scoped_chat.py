"""Advitiya PRIORITY agent chats: per-seat / 1:1 threads on the existing
`tickets msg` log — no second store, no shared-memory brain.
"""
import importlib.util
import json
import os
import re
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

_TESTS = Path(__file__).resolve().parent
if str(_TESTS) not in sys.path:
    sys.path.insert(0, str(_TESTS))

from test_wakeup import TOOL, board, run  # noqa: E402,F401
from ui_server_harness import UiServer, _free_port, make_ui_server_fixture

ui_server = make_ui_server_fixture("t546-probe")


def _tickets():
    spec = importlib.util.spec_from_file_location("tickets_agent_chat", TOOL)
    tk = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tk)
    return tk


def _ui_html():
    text = TOOL.read_text(encoding="utf-8")
    start = text.index('UI_HTML = r"""') + len('UI_HTML = r"""')
    end = text.index('"""', start)
    return text[start:end]


def test_scope_helpers_split_board_from_seat_thread():
    tk = _tickets()
    board_msg = {"from": "alice", "to": "", "text": "hello everyone", "mentions": []}
    everyone = {"from": "alice", "to": "", "text": "@everyone standup", "mentions": ["everyone"]}
    dm = {"from": "alice", "to": "cursor", "text": "take this", "mentions": []}
    mention = {"from": "alice", "to": "", "text": "@cursor look", "mentions": ["cursor"]}
    reply = {"from": "cursor", "to": "alice", "text": "on it", "mentions": []}
    cursor_broadcast = {"from": "cursor", "to": "", "text": "joining", "mentions": []}

    assert tk.is_board_broadcast(board_msg)
    assert tk.is_board_broadcast(everyone)
    assert not tk.is_board_broadcast(dm)
    assert not tk.is_board_broadcast(mention)

    assert tk.message_involves_seat(dm, "cursor")
    assert tk.message_involves_seat(mention, "cursor")
    assert tk.message_involves_seat(reply, "cursor")
    assert tk.message_involves_seat(reply, "alice")
    assert not tk.message_involves_seat(board_msg, "cursor")
    assert not tk.message_involves_seat(cursor_broadcast, "cursor"), (
        "a seat's own board broadcast stays on the Board thread"
    )

    scoped = tk.filter_messages_for_scope(
        [board_msg, everyone, dm, mention, reply, cursor_broadcast], "cursor")
    assert [m["text"] for m in scoped] == ["take this", "@cursor look", "on it"]
    board_only = tk.filter_messages_for_scope(
        [board_msg, everyone, dm, mention, reply, cursor_broadcast], "")
    assert [m["text"] for m in board_only] == ["hello everyone", "@everyone standup", "joining"]


def test_inbox_seat_lists_only_that_thread_and_does_not_mark_read(board):
    run(board, "join", "cursor", agent="cursor")
    run(board, "join", "alice", agent="alice")
    run(board, "msg", "hello everyone", agent="alice")
    run(board, "msg", "please take the seat thread", "--to", "cursor", agent="alice")
    run(board, "msg", "@cursor mentioned too", agent="alice")

    seat = run(board, "inbox", "--seat", "cursor", "--keep")
    assert seat.returncode == 0, seat.stderr
    assert "seat thread · cursor" in seat.stdout
    assert "please take the seat thread" in seat.stdout
    assert "mentioned too" in seat.stdout
    assert "hello everyone" not in seat.stdout
    assert "same messages.jsonl" in seat.stdout

    empty = run(board, "inbox", "--seat", "nobody-here")
    assert empty.returncode == 0
    assert "no messages in nobody-here's seat thread" in empty.stdout

    # --seat must not consume cursor's unread watermark.
    unread = run(board, "inbox", "--keep", agent="cursor")
    assert "please take the seat thread" in unread.stdout
    assert "mentioned too" in unread.stdout


def test_board_snapshot_seat_threads_and_optional_seat_filter(board):
    run(board, "join", "cursor", agent="cursor")
    run(board, "join", "alice", agent="alice")
    run(board, "master", "take", agent="boss")
    run(board, "msg", "channel ping", agent="alice")
    run(board, "msg", "only for cursor", "--to", "cursor", agent="alice")

    snap = json.loads(run(board, "ui", "--json").stdout)
    assert "seat_threads" in snap
    assert snap["seat_threads"]["board"]["kind"] == "board"
    assert snap["seat_threads"]["board"]["n"] >= 1
    names = [s["name"] for s in snap["seat_threads"]["seats"]]
    assert "cursor" in names and "alice" in names
    cursor = next(s for s in snap["seat_threads"]["seats"] if s["name"] == "cursor")
    assert cursor["n"] >= 1
    assert cursor["last_at"]
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", cursor["last_at"]), cursor["last_at"]
    # Full log stays on messages (backward compatible); seat filter is opt-in.
    assert any(m["text"] == "only for cursor" for m in snap["messages"])
    assert any(m["text"] == "channel ping" for m in snap["messages"])

    tk = _tickets()
    scoped = tk.board_snapshot_for_request(str(board), seat="cursor")
    assert scoped["seat"] == "cursor"
    texts = [m["text"] for m in scoped["messages"]]
    assert "only for cursor" in texts
    assert "channel ping" not in texts
    for m in scoped["messages"]:
        assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", m["at"]), m["at"]


def test_ui_html_has_seat_thread_ia_without_football():
    ui = _ui_html()
    for marker in (
        'id="chatRail"', 'id="chatHead"', "openSeatChat", "involvesSeat",
        "isBoardBroadcast", "visibleMessages", "seat_threads",
        "data-seat-chat", "it.id||'board'",
        "class=\"intervene\"", ">Msg<", ">Work<",
        "tickets msg --to",
        "Channel-wide", "1:1 with this BYOA seat",
        "Intervene ·",
    ):
        assert marker in ui, "missing UI marker: %s" % marker
    banned = (
        "Total Football", "total football", "football", "goalpost",
        "keeper", "shirts", "midfield", "on the pitch", "renderPitch",
        "mandala", "awakening",
    )
    for word in banned:
        assert word not in ui, "retired sports/kitsch copy: %s" % word
    # Same composer path — no second chat POST.
    assert "fetch('/msg'" in ui
    assert "fmtWhen(m.at)" in ui


def test_msg_api_to_seat_is_the_same_jsonl(board, ui_server):
    run(board, "join", "cursor", agent="cursor")
    run(board, "join", "alice", agent="alice")
    status, out = ui_server.post("/msg", {
        "from": "alice", "text": "seat scoped via composer", "to": "cursor",
    })
    assert status == 200 and out["ok"], out
    page = ui_server.get("/", raw=True).decode()
    assert 'id="chatRail"' in page
    assert "data-seat-chat" in page
    all_msgs = ui_server.get("/board.json")
    assert any(m["text"] == "seat scoped via composer" for m in all_msgs["messages"])
    scoped = ui_server.get("/board.json?seat=cursor")
    assert scoped.get("seat") == "cursor"
    assert any(m["text"] == "seat scoped via composer" for m in scoped["messages"])
    board_only = ui_server.get("/board.json?seat=")
    # empty seat query is the unfiltered snapshot
    assert "seat" not in board_only or board_only.get("seat") in ("", None)

    live = (board / "messages.jsonl").read_text()
    assert "seat scoped via composer" in live
    rec = json.loads(live.strip().splitlines()[-1])
    assert rec["to"] == "cursor"
    assert rec["from"] == "alice"
    assert rec["at"].endswith("Z")


def test_inbox_seat_survives_join_broadcasts(board):
    """Join posts a channel-wide 'joined the board' line; it is not 1:1 mail."""
    run(board, "join", "cursor", agent="cursor")
    out = run(board, "inbox", "--seat", "cursor").stdout
    assert "joined the board" not in out
    assert "no messages in cursor's seat thread" in out


def test_ceo_pm_lock_seat_chat_writes_only_messages_jsonl(board):
    """CEO/PM PRIORITY lock: chat stays on msg/inbox. No brain, no vector store."""
    run(board, "join", "cursor", agent="cursor")
    (board / "briefs").mkdir(exist_ok=True)
    brief = board / "briefs" / "cursor.md"
    brief.write_text("standing: review the backend lane\n")
    before_brief = brief.read_text()
    before_names = set(p.name for p in board.iterdir())

    run(board, "msg", "lock: stay on this seat", "--to", "cursor", agent="alice")
    listed = run(board, "inbox", "--seat", "cursor")
    assert listed.returncode == 0
    assert "lock: stay on this seat" in listed.stdout

    assert (board / "messages.jsonl").is_file()
    recs = [json.loads(ln) for ln in (board / "messages.jsonl").read_text().splitlines() if ln.strip()]
    assert any(r.get("to") == "cursor" and "lock: stay on this seat" in r.get("text", "") for r in recs)
    assert brief.read_text() == before_brief, "seat chat must not write .tickets/briefs/"
    created = set(p.name for p in board.iterdir()) - before_names
    assert not created.intersection({
        "memory.jsonl", "brain.json", "vectors.json", "embeddings.jsonl", "chroma",
    })
    for banned in ("chroma", "faiss", "pinecone", "vector.db", "shared-memory"):
        assert not (board / banned).exists()
    ia = (TOOL.parent / "docs" / "product" / "atman-team-ia-v1.md").read_text()
    assert "CEO/PM PRIORITY lock" in ia
    assert "Not a shared-memory brain" in ia or "not a shared-memory brain" in ia.lower()
    assert "vector DB" in ia
    assert ".tickets/briefs/" in ia


def test_wait_up_rejects_foreign_server_on_same_port(board, monkeypatch):
    """Mutation (b): a stray ui on our port must fail at _wait_up, not at content asserts."""
    foreign_repo = board.parent.parent / "foreign-repo-t546"
    foreign_repo.mkdir(exist_ok=True)
    subprocess.run(["git", "init", "-q", str(foreign_repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(foreign_repo), "commit", "-q", "--allow-empty", "-m", "init"],
                   check=True, env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                                        GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"))
    fb = foreign_repo / ".tickets"
    run(fb, "create", "foreign", "--role", "backend", cwd=foreign_repo)
    port = _free_port()
    stray = subprocess.Popen(
        [sys.executable, str(TOOL), "ui", "--port", str(port), "--host", "127.0.0.1"],
        env=dict(os.environ, TICKETS_DIR=str(fb)),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                urllib.request.urlopen("http://127.0.0.1:%d/board.json" % port, timeout=1)
                break
            except (urllib.error.URLError, ConnectionError):
                time.sleep(0.1)
        monkeypatch.setattr("ui_server_harness._free_port", lambda: port)
        with pytest.raises(RuntimeError, match="foreign tickets ui"):
            UiServer(board, probe_prefix="t546-probe")
    finally:
        try:
            os.killpg(os.getpgid(stray.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            stray.terminate()
        stray.wait(timeout=5)
