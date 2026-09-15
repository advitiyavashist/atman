"""T-1027: native notifications must use the judged snapshot, not chat copy.

A worker completion note is not verification. The notification text must
never say accepted for done-without-ACCEPT.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from test_wakeup import board, run  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from ticket_board import native_notify as nn  # noqa: E402


def test_unverified_done_never_says_accepted():
    node = {
        "id": "T-006",
        "title": "I completed it in chat",
        "status": "done",
        "unverified": True,
        "review": {"verified": False, "label": "none recorded"},
    }
    alert = nn.ticket_alert(node)
    assert alert["kind"] == "unverified-done"
    blob = (alert["title"] + " " + alert["body"]).lower()
    assert "accepted" not in blob
    assert "verification not recorded" in alert["title"].lower()


def test_submitted_review_is_not_accepted():
    node = {
        "id": "T-005",
        "title": "Ship the app",
        "status": "review",
        "unverified": False,
        "review": {"verified": False, "label": "awaiting review"},
    }
    alert = nn.ticket_alert(node)
    assert alert["kind"] == "submitted"
    assert "accepted" not in (alert["title"] + alert["body"]).lower()
    assert "awaiting review" in alert["body"]


def test_structured_accept_may_say_accepted():
    node = {
        "id": "T-007",
        "title": "Honest filter",
        "status": "done",
        "unverified": False,
        "review": {"verified": True, "label": "accepted"},
    }
    alert = nn.ticket_alert(node)
    assert alert["kind"] == "accepted"
    assert alert["title"] == "Accepted"


def test_binary_found_is_not_connected():
    row = nn.fleet_row({
        "name": "alice",
        "auth": "login_required",
        "reachable": False,
        "adapter_online": False,
    })
    assert row["connected"] is False
    assert row["phase"] == "found"
    assert row["label"] == "login required"


def test_limited_is_not_idle_or_connected():
    row = nn.fleet_row({
        "name": "bob",
        "auth": "ok",
        "reachable": True,
        "limit": {"until": "2026-09-15T12:00:00Z"},
    })
    assert row["connected"] is False
    assert row["phase"] == "limited"


def test_reachable_authenticated_is_connected():
    row = nn.fleet_row({
        "name": "cara",
        "auth": "ok",
        "reachable": True,
        "adapter_online": True,
    })
    assert row["connected"] is True
    assert row["phase"] == "connected"


def test_module_cli_reads_snapshot(tmp_path):
    snap = {
        "objective": {"text": "Keep the board honest"},
        "work": {"nodes": [{
            "id": "T-006", "title": "chat done", "status": "done",
            "unverified": True, "review": {"verified": False},
        }]},
        "agents": [{"name": "alice", "auth": "login_required", "reachable": False}],
    }
    path = tmp_path / "board.json"
    path.write_text(json.dumps(snap), encoding="utf-8")
    r = subprocess.run(
        [sys.executable, "-m", "ticket_board.native_notify", "--snapshot", str(path)],
        capture_output=True, text=True,
        env=dict(os.environ, PYTHONPATH=str(ROOT / "src")),
        cwd=str(ROOT),
    )
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["objective"] == "Keep the board honest"
    assert out["tickets"][0]["kind"] == "unverified-done"
    assert "accepted" not in json.dumps(out["tickets"][0]).lower()
    assert out["fleet"][0]["connected"] is False


def test_snapshot_from_real_ui_json(board):
    """Drive the same board.json the local app serves — not a second model."""
    run(board, "join", "alice", "--roles", "backend", agent="alice")
    run(board, "create", "Finish in chat", "--role", "backend")
    # board fixture already has T-001; this is T-002
    raw = run(board, "ui", "--json")
    assert raw.returncode == 0, raw.stderr
    snap = json.loads(raw.stdout)
    assert "work" in snap and "agents" in snap
    judged = nn.alerts_from_snapshot(snap)
    for alert in judged["tickets"]:
        if alert["kind"] != "accepted":
            assert "accepted" not in (alert["title"] + " " + alert["body"]).lower()
