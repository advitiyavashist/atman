"""T-396: tokens and cost on run_end, so "fewest turns at LEAST COST" is measurable.

Runs the real CLI as a subprocess against a throwaway board and a throwaway
HOME (test_wakeup.run already isolates TICKETS_DIR and HOME), so these tests
never read the live trajectory log or the operator's real session stores.

The load-bearing claims are the ones a cost-learning layer would be silently
wrong about:
  - a run whose harness reported nothing is UNMEASURED, never $0.00;
  - a harness that reported something UNREADABLE is neither of those, and says so;
  - a cumulative counter (codex) is not summed into a multiplied bill;
  - one worktree's codex session is never billed to another agent.
"""

import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from test_wakeup import board, run  # noqa: F401
from test_trajectories import events, worked  # noqa: F401

TOOL = Path(__file__).resolve().parents[1] / "tickets.py"

# `claude -p --output-format json` final result object.
CLAUDE_RESULT = ('{"type":"result","num_turns":3,"total_cost_usd":0.25,'
                 '"duration_ms":4200,"usage":{"input_tokens":11,"output_tokens":22,'
                 '"cache_read_input_tokens":33,"cache_creation_input_tokens":44}}')


def _harness(tmp_path, name, body):
    """A fake harness whose FILE NAME is what the watcher reads the harness
    from (_harness_of_cmd takes the basename), so `claude` and `codex` here
    exercise the real per-harness branches."""
    d = tmp_path / ("bin_" + name)
    d.mkdir(exist_ok=True)
    p = d / name
    p.write_text("#!/bin/sh\n" + body)
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return p


def _watch(b, repo, fake):
    return run(b, "watch", "--agent", "alice", "--once", "--exec", str(fake),
               "--cwd", str(repo), agent="alice", cwd=repo)


def _slug(path):
    """The Claude CLI's project-directory slug: the absolute path with every
    non-alphanumeric character replaced by '-'. Verified against a real
    ~/.claude/projects entry."""
    return re.sub(r"[^A-Za-z0-9]", "-", os.path.abspath(str(path)))


def _end(b):
    e = events(b, kind="run_end")
    assert e, "no run_end recorded"
    return e[-1]


NO_USAGE_KEYS = ("tokens_in", "tokens_out", "tokens_cache_read",
                 "tokens_cache_write", "cost_usd", "cost_source")


# ---- stdout: the harness was asked for a JSON output format --------------

def test_claude_result_object_gives_tokens_cost_and_a_harness_cost_source(worked, tmp_path):
    b = worked
    fake = _harness(tmp_path, "claude", "echo chatter\necho '%s'\n" % CLAUDE_RESULT)
    _watch(b, b.parent, fake)
    e = _end(b)
    assert e["tokens_in"] == 11 and e["tokens_out"] == 22
    assert e["tokens_cache_read"] == 33 and e["tokens_cache_write"] == 44
    assert e["cost_usd"] == 0.25 and e["turns"] == 3
    # The only source on this board that reports a cost the harness itself
    # computed. Anything else must not claim 'harness'.
    assert e["cost_source"] == "harness"
    assert "usage_error" not in e


def test_codex_json_stream_gives_tokens_and_no_invented_cost(worked, tmp_path):
    """`codex exec --json` streams token_count events. They are CUMULATIVE, so
    the last one is the answer -- and codex reports no cost at all, so cost
    must stay absent rather than be estimated from tokens."""
    b = worked
    stream = "\n".join([
        '{"timestamp":"2026-09-08T02:00:00.000Z","type":"event_msg","payload":'
        '{"type":"token_count","info":{"total_token_usage":{"input_tokens":100,'
        '"output_tokens":10,"cached_input_tokens":50,"cache_write_input_tokens":5}}}}',
        '{"timestamp":"2026-09-08T02:00:09.000Z","type":"event_msg","payload":'
        '{"type":"token_count","info":{"total_token_usage":{"input_tokens":300,'
        '"output_tokens":30,"cached_input_tokens":150,"cache_write_input_tokens":15}}}}',
    ])
    fake = _harness(tmp_path, "codex", "cat <<'EOF'\n%s\nEOF\n" % stream)
    _watch(b, b.parent, fake)
    e = _end(b)
    # 300, not 400: summing a cumulative counter would overstate the bill by
    # one full turn's tokens every turn.
    assert e["tokens_in"] == 300 and e["tokens_out"] == 30
    assert e["tokens_cache_read"] == 150 and e["tokens_cache_write"] == 15
    assert "cost_usd" not in e and "cost_source" not in e


