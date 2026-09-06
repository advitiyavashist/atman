"""PROVE THE HARNESS ITSELF (T-200 acceptance bar).

1. Against an unmutated fixture-replay stub, every row must PASS.
2. Against a stub with one response mutated (wrong type, or a dropped
   required field), only the row(s) for that route must FAIL -- everything
   else must keep passing, so a real drift shows up as a localized signal
   and not a wall of unrelated red.
"""

from __future__ import annotations

import copy

import pytest

from . import harness
from .stub_server import FixtureReplayStub


@pytest.fixture
def running_stub():
    def _start(mutations=None):
        stub = FixtureReplayStub(mutations=mutations)
        base_url = stub.start()
        return stub, base_url

    started = []

    def factory(mutations=None):
        stub, base_url = _start(mutations)
        started.append(stub)
        return base_url

    yield factory
    for stub in started:
        stub.stop()


def test_all_pass_against_the_unmutated_stub(running_stub):
    base_url = running_stub()
    results = harness.run(base_url)

    failed = [r for r in results if not r.passed]
    assert not failed, "\n".join(f"{r.label} ({r.kind}): {r.errors}" for r in failed)
    assert len(results) > 40, "expected one row per case plus one actor-reject row per mutation"


def test_wrong_type_in_a_response_is_caught(running_stub):
    def break_type(body):
        body = copy.deepcopy(body)
        body["counts"] = "not-an-object"
        return body

    base_url = running_stub(mutations={"getOverview": break_type})
    results = harness.run(base_url)
    by_label = {r.label: r for r in results}

    assert not by_label["getOverview"].passed
    assert any("counts" in e for e in by_label["getOverview"].errors)

    others_failed = [r.label for r in results if r.label != "getOverview" and not r.passed]
    assert not others_failed, f"mutation to getOverview leaked into: {others_failed}"


def test_dropped_required_field_in_a_response_is_caught(running_stub):
    def drop_id(body):
        body = copy.deepcopy(body)
        del body["id"]
        return body

    base_url = running_stub(mutations={"claimTicket": drop_id})
    results = harness.run(base_url)
    by_label = {r.label: r for r in results}

    assert not by_label["claimTicket"].passed
    assert any("'id' is a required property" in e for e in by_label["claimTicket"].errors)

    # The actor-reject row for the same route is a different request/response
    # path (it never reaches the mutated success body) and must be unaffected.
    assert by_label["claimTicket[actor-reject]"].passed

    others_failed = [
        r.label for r in results
        if r.label not in ("claimTicket", "claimTicket[actor-reject]") and not r.passed
    ]
    assert not others_failed, f"mutation to claimTicket leaked into: {others_failed}"
