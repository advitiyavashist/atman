"""T-947: Codex thread/loaded/list data[] + turn/start when idle (no queue)."""
from unittest import mock

import session_adapters as sa


def test_loaded_result_reads_string_data_ids():
    resp = {"result": {"data": ["t1"], "nextCursor": "c2"}}
    assert sa._thread_ids_from_loaded_result(resp) == ["t1"]
    assert sa._loaded_next_cursor(resp) == "c2"


def test_loaded_result_still_reads_id_objects():
    resp = {"result": {"data": [{"id": "thread-live"}]}}
    assert "thread-live" in sa._thread_ids_from_loaded_result(resp)


def test_loaded_list_follows_next_cursor():
    pages = [
        {"result": {"data": ["t1"], "nextCursor": "p2"}},
        {"result": {"data": ["t2"]}},
    ]

    def rpc(method, params, timeout=5):
        assert method == "thread/loaded/list"
        if params.get("cursor") == "p2":
            return pages[1]
        return pages[0]

    with mock.patch.object(sa, "_codex_app_server_rpc", side_effect=rpc):
        assert sa._codex_loaded_thread_ids() == ["t1", "t2"]
        assert sa._codex_thread_is_loaded("t2") is True
        assert sa._codex_thread_is_loaded("missing") is False


def test_idle_loaded_turn_start_without_queue():
    ep = {"thread": "t-idle"}
    queues = []

    def rpc(method, params, timeout=5):
        if method == "thread/loaded/list":
            return {"result": {"data": ["t-idle"]}}
        if method == "thread/read":
            return {"result": {"status": "idle"}}
        if method == "turn/start":
            assert params["threadId"] == "t-idle"
            assert params["input"] == [{"type": "text", "text": "hello"}]
            assert params["clientUserMessageId"] == "m-9"
            return {"result": {"ok": True}}
        raise AssertionError(method)

    with mock.patch.object(sa, "_codex_app_server_rpc", side_effect=rpc):
        with mock.patch.object(sa, "_codex_queue_cli", side_effect=lambda *a: queues.append(a) or True):
            label = sa._poke_codex_wake(ep, "hello", message_id="m-9")
    assert label == "woken"
    assert queues == []


def test_busy_loaded_queues_and_does_not_turn_start():
    ep = {"thread": "t-busy"}
    turns = []
    queues = []

    def rpc(method, params, timeout=5):
        if method == "thread/loaded/list":
            return {"result": {"data": ["t-busy"]}}
        if method == "thread/read":
            return {"result": {"turn": {"status": "inProgress"}}}
        if method == "turn/start":
            turns.append(params)
            return {"result": {"ok": True}}
        raise AssertionError(method)

    with mock.patch.object(sa, "_codex_app_server_rpc", side_effect=rpc):
        with mock.patch.object(sa, "_codex_queue_cli", side_effect=lambda *a: queues.append(a) or True):
            label = sa._poke_codex_wake(ep, "later")
    assert label == "queued-busy"
    assert queues == [("t-busy", "later")]
    assert turns == []


def test_unloaded_queues_offline_no_turn_start():
    ep = {"thread": "t-vs"}
    turns = []

    def rpc(method, params, timeout=5):
        if method == "thread/loaded/list":
            return {"result": {"data": []}}
        if method == "turn/start":
            turns.append(params)
            return {"result": {"ok": True}}
        return {"error": {"message": "no"}}

    with mock.patch.object(sa, "_codex_app_server_rpc", side_effect=rpc):
        with mock.patch.object(sa, "_codex_queue_cli", return_value=True):
            label = sa._poke_codex_wake(ep, "hi")
    assert label == "queued-offline"
    assert turns == []


def test_should_not_persist_poke_queued_busy():
    import tickets as tk
    assert tk._should_poke_persist("queued-busy") is False
    assert tk._should_poke_persist("woken") is False
    assert tk._should_poke_persist("queued-offline") is True
