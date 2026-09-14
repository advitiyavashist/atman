"""Console-script entry: run the full tickets.py runtime (T-881).

`ticket_board.cli` stays the packaged subset used by wheel import tests.
The `tickets` script README first-run documents (`ui`, `quickstart`,
`--version`) live on root tickets.py.
"""
from __future__ import annotations

import runpy
import sys
from pathlib import Path


def tickets_py_path():
    here = Path(__file__).resolve()
    candidates = [
        here.parents[2] / "tickets.py",
        here.with_name("oracle_tickets.py"),
        Path(sys.prefix) / "share" / "ticket-board" / "tickets.py",
    ]
    for path in candidates:
        if path.is_file():
            return path
    return None


def main(argv=None):
    if argv is not None:
        sys.argv = list(argv)
    path = tickets_py_path()
    if path is None:
        sys.exit(
            "tickets full runtime not found (need repo tickets.py). "
            "Use `pip install -e .` from an Atman checkout, or ./install.sh"
        )
    sys.argv[0] = str(path)
    runpy.run_path(str(path), run_name="__main__")
