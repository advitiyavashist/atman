import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ticket_board.storage import BoardStore  # noqa: E402


@pytest.fixture()
def db_path(tmp_path):
    return tmp_path / "board.sqlite3"


@pytest.fixture()
def store(db_path):
    s = BoardStore(db_path)
    try:
        yield s
    finally:
        s.close()


@pytest.fixture()
def project(store):
    return store.create_project("Demo")


@pytest.fixture()
def agent(store, project):
    return store.create_agent(project["id"], "backend-1", role="backend")
