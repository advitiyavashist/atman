"""T-606: accepted portfolio identity on the live embedded dashboard."""

from pathlib import Path


TOOL = Path(__file__).resolve().parents[1] / "tickets.py"


def _ui_html():
    text = TOOL.read_text(encoding="utf-8")
    start = text.index('UI_HTML = r"""') + len('UI_HTML = r"""')
    return text[start:text.index('"""', start)]


def test_family_tokens_cover_dark_and_light_without_old_action_palette():
    ui = _ui_html()
    assert "--bg:#0b1416" in ui and "--card:#121c1e" in ui
    assert "--bg:#f3f6f4" in ui and "--fg:#142022" in ui
    assert "--acc:#c8f04a" in ui and "--on-acc:#142022" in ui
    assert "--ok:#5ec7b0" in ui and "--warn:#e0a53d" in ui and "--bad:#e85d4c" in ui
    assert "--acc:#5b8def" not in ui
    assert "--live:" not in ui and "--intervene:" not in ui
    assert "linear-gradient" not in ui and "box-shadow:0 0 0" not in ui


def test_atman_mark_and_portfolio_caret_have_separate_jobs():
    ui = _ui_html()
    header = ui[ui.index("<header"):ui.index("</header>")]
    assert header.count('<circle ') == 5
    assert header.count('fill="currentColor"') == 5
    assert '<span class="caret" aria-hidden="true">^</span>' in header
    assert "Products · Atman" in header
    assert "Brahman" in header and "Model communication · not connected" in header
    assert "ATI" in header and "Experiment lab · not connected" in header
    assert "steer.md" in header and "Policy for output · separate product" in header


def test_locked_ia_and_keyboard_accessibility_markers():
    ui = _ui_html()
    tabs = ui[ui.index('<nav class="tabs"'):ui.index("</nav>", ui.index('<nav class="tabs"'))]
    labels = [">Objective<", ">Team<", ">Work<", ">Intervene<"]
    assert all(label in tabs for label in labels)
    assert [tabs.index(label) for label in labels] == sorted(tabs.index(label) for label in labels)
    assert 'role="tablist"' in tabs
    assert tabs.count('role="tab"') == 4
    assert tabs.count('aria-controls="pane-') == 4
    assert "setAttribute('aria-selected'" in ui
    assert "ArrowRight" in ui and "ArrowLeft" in ui and "addEventListener('keydown'" in ui
    assert "prefers-reduced-motion:reduce" in ui
    assert "focus-visible" in ui
    assert 'id="themeLabel">Light mode</span>' in ui
    assert "updateThemeControl" in ui


def test_lime_is_reserved_for_actions_and_selection():
    ui = _ui_html()
    assert ".bar i{display:block;height:100%;background:var(--progress)}" in ui
    assert ".health i{width:8px;height:8px;border-radius:2px;background:var(--ok)}" in ui
    assert ".epic .id{font-weight:700;color:var(--fg)}" in ui
    assert ".card-top .id{font-weight:700;color:var(--fg)}" in ui
    assert "#composer button{background:var(--acc);color:var(--on-acc)" in ui
    assert ".next-step .lbl{font-weight:700;color:var(--acc)" in ui
    assert "nav.tabs button.on{color:var(--fg);border-bottom-color:var(--acc)}" in ui


def test_live_task_and_refresh_contract_markers_remain_present():
    ui = _ui_html()
    for marker in (
        "fetch('/msg'", "'Content-Type':'application/json'", "kind", "deliveryTags",
        "AbortController", "_loadCtl", "setInterval(load,5000)", "inbox_seen",
    ):
        assert marker in TOOL.read_text(encoding="utf-8"), marker
    assert "connected" in ui
    assert "No alerts" in ui