# ---- reported nothing vs reported something unreadable ------------------

def test_a_harness_that_reports_nothing_records_no_usage_field_at_all(worked, tmp_path):
    """Absence is the truth for the DEFAULT watch commands, which do not ask
    for a JSON output format. A zero here would make every real run look free."""
    b = worked
    fake = _harness(tmp_path, "claude", "echo 'just prose, no json'\n")
    _watch(b, b.parent, fake)
    e = _end(b)
    for k in NO_USAGE_KEYS:
        assert k not in e, "%s must be absent, not defaulted: %r" % (k, e)
    assert "usage_error" not in e, "nothing reported is not an error"
    assert e["exit"] == 0  # exit 0 IS recorded: zero is meaningful there


def test_a_malformed_usage_blob_is_recorded_as_an_error_not_a_silent_skip(worked, tmp_path):
    """The dangerous case: the harness DID report usage and the parser could
    not read it. Silently returning {} would file a real, billed run as
    unmeasured forever, and nothing downstream could tell the two apart."""
    b = worked
    bad = ('{"type":"result","total_cost_usd":"a lot",'
           '"usage":{"input_tokens":5,"output_tokens":6}}')
    fake = _harness(tmp_path, "claude", "echo '%s'\n" % bad)
    _watch(b, b.parent, fake)
    e = _end(b)
    assert "usage_error" in e, "an unreadable usage blob must say so: %r" % e
    assert "total_cost_usd" in e["usage_error"]
    for k in NO_USAGE_KEYS:
        assert k not in e, "%s must not be half-recorded from a bad blob" % k


def test_usage_is_not_read_out_of_a_boolean(worked, tmp_path):
    """`True` is an int in Python: without an explicit bool guard a
    "input_tokens": true would land in the log as a token count of 1."""
    b = worked
    bad = '{"type":"result","usage":{"input_tokens":true,"output_tokens":6}}'
    fake = _harness(tmp_path, "claude", "echo '%s'\n" % bad)
    _watch(b, b.parent, fake)
    e = _end(b)
    assert "usage_error" in e and e.get("tokens_in") is not True
    assert "tokens_in" not in e


# ---- the session store: what the DEFAULT watch commands actually leave ---

def _writes(path, fmt, *args):
    """A shell line that appends one JSON record stamped with the CURRENT time.

    printf with a single-quoted format keeps every inner double quote intact,
    which the earlier heredoc/echo forms did not: a mis-escaped $TS produced a
    quoted-inside-quotes timestamp that parsed as a string and silently
    dropped the record.
    """
    body = fmt % args
    return "printf '%s\\n' \"$TS\" >> \"%s\"\n" % (body, path)


CLAUDE_MSG = ('{"type":"assistant","timestamp":"%s","message":'
              '{"model":"claude-opus-5","usage":{"input_tokens":%d,'
              '"output_tokens":%d,"cache_read_input_tokens":7,'
              '"cache_creation_input_tokens":3}}}')

CODEX_META = ('{"timestamp":"%s","type":"session_meta","payload":'
              '{"type":"session_meta","cwd":"%s"}}')

CODEX_TC = ('{"timestamp":"%s","type":"event_msg","payload":'
            '{"type":"token_count","info":{"total_token_usage":'
            '{"input_tokens":%d,"output_tokens":%d,"cached_input_tokens":%d,'
            '"cache_write_input_tokens":%d}}}}')


def _store_body(dirpath, lines, tail="echo prose only\n"):
    return ('mkdir -p "%s"\n'
            'TS=$(date -u +%%Y-%%m-%%dT%%H:%%M:%%SZ)\n'
            % dirpath) + "".join(lines) + tail


