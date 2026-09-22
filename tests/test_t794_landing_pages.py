"""T-794 / T-1047: Pages still publish landing/; four sections, one promise."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LANDING = (ROOT / "landing" / "index.html").read_text(encoding="utf-8")
README = (ROOT / "landing" / "README.md").read_text(encoding="utf-8")
REPO_README = (ROOT / "README.md").read_text(encoding="utf-8")
WORKFLOW = (ROOT / ".github" / "workflows" / "pages.yml").read_text(encoding="utf-8")
PAGES = "https://advitiyavashist.github.io/atman/"
PROMISE = "The next ticket opens only after someone else accepts that commit."


def test_first_class_section_ids_and_order():
    for needed in ("promise", "path", "status", "start"):
        assert 'id="%s"' % needed in LANDING, needed
    assert LANDING.index('id="promise"') < LANDING.index('id="path"')
    assert LANDING.index('id="path"') < LANDING.index('id="status"')
    assert LANDING.index('id="status"') < LANDING.index('id="start"')
    for gone in ("philosophy", "workflow", "per-turn", "roadmap"):
        assert 'id="%s"' % gone not in LANDING, gone


def test_hero_and_readme_share_the_promise():
    assert PROMISE in LANDING[LANDING.index('id="promise"') : LANDING.index('id="path"')]
    assert PROMISE in REPO_README
    assert "127.0.0.1" not in LANDING
    assert "localhost" not in LANDING.lower()


def test_path_is_install_to_accepted_dependent():
    block = LANDING[LANDING.index('id="path"') : LANDING.index('id="status"')]
    assert "atm objective" in block
    assert "atm accept" in block
    assert "atm next" in block
    assert "T-002 opens" in block


def test_status_points_at_the_readme_table():
    block = LANDING[LANDING.index('id="status"') : LANDING.index('id="start"')]
    assert "README.md#preview-status-and-limitations" in block


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
