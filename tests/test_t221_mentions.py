"""T-221 part A: `tickets msg` parses @handles in message text and treats
them as recipients in addition to --to, backward compatible with plain
--to / broadcast messages."""
import json

from test_wakeup import board, run  # noqa: F401


def test_mentioned_agent_sees_message_addressed_to_someone_else(board):
    run(board, "join", "alice", agent="alice")
    run(board, "join", "opus-backend", agent="opus-backend")
    run(board, "join", "cursor", agent="cursor")
    r = run(board, "msg", "please review, @cursor @claude-fable", "--to", "opus-backend", agent="alice")
    assert r.returncode == 0, r.stderr

    out = run(board, "inbox", agent="opus-backend").stdout
    assert "please review" in out

    out = run(board, "inbox", agent="cursor").stdout
    assert "please review" in out, "explicit --to must not crowd out an @mentioned agent"

    out = run(board, "inbox", agent="bystander").stdout
    assert "please review" not in out, "an agent neither --to nor @mentioned must not see it"


def test_bare_mention_with_no_explicit_to_becomes_the_recipient(board):
    run(board, "join", "cursor", agent="cursor")
    r = run(board, "msg", "@cursor take T-221", agent="alice")
    assert r.returncode == 0, r.stderr
    assert "-> cursor" in r.stdout, r.stdout

    out = run(board, "inbox", agent="cursor").stdout
    assert "take T-221" in out
    out = run(board, "inbox", agent="bystander").stdout
    assert "take T-221" not in out


def test_multiple_mentions_reach_only_the_named_agents(board):
    run(board, "join", "cursor", agent="cursor")
    run(board, "join", "claude-fable", agent="claude-fable")
    r = run(board, "msg", "@cursor @claude-fable pair on this", agent="alice")
    assert r.returncode == 0, r.stderr
    assert "-> cursor" not in r.stdout and "-> claude-fable" not in r.stdout, (
        "two named mentions is ambiguous for a single --to; it must not silently pick one"
    )
    for agent in ("cursor", "claude-fable"):
        assert "pair on this" in run(board, "inbox", agent=agent).stdout
    assert "pair on this" not in run(board, "inbox", agent="bystander").stdout, (
        "named mentions are targeted recipients, not a broadcast to everyone"
    )


def test_everyone_mention_is_broadcast_not_a_literal_recipient(board):
    run(board, "join", "cursor", agent="cursor")
    r = run(board, "msg", "@everyone standup in 5", agent="alice")
    assert r.returncode == 0, r.stderr
    assert run(board, "inbox", agent="cursor").stdout.count("standup in 5") == 1
    assert "standup in 5" in run(board, "inbox", agent="anyone-at-all").stdout


def test_explicit_to_always_wins_over_mention_autoresolve(board):
    run(board, "join", "opus-backend", agent="opus-backend")
    run(board, "join", "cursor", agent="cursor")
    r = run(board, "msg", "@cursor fyi", "--to", "opus-backend", agent="alice")
    assert r.returncode == 0
    assert "-> opus-backend" in r.stdout
    assert "fyi" in run(board, "inbox", agent="opus-backend").stdout
    assert "fyi" in run(board, "inbox", agent="cursor").stdout


def test_email_and_double_at_are_not_parsed_as_mentions(board):
    run(board, "join", "cursor", agent="cursor")
    r = run(board, "msg", "reach ops@example.com about @@not-a-handle", agent="alice")
    assert r.returncode == 0
    rec = json.loads((board / "messages.jsonl").read_text().splitlines()[-1])
    assert "mentions" not in rec or rec["mentions"] == []


def test_mention_inside_code_span_is_not_a_real_mention(board):
    run(board, "join", "opus-backend-2", agent="opus-backend-2")
    run(board, "join", "cursor", agent="cursor")
    r = run(board, "msg", "see `@opus-backend-2` for context, ping @cursor instead", agent="alice")
    assert r.returncode == 0, r.stderr
    rec = json.loads((board / "messages.jsonl").read_text().splitlines()[-1])
    assert rec.get("mentions") == ["cursor"], rec

    out = run(board, "inbox", agent="opus-backend-2").stdout
    assert "see `@opus-backend-2`" not in out, "a backtick-quoted mention must not false-ping"

    r = run(board, "msg", "example: ```\ntickets msg \"@cursor take this\"\n```", agent="alice")
    assert r.returncode == 0, r.stderr
    rec = json.loads((board / "messages.jsonl").read_text().splitlines()[-1])
    assert rec.get("mentions") in (None, []), rec


def test_mentioned_agent_counted_as_direct_by_pending(board):
    run(board, "join", "cursor", agent="cursor")
    run(board, "msg", "@cursor please take this", agent="alice")
    r = run(board, "pending", "--agent", "cursor", "--json")
    p = json.loads(r.stdout)
    assert "messages_to_me" in p, p
    assert any("please take this" in m for m in p["messages_to_me"])
