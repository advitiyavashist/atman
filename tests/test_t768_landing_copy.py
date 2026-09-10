"""T-768: public landing is product-only knowledge copy and a truthful workflow."""

from pathlib import Path
from html.parser import HTMLParser

ROOT = Path(__file__).resolve().parents[1]
LANDING = (ROOT / "landing" / "index.html").read_text(encoding="utf-8")
CSS = (ROOT / "landing" / "styles.css").read_text(encoding="utf-8")
README = (ROOT / "landing" / "README.md").read_text(encoding="utf-8")


class _Ids(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []

    def handle_starttag(self, tag, attrs):
        found = dict(attrs).get("id")
        if found:
            self.ids.append(found)


def test_dom_keeps_team_runtime_flow_and_shipped_knowledge():
    parser = _Ids()
    parser.feed(LANDING)
    assert "main" in parser.ids
    assert "board" in parser.ids
    assert "surfaces" in parser.ids
    assert "start" in parser.ids
    assert "boundary-title" in parser.ids
    assert LANDING.index('id="surfaces"') < LANDING.index('id="boundary-title"')
    assert LANDING.index('id="boundary-title"') < LANDING.index('id="start"')
    flow = LANDING[LANDING.index("product-flow") : LANDING.index("layer-knowledge")]
    assert "Sit a seat" in flow
    assert "Take ready work" in flow
    assert "Read the handoff" in flow
    assert "Review or recover" in flow
    knowledge = LANDING[LANDING.index("layer-knowledge") : LANDING.index('id="start"')]
    assert "Team knowledge" in knowledge
    assert "Tickets keep who owns the work" in knowledge
    assert "new or returning seat" in knowledge


def test_no_brahman_or_research_jargon_on_public_landing():
    lowered = LANDING.lower()
    for banned in (
        "brahman",
        "model communication",
        "kv-cache",
        "latent transfer",
        "subgraph",
        "entity/relation",
        "vector database",
        "gliner",
    ):
        assert banned not in lowered, banned
    assert "research only" not in lowered
    assert "Brahman · research" not in LANDING
    assert "product-only" in README.lower() or "Do not present" in README


def test_responsive_capture_and_visual_tokens_stay():
    assert 'src="assets/t732-dashboard-1440.png"' in LANDING
    assert "assets/t732-dashboard-390.png 390w" in LANDING
    assert "assets/t732-dashboard-768.png 768w" in LANDING
    assert "--bg: #0c0e12" in CSS
    assert "--accent: #c4b49a" in CSS
    assert "--fg: #ece8e1" in CSS
    assets = ROOT / "landing" / "assets"
    for width in ("1440", "768", "390"):
        path = assets / ("t732-dashboard-%s.png" % width)
        assert path.is_file() and path.stat().st_size > 10_000, path
