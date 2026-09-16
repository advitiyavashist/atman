"""T-787 / T-1047: one promise on both surfaces; four landing sections."""

from pathlib import Path
from html.parser import HTMLParser

ROOT = Path(__file__).resolve().parents[1]
LANDING = (ROOT / "landing" / "index.html").read_text(encoding="utf-8")
README = (ROOT / "landing" / "README.md").read_text(encoding="utf-8")
REPO_README = (ROOT / "README.md").read_text(encoding="utf-8")

PROMISE = "The next ticket opens only after someone else accepts that commit."
CONTACT = "https://github.com/advitiyavashist/atman/issues"


class _Ids(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.hrefs = []

    def handle_starttag(self, tag, attrs):
        found = dict(attrs)
        if found.get("id"):
            self.ids.append(found["id"])
        if found.get("href"):
            self.hrefs.append(found["href"])


def test_page_order_is_promise_path_status_start():
    parser = _Ids()
    parser.feed(LANDING)
    for needed in ("main", "promise", "path", "status", "start"):
        assert needed in parser.ids, needed
    assert LANDING.index("<h1>") < LANDING.index('id="path"')
    assert LANDING.index('id="path"') < LANDING.index('id="status"')
    assert LANDING.index('id="status"') < LANDING.index('id="start"')
    for gone in ("philosophy", "how", "workflow", "per-turn", "roadmap", "surfaces"):
        assert gone not in parser.ids, gone


def test_hero_is_one_clear_promise_sentence():
    hero = LANDING[LANDING.index("<h1>") : LANDING.index("</h1>")]
    assert PROMISE in hero
    assert "fewest turns" not in hero.lower()
    assert "coordinates the agents you already run" not in hero.lower()


def test_contact_is_github_issues_not_invented_email():
    parser = _Ids()
    parser.feed(LANDING)
    assert CONTACT in parser.hrefs
    header = LANDING[LANDING.index("<header") : LANDING.index("<main")]
    assert "GitHub issues" in header
    assert CONTACT in header
    assert "mailto:" not in LANDING.lower()


def test_repo_readme_leads_with_the_same_promise():
    assert PROMISE in REPO_README
    assert CONTACT in REPO_README or "github.com/advitiyavashist/atman/issues" in REPO_README
    assert "coordinates the agents you already run" not in REPO_README.lower()


def test_landing_readme_records_the_lock():
    assert PROMISE.lower() in README.lower()
    assert "GitHub issues" in README
    assert "Four sections only" in README or "four sections only" in README.lower()


def test_start_creates_a_project_before_opening_atm_ui():
    start = LANDING[LANDING.index('id="start"') :]
    assert "./install.sh --prefix" in start
    assert "unset TICKETS_DIR" in start
    assert start.index("atm where") < start.index("atm quickstart") < start.index("atm ui")
    assert "CoS is cursor" not in start
    assert "atm ui" in start
    assert "atm next" not in start
    assert "alice" not in start