def test_claude_transcript_supplies_tokens_when_stdout_carries_none(worked, tmp_path):
    """The real default command is `claude -p <prompt> --dangerously-skip-permissions`
    with no --output-format json, so stdout is prose and the stdout parser is
    dead. Measured on the live board before this ticket: 105 run_end records,
    0 with tokens. The counts are read from the transcript the CLI writes for
    itself instead -- without changing how the harness is invoked."""
    b = worked
    repo = b.parent
    d = "$HOME/.claude/projects/%s" % _slug(repo)
    fake = _harness(tmp_path, "claude", _store_body(d, [
        _writes(d + "/session.jsonl", CLAUDE_MSG, "%s", 1000, 200),
        _writes(d + "/session.jsonl", CLAUDE_MSG, "%s", 500, 100),
    ]))
    _watch(b, repo, fake)
    e = _end(b)
    # Per-message usage IS additive: each assistant message reports its own
    # request. 1000+500, 200+100.
    assert e["tokens_in"] == 1500 and e["tokens_out"] == 300, e
    assert e["tokens_cache_read"] == 14 and e["tokens_cache_write"] == 6
    # The transcript carries no cost, and none is invented from tokens.
    assert "cost_usd" not in e and "cost_source" not in e


def test_a_transcript_from_another_worktree_is_not_billed_to_this_agent(worked, tmp_path):
    """Two agents run on one box. The transcript directory is keyed by cwd,
    and reading the wrong one would bill one agent for another's tokens."""
    b = worked
    repo = b.parent
    other = "$HOME/.claude/projects/%s" % _slug(tmp_path / "somewhere-else")
    fake = _harness(tmp_path, "claude", _store_body(other, [
        _writes(other + "/session.jsonl", CLAUDE_MSG, "%s", 9999, 9999),
    ]))
    _watch(b, repo, fake)
    e = _end(b)
    for k in NO_USAGE_KEYS:
        assert k not in e, "another worktree's tokens leaked in as %s: %r" % (k, e)


def test_codex_rollout_supplies_the_last_cumulative_total_for_this_cwd(worked, tmp_path):
    b = worked
    repo = b.parent
    d = "$HOME/.codex/sessions/2026/09/08"
    fake = _harness(tmp_path, "codex", _store_body(d, [
        _writes(d + "/r.jsonl", CODEX_META, "%s", str(repo)),
        _writes(d + "/r.jsonl", CODEX_TC, "%s", 2000, 200, 0, 0),
        _writes(d + "/r.jsonl", CODEX_TC, "%s", 6000, 600, 0, 0),
    ]))
    _watch(b, repo, fake)
    e = _end(b)
    # 6000, not 8000: total_token_usage is cumulative for the session.
    assert e["tokens_in"] == 6000 and e["tokens_out"] == 600, e
    assert "cost_usd" not in e


def test_a_codex_rollout_that_names_another_cwd_is_not_billed_here(worked, tmp_path):
    b = worked
    repo = b.parent
    d = "$HOME/.codex/sessions/2026/09/08"
    fake = _harness(tmp_path, "codex", _store_body(d, [
        _writes(d + "/r.jsonl", CODEX_META, "%s", "/somewhere/else"),
        _writes(d + "/r.jsonl", CODEX_TC, "%s", 9999, 9999, 0, 0),
    ]))
    _watch(b, repo, fake)
    e = _end(b)
    for k in NO_USAGE_KEYS:
        assert k not in e, "another agent's codex tokens leaked in as %s" % k


def test_cursor_reports_no_usage_and_that_is_recorded_as_absent(worked, tmp_path):
    """Verified 2026-09-08: `agent -p` emits no usage in text or json output
    format, and its transcripts carry only role/message/status/type. Absent,
    not zero, until Cursor exposes one."""
    b = worked
    fake = _harness(tmp_path, "cursor", "echo 'cursor prose'\n")
    _watch(b, b.parent, fake)
    e = _end(b)
    for k in NO_USAGE_KEYS:
        assert k not in e


# ---- readers: tickets turns / trajectories --summary ---------------------

