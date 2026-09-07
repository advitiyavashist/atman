"""T-372: promise hero + turns/usage/coverage surfaces on the T-323 console."""

import json
import shutil
from pathlib import Path

from ticket_board.turns import ROW_KEYS
from test_wakeup import board, run  # noqa: F401

TOOL = Path(__file__).resolve().parents[1] / "tickets.py"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "turns_trajectories.jsonl"


def _ui_html():
    text = TOOL.read_text(encoding="utf-8")
    start = text.index('UI_HTML = r"""') + len('UI_HTML = r"""')
    end = text.index('"""', start)
    return text[start:end]


def _costed_fixture(dest):
    """turns fixture plus harness cost on measured run_ends only."""
    rows = []
    for ln in FIXTURE.read_text().splitlines():
        ln = ln.strip()
        if not ln:
            continue
        rec = json.loads(ln)
        if rec.get("kind") == "run_end" and rec.get("ticket") == "T-010":
            rec["cost_usd"] = 0.40
            rec["tokens_in"] = 100
            rec["tokens_out"] = 20
        if rec.get("kind") == "run_end" and rec.get("ticket") == "T-011":
            rec["cost_usd"] = 0.10
        rows.append(json.dumps(rec))
    dest.write_text("\n".join(rows) + "\n")


def test_ui_html_keeps_t323_ia_and_adds_promise_markers():
    ui = _ui_html()
    for marker in (
        "promiseHero", "heroEyebrow", "heroMedian", "heroYield", "Yield@cost", "Median turns",
        "Fewest turns. Max output at least cost.",
        "Lower is better · unknown is not zero",
        "turnsPanel", "Turns efficiency", "turnsWorst", "turnsAgents",
        "renderTurns", "turns-grid", "Worst tickets", "Per-agent median",
        "usagePanel", "Usage / cost", "usageHonesty", "Not reported by harness",
        "renderUsage", "coverageLede", "Total Football.", "band-keep", "band-attack",
        "renderPitch", "uncovered", "emptyBoard", "nextStep", "obSteps",
        'data-tab-btn="board"', 'data-tab-btn="agents"', 'data-tab-btn="messages"',
        "fetch('/msg'", "mentionBar",
    ):
        assert marker in ui, "missing UI marker: %s" % marker
    assert "DAG" not in ui
    assert "graph editor" not in ui.lower()
    assert 'data-tab-btn="board"' in ui


def test_promise_hero_copy_nits():
    """T-372 follow-up: eyebrow + median hint + Total Football casing. CLI flag stays out of the hero."""
    ui = _ui_html()
    assert "Fewest turns. Max output at least cost." in ui
    assert "Lower is better · unknown is not zero" in ui
    assert "Total Football." in ui
    assert "tickets turns --json" not in ui
    assert "Total football." not in ui
    # Median hint is static copy; do not overwrite it with CLI/measured text.
    assert "heroMedianHint').textContent" not in ui
    assert 'heroMedianHint").textContent' not in ui


def test_board_snapshot_turns_matches_cli_json(board):
    shutil.copy(FIXTURE, board / "trajectories.jsonl")
    run(board, "master", "take", agent="boss")
    run(board, "master", "init", agent="boss")
    snap = json.loads(run(board, "ui", "--json").stdout)
    cli = json.loads(run(board, "turns", "--json").stdout)
    assert snap["turns"] == cli
    assert snap["turns"]["v"] == 1
    for row in snap["turns"]["tickets"]:
        assert tuple(row.keys()) == ROW_KEYS
    by = {r["ticket"]: r for r in snap["turns"]["tickets"]}
    assert by["T-010"]["turns"] == 2
    assert by["T-012"]["turns"] is None
    assert snap["promise"]["median_turns"] == cli["aggregates"]["median"]
    assert snap["promise"]["n_turns"] == cli["aggregates"]["n"]
    assert snap["promise"]["n_unmeasured_turns"] == cli["aggregates"]["n_unmeasured"]


def test_promise_hero_yield_unknown_is_not_zero(board):
    shutil.copy(FIXTURE, board / "trajectories.jsonl")
    run(board, "master", "take", agent="boss")
    d = json.loads(run(board, "ui", "--json").stdout)
    # Fixture run_ends have no cost_usd: yield is unknown, not 0.
    assert d["promise"]["median_turns"] == 2.0
    assert d["promise"]["yield_per_usd"] is None
    assert d["promise"]["cost_usd"] is None
    assert d["promise"]["done_with_cost"] == 0
    assert d["promise"]["n_unmeasured_cost"] >= 1
    assert d["usage"]["cost_usd"] is None
    assert d["usage"]["n_runs_unmeasured"] >= 1
    assert d["usage"]["n_runs_with_cost"] == 0


def test_promise_hero_yield_from_harness_cost(board):
    _costed_fixture(board / "trajectories.jsonl")
    run(board, "master", "take", agent="boss")
    d = json.loads(run(board, "ui", "--json").stdout)
    assert d["promise"]["median_turns"] == 2.0
    # T-010: two run_ends at $0.40; T-011: two run_ends at $0.10 (reopen).
    assert d["promise"]["done_with_cost"] == 2
    assert abs(d["promise"]["cost_usd"] - 1.00) < 1e-9
    assert abs(d["promise"]["yield_per_usd"] - 2.0) < 1e-6
    assert d["usage"]["cost_usd"] == 1.00
    assert d["usage"]["tokens_in"] == 200
    assert d["usage"]["n_runs_with_cost"] == 4
    agents = {r["agent"]: r for r in d["usage"]["by_agent"]}
    assert agents["alice"]["cost_usd"] == 0.80
    assert agents["bob"]["cost_usd"] == 0.20


def test_coverage_uncovered_ready_and_keeper(board):
    run(board, "master", "take", agent="boss")
    run(board, "master", "init", agent="boss")
    run(board, "join", "worker", "--roles", "backend")
    d = json.loads(run(board, "ui", "--json").stdout)
    assert "coverage" in d
    assert any(k["name"] == "boss" and k["role"] == "master" for k in d["coverage"]["keeper"])
    # test_wakeup board fixture creates an unowned docs ticket
    assert d["coverage"]["uncovered_ready"]
    worker = next(a for a in d["agents"] if a["name"] == "worker")
    assert "backend" in (worker.get("roles") or [])


def test_empty_board_promise_surfaces_are_honest(board):
    # wipe the fixture ticket so this is a true empty board
    for p in board.glob("T-*.json"):
        p.unlink()
    d = json.loads(run(board, "ui", "--json").stdout)
    assert d["empty_board"] is True
    assert d["turns"]["v"] == 1
    assert d["turns"]["tickets"] == []
    assert d["promise"]["median_turns"] is None
    assert d["promise"]["yield_per_usd"] is None
    assert d["usage"]["cost_usd"] is None
    assert d["coverage"]["uncovered_ready"] == []
    assert d["onboarding"]["first_ticket"] is False
    assert d["next_step"]["kind"] == "start"
