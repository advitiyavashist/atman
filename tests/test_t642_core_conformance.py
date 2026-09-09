"""T-642: portable behavior contract, corpus and Python oracle evidence."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
ROOT = REPO / "conformance" / "v1"
CONTRACT = ROOT / "contract.json"
CORPUS = ROOT / "corpus.json"
SCHEMA = ROOT / "corpus.schema.json"
GOLDEN = ROOT / "golden" / "python-oracle.json"
RUNNER = REPO / "tools" / "run_core_conformance.py"


def test_contract_is_versioned_complete_and_traceable():
    contract = json.loads(CONTRACT.read_text())
    assert contract["id"] == "atman-core/v1"
    assert contract["status"] == "frozen"
    assert re.fullmatch(r"[0-9a-f]{40}", contract["oracle"]["git_revision"])
    requirements = contract["requirements"]
    ids = [item["id"] for item in requirements]
    assert len(ids) == len(set(ids))
    assert {
        "storage", "atomic_claims", "dependencies", "objectives", "messages",
        "wakes", "duplicate_delivery", "identity", "hooks", "runner_leases",
        "recovery", "path_containment", "clock_skew", "knowledge", "cli_json",
        "backwards_compatibility", "performance",
    } <= {item["area"] for item in requirements}
    assert all(item["rule"].strip() and item["evidence"] for item in requirements)


def test_corpus_has_unique_cases_and_every_mandatory_adversarial_fixture():
    corpus = json.loads(CORPUS.read_text())
    assert corpus["contract_version"] == "atman-core/v1"
    assert corpus["oracle_revision"] == json.loads(CONTRACT.read_text())["oracle"]["git_revision"]
    ids = [case["id"] for case in corpus["cases"]]
    assert len(ids) == len(set(ids))
    assert {
        "atomic-claim-eight-way",
        "dependency-ordering",
        "bounded-objective",
        "task-message-wake-ack",
        "durable-identity-and-remote-hook",
        "duplicate-delivery",
        "runner-lease-fencing",
        "crash-orphan-partial-write",
        "backup-restore-recovery",
        "knowledge-reference",
        "knowledge-path-containment",
        "knowledge-future-clock",
        "legacy-board-read",
        "cli-json-and-errors",
        "board-read-performance",
    } == set(ids)
    assert json.loads(SCHEMA.read_text())["$defs"]["case"]["additionalProperties"] is False


def test_corpus_validates_against_its_language_neutral_schema():
    try:
        import jsonschema
    except ImportError:
        # The repository keeps JSON Schema in an optional contracts extra.
        return
    jsonschema.Draft202012Validator(json.loads(SCHEMA.read_text())).validate(
        json.loads(CORPUS.read_text())
    )


def test_black_box_python_oracle_matches_committed_golden():
    env = dict(os.environ, PYTHONPYCACHEPREFIX="/tmp/atman-t642-pycache")
    result = subprocess.run(
        [sys.executable, str(RUNNER)],
        cwd=str(REPO), env=env, text=True, capture_output=True, timeout=120,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert "15/15 passed" in result.stdout

    golden = json.loads(GOLDEN.read_text())
    assert golden["contract_version"] == "atman-core/v1"
    assert golden["oracle_revision"] == json.loads(CONTRACT.read_text())["oracle"]["git_revision"]
    assert re.fullmatch(r"[0-9a-f]{64}", golden["corpus_sha256"])
    assert golden["summary"] == {"passed": 15, "failed": 0}
    metric = next(
        case["metrics"]["warm-board"] for case in golden["cases"]
        if case["id"] == "board-read-performance"
    )
    assert metric["samples"] == 7
    assert metric["p95_ms"] < metric["p95_under_ms"] == 1000.0


def test_differential_runner_does_not_import_the_python_implementation():
    source = RUNNER.read_text()
    assert "import ticket_board" not in source
    assert "subprocess.run" in source
    assert "--adapter-command-json" in source
