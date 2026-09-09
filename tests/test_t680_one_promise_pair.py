"""T-680: one promise pair — header chips; no Work hero / chrome strip triple."""

from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "tickets.py"


def _ui_html():
    text = TOOL.read_text(encoding="utf-8")
    start = text.index('UI_HTML = r"""') + len('UI_HTML = r"""')
    end = text.index('"""', start)
    return text[start:end]


def test_one_promise_pair_is_header_chips():
    ui = _ui_html()
    header = ui[ui.index("<header"):ui.index("</header>")]
    board = ui[ui.index('id="pane-board"'):ui.index('id="pane-agents"')]
    objective = ui[ui.index('id="pane-objective"'):ui.index('id="pane-board"')]
    assert 'id="promiseChips"' in header
    assert 'id="hdrMedian"' in header and 'id="hdrYield"' in header
    assert "median turns" in header and "yield@cost" in header
    assert 'id="promiseHero"' not in ui
    assert "heroMedianVal" not in ui
    assert board.count("median turns") == 0
    assert 'id="promiseStrip"' in objective
    assert 'id="promiseStrip"' not in ui[ui.index("<header"):ui.index("<nav")]
