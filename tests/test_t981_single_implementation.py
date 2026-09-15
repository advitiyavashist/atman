"""T-981: packaged modules must not also be importable from the repo root."""
import importlib.util
import re
from pathlib import Path

import ticket_board.board_backup as pkg_backup
import ticket_board.ticket_coordination as pkg_coord

ROOT = Path(__file__).resolve().parents[1]
OPERATOR_HOME_RE = re.compile(
    r"(?<![A-Za-z0-9_./-])/(?:Users|home)/([A-Za-z0-9_.-]+)(?:/[A-Za-z0-9_.<>-]+)*"
)
PLACEHOLDER_HOME_SEGMENTS = {"agent", "operator", "runner", "user", "someone"}
INTERNAL_NAMES = (
    "HISTORY_REWRITE",
    "PUBLIC_PREP",
    "VISIBILITY_FLIP",
    "README-safe-clear",
    "prototype.html",
)


def test_root_does_not_ship_duplicate_packaged_modules():
    for name in ("board_backup.py", "ticket_coordination.py"):
        assert not (ROOT / name).exists(), "root still ships a second %s" % name


def test_packaged_modules_resolve_to_src_ticket_board():
    assert Path(pkg_backup.__file__).resolve() == (
        ROOT / "src" / "ticket_board" / "board_backup.py"
    ).resolve()
    assert Path(pkg_coord.__file__).resolve() == (
        ROOT / "src" / "ticket_board" / "ticket_coordination.py"
    ).resolve()


def test_top_level_names_are_not_importable():
    """A checkout must not expose two import paths for the same module."""
    assert importlib.util.find_spec("board_backup") is None
    assert importlib.util.find_spec("ticket_coordination") is None


def test_public_onboarding_does_not_link_internal_hygiene_docs():
    paths = [ROOT / "README.md", ROOT / "docs" / "first-session.md"]
    onboarding = ROOT / "docs" / "onboarding"
    if onboarding.is_dir():
        paths.extend(p for p in onboarding.iterdir() if p.is_file())
    leaked = []
    for path in paths:
        text = path.read_text()
        for name in INTERNAL_NAMES:
            if name in text:
                leaked.append("%s mentions %s" % (path.relative_to(ROOT), name))
    assert not leaked, "public onboarding still points at internal notes: " + ", ".join(leaked)


def test_docs_and_scripts_do_not_name_a_real_operator_home():
    suffixes = {".py", ".md", ".html", ".txt", ".yaml", ".yml", ".json", ".sh"}
    found = []
    for folder in (ROOT / "docs", ROOT / "scripts"):
        for path in folder.rglob("*"):
            if not path.is_file() or path.suffix not in suffixes:
                continue
            for match in OPERATOR_HOME_RE.finditer(path.read_text()):
                if match.group(1) in PLACEHOLDER_HOME_SEGMENTS:
                    continue
                found.append("%s -> %s" % (path.relative_to(ROOT), match.group(0)))
    assert not found, "docs/scripts name a real operator home: " + ", ".join(found)
