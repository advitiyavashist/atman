"""T-497: warn unknown --to, split comma --to, filter unknown mentions first."""
import json
import time

from test_wakeup import board, run  # noqa: F401


def _last(board):
    return json.loads((board / "messages.jsonl").read_text().splitlines()[-1])


def test_explicit_unknown_to_warns_and_still_delivers(board):
    run(board, "join", "cursor", agent="cursor")
    r = run(board, "msg", "probe mailbox", "--to", "deadbee", agent="alice")
    assert r.returncode == 0, r.stderr
    assert "WARNING:" in r.stdout
    assert "--to deadbee is not a registered agent" in r.stdout
    assert "message broadcast" not in r.stdout
    assert "-> deadbee" in r.stdout
    rec = _last(board)
    assert rec.get("to") == "deadbee"
    assert "_unregistered_explicit" not in rec
    assert "probe mailbox" not in run(board, "inbox", agent="cursor").stdout
    assert "probe mailbox" not in run(board, "inbox", agent="bystander").stdout


def test_comma_to_delivers_each_registered_handle(board):
    run(board, "join", "cursor", agent="cursor")
    run(board, "join", "claude-fable", agent="claude-fable")
    r = run(board, "msg", "pair please", "--to", "cursor,claude-fable", agent="alice")
    assert r.returncode == 0, r.stderr
    assert "WARNING:" not in r.stdout
    rec = _last(board)
    assert rec.get("to") == "cursor,claude-fable"
    assert "pair please" in run(board, "inbox", agent="cursor").stdout
    assert "pair please" in run(board, "inbox", agent="claude-fable").stdout
    assert "pair please" not in run(board, "inbox", agent="bystander").stdout


def test_comma_to_warns_unknown_and_delivers_registered(board):
    run(board, "join", "cursor", agent="cursor")
    r = run(board, "msg", "split probe", "--to", "cursor,no-such", agent="alice")
    assert r.returncode == 0, r.stderr
    assert "WARNING:" in r.stdout
    assert "--to no-such is not a registered agent" in r.stdout
    rec = _last(board)
    assert rec.get("to") == "cursor,no-such"
    assert "split probe" in run(board, "inbox", agent="cursor").stdout
    assert "split probe" not in run(board, "inbox", agent="bystander").stdout


def test_one_registered_plus_unknown_mention_directs_and_warns(board):
    run(board, "join", "cursor", agent="cursor")
    r = run(board, "msg", "ping @cursor about @ce6042f", agent="alice")
    assert r.returncode == 0, r.stderr
    assert "WARNING:" in r.stdout
    assert "@handle ce6042f is not a registered agent" in r.stdout
    assert "directed to cursor" in r.stdout
    assert "message broadcast" not in r.stdout
    assert "-> cursor" in r.stdout
    rec = _last(board)
    assert rec.get("to") == "cursor"
    assert rec.get("mentions") == ["cursor", "ce6042f"], rec
    assert "_unregistered_dropped" not in rec
    assert "ping @cursor" in run(board, "inbox", agent="cursor").stdout
    assert "ping @cursor" not in run(board, "inbox", agent="bystander").stdout


def test_to_master_aliases_to_master_json_owner(board):
    run(board, "join", "cursor", agent="cursor")
    (board / "master.json").write_text(json.dumps({"owner": "cursor"}))
    r = run(board, "msg", "need a merge", "--to", "master", agent="alice")
    assert r.returncode == 0, r.stderr
    assert "WARNING:" not in r.stdout
    rec = _last(board)
    assert rec.get("to") == "cursor"
    assert "need a merge" in run(board, "inbox", agent="cursor").stdout
    assert "need a merge" not in run(board, "inbox", agent="bystander").stdout


def test_pre_join_comma_to_still_delivers_after_join(board):
    time.sleep(1.1)
    r = run(board, "msg", "brief before join", "--to", "cursor,claude-fable", agent="alice")
    assert r.returncode == 0, r.stderr
    run(board, "join", "cursor", agent="cursor")
    assert "brief before join" in run(board, "inbox", agent="cursor").stdout
    assert "brief before join" not in run(board, "inbox", agent="bystander").stdout
