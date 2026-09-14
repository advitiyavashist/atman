"""T-938: objective --set/--replaced policy, keep-exit, history, declared-board guard."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from test_wakeup import board, run  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tickets.py"


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _obj(board):
    return json.loads((board / "objective.json").read_text())


def _take_master(board, owner="ceo"):
    assert run(board, "join", owner, "--roles", "master", agent=owner).returncode == 0
    r = run(board, "master", "take", agent=owner)
    assert r.returncode == 0, r.stderr


def test_worker_cannot_set_or_replace_when_master_exists(board):
    _take_master(board, "ceo")
    run(board, "join", "verifier", "--roles", "verification", agent="verifier")
    r = run(board, "objective", "--set", "overwrite the living goal",
            "--exit", "gates green", agent="verifier")
    assert r.returncode != 0, r.stdout
    err = (r.stderr or "") + (r.stdout or "")
    assert "objective policy" in err and "ceo" in err
    assert not (board / "objective.json").exists()

    assert run(board, "objective", "--set", "Ship V1", "--exit", "gates green",
               agent="ceo").returncode == 0
    refused = run(board, "objective", "--replaced", "cut scope", agent="verifier")
    assert refused.returncode != 0
    assert "objective policy" in (refused.stderr or "") + (refused.stdout or "")
    assert _obj(board)["state"] == "active"


def test_ceo_or_master_may_set(board):
    _take_master(board, "ceo")
    r = run(board, "objective", "--set", "Ship V1", "--exit", "demo recorded",
            agent="ceo")
    assert r.returncode == 0, r.stderr
    rec = _obj(board)
    assert rec["text"] == "Ship V1"
    assert rec["exit_criterion"] == "demo recorded"
    assert rec["set_by"] == "ceo"


def test_set_without_exit_keeps_existing_criterion(board):
    run(board, "objective", "Ship V1", "--exit", "review queue empty", agent="boss")
    r = run(board, "objective", "--set", "Ship V1", agent="boss")
    assert r.returncode == 0, r.stderr
    rec = _obj(board)
    assert rec["text"] == "Ship V1"
    assert rec["exit_criterion"] == "review queue empty"
    assert rec.get("exit_missing") is False
    assert "FLAG" not in r.stdout


def test_clear_exit_is_explicit(board):
    run(board, "objective", "Ship V1", "--exit", "review queue empty", agent="boss")
    r = run(board, "objective", "--set", "Ship V1", "--clear-exit", agent="boss")
    assert r.returncode == 0, r.stderr
    rec = _obj(board)
    assert rec["exit_criterion"] == ""
    assert rec.get("exit_missing") is True
    assert "FLAG" in r.stdout
    both = run(board, "objective", "--set", "Ship V1", "--clear-exit",
               "--exit", "new gate", agent="boss")
    assert both.returncode != 0
    assert "--exit or --clear-exit" in (both.stderr or "") + (both.stdout or "")


def test_every_change_appends_history_with_actor_and_digests(board):
    run(board, "objective", "Ship V1", "--exit", "gates green", agent="boss")
    rec = _obj(board)
    assert len(rec["history"]) == 1
    first = rec["history"][0]
    assert first["actor"] == "boss" and first["action"] == "set"
    assert first["before"] != first["after"]
    before_set = first["after"]

    run(board, "objective", "--set", "Ship V1 still", agent="boss")
    rec = _obj(board)
    assert rec["exit_criterion"] == "gates green"
    assert len(rec["history"]) == 2
    second = rec["history"][1]
    assert second["actor"] == "boss" and second["action"] == "set"
    assert second["before"] == before_set
    assert second["after"] != second["before"]

    assert run(board, "objective", "--done", "shipped", agent="boss").returncode == 0
    rec = _obj(board)
    assert rec["state"] == "achieved"
    assert rec["history"][-1]["action"] == "achieved"
    assert rec["history"][-1]["actor"] == "boss"
    assert rec["history"][-1]["before"] != rec["history"][-1]["after"]


def test_declared_tickets_dir_mismatch_is_refused(tmp_path, monkeypatch):
    declared = tmp_path / "declared" / ".tickets"
    other = tmp_path / "other" / ".tickets"
    declared.mkdir(parents=True)
    other.mkdir(parents=True)
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "tests/test_t938_objective_policy.py::unit")
    monkeypatch.setenv("TICKETS_DIR", str(declared))
    tk = _load(TOOL, "tickets_t938")
    tk._refuse_board_outside_declared_tickets_dir(str(declared))
    with pytest.raises(SystemExit) as ei:
        tk._refuse_board_outside_declared_tickets_dir(str(other))
    msg = str(ei.value)
    assert "REFUSING TO USE BOARD" in msg
    assert "declared TICKETS_DIR" in msg
    sys.path.insert(0, str(ROOT / "src"))
    from ticket_board import cli as tb_cli  # noqa: E402
    with pytest.raises(SystemExit) as ei:
        tb_cli._refuse_board_outside_declared_tickets_dir(str(other))
    assert "declared TICKETS_DIR" in str(ei.value)


def test_declared_tickets_dir_guard_does_not_fire_outside_pytest(tmp_path, monkeypatch):
    declared = tmp_path / "declared" / ".tickets"
    other = tmp_path / "other" / ".tickets"
    declared.mkdir(parents=True)
    other.mkdir(parents=True)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("TICKETS_DIR", str(declared))
    tk = _load(TOOL, "tickets_t938_nopat")
    tk._refuse_board_outside_declared_tickets_dir(str(other))
