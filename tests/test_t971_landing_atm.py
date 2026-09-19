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


def test_og_image_stays_the_checked_in_capture_and_hero_has_no_demo_img():
    assert PAGES + CAPTURE in _head()
    assert "../docs/brand/evidence" not in LANDING
    hero = LANDING[LANDING.index('class="hero"') : LANDING.index('id="path"')]
    assert "assets/demo/hero.gif" in hero
    assert "../docs/assets/demo/hero.gif" not in hero
    assert "HERO DEMO PLACEHOLDER" not in hero
    path = LANDING_DIR / "assets" / "t971-app-work-1440.png"
    assert path.is_file() and path.stat().st_size > 10_000, path


def _status_block():
    return LANDING[LANDING.index('id="status"') : LANDING.index('id="start"')]


def test_status_links_the_readme_table_instead_of_duplicating_it():
    """Status points at the README table; it never restates a table or an
    install recipe. Install commands live only in the install terminals."""
    block = _status_block()
    assert "README.md#preview-status-and-limitations" in block
    assert "<table" not in block
    assert "<pre" not in block
    for recipe in ("brew tap", "brew install", "install.sh", "git clone", "pipx install", "uv tool install"):
        assert recipe not in block, recipe


def test_no_invented_numbers_or_dead_local_references():
    parser = _Refs()
    parser.feed(LANDING)
    assert parser.local, "expected local stylesheet, icon and image references"
    for ref in parser.local:
        if Path(ref).as_posix().startswith("assets/demo/"):
            continue
        assert (LANDING_DIR / ref).is_file(), ref
    for banned in ("<dd>0</dd>", "<dd>$0", "roster of", "v1.0", "1.0.0"):
        assert banned not in LANDING, banned
    for doc in ("docs/first-session.md", "docs/byoa.md"):
        assert doc in LANDING and (ROOT / doc).is_file(), doc


_INSTALL_CMD = re.compile(
    r"\b(?:brew install|brew tap|pipx install|pip3? install|uv tool install|npm install|"
    r"apt(?:-get)? install|cargo install|go install)\s+[A-Za-z0-9@/_.-]*[A-Za-z0-9]"
)
_NEGATED = ("unrelated project", "no pypi package")


def _formula_release():
    """The committed mirror of the tap's Formula/atman.rb. Returns the release
    version it installs, or None if it does not name a published release
    (placeholder sha256, non-release URL, or a version this checkout is not)."""
    formula = (ROOT / "packaging/homebrew/atman.rb").read_text(encoding="utf-8")
    url = re.search(
        r'url "https://github\.com/advitiyavashist/atman/releases/download/v(\d+\.\d+\.\d+)/atman-(\d+\.\d+\.\d+)\.tar\.gz"',
        formula,
    )
    sha = re.search(r'sha256 "([0-9a-f]{64})"', formula)
    version = re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"), re.M)
    if not (url and sha and version) or url.group(1) != url.group(2) or url.group(1) != version.group(1):
        return None
    return url.group(1)


def _homebrew_claim_is_real():
    release = _formula_release()
    if release is None:
        return False
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    row = next((line for line in readme.splitlines() if line.startswith("| Install: Homebrew")), "")
    return "published" in row and "`v%s`" % release in row and "advitiyavashist/homebrew-tap" in row


def test_every_install_claim_is_a_path_that_works():
    """No install command on the page unless it is backed by evidence in this
    checkout: brew only while the committed tap formula pins a real sha256 for
    this package version's release and the README row says it is published;
    a pip command only as the stated warning that it is not ours; nothing else."""
    text = re.sub(r"<[^>]+>", "", LANDING)
    claims = [(m.group(0), m.start()) for m in _INSTALL_CMD.finditer(text)]
    for claim, at in claims:
        if claim in ("brew tap advitiyavashist/tap", "brew install atman"):
            assert _homebrew_claim_is_real(), "%s claimed but the tap formula names no published release" % claim
            continue
        if claim == "pip install atm":
            sentence = text[max(0, at - 120) : at + 120].lower()
            assert any(n in sentence for n in _NEGATED), claim
            continue
        raise AssertionError("install claim with no working path behind it: %s" % claim)


def test_landing_readme_records_the_atm_lock_and_capture_provenance():
    assert "`atm` is primary" in README
    assert "t971-app-work-1440.png" in README
    assert "never the live board" in README
    assert "TICKETS_DIR" in README
