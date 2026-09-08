"""CLI entrypoint for the T-200 conformance harness.

    BOARD_URL=http://127.0.0.1:4319/api/v1 python -m tests.acceptance.conformance.run_conformance

With no `BOARD_URL`, runs against the in-process fixture-replay stub instead
of failing -- this is what proves the harness itself works (see
`test_self_check.py` for the accompanying mutation check). T-180 (the real
board API) does not exist yet, so until T-185 points `BOARD_URL` at a real
server, every report this produces is a stub report, and says so.

By default the report is written to a scratch output directory, not the
committed `docs/acceptance/conformance-report.md` -- every local or ad hoc
run used to overwrite that committed file, so a stale stub-only report from
someone's laptop could silently replace the last real, reviewed run. Pass
`--commit-report` to write the committed copy on purpose.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

from . import harness
from .report import render
from .stub_server import FixtureReplayStub

REPO = Path(__file__).resolve().parents[3]
COMMITTED_REPORT_PATH = REPO / "docs" / "acceptance" / "conformance-report.md"
DEFAULT_OUTPUT_DIR = Path(tempfile.gettempdir()) / "ticket-board-conformance"

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


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--commit-report", action="store_true",
        help="Write the committed docs/acceptance/conformance-report.md "
             "instead of a scratch output directory.",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR,
        help=f"Where to write the report when not committing it "
             f"(default: {DEFAULT_OUTPUT_DIR}).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
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
    report_path = (COMMITTED_REPORT_PATH if args.commit_report
                   else args.output_dir / "conformance-report.md")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report)
    print(f"report written to {report_path}")
    print(report)

    failed = [r for r in results if not r.passed]
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
