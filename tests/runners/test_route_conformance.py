"""Every runner-route response validates against the frozen T-178 schemas.

The storage records were already checked against `WakeJob`, `Run` and
`RunnerLease` by T-179's suite. What was not checked is what these four new
routes actually put on the wire -- and a route can reshape a record on the way
out (a wrapper key, a dropped field, an extra one) without any storage test
noticing. Every schema on this surface is `additionalProperties: false`, so
"one extra field" is a contract violation and not a harmless addition.

Requests are validated too, in the same pass. A request body this repo sends
that the contract would reject is the same defect seen from the other side.
"""

from pathlib import Path

import pytest

from tests.runner_helpers import make_wake_job  # noqa: F401

jsonschema = pytest.importorskip("jsonschema")
yaml = pytest.importorskip("yaml")
referencing = pytest.importorskip("referencing")

from jsonschema import Draft202012Validator  # noqa: E402
from referencing import Registry, Resource  # noqa: E402
from referencing.jsonschema import DRAFT202012  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
SPEC_URI = "urn:ticket-board:openapi"

with (REPO / "docs" / "contracts" / "openapi.yaml").open() as _fh:
    SPEC = yaml.safe_load(_fh)

REGISTRY = Registry().with_resource(
    SPEC_URI, Resource.from_contents(SPEC, default_specification=DRAFT202012))


def check(name, instance):
    validator = Draft202012Validator(
        {"$ref": "{}#/components/schemas/{}".format(SPEC_URI, name)},
        registry=REGISTRY)
    errors = sorted(validator.iter_errors(instance), key=lambda e: list(e.path))
    assert not errors, "does not match the frozen {} schema:\n{}".format(
        name, "\n".join("  {}: {}".format(list(e.path), e.message)
                        for e in errors))


def test_register_returns_a_runner_lease(server, project, agent, client):
    check("RunnerLease",
          client.register("rnr_aaaa1111", agent["agent_id"], "/tmp/wt"))


def test_jobs_returns_a_wake_job_list_response(server, project, agent, client):
    client.register("rnr_aaaa1111", agent["agent_id"], "/tmp/wt")
    check("WakeJobListResponse", client.jobs("rnr_aaaa1111", wait_seconds=0))
    make_wake_job(server, project, agent["agent_id"])
    populated = client.jobs("rnr_aaaa1111", wait_seconds=0)
    check("WakeJobListResponse", populated)
    check("WakeJob", populated["items"][0])


def test_every_run_event_and_the_cancel_return_a_run(server, project, agent,
                                                     client):
    client.register("rnr_aaaa1111", agent["agent_id"], "/tmp/wt")
    job = make_wake_job(server, project, agent["agent_id"])
    client.jobs("rnr_aaaa1111", wait_seconds=0)
    run_id = "run_" + job["id"][4:]

    run = client.run_event(run_id, "starting", expected_version=1,
                           session_id="ses_child001")
    check("Run", run)
    for event in ("started", "turn_completed", "needs_approval"):
        run = client.run_event(run_id, event, expected_version=run["version"],
                               reason="A visible reason.")
        check("Run", run)
    check("Run", client.cancel(run_id, expected_version=run["version"],
                               reason="Operator canceled."))


def test_an_unattributed_event_still_returns_a_valid_run(server, project,
                                                         agent, client):
    """The denied path returns the run unchanged, and unchanged must still be
    a `Run` -- a refusal that returns a malformed body is two bugs."""
    client.register("rnr_aaaa1111", agent["agent_id"], "/tmp/wt")
    job = make_wake_job(server, project, agent["agent_id"])
    client.jobs("rnr_aaaa1111", wait_seconds=0)
    run_id = "run_" + job["id"][4:]
    client.run_event(run_id, "starting", expected_version=1,
                     session_id="ses_child001")

    denied = server.store.record_run_event(
        project["id"], run_id, "started", expected_version=2,
        reporter_session_id=None, session_id="ses_child001")
    check("Run", denied)


def test_the_bodies_this_client_sends_are_ones_the_contract_accepts(
        server, project, agent, client, monkeypatch):
    """Validated at the point of sending, so an unused optional field or a
    misspelled key fails here rather than at a reviewer's reading."""
    sent = []
    original = client._call

    def recording(method, path, *, body=None, query=""):
        if body is not None:
            sent.append((path, body))
        return original(method, path, body=body, query=query)

    monkeypatch.setattr(client, "_call", recording)

    client.register("rnr_aaaa1111", agent["agent_id"], "/tmp/wt")
    job = make_wake_job(server, project, agent["agent_id"])
    client.jobs("rnr_aaaa1111", wait_seconds=0)
    run_id = "run_" + job["id"][4:]
    run = client.run_event(run_id, "starting", expected_version=1,
                           session_id="ses_child001",
                           budget={"max_hops": 3, "max_turns": 10,
                                   "max_seconds": 900, "seconds_used": 4})
    client.cancel(run_id, expected_version=run["version"], reason="done")

    by_path = {
        "/runners/register": "RegisterRunnerRequest",
        "/runs/{}/events".format(run_id): "RunEventRequest",
        "/runs/{}/cancel".format(run_id): "CancelRunRequest",
    }
    assert set(by_path) == {path for path, _ in sent}
    for path, body in sent:
        check(by_path[path], body)


def test_a_partial_budget_is_refused_because_the_schema_rejects_it(
        server, project, agent, client):
    """`RunBudget` is required:[max_hops, max_turns, max_seconds].

    So `{"turns_used": 1}` is not a RunBudget, and accepting it would let a
    client write a body the published schema rejects. Found by the request
    half of this module rather than by reading the spec.
    """
    from ticket_board.runners import ApiError

    client.register("rnr_aaaa1111", agent["agent_id"], "/tmp/wt")
    job = make_wake_job(server, project, agent["agent_id"])
    client.jobs("rnr_aaaa1111", wait_seconds=0)

    with pytest.raises(ApiError) as caught:
        client.run_event("run_" + job["id"][4:], "starting",
                         expected_version=1, session_id="ses_child001",
                         budget={"turns_used": 1})
    assert caught.value.status == 400
    assert caught.value.details["missing_fields"] == \
        ["max_hops", "max_seconds", "max_turns"]
