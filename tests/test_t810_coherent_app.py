"""T-810: coherent tickets ui — graph detail, T-884 folds, honest surfaces."""

import json
from pathlib import Path

from test_wakeup import board, run  # noqa: F401

TOOL = Path(__file__).resolve().parents[1] / "tickets.py"


def _ui_html():
    text = TOOL.read_text(encoding="utf-8")
    start = text.index('UI_HTML = r"""') + len('UI_HTML = r"""')
    return text[start:text.index('"""', start)]


def test_compose_never_defaults_to_t000():
    ui = _ui_html()
    assert 'placeholder="T-000"' not in ui
    assert "T-000" not in ui[ui.index('id="cRe"'):ui.index("</label>", ui.index('id="cRe"'))]
    assert "function defaultComposeTicket" in ui
    assert "cur==='T-000'" in ui


def test_onboarding_coordinator_required_for_complete():
    ui = _ui_html()
    ob = ui[ui.index("function renderOnboarding"):ui.index("function renderNextStep")]
    assert "coordinator" in ob
    assert "atm master take" in ob
    assert "0/8" in ui


def test_health_pulse_and_attention_share_one_source():
    ui = _ui_html()
    load = ui[ui.index("async function load"):ui.index("function renderOnboarding")]
    assert "(d.attention||[]).filter(x=>x.sev==='CRIT')" in load
    assert "(d.health||[]).filter" not in load
    assert "function renderNow" in ui
    assert 'id="nowStrip"' in ui
    assert 'id="graphDetail"' in ui
    assert "success starts" in ui


def test_objective_pane_uses_standing_objective_not_mission_dump():
    ui = _ui_html()
    pane = ui[ui.index('id="pane-objective"'):ui.index('id="pane-board"')]
    assert 'id="objectiveText"' in pane
    assert "read-only — set via" in pane
    assert "MASTER.md (not the standing objective)" in pane
    assert "function renderObjective" in ui
    assert "setTxt('missionOne'" in ui


def test_graph_nodes_expose_wait_reason_phase_and_trigger(board):
    run(board, "master", "take", agent="boss")
    run(board, "master", "init", agent="boss")
    run(board, "objective", "--set", "Ship the coherent app", agent="boss")
    created = run(board, "create", "Build login UI", "--role", "frontend", "--deps", "T-001")
    assert created.returncode == 0, created.stderr
    d = json.loads(run(board, "ui", "--json").stdout)
    by_id = dict((n["id"], n) for n in d["graph"]["nodes"])
    child = by_id["T-002"]
    assert child["waiting"] == ["T-001"]
    assert child["wait_reason"] == "deps"
    assert child["phase"] in ("waiting", "ready", "capture")
    assert "proof" in child and "handoff" in child and "verdict" in child
    assert "success_starts" in by_id["T-001"]
    assert d["onboarding"]["coordinator"] is True
    assert d["onboarding"]["objective_set"] is True
    assert d["objective"]["text"]
    assert "MISSION" not in (d["objective"].get("text") or "")
    assert d["next_step"]["kind"] != "ok" or not d["attention"]


def test_onboarding_incomplete_without_master(board):
    run(board, "join", "worker", "--roles", "backend")
    d = json.loads(run(board, "ui", "--json").stdout)
    assert d["master"] in ("", None, "nobody") or not d["master"]
    assert d["onboarding"]["coordinator"] is False


def test_next_step_not_on_track_when_attention_exists(board):
    run(board, "master", "take", agent="boss")
    run(board, "master", "init", agent="boss")
    run(board, "join", "worker", "--roles", "backend")
    run(board, "create", "Do the work", agent="boss")
    claimed = run(board, "next", agent="worker")
    assert claimed.returncode == 0, claimed.stderr
    d = json.loads(run(board, "ui", "--json").stdout)
    if d.get("attention"):
        assert d["next_step"]["kind"] != "ok"
        assert d["next_step"]["label"] != "On track"
