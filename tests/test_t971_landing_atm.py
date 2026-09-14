"""T-971: the GitHub Pages landing leads with atm and only claims what main ships.

Pins: atm is the CLI on the page (tickets appears only as the alias), the app
capture is a current checked-in atm ui capture under landing/assets, shipped vs
in-review vs planned labels exist, and every local link or image resolves.
"""

import re
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LANDING_DIR = ROOT / "landing"
LANDING = (LANDING_DIR / "index.html").read_text(encoding="utf-8")
CSS = (LANDING_DIR / "styles.css").read_text(encoding="utf-8")
README = (LANDING_DIR / "README.md").read_text(encoding="utf-8")
PAGES = "https://advitiyavashist.github.io/atman/"
CAPTURE = "assets/t971-app-work-1440.png"


class _Refs(HTMLParser):
    def __init__(self):
        super().__init__()
        self.local = []

    def handle_starttag(self, tag, attrs):
        found = dict(attrs)
        for key in ("href", "src"):
            value = found.get(key)
            if not value or value.startswith(("http://", "https://", "#", "mailto:")):
                continue
            self.local.append(value)
        for candidate in (found.get("srcset") or "").split(","):
            url = candidate.strip().split(" ")[0]
            if url:
                self.local.append(url)


def _head():
    return LANDING[: LANDING.index("</head>")]


def _block(start_id, end_id):
    return LANDING[LANDING.index('id="%s"' % start_id) : LANDING.index('id="%s"' % end_id)]


def test_head_and_hero_lead_with_atm_not_tickets_ui():
    head = _head()
    assert "atm" in head
    assert "tickets ui" not in head
    hero = LANDING[LANDING.index('class="hero"') : LANDING.index('id="app"')]
    assert "<code>atm</code>" in hero
    assert "tickets ui" not in hero
    assert "See tickets ui" not in LANDING
    assert "Atman coordinates the agents you already run." in hero


def test_tickets_is_only_the_compatibility_alias():
    # Every <code>tickets</code> on the page sits in the alias sentence, never as the product name,
    # and no command is spelled with the old name. Prose such as "done tickets" is not a CLI mention.
    mentions = list(re.finditer(r"<code>tickets</code>", LANDING))
    assert mentions
    for match in mentions:
        window = LANDING[max(0, match.start() - 200) : match.end() + 200].lower()
        assert "alias" in window, LANDING[match.start() - 60 : match.end() + 60]
    assert "compatibility alias" in LANDING
    assert not re.search(r"\btickets (connect|ui|route|next|review|hooks|plan|msg|quickstart|show)\b", LANDING)


def test_one_source_install_creates_sample_then_opens_atm_ui():
    start = LANDING[LANDING.index('id="start"') :]
    panels = re.findall(r"<pre[^>]*>(.*?)</pre>", start, flags=re.S)
    assert len(panels) == 1
    for panel in panels:
        assert "./install.sh" in panel
        assert "./install.sh --prefix" in panel
        assert "unset TICKETS_DIR" in panel
        assert panel.index("atm where") < panel.index("atm quickstart") < panel.index("atm ui")
        assert "tickets connect" not in panel and "tickets ui" not in panel
        assert "atm next" not in panel and "alice" not in panel


def test_app_capture_is_current_checked_in_and_is_the_og_image():
    block = _block("app", "philosophy")
    assert 'src="%s"' % CAPTURE in block
    assert "assets/t971-app-work-768.png" in block
    assert "t732-dashboard" not in LANDING
    assert PAGES + CAPTURE in _head()
    assert "../docs/brand/evidence" not in LANDING
    for name in ("t971-app-work-1440.png", "t971-app-work-768.png", "t889-work-graph-1400.png"):
        path = LANDING_DIR / "assets" / name
        assert path.is_file() and path.stat().st_size > 10_000, path
    assert "atm quickstart" in block
    assert "sample project" in block.lower()
    assert "this site shows a product capture" in block.lower()
    assert "your live team appears in the local app" in block.lower()


def test_status_strip_separates_on_main_from_in_review_and_planned():
    block = _block("app", "philosophy")
    strip = block[block.index('class="status-strip"') : block.index('class="command-board"')]
    assert strip.index("<dt>On main</dt>") < strip.index("<dt>In review") < strip.index("<dt>Planned")
    assert "not on main yet" in strip
    assert "not built or not published" in strip
    # In-review claims are the T-810 first screen; they must not be sold as shipped.
    on_main = strip[strip.index("<dt>On main</dt>") : strip.index("<dt>In review")]
    assert "first screen" not in on_main.lower()
    assert "receipt" not in on_main.lower()
    # Planned rows name what the repository README calls not ready or not published.
    planned = strip[strip.index("<dt>Planned") :]
    assert "Homebrew" in planned and "not published" in planned
    assert "brew install" not in LANDING
    assert "status-strip" in CSS


def test_workflow_section_shows_the_merged_work_graph_capture():
    block = _block("workflow", "per-turn")
    assert "atm ui" in block
    assert 'src="assets/t889-work-graph-1400.png"' in block
    assert "on <code>main</code>" in block
    assert "waiting on T-002" in block


def test_no_invented_numbers_or_dead_local_references():
    parser = _Refs()
    parser.feed(LANDING)
    assert parser.local, "expected local stylesheet, icon and image references"
    for ref in parser.local:
        assert (LANDING_DIR / ref).is_file(), ref
    for banned in ("<dd>0</dd>", "<dd>$0", "roster of", "v1.0", "1.0.0", "brew install"):
        assert banned not in LANDING, banned
    for doc in ("docs/first-session.md", "docs/byoa.md"):
        assert doc in LANDING and (ROOT / doc).is_file(), doc


def test_landing_readme_records_the_atm_lock_and_capture_provenance():
    assert "`atm` is primary" in README
    assert "t971-app-work-1440.png" in README
    assert "never the live board" in README
    assert "TICKETS_DIR" in README