def test_turns_shows_cost_and_tokens_and_unmeasured_is_not_zero(worked, tmp_path):
    b = worked
    repo = b.parent
    fake = _harness(tmp_path, "claude", "echo '%s'\n" % CLAUDE_RESULT)
    run(b, "next", "--role", "docs", agent="alice", cwd=repo)
    _watch(b, repo, fake)
    r = run(b, "turns", agent="alice", cwd=repo)
    assert r.returncode == 0, r.stderr
    assert "cost" in r.stdout and "$0.2500" in r.stdout, r.stdout
    assert "tok in" in r.stdout
    assert "UNMEASURED, not $0.00" in r.stdout, "the table must say what '-' means"

    j = json.loads(run(b, "turns", "--json", agent="alice", cwd=repo).stdout)
    assert j["v"] == 1
    row = [t for t in j["tickets"] if t.get("cost_usd") is not None]
    assert row and row[0]["cost_usd"] == 0.25
    assert row[0]["tokens_in"] == 11 and row[0]["tokens_out"] == 22
    # T-349 froze the existing keys: this ticket is additive only.
    for k in ("ticket", "owner", "model", "turns", "wall_clock_s", "reopens",
              "stuck", "outcome"):
        assert k in j["tickets"][0]
    assert j["aggregates"]["cost"]["total"] == 0.25


def test_a_ticket_whose_runs_reported_no_cost_reads_as_unknown_not_free(worked, tmp_path):
    """The mutation check the ticket asks for, stated as behaviour: with no
    cost in the log, the column must be '-' and the JSON must be null. If a
    reader ever defaults these to 0, the cheapest agent on the board becomes
    the one we know least about."""
    b = worked
    repo = b.parent
    fake = _harness(tmp_path, "claude", "echo 'prose, no usage'\n")
    run(b, "next", "--role", "docs", agent="alice", cwd=repo)
    _watch(b, repo, fake)

    j = json.loads(run(b, "turns", "--json", agent="alice", cwd=repo).stdout)
    row = j["tickets"][0]
    assert row["cost_usd"] is None and row["tokens_in"] is None, row
    assert row["cost_usd"] != 0 and row["tokens_in"] != 0
    assert j["aggregates"]["cost"]["total"] is None
    assert j["aggregates"]["cost"]["n_unmeasured"] >= 1
    # T-425: a no-write watch is not a turn; cost and turns are independent.
    assert row["turns"] is None

    r = run(b, "turns", agent="alice", cwd=repo)
    # Only the DATA rows: the footer legend quotes "$0.00" to explain what
    # '-' means, which is the opposite of the bug this guards.
    rows = [l for l in r.stdout.splitlines() if l.startswith("T-00")]
    assert rows and all("$" not in l for l in rows), rows
    assert all(l.split()[5] == "-" for l in rows), rows


def test_trajectories_summary_carries_cost_without_moving_the_old_columns(worked, tmp_path):
    b = worked
    repo = b.parent
    fake = _harness(tmp_path, "claude", "echo '%s'\n" % CLAUDE_RESULT)
    run(b, "next", "--role", "docs", agent="alice", cwd=repo)
    _watch(b, repo, fake)
    r = run(b, "trajectories", "--summary", agent="alice", cwd=repo)
    assert r.returncode == 0, r.stderr
    assert "cost" in r.stdout and "$0.2500" in r.stdout, r.stdout
    line = [x for x in r.stdout.splitlines() if x.startswith("T-00")][-1]
    cols = line.split()
    # ticket runs turns upd msgs reopens cost ... -- cost is APPENDED, so the
    # existing positional columns other readers rely on do not shift.
    assert cols[1] == "1" and cols[2] == "3" and cols[6] == "$0.2500", cols


