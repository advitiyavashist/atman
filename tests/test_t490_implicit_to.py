"""T-490: implicit --to only for registered agents; unknown @handles broadcast."""
import json
import time

from test_wakeup import board, run  # noqa: F401


def _last(board):
    return json.loads((board / "messages.jsonl").read_text().splitlines()[-1])


def test_unknown_sha_mention_is_broadcast_with_warning(board):
    run(board, "join", "cursor", agent="cursor")
    time.sleep(1.1)
    r = run(board, "msg", "checked pin ce6042f as @ce6042f on the recut", agent="alice")
    assert r.returncode == 0, r.stderr
    assert "WARNING:" in r.stdout
    assert "ce6042f is not a registered agent" in r.stdout
    assert "message broadcast" in r.stdout
    assert "-> ce6042f" not in r.stdout
    rec = _last(board)
    assert rec.get("to") in ("", None)
    assert rec.get("mentions") == ["ce6042f"], rec
    assert "_unregistered_implicit" not in rec
    assert "checked pin" in run(board, "inbox", agent="cursor").stdout
    assert "checked pin" in run(board, "inbox", agent="bystander").stdout


def test_registered_mention_still_directs(board):
    run(board, "join", "cursor", agent="cursor")
    r = run(board, "msg", "@cursor take T-490", agent="alice")
    assert r.returncode == 0, r.stderr
    assert "WARNING:" not in r.stdout
    assert "-> cursor" in r.stdout
    rec = _last(board)
    assert rec.get("to") == "cursor"
    assert rec.get("mentions") == ["cursor"]
    assert "take T-490" in run(board, "inbox", agent="cursor").stdout
    assert "take T-490" not in run(board, "inbox", agent="bystander").stdout


def test_two_registered_mentions_stay_untargeted_to(board):
    run(board, "join", "cursor", agent="cursor")
    run(board, "join", "claude-fable", agent="claude-fable")
    r = run(board, "msg", "@cursor @claude-fable pair on this", agent="alice")
    assert r.returncode == 0, r.stderr
    assert "WARNING:" not in r.stdout
    assert "-> cursor" not in r.stdout and "-> claude-fable" not in r.stdout
    rec = _last(board)
    assert rec.get("to") in ("", None)
    assert rec.get("mentions") == ["cursor", "claude-fable"]
    for agent in ("cursor", "claude-fable"):
        assert "pair on this" in run(board, "inbox", agent=agent).stdout
    assert "pair on this" not in run(board, "inbox", agent="bystander").stdout


def test_explicit_to_unknown_name_directs_without_warning(board):
    run(board, "join", "cursor", agent="cursor")
    r = run(board, "msg", "probe mailbox", "--to", "deadbee", agent="alice")
    assert r.returncode == 0, r.stderr
    assert "WARNING:" not in r.stdout
    assert "-> deadbee" in r.stdout
    rec = _last(board)
    assert rec.get("to") == "deadbee"
    assert "probe mailbox" not in run(board, "inbox", agent="cursor").stdout
    assert "probe mailbox" not in run(board, "inbox", agent="bystander").stdout


def test_code_span_mention_is_still_ignored(board):
    run(board, "join", "cursor", agent="cursor")
    time.sleep(1.1)
    r = run(board, "msg", "see `@deadbee` for the pin, nothing else", agent="alice")
    assert r.returncode == 0, r.stderr
    assert "WARNING:" not in r.stdout
    rec = _last(board)
    assert rec.get("mentions") in (None, [])
    assert rec.get("to") in ("", None)
    assert "see `@deadbee`" in run(board, "inbox", agent="cursor").stdout
    assert "see `@deadbee`" in run(board, "inbox", agent="bystander").stdout
