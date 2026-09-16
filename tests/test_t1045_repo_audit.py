"""T-1045: public-tree audit cleanup.

Operator home is derived at runtime, demo receipts do not publish machine
paths, and contract fixtures have one on-disk source of truth.
"""
from __future__ import annotations

import importlib.util
import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
T1010 = ROOT / "tests" / "test_t1010_atm_user_facing.py"
VALIDATION = ROOT / "docs" / "demo" / "VALIDATION.md"
FIXTURES = ROOT / "tests" / "fixtures"
PLACEHOLDER_USERS = {"agent", "operator", "runner", "user", "someone", "op"}
USERS_PATH = re.compile(r"/Users/([A-Za-z0-9._-]+)")
MACHINE_PATH = re.compile(r"/private/(?:tmp|var/folders)/")


def _operator_home() -> str:
    return os.path.expanduser("~")


def _load_t1010():
    spec = importlib.util.spec_from_file_location("t1045_t1010", T1010)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_t1010_derives_operator_home_and_still_forbids_it():
    mod = _load_t1010()
    src = T1010.read_text(encoding="utf-8")
    home = _operator_home()
    assert home not in src
    if home in mod.PLACEHOLDER_USER_HOMES:
        pytest.skip("process HOME is a documented placeholder")
    assert home in mod.FORBIDDEN_PUBLIC


def test_tracked_files_omit_operator_home_literal():
    home = _operator_home()
    if home in {f"/Users/{name}" for name in PLACEHOLDER_USERS}:
        pytest.skip("process HOME is a documented placeholder")
    listed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout.split(b"\0")
    leaks = []
    for raw in listed:
        if not raw:
            continue
        path = ROOT / raw.decode()
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if home in text:
            leaks.append(path.relative_to(ROOT).as_posix())
    assert leaks == [], "operator home published in %s" % leaks


def test_tracked_users_paths_are_placeholders_only():
    listed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout.split(b"\0")
    bad = []
    for raw in listed:
        if not raw:
            continue
        path = ROOT / raw.decode()
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        rel = path.relative_to(ROOT).as_posix()
        for match in USERS_PATH.finditer(text):
            if match.group(1) not in PLACEHOLDER_USERS:
                bad.append("%s:%s" % (rel, match.group(0)))
    assert bad == [], "non-placeholder /Users/ path in %s" % bad


def test_validation_md_has_no_absolute_machine_path():
    # T-1039/#219 deleted the rehearsal kit. If the file returns, it must
    # not republish machine run directories.
    if not VALIDATION.is_file():
        return
    text = VALIDATION.read_text(encoding="utf-8")
    assert MACHINE_PATH.search(text) is None
    assert "/private/tmp/" not in text
    assert "/private/var/folders/" not in text


def test_contract_fixture_copies_are_gone():
    copies = []
    for other in (
        ROOT / "ui" / "src" / "fixtures" / "data",
        ROOT / "tests" / "ui" / "api" / "fixtures" / "data",
    ):
        if not other.exists():
            continue
        copies.extend(
            p.relative_to(ROOT).as_posix() for p in other.rglob("*.json")
        )
    assert copies == [], "duplicate fixture blobs remain: %s" % copies
    assert (FIXTURES / "manifest.json").is_file()
    assert (FIXTURES / "messages" / "response-create-channel.json").is_file()
