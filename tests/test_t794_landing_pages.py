"""T-794: GitHub Pages landing with first-class graph, roadmap, app, efficiency."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LANDING = (ROOT / "landing" / "index.html").read_text(encoding="utf-8")
README = (ROOT / "landing" / "README.md").read_text(encoding="utf-8")
REPO_README = (ROOT / "README.md").read_text(encoding="utf-8")
WORKFLOW = (ROOT / ".github" / "workflows" / "pages.yml").read_text(encoding="utf-8")
PAGES = "https://advitiyavashist.github.io/atman/"


def test_first_class_section_ids_and_order():
    for needed in ("workflow", "app", "per-turn", "roadmap"):
        assert 'id="%s"' % needed in LANDING, needed
    assert LANDING.index('id="app"') < LANDING.index('id="philosophy"')
    how = LANDING.index('id="how"')
    assert how < LANDING.index('id="workflow"')
    assert LANDING.index('id="workflow"') < LANDING.index('id="per-turn"')
    assert LANDING.index('id="per-turn"') < LANDING.index('id="roadmap"')
    assert LANDING.index('id="roadmap"') < LANDING.index('id="start"')


def test_tickets_ui_capture_is_shareable():
    block = LANDING[LANDING.index('id="app"') : LANDING.index('id="philosophy"')]
    for surface in ("Objective", "Team", "Work", "Intervene"):
        assert surface in block, surface
    assert "assets/t732-dashboard-1440.png" in block
    assert "not a hosted demo" in block.lower()
    assert "not fabricated activity" in block.lower()
    assert "tickets connect" in block
    assert "atman-&lt;seat&gt;" in block
    assert "tickets ui" in block
    assert "og:image" in LANDING
    assert PAGES + "assets/t732-dashboard-1440.png" in LANDING
    assert "127.0.0.1" not in LANDING
    assert "localhost" not in LANDING.lower()


def test_workflow_graph_is_first_class():
    block = LANDING[LANDING.index('id="workflow"') : LANDING.index('id="per-turn"')]
    assert "Work stays invisible until its dependencies are done" in block
    assert "tickets ui" in block
    assert "T-001" in block
    assert "T-002" in block
    assert "T-003" in block
    assert "waiting on T-002" in block
    assert "hosted board" in block


def test_app_is_tickets_ui_first_session_not_cloud_demo():
    block = LANDING[LANDING.index('id="app"') : LANDING.index('id="philosophy"')]
    assert "tickets ui" in block
    assert "tickets connect" in block
    assert "atman-&lt;seat&gt;" in block
    assert "not a hosted demo" in block.lower()
    assert "assets/t732-dashboard-1440.png" in block
    assert "127.0.0.1" not in LANDING
    assert "localhost" not in LANDING.lower()


def test_per_turn_unknown_is_not_zero():
    block = LANDING[LANDING.index('id="per-turn"') : LANDING.index('id="roadmap"')]
    assert "Unknown is not zero until a done ticket reports" in block
    assert block.count("—") >= 2
    assert "$0" not in block
    assert "0.0" not in block


def test_engineering_roadmap_is_first_class():
    block = LANDING[LANDING.index('id="roadmap"') : LANDING.index('id="surfaces"')]
    assert "V0" in block
    assert "V1" in block
    assert "V2" in block
    assert "V3" in block
    assert "V4" in block
    assert "tickets route" in block
    assert "named prior" in block.lower() or "named prior" in block
    assert ">Now<" in block or ">Now</span>" in block
    assert ">Next<" in block or ">Next</span>" in block
    assert ">Later<" in block or ">Later</span>" in block
    assert "No invented ship dates" in block
    assert "claimable only after cause, change, and proof exist" in block
    lowered = block.lower()
    for banned in ("next week", "q1", "q2", "q3", "q4", "ship by"):
        assert banned not in lowered, banned
    assert "2026-" not in block
    assert "DAG" not in block
    assert "task graph" not in lowered


def test_pages_workflow_publishes_landing_directory():
    assert "path: landing" in WORKFLOW
    assert "actions/deploy-pages" in WORKFLOW
    assert "actions/configure-pages" in WORKFLOW
    assert (ROOT / "landing" / ".nojekyll").exists()


def test_live_url_is_github_pages_not_jsdelivr():
    assert PAGES in README
    assert PAGES in REPO_README
    assert "jsdelivr.net" not in LANDING.lower()
    assert "vercel.app" not in LANDING.lower()
