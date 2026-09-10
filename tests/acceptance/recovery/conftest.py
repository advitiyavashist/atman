"""Disposable board only. Never pointed at the live Steer board."""

import pytest

from tests.acceptance.messaging.live_board import LiveBoard


@pytest.fixture()
def board(tmp_path):
    live = LiveBoard(tmp_path)
    try:
        yield live
    finally:
        live.close()
