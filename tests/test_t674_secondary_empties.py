"""T-674: tickets ui secondary empties — Team roster + Intervene thread craft."""

from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "tickets.py"


def _ui_html():
    text = TOOL.read_text(encoding="utf-8")
    start = text.index('UI_HTML = r"""') + len('UI_HTML = r"""')
    end = text.index('"""', start)
    return text[start:end]


def test_empty_craft_helper_matches_day_one_tone():
    ui = _ui_html()
    helper = ui[ui.index("function emptyCraft"):ui.index("const dash=")]
    assert 'class="empty-board empty-secondary"' in helper
    assert "empty-kicker" in helper
    assert "empty-honesty" in helper
    assert 'class="cta"' in helper
    assert "football" not in helper.lower()


def test_agents_empty_has_honesty_and_join_quickstart_cta():
    ui = _ui_html()
    roster = ui[ui.index("document.getElementById('agents')"):ui.index("function renderOnboarding")]
    assert "No agents checked in." in roster
    assert "until a seat heartbeats" in roster
    assert "tickets join" in roster and "--harness" in roster
    assert "tickets quickstart --agent" in roster
    assert '<div class="empty">no agents checked in</div>' not in roster


def test_messages_empty_board_and_seat_have_honesty_and_msg_cta():
    ui = _ui_html()
    msgs = ui[ui.index("const msgEmpty="):ui.index("function renderOnboarding")]
    assert "No messages yet." in msgs
    assert "No messages with this seat yet." in msgs
    assert "never implied progress on tickets" in msgs
    assert 'tickets msg "text"' in msgs
    assert "tickets msg --to " in msgs
    assert '<div class="empty">no messages yet</div>' not in ui
