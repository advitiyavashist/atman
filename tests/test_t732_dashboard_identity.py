"""T-732: live dashboard uses T-713 bone/brass, not Steer-family lime."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tickets.py"
README = (ROOT / "README.md").read_text(encoding="utf-8")
LANDING = (ROOT / "landing" / "index.html").read_text(encoding="utf-8")
REACT_CSS = (ROOT / "ui" / "src" / "styles.css").read_text(encoding="utf-8")

EVIDENCE = "docs/brand/evidence/t732-dashboard-1440.png"


def _ui_html():
    text = TOOL.read_text(encoding="utf-8")
    start = text.index('UI_HTML = r"""') + len('UI_HTML = r"""')
    return text[start : text.index('"""', start)]


def test_embedded_dashboard_uses_bone_brass_not_steer_lime():
    ui = _ui_html()
    assert "--bg:#0c0e12" in ui
    assert "--fg:#ece8e1" in ui
    assert "--acc:#c4b49a" in ui
    assert "--ok:#6f9e96" in ui
    assert "--warn:#e0a53d" in ui
    assert "#c8f04a" not in ui
    assert "#0b1416" not in ui
    assert "#5b8def" not in ui
    assert "repeating-linear" not in ui
    assert "4.5rem 4.5rem" not in ui
    assert 'class="wordmark">atman</span>' in ui
    assert ui.count("<circle ") >= 5
    labels = [">Objective<", ">Team<", ">Work<", ">Intervene<"]
    tabs = ui[ui.index('<nav class="tabs"') : ui.index("</nav>", ui.index('<nav class="tabs"'))]
    assert all(label in tabs for label in labels)


def test_react_parity_surface_uses_the_same_action_accent():
    assert "--bg: #0c0e12" in REACT_CSS
    assert "--acc: #c4b49a" in REACT_CSS
    assert "--fg: #ece8e1" in REACT_CSS
    assert "--ok: #6f9e96" in REACT_CSS
    assert "#c8f04a" not in REACT_CSS
    assert "#5b8def" not in REACT_CSS
    assert "--blue:" not in REACT_CSS
    assert "--live:" not in REACT_CSS


def test_landing_and_readme_point_at_the_t732_product_capture():
    assert EVIDENCE in README
    assert "../" + EVIDENCE in LANDING
    assert "t606-atman-dark-desktop.png" not in README
    assert "t606-atman-dark-desktop.png" not in LANDING
    for width in ("1440", "768", "390"):
        path = ROOT / "docs" / "brand" / "evidence" / ("t732-dashboard-%s.png" % width)
        assert path.is_file() and path.stat().st_size > 10_000, path
