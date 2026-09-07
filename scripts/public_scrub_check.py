#!/usr/bin/env python3
"""Pre-public release scrub gate for advitiyavashist/atman.

Scans tracked source paths (not local worktrees or venvs) for patterns that must
not ship in a public repo: real home-directory paths, operator handles, secret
shapes, and private Steer-only ops docs.

Usage:
    python3 scripts/public_scrub_check.py          # exit 0 = clean
    python3 scripts/public_scrub_check.py --json   # machine-readable report

Tip-of-tree scan only; full git history should be reviewed separately with
gitleaks/trufflehog before the visibility flip.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Paths we never scan (local-only or generated).
SKIP_PREFIXES = (
    ".git/",
    ".worktrees/",
    ".venv",
    "node_modules/",
    "ui/dist/",
)

# Files/dirs that must not exist on public main (private ops only).
FORBIDDEN_PATHS = (
    "docs/CROSS_REPO_PINS.md",
    "docs/INTEGRATION.md",
    "docs/messaging-plan.json",
    "docs/implementation-plan.json",
)

# Each rule: (name, regex, hint)
RULES: list[tuple[str, re.Pattern[str], str]] = [
    (
        "absolute-home-path",
        re.compile(r"/Users/[A-Za-z0-9._-]+/"),
        "Replace with a generic placeholder such as /home/agent/ or ~/your-checkout",
    ),
    (
        "downloads-checkout",
        re.compile(r"~/Downloads/(?:steer|tickets|atman)"),
        "Use ~/your-project or a neutral example path",
    ),
    (
        "operator-handle",
        re.compile(r"\bkavana\b", re.IGNORECASE),
        "Remove operator-specific handles from committed files",
    ),
    (
        "github-pat",
        re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),
        "Rotate and purge the token; never commit PATs",
    ),
    (
        "openai-key",
        re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
        "Rotate and purge the key",
    ),
    (
        "aws-key",
        re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
        "Rotate and purge the key",
    ),
    (
        "slack-token",
        re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
        "Rotate and purge the token",
    ),
]


def tracked_files() -> list[str]:
    out = subprocess.check_output(
        ["git", "ls-files"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL,
    )
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def should_scan(rel: str) -> bool:
    if any(rel.startswith(p) or ("/" + p) in rel for p in SKIP_PREFIXES):
        return False
    if rel.startswith(".worktrees"):
        return False
    return True


def scan_file(rel: str) -> list[dict]:
    path = ROOT / rel
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    hits: list[dict] = []
    in_tests = rel.startswith("tests/")
    for line_no, line in enumerate(text.splitlines(), 1):
        for name, pattern, hint in RULES:
            if in_tests and name in (
                "absolute-home-path",
                "downloads-checkout",
                "operator-handle",
            ):
                continue
            if pattern.search(line):
                hits.append({
                    "rule": name,
                    "file": rel,
                    "line": line_no,
                    "hint": hint,
                    "excerpt": line.strip()[:120],
                })
    return hits


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--json", action="store_true", help="print JSON report")
    args = ap.parse_args()

    missing_forbidden = [p for p in FORBIDDEN_PATHS if (ROOT / p).exists()]
    file_hits: list[dict] = []
    for rel in tracked_files():
        if should_scan(rel):
            file_hits.extend(scan_file(rel))

    report = {
        "root": str(ROOT),
        "forbidden_paths_present": missing_forbidden,
        "findings": file_hits,
        "clean": not missing_forbidden and not file_hits,
    }

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        if missing_forbidden:
            print("FORBIDDEN paths still present (remove before public):")
            for p in missing_forbidden:
                print(f"  - {p}")
        for hit in file_hits:
            print(
                f"{hit['file']}:{hit['line']} [{hit['rule']}] {hit['excerpt']}"
            )
            print(f"    -> {hit['hint']}")
        if report["clean"]:
            print("public_scrub_check: CLEAN")
        else:
            n = len(missing_forbidden) + len(file_hits)
            print(f"public_scrub_check: FAIL ({n} issue(s))")

    return 0 if report["clean"] else 1


if __name__ == "__main__":
    sys.exit(main())
