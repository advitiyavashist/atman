import pytest

from .live_board import LiveBoard


@pytest.fixture()
def board(tmp_path):
    live = LiveBoard(tmp_path)
    try:
        yield live
    finally:
        live.close()
