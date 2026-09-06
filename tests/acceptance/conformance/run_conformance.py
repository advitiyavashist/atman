"""CLI entrypoint for the T-200 conformance harness.

    BOARD_URL=http://127.0.0.1:4319/api/v1 python -m tests.acceptance.conformance.run_conformance

With no `BOARD_URL`, runs against the in-process fixture-replay stub instead
of failing -- this is what proves the harness itself works (see
`test_self_check.py` for the accompanying mutation check). T-180 (the real
board API) does not exist yet, so until T-185 points `BOARD_URL` at a real
server, every report this produces is a stub report, and says so.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from . import harness
from .report import render
from .stub_server import FixtureReplayStub

REPO = Path(__file__).resolve().parents[3]
REPORT_PATH = REPO / "docs" / "acceptance" / "conformance-report.md"

STUB_NOTE = (
    "**Target is the in-process fixture-replay stub, not a real server.** "
    "T-180 (the board API) does not exist yet. This report is a harness "
    "self-check: it proves the harness correctly matches status codes and "
    "schemas against known-good fixtures. It is not evidence about any real "
    "server's behaviour."
)
LIVE_NOTE = (
    "Target is `BOARD_URL` from the environment. Whether this is the real "
    "T-180 server or another stub is outside this harness's knowledge -- see "
    "the run command below for exactly what was checked."
)


def main() -> int:
    board_url = os.environ.get("BOARD_URL")
    stub = None
    note = LIVE_NOTE
    if not board_url:
        stub = FixtureReplayStub()
        board_url = stub.start()
        note = STUB_NOTE

    try:
        results = harness.run(board_url)
    finally:
        if stub is not None:
            stub.stop()

    report = render(results, base_url=board_url, note=note)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report)
    print(report)

    failed = [r for r in results if not r.passed]
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
