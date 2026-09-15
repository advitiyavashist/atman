"""T-884: compose never T-000; onboarding needs coordinator; pulse matches next-step."""

import json
from pathlib import Path

from test_wakeup import board, run  # noqa: F401

TOOL = Path(__file__).resolve().parents[1] / "tickets.py"


def _ui_html():
    text = TOOL.read_text(encoding="utf-8")
    start = text.index('UI_HTML = r"""') + len('UI_HTML = r"""')
    return text[start:text.index('"""', start)]


def test_compose_input_has_no_t000_default():
    ui = _ui_html()
    chunk = ui[ui.index('id="cRe"'):ui.index("</label>", ui.index('id="cRe"'))]
    assert "T-000" not in chunk
    assert 'placeholder="ticket id"' in chunk
    assert "if(flight&&flight.id)re.value=flight.id" not in ui
    assert "cur==='T-000'" in ui


def test_onboarding_cannot_complete_without_coordinator(board):
    run(board, "join", "alice", "--roles", "backend")
    run(board, "join", "bob", "--roles", "ui", "--harness", "cursor")
    run(board, "objective", "--set", "Ship honest UI")
    run(board, "create", "Ticket A", "--role", "backend")
    d = json.loads(run(board, "ui", "--json").stdout)
    ob = d["onboarding"]
    assert ob["coordinator"] is False
    done = sum(1 for k in (
        "initialized", "coordinator", "first_ticket", "first_agent",
        "second_harness", "first_review", "first_merge", "objective_set",
    ) if ob.get(k))
    assert done < 8
    assert d["master"] in ("", None, "nobody") or not d["master"]


def test_attention_and_next_step_never_say_on_track(board):
    run(board, "master", "take", agent="boss")
    run(board, "master", "init", agent="boss")
    run(board, "join", "alice", "--roles", "backend")
    run(board, "join", "bob", "--roles", "ui")
    run(board, "objective", "--set", "Finish A then B")
    run(board, "create", "Ticket A", "--role", "backend")
    run(board, "create", "Ticket B", "--role", "backend", "--deps", "T-001")
    claimed = run(board, "next", agent="alice")
    assert claimed.returncode == 0, claimed.stderr
    d = json.loads(run(board, "ui", "--json").stdout)
    warn = [x for x in (d.get("attention") or []) if x.get("sev") in ("CRIT", "WARN")]
    if warn:
        assert d["next_step"]["kind"] != "ok"
        assert d["next_step"]["label"] != "On track"
    pulse_would_warn = bool(warn)
    if pulse_would_warn:
        assert d["next_step"]["kind"] in (
            "attention", "unblock", "merge", "spawn", "route", "start",
        )


def test_objective_snapshot_is_standing_text_not_mission_header(board):
    run(board, "master", "take", agent="boss")
    run(board, "master", "init", agent="boss")
    run(board, "objective", "--set", "Ship the coherent app")
    d = json.loads(run(board, "ui", "--json").stdout)
    assert d["objective"]["text"] == "Ship the coherent app"
    assert "MISSION" not in (d["objective"].get("text") or "")
    assert "SPRINT PLAN" not in (d["objective"].get("text") or "")
    ui = _ui_html()
    pane = ui[ui.index('id="pane-objective"'):ui.index('id="pane-board"')]
    assert "set via" in pane
    assert "MASTER.md (not the standing objective)" in pane
    assert "h==='objective'||h==='agents'||h==='messages'||h==='board'" in ui
