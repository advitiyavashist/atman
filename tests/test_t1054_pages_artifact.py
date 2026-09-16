"""T-1054: Pages artifact must contain every landing HTML local asset."""

from __future__ import annotations

import re
import subprocess
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LANDING = ROOT / "landing"
WORKFLOW = (ROOT / ".github" / "workflows" / "pages.yml").read_text(encoding="utf-8")
HERO_SRC = "docs/assets/demo/hero.gif"
HERO_DEST = "landing/assets/demo/hero.gif"
HERO_REF = "assets/demo/hero.gif"


class _Refs(HTMLParser):
    def __init__(self):
        super().__init__()
        self.local = []

    def handle_starttag(self, tag, attrs):
        found = dict(attrs)
        for key in ("href", "src"):
            value = found.get(key)
            if not value or value.startswith(("http://", "https://", "#", "mailto:", "data:")):
                continue
            self.local.append(value)
        for candidate in (found.get("srcset") or "").split(","):
            url = candidate.strip().split(" ")[0]
            if not url or url.startswith(("http://", "https://", "#", "mailto:", "data:")):
                continue
            self.local.append(url)


def _copied_into_landing():
    dests = {}
    for src, dest in re.findall(r"^\s*cp(?:\s+-\S+)*\s+(\S+)\s+(\S+)\s*$", WORKFLOW, flags=re.M):
        assert not Path(src).is_absolute(), src
        assert (ROOT / src).is_file(), src
        assert dest.startswith("landing/"), dest
        dests[dest[len("landing/") :]] = src
    return dests


def test_pages_workflow_copies_hero_gif_into_landing_artifact():
    assert "mkdir -p landing/assets/demo" in WORKFLOW
    assert "cp %s %s" % (HERO_SRC, HERO_DEST) in WORKFLOW
    assert "path: landing" in WORKFLOW
    assert "docs/assets/demo/**" in WORKFLOW
    assert (ROOT / HERO_SRC).is_file()
    listed = subprocess.check_output(
        ["git", "ls-files", "-z", "landing/assets/demo"],
        cwd=ROOT,
    )
    assert listed == b"", listed


def test_landing_html_stays_inside_the_published_artifact():
    copied = _copied_into_landing()
    assert HERO_REF in copied
    htmls = sorted(LANDING.glob("*.html"))
    assert htmls
    for html in htmls:
        parser = _Refs()
        parser.feed(html.read_text(encoding="utf-8"))
        assert parser.local, html
        for ref in parser.local:
            parts = Path(ref).parts
            assert ".." not in parts, ref
            assert not ref.startswith("/"), ref
            on_disk = LANDING / ref
            assert on_disk.is_file() or ref in copied, ref
