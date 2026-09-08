"""T-571 / T-567: Atman UX bar — emptyBoard 3-step, chrome, Done (24h), promise chips."""

from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "tickets.py"

BANNED = (
    "Total Football", "total football", "football", "goalpost",
    "keeper", "shirts", "midfield", "Attack ·", "Defense ·",
    "on the pitch", 'class="pitch"', "renderPitch", "playerChip",
    "#2a7a4c", "#14532d", "band-keep", "band-attack",
    "mandala", "awakening", "Brahman", "Om ",
    "↗ Ticket Board", "Steer ^",
    'class="prod">tickets',
    "#c6ff00", "#bef264", "#ccff00",
    "self ↔ whole",
    "shared-memory brain",
    "vector DB",
)


def _ui_html():
    text = TOOL.read_text(encoding="utf-8")
    start = text.index('UI_HTML = r"""') + len('UI_HTML = r"""')
    end = text.index('"""', start)
    return text[start:end]


def test_empty_board_is_three_command_steps_with_honesty():
    ui = _ui_html()
    empty_fn = ui[ui.index("function renderEmptyBoard"):ui.index("load();setInterval")]
    assert 'id="emptySteps"' in empty_fn
    assert empty_fn.count("<li>") == 3
    assert "tickets quickstart --agent" in empty_fn
    assert "tickets join" in empty_fn and "--harness" in empty_fn
    assert "tickets objective" in empty_fn
    assert "Median turns and yield@cost stay — until a done ticket reports." in empty_fn
    assert "Welcome to Atman." not in empty_fn
    assert "Everyone reads the same board state" not in empty_fn


def test_atman_chrome_wordmark_and_formation_dots():
    ui = _ui_html()
    header = ui[ui.index("<header"):ui.index("</header>")]
    assert 'class="wordmark">atman</span>' in header
    assert "<title>atman</title>" in ui
    assert 'letter-spacing:.22em' in ui
    assert "text-transform:lowercase" in ui
    assert 'class="mark" viewBox="0 0 32 32"' in header
    assert header.count("<circle ") >= 5
    assert "--bg:#0c0e12" in ui
    assert "--acc:#5b8def" in ui
    assert "↗" not in header
    assert "Ticket board" not in header
    assert 'class="prod"' not in header


def test_done_24h_label_on_agent_roster():
    ui = _ui_html()
    assert ">Done (24h)<" in ui
    assert "not lifetime done" in ui
    roster = ui[ui.index("document.getElementById('agents')"):]
    assert "Done (24h)" in roster
    assert ">Turns</span>" not in roster


def test_promise_chips_stay_visible_and_turns_open_when_unknown():
    ui = _ui_html()
    header = ui[ui.index("<header"):ui.index("</header>")]
    assert 'id="promiseChips"' in header
    assert 'id="hdrMedian"' in header
    assert 'id="hdrYield"' in header
    assert "median turns" in header
    assert "yield@cost" in header
    assert "body[data-tab=board] .promise-chips{display:none}" not in ui
    board = ui[ui.index('id="pane-board"'):ui.index('id="pane-agents"')]
    assert 'id="promiseHero"' in board
    assert "Median turns" in board
    assert "Yield@cost" in board
    assert "turnsPanel" in ui
    assert "panel.open=true" in ui
    assert "median_turns==null" in ui and "yield_per_usd==null" in ui


def test_onboard_merge_is_tickets_merge_and_second_harness():
    ui = _ui_html()
    ob = ui[ui.index("function renderOnboarding"):ui.index("function renderNextStep")]
    assert "['first_merge','First merge','tickets merge']" in ob
    assert "tickets done <id>" not in ob
    assert "second_harness" in ob
    assert "tickets join <name> --harness" in ob


def test_team_ledes_stay_lean():
    ui = _ui_html()
    team = ui[ui.index('id="pane-agents"'):ui.index('id="pane-messages"')]
    assert "Who’s present. What’s uncovered." in team
    assert "Open seat — uncovered work." in ui
    assert "self ↔ whole" not in team
    assert "shared-memory brain" not in team
    assert "vector DB" not in team
    assert 'class="mark" viewBox="0 0 32 32"' in team


def test_ux_bar_bans_sports_kitsch_and_steer_chrome():
    ui = _ui_html()
    for word in BANNED:
        assert word not in ui, "banned chrome still in console: %s" % word
