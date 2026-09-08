"""T-571 / T-567: Atman UX bar — emptyBoard 3-step, chrome, Done(24h), promise chips."""

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
)


def _ui_html():
    text = TOOL.read_text(encoding="utf-8")
    start = text.index('UI_HTML = r"""') + len('UI_HTML = r"""')
    end = text.index('"""', start)
    return text[start:end]


def test_empty_board_is_three_ia_steps_not_dead_prose():
    ui = _ui_html()
    empty_fn = ui[ui.index("function renderEmptyBoard"):ui.index("load();setInterval")]
    assert 'id="emptySteps"' in empty_fn
    assert "<ol class=\"empty-steps\"" in empty_fn or "<ol class='empty-steps'" in empty_fn
    assert empty_fn.count("<li>") == 3
    assert "Objective" in empty_fn and "Team" in empty_fn and "Work" in empty_fn
    assert "Objective · Team · Work · Intervene" in empty_fn
    assert "tickets objective" in empty_fn
    assert "tickets join" in empty_fn
    assert "tickets create" in empty_fn
    assert "tickets quickstart" in empty_fn
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
    assert ">Done(24h)<" in ui
    assert "not lifetime done" in ui
    # Lifetime board count may still say "done"; the 24h seat stat must not
    # reuse the old "Turns" label that collided with median turns.
    roster = ui[ui.index("document.getElementById('agents')"):]
    assert "Done(24h)" in roster
    assert ">Turns</span>" not in roster


def test_promise_chips_stay_visible_above_the_fold():
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


def test_ux_bar_bans_sports_kitsch_and_steer_chrome():
    ui = _ui_html()
    for word in BANNED:
        assert word not in ui, "banned chrome still in console: %s" % word
