"""T-682: onboard day-one order, button types, overview empty_state honesty."""

from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "tickets.py"
VIEWS = Path(__file__).resolve().parents[1] / "src" / "ticket_board" / "server" / "views.py"


def _ui_html():
    text = TOOL.read_text(encoding="utf-8")
    start = text.index('UI_HTML = r"""') + len('UI_HTML = r"""')
    end = text.index('"""', start)
    return text[start:end]


def test_csend_is_type_button():
    assert '<button type="button" id="cSend">Post</button>' in _ui_html()


def test_overview_empty_state_matches_day_one():
    src = VIEWS.read_text(encoding="utf-8")
    assert "Create a ticket or import a board." not in src
    assert "Work · Team · Objective" in src
    assert "until a done ticket reports." in src
