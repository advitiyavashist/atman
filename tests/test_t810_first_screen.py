"""T-810 CEO ADOPT t892 #1 first screen + #5 shell integration."""

import json
from pathlib import Path

from test_wakeup import board, run  # noqa: F401

TOOL = Path(__file__).resolve().parents[1] / "tickets.py"


def _ui_html():
    text = TOOL.read_text(encoding="utf-8")
    start = text.index('UI_HTML = r"""') + len('UI_HTML = r"""')
    return text[start:text.index('"""', start)]


def _snap(board):
    r = run(board, "ui", "--json")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _team(board):
    assert run(board, "master", "take", agent="boss").returncode == 0
    assert run(board, "join", "alice", "--roles", "backend", agent="alice").returncode == 0
    assert run(board, "join", "bob", "--roles", "docs", agent="bob").returncode == 0


def test_work_objective_sits_above_summary_and_names_done_when():
    ui = _ui_html()
    obj = ui.index('id="workObjective"')
    now = ui.index('id="nowStrip"')
    graph = ui.index('id="workflowGraph"')
    assert obj < now < graph
    assert 'id="workDoneWhen"' in ui
    assert 'id="workDoneWhenText"' in ui
    assert 'data-done-when' in ui
    assert "function renderObjective" in ui
    assert "workDoneWhenText" in ui
    assert "no exit criterion yet" in ui


def test_layout_controls_are_pressed_toggles_not_tabs():
    ui = _ui_html()
    chunk = ui[ui.index('id="workViews"'):ui.index('id="graphLede"')]
    assert 'role="group"' in chunk
    assert 'role="tablist"' not in chunk
    assert 'data-work-view="graph"' in chunk
    assert 'data-work-view="list"' in chunk
    assert 'data-work-view="columns"' in chunk
    assert "aria-pressed" in chunk
    assert "setAttribute('aria-pressed'" in ui
    assert "AtmanWork.setMode" in ui


def test_shell_listens_for_work_select_and_has_mobile_jump():
    ui = _ui_html()
    assert "atman:work-select" in ui
    assert "function applyWorkSelect" in ui
    assert "extra.who_kind" in ui
    assert "ensureToOption(extra.to)" in ui
    assert 'id="workJump"' in ui
    assert 'id="workJumpCompose"' in ui
    assert "setTab('messages')" in ui
    assert "max-width:700px" in ui
    assert "Reserved for @" in ui
    assert "Task posted to @" in ui


def test_light_theme_uses_readable_action_token():
    ui = _ui_html()
    light = ui[ui.index("body[data-theme=light]"):ui.index("body[data-theme=light]") + 400]
    assert "--acc:#6b5344" in light
    assert "--on-acc:#fcfaf6" in light
    assert "ph-reserved" in ui
    assert "ph-posted{--wv-c:var(--flight)}" in ui


def test_first_screen_payload_names_objective_blocker_and_next(board):
    _team(board)
    assert run(board, "objective", "--set", "Ship the first screen",
               "--exit", "named blocker and next teammate visible",
               agent="boss").returncode == 0
    assert run(board, "create", "Ready work", "--role", "backend", agent="boss").returncode == 0
    assert run(board, "create", "Child waits", "--role", "docs", "--deps", "T-001",
               agent="boss").returncode == 0
    assert run(board, "create", "Parked", "--role", "docs",
               "--body", "HOLD until tester week", agent="boss").returncode == 0
    assert run(board, "create", "Unassigned next", "--role", "backend", agent="boss").returncode == 0
    assert run(board, "next", agent="alice").returncode == 0
    d = _snap(board)
    assert d["objective"]["text"] == "Ship the first screen"
    assert d["objective"]["exit_criterion"] == "named blocker and next teammate visible"
    assert d["work"]["objective"]["text"] == d["objective"]["text"]
    fs = d["first_screen"]
    assert fs["finishing"]["title"] == "Ready work"
    assert fs["finishing"]["owner"] == "alice"
    assert fs["blocker"]["kind"] in ("deps", "hold", "waiting", "capture")
    assert fs["blocker"]["title"]
    assert fs["blocker"]["kind"] == "hold" or fs["blocker"]["phase"] in ("hold", "waiting")
    assert fs["blocker_count"] >= 2
    assert any(w.get("id") for w in (fs.get("waiting") or []))
    assert fs["next"]["id"]
    assert fs["next"]["title"]
    assert fs["next"]["phase"] in ("ready", "reserved", "posted", "dispatched")
    by = dict((n["id"], n) for n in d["work"]["nodes"])
    nxt = by[fs["next"]["id"]]
    assert nxt["title"]
    assert nxt["phase"] in ("ready", "reserved", "posted", "dispatched")


def test_missing_objective_is_an_explicit_empty_state(board):
    _team(board)
    assert run(board, "create", "Lone task", "--role", "backend", agent="boss").returncode == 0
    d = _snap(board)
    assert d["objective"]["text"] == ""
    assert d["work"]["objective"]["text"] == ""
    assert d["first_screen"]["next"]["id"] == "T-001"
    ui = _ui_html()
    assert 'tickets objective --set "what we are finishing"' in ui
