"""T-681: Day-one in-console Copy command / Open tab actions."""

from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "tickets.py"


def _ui_html():
    text = TOOL.read_text(encoding="utf-8")
    start = text.index('UI_HTML = r"""') + len('UI_HTML = r"""')
    end = text.index('"""', start)
    return text[start:end]


def test_day_one_has_copy_and_open_actions():
    ui = _ui_html()
    assert "Copy command" in ui
    assert "data-copy=" in ui
    assert "function dayOneStep" in ui
    assert ",'agents')" in ui
    assert ",'objective')" in ui
    assert 'data-tab="messages"' in ui
    assert "navigator.clipboard" in ui
    assert "setTab(tab)" in ui
