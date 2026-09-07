"""The client's error type, and why it is not a dataclass.

This module exists because of a bug that was invisible until it wasn't. The
first version of `ApiError` was a `@dataclass(frozen=True)` subclassing
`Exception`, which reads perfectly well and constructs fine. It only breaks
while an exception is *propagating*: `contextlib` assigns `__traceback__` on
the exception as it unwinds a `with`, and a frozen dataclass refuses the
assignment. The result is that the real failure is replaced by an unrelated
`FrozenInstanceError` at precisely the moment you are trying to read it.

Nothing in the route or supervisor suites caught it -- they all raise and catch
`ApiError` without unwinding a context manager on the way out -- so the fix
gets its own test rather than being trusted to a coincidence.
"""

from __future__ import annotations

import contextlib

import pytest

from ticket_board.runners import ApiError


def test_an_api_error_survives_propagating_through_a_context_manager():
    @contextlib.contextmanager
    def unwinding():
        yield

    with pytest.raises(ApiError) as caught:
        with unwinding():
            raise ApiError(409, "run_already_active", "Another runner holds it.",
                           {"epoch": 2})

    assert caught.value.code == "run_already_active"
    assert caught.value.details["epoch"] == 2
    assert caught.value.__traceback__ is not None


def test_an_api_error_carries_its_contract_fields_into_str():
    error = ApiError(422, "invalid_state_transition", "Not from responded.")
    assert error.status == 422
    assert "invalid_state_transition" in str(error)
    assert error.details is None
