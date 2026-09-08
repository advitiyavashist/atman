"""T-483: release_sha on run_start and row_era refuses wall-clock without it."""

import json

from ticket_board.scheduler import (
    ERA_POST,
    ERA_PRE,
    ERA_UNKNOWN,
    FLAG_AT,
    FLAG_PIN,
    row_era,
    score_shadow,
)
from test_scheduler_score import (
    _event,
    _finish_with_turns,
    _join,
    _score_agent,
    _stamp,
    _write_jsonl,
)
from test_wakeup import board, run  # noqa: F401


def _bound_run_start(tid, agent, model, at, release_sha=None):
    extra = {}
    if release_sha:
        extra["release_sha"] = release_sha
    return [
        _event("claim", tid, agent, model, at, **extra),
        _event("run_start", tid, agent, model, at, run_no=1, **extra),
        _event("run_end", tid, agent, model, at, run_no=1, exit=0, **extra),
        _event("done", tid, agent, model, at, outcome="done", **extra),
    ]


def test_row_era_release_sha_pre_post_unknown(board):
    pre = _bound_run_start("T-001", "alice", "opus", "2026-05-01T10:00:00Z",
                           release_sha="21ca63c")
    post = _bound_run_start("T-002", "alice", "opus", "2026-09-08T10:00:00Z",
                            release_sha=FLAG_PIN)
    missing = _bound_run_start("T-003", "alice", "opus", "2026-09-08T10:00:00Z")
    assert row_era(pre) == ERA_PRE
    assert row_era(post) == ERA_POST
    assert row_era(missing) == ERA_UNKNOWN


def test_row_era_post_timed_pre_sha_is_pre_not_post(board):
    """Post-FLAG wall-clock with pre-FLAG release_sha must label pre (T-445 bug)."""
    evs = _bound_run_start(
        "T-004", "alice", "opus", FLAG_AT, release_sha="21ca63c")
    assert row_era(evs) == ERA_PRE
    assert row_era(evs, era_by_time=True) == ERA_PRE


def test_row_era_missing_release_sha_era_by_time_fallback(board):
    evs = _bound_run_start("T-005", "alice", "opus", FLAG_AT)
    assert row_era(evs) == ERA_UNKNOWN
    assert row_era(evs, era_by_time=True) == ERA_POST


def test_scorecard_three_era_lines_unknown_excluded_from_pct(board):
    _join(board, "alice", "backend", "opus", "high")
    events = []
    _stamp(board, "T-001", "alice", "backend", 1)
    events.extend(_finish_with_turns("T-001", "alice", "opus", 2, "2026-05-01",
                                     release_sha="21ca63c"))
    _stamp(board, "T-002", "alice", "backend", 1)
    events.extend(_finish_with_turns("T-002", "alice", "opus", 2, "2026-09-08",
                                     release_sha=FLAG_PIN))
    _stamp(board, "T-003", "alice", "backend", 1)
    events.extend(_finish_with_turns("T-003", "alice", "opus", 2, "2026-09-08"))
    _write_jsonl(board, events)
    tickets = [json.loads(p.read_text()) for p in board.glob("T-*.json")]
    workforce = json.loads((board / "workforce.json").read_text())
    roles = json.loads((board / "roles.json").read_text())
    rep = score_shadow(
        __import__("ticket_board.turns").turns.load_trajectory_events(str(board)),
        tickets, workforce, roles, str(board), _score_agent)
    by_id = {r["ticket"]: r for r in rep["rows"]}
    assert by_id["T-001"]["era"] == ERA_PRE
    assert by_id["T-002"]["era"] == ERA_POST
    assert by_id["T-003"]["era"] == ERA_UNKNOWN
    assert rep["n_unknown"] == 1
    assert rep["agreement_rate"] is None
    r = run(board, "route", "--shadow", "--score", cwd=board.parent)
    assert r.returncode == 0, r.stderr
    assert "agreement unknown: n/a (n=1, excluded from pct)" in r.stdout
    assert "unknown never mixed into pct" in r.stdout


def test_run_start_stamps_release_sha_from_release_json(board, tmp_path, monkeypatch):
    """Watch run_start carries release_sha when release.json is present."""
    rel = tmp_path / "rel"
    rel.mkdir()
    (rel / "release.json").write_text(json.dumps({"commit": "abc123def456"}))
    monkeypatch.setattr("tickets._release_commit", lambda: "abc123def456")
    from tickets import traj_event
    traj_event(str(board), "run_start", agent="alice", ticket="T-001", run_no=1,
               release_sha="abc123def456")
    line = json.loads((board / "trajectories.jsonl").read_text().strip())
    assert line["release_sha"] == "abc123def456"
