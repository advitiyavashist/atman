"""T-971 / T-1047: Pages landing leads with atm and the accept-gate promise."""

import re
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LANDING_DIR = ROOT / "landing"
LANDING = (LANDING_DIR / "index.html").read_text(encoding="utf-8")
README = (LANDING_DIR / "README.md").read_text(encoding="utf-8")
PAGES = "https://advitiyavashist.github.io/atman/"
CAPTURE = "assets/t971-app-work-1440.png"
PROMISE = "The next ticket opens only after someone else accepts that commit."


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


def test_head_and_hero_lead_with_atm_not_tickets_ui():
    head = _head()
    assert "atm" in head
    assert "tickets ui" not in head
    hero = LANDING[LANDING.index('class="hero"') : LANDING.index('id="path"')]
    assert "<code>atm</code>" in hero
    assert "tickets ui" not in hero
    assert "See tickets ui" not in LANDING
    assert PROMISE in hero


def test_tickets_is_only_the_compatibility_alias():
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


def test_og_image_stays_the_checked_in_capture_and_hero_uses_artifact_gif():
    assert PAGES + CAPTURE in _head()
    assert "../docs/brand/evidence" not in LANDING
    hero = LANDING[LANDING.index('class="hero"') : LANDING.index('id="path"')]
    assert "assets/demo/hero.gif" in hero
    assert "../docs/assets/demo/hero.gif" not in LANDING
    assert "HERO DEMO PLACEHOLDER" not in hero
    path = LANDING_DIR / "assets" / "t971-app-work-1440.png"
    assert path.is_file() and path.stat().st_size > 10_000, path


def test_status_links_the_readme_table_instead_of_duplicating_it():
    block = LANDING[LANDING.index('id="status"') : LANDING.index('id="start"')]
    assert "README.md#preview-status-and-limitations" in block
    assert "<table" not in block
    assert "brew install" not in LANDING


def _copied_into_landing():
    workflow = (ROOT / ".github" / "workflows" / "pages.yml").read_text(encoding="utf-8")
    dests = set()
    for _src, dest in re.findall(r"^\s*cp(?:\s+-\S+)*\s+(\S+)\s+(\S+)\s*$", workflow, flags=re.M):
        if dest.startswith("landing/"):
            dests.add(dest[len("landing/") :])
    return dests


def test_no_invented_numbers_or_dead_local_references():
    parser = _Refs()
    parser.feed(LANDING)
    assert parser.local, "expected local stylesheet, icon and image references"
    copied = _copied_into_landing()
    for ref in parser.local:
        assert (LANDING_DIR / ref).is_file() or ref in copied, ref
    for banned in ("<dd>0</dd>", "<dd>$0", "roster of", "v1.0", "1.0.0", "brew install"):
        assert banned not in LANDING, banned
    for doc in ("docs/first-session.md", "docs/byoa.md"):
        assert doc in LANDING and (ROOT / doc).is_file(), doc


def test_landing_readme_records_the_atm_lock_and_capture_provenance():
    assert "`atm` is primary" in README
    assert "t971-app-work-1440.png" in README
    assert "never the live board" in README
    assert "TICKETS_DIR" in README
