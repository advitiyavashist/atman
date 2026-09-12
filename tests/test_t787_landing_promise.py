"""T-787: Atman landing promise, philosophy, four unburied sentences, Contact us."""

from pathlib import Path
from html.parser import HTMLParser

ROOT = Path(__file__).resolve().parents[1]
LANDING = (ROOT / "landing" / "index.html").read_text(encoding="utf-8")
README = (ROOT / "landing" / "README.md").read_text(encoding="utf-8")
REPO_README = (ROOT / "README.md").read_text(encoding="utf-8")

PROMISE = "Atman coordinates the agents you already run."
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


def _between(start_id, end_id):
    start = LANDING.index('id="%s"' % start_id)
    end = LANDING.index('id="%s"' % end_id)
    return LANDING[start:end]


def test_page_order_is_promise_philosophy_then_four():
    parser = _Ids()
    parser.feed(LANDING)
    for needed in (
        "main",
        "philosophy",
        "how",
        "integration",
        "flow",
        "efficiency",
        "graph",
        "start",
    ):
        assert needed in parser.ids, needed
    assert LANDING.index("<h1>") < LANDING.index('id="philosophy"')
    assert LANDING.index('id="philosophy"') < LANDING.index('id="how"')
    assert LANDING.index('id="how"') < LANDING.index('id="surfaces"')
    assert LANDING.index('id="how"') < LANDING.index('id="start"')


def test_hero_is_one_clear_promise_sentence():
    hero = LANDING[LANDING.index("<h1>") : LANDING.index("</h1>")]
    assert PROMISE in hero
    assert "fewest turns" not in hero.lower()
    assert "local control plane" not in hero.lower()


def test_philosophy_states_the_not_line():
    philosophy = _between("philosophy", "how")
    lowered = philosophy.lower()
    assert "not a multi-agent framework" in lowered
    assert "not shared memory" in lowered
    assert "not a model router" in lowered
    assert "mail is the ticket board" in lowered
    assert "feel like using atman, not the provider" in lowered


def test_four_unburied_sentences():
    how = _between("how", "surfaces")
    assert "Connect Claude Code, Codex, Cursor, or your own harness" in how
    assert "the seat should feel like using Atman, not the provider" in how
    assert "Claim work from the board, inherit the last handoff, finish or recover" in how
    assert "Fewest turns and measured cost stay blank" in how
    assert "Work stays invisible until its dependencies are done" in how
    assert "Workflow dependency graph" in how
    assert "T-001" in how
    assert "T-002" in how
    assert "T-003" in how


def test_contact_us_is_visible_not_invented_email():
    parser = _Ids()
    parser.feed(LANDING)
    assert CONTACT in parser.hrefs
    header = LANDING[LANDING.index("<header") : LANDING.index("<main")]
    assert "Contact us" in header
    assert CONTACT in header
    assert "mailto:" not in LANDING.lower()


def test_repo_readme_leads_with_the_same_promise():
    assert PROMISE in REPO_README
    assert "Not a multi-agent framework, not shared memory, not a model router" in REPO_README
    assert CONTACT in REPO_README or "github.com/advitiyavashist/atman/issues" in REPO_README


def test_landing_readme_records_the_lock():
    assert "coordinates the agents you already run" in README.lower()
    assert "Contact us" in README or "GitHub issues" in README
