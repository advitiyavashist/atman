"""T-1054: Pages landing refs must stay inside the published artifact."""

import re
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LANDING_DIR = ROOT / "landing"
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "pages.yml"
WORKFLOW = WORKFLOW_PATH.read_text(encoding="utf-8")
SKIP_PREFIXES = ("http://", "https://", "#", "mailto:", "data:", "//")


class _Refs(HTMLParser):
    def __init__(self):
        super().__init__()
        self.local = []

    def handle_starttag(self, tag, attrs):
        found = dict(attrs)
        for key in ("href", "src"):
            value = found.get(key)
            if not value or value.startswith(SKIP_PREFIXES):
                continue
            self.local.append(value)
        poster = found.get("poster")
        if poster and not poster.startswith(SKIP_PREFIXES):
            self.local.append(poster)
        for candidate in (found.get("srcset") or "").split(","):
            url = candidate.strip().split(" ")[0]
            if url and not url.startswith(SKIP_PREFIXES):
                self.local.append(url)


def _local_refs(html_text):
    parser = _Refs()
    parser.feed(html_text)
    return parser.local


def _copied_into_landing_artifact():
    """Relative paths under landing/ that pages.yml copies into the artifact."""
    dests = set()
    for match in re.finditer(r"\bcp\s+(.+)", WORKFLOW):
        tokens = match.group(1).strip().split()
        if len(tokens) < 2:
            continue
        dest = tokens[-1]
        sources = tokens[:-1]
        if not dest.startswith("landing/"):
            continue
        dest_rel = dest[len("landing/"):]
        if dest.endswith("/") or dest_rel.endswith("demo"):
            dest_dir = dest_rel.rstrip("/")
            for src in sources:
                dests.add("%s/%s" % (dest_dir, Path(src).name))
        else:
            dests.add(dest_rel)
    return dests


def test_landing_local_refs_stay_inside_the_pages_artifact():
    copied = _copied_into_landing_artifact()
    assert copied, "pages.yml must copy demo media into the landing artifact"
    assert WORKFLOW.index("cp ") < WORKFLOW.index("upload-pages-artifact")

    missing = []
    for html in sorted(LANDING_DIR.glob("*.html")):
        for ref in _local_refs(html.read_text(encoding="utf-8")):
            assert ".." not in Path(ref).parts, "%s %s escapes landing/" % (
                html.name, ref
            )
            if html.name != "index.html":
                continue
            if (LANDING_DIR / ref).is_file() or ref in copied:
                continue
            missing.append(ref)
    assert not missing, "landing/index.html refs missing from landing/ and pages.yml: %s" % (
        ", ".join(missing)
    )
    for dest in copied:
        source = ROOT / "docs" / "assets" / "demo" / Path(dest).name
        assert source.is_file(), source