def test_both_copies_of_the_cli_render_the_same_cost_summary(worked, tmp_path):
    """Two-copy rule (T-311/T-351): root tickets.py and the packaged cli.py
    must not drift, or a fix reaches only the entry point that was edited."""
    b = worked
    repo = b.parent
    fake = _harness(tmp_path, "claude", "echo '%s'\n" % CLAUDE_RESULT)
    run(b, "next", "--role", "docs", agent="alice", cwd=repo)
    _watch(b, repo, fake)
    root = run(b, "trajectories", "--summary", agent="alice", cwd=repo).stdout
    e = dict(os.environ, TICKETS_DIR=str(b), TICKET_AGENT="alice",
             HOME=str(b.parent.parent / "home"),
             PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
    e.pop("TICKETS_STOP_HOOK", None)
    pkg = subprocess.run([sys.executable, "-m", "ticket_board", "trajectories", "--summary"],
                         capture_output=True, text=True, env=e, cwd=repo)
    assert pkg.returncode == 0, pkg.stderr
    def cost_cols(out):
        return [l.split()[6] for l in out.splitlines() if l.startswith("T-00")]
    assert cost_cols(root) == cost_cols(pkg.stdout) == ["$0.2500"], (root, pkg.stdout)


# ---- unit level: attribution rules that a subprocess test cannot pin -----

def _tickets_module():
    """Import tickets.py as a module, the way test_liveness.py does, so the
    store readers can be driven with exact timestamps. The subprocess tests
    above cannot: `date` has one-second resolution, so two records written
    inside one run are indistinguishable in time."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("tickets_under_test_t396", str(TOOL))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _epoch(stamp):
    from datetime import datetime, timezone
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")).replace(
        tzinfo=timezone.utc).timestamp()


def _rollout(path, cwd, records, mtime=None):
    """A codex rollout file with a pinned mtime.

    The mtime matters: the readers stat before they read, so a store last
    written before the run started cannot hold that run's events. Fixtures
    therefore set it explicitly instead of inheriting the wall clock, which
    would otherwise make these tests pass or fail depending on how the chosen
    timestamps sit relative to "now".
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps({"timestamp": "2026-09-08T02:00:00Z", "type": "session_meta",
                         "payload": {"type": "session_meta", "cwd": str(cwd)}})]
    for at, tin in records:
        lines.append(json.dumps({
            "timestamp": at, "type": "event_msg",
            "payload": {"type": "token_count", "info": {"total_token_usage": {
                "input_tokens": tin, "output_tokens": 1,
                "cached_input_tokens": 0, "cache_write_input_tokens": 0}}}}))
    path.write_text("\n".join(lines) + "\n")
    at = _epoch(mtime or records[-1][0])
    os.utime(path, (at, at))


def test_the_newest_matching_codex_rollout_wins_not_glob_order(tmp_path, monkeypatch):
    """Two sessions for the SAME worktree can both fall in one window when an
    older one is merely touched. Without an explicit newest-wins rule the
    filesystem's glob order would decide the bill."""
    m = _tickets_module()
    monkeypatch.setattr(m, "CODEX_SESSIONS_DIR", str(tmp_path / "sessions"))
    work = tmp_path / "wt"
    work.mkdir()
    _rollout(tmp_path / "sessions/a.jsonl", work, [("2026-09-08T02:00:05Z", 100)])
    _rollout(tmp_path / "sessions/b.jsonl", work, [("2026-09-08T02:00:30Z", 900)])
    got = m._session_store_usage("codex", str(work), "2026-09-08T02:00:00Z",
                                 "2026-09-08T02:01:00Z")
    assert got["tokens_in"] == 900, got


def test_codex_events_outside_the_run_window_are_not_counted(tmp_path, monkeypatch):
    """The window is what makes a per-run number a per-run number: without it
    a long-lived session's whole-day total lands on whichever run read it."""
    m = _tickets_module()
    monkeypatch.setattr(m, "CODEX_SESSIONS_DIR", str(tmp_path / "sessions"))
    work = tmp_path / "wt"
    work.mkdir()
    _rollout(tmp_path / "sessions/a.jsonl", work, [
        ("2026-09-08T01:00:00Z", 500),      # before the run
        ("2026-09-08T02:00:10Z", 700),      # during
        ("2026-09-08T03:00:00Z", 90000),    # after
    ])
    got = m._session_store_usage("codex", str(work), "2026-09-08T02:00:00Z",
                                 "2026-09-08T02:01:00Z")
    assert got["tokens_in"] == 700, got


def test_an_unreadable_usage_blob_raises_rather_than_reading_as_absent(tmp_path):
    m = _tickets_module()
    with pytest.raises(m.HarnessUsageError):
        m.parse_harness_usage('{"type":"result","usage":"twelve tokens"}')
    # ...while a run that genuinely reported nothing is simply empty.
    assert m.parse_harness_usage("no json here at all") == {}
    assert m.parse_harness_usage("") == {}


def test_a_codex_token_count_with_no_info_is_absent_not_an_error(tmp_path):
    """Every usage-limited codex run writes exactly this. It reported nothing,
    which is not the same as reporting something unreadable."""
    m = _tickets_module()
    line = ('{"timestamp":"2026-09-08T02:00:00Z","type":"event_msg","payload":'
            '{"type":"token_count","info":null,"rate_limits":{"limit_id":"premium"}}}')
    assert m.parse_harness_usage(line) == {}
