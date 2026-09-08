"""Advitiya PRIORITY agent chats: per-seat / 1:1 threads on the existing
`tickets msg` log — no second store, no shared-memory brain.
"""
import importlib.util
import json
import os
import re
import urllib.error
import urllib.request

from test_t276 import _Server
from test_wakeup import TOOL, board, run  # noqa: F401


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
        "data-seat-chat", "data-testid=\"thread-board\"",
        "class=\"intervene\"", ">Msg<", ">Work<",
        "tickets msg --to", "Not a shared-memory brain",
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


def test_msg_api_to_seat_is_the_same_jsonl(board):
    run(board, "join", "cursor", agent="cursor")
    run(board, "join", "alice", agent="alice")
    srv = _Server(board, 18771)
    try:
        status, out = srv.post("/msg", {
            "from": "alice", "text": "seat scoped via composer", "to": "cursor",
        })
        assert status == 200 and out["ok"], out
        page = srv.get("/", raw=True).decode()
        assert 'id="chatRail"' in page
        assert "data-seat-chat" in page
        all_msgs = srv.get("/board.json")
        assert any(m["text"] == "seat scoped via composer" for m in all_msgs["messages"])
        scoped = srv.get("/board.json?seat=cursor")
        assert scoped.get("seat") == "cursor"
        assert any(m["text"] == "seat scoped via composer" for m in scoped["messages"])
        board_only = srv.get("/board.json?seat=")
        # empty seat query is the unfiltered snapshot
        assert "seat" not in board_only or board_only.get("seat") in ("", None)
    finally:
        srv.stop()

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
