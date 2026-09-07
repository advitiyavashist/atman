"""The operator CLI: it must never fail quietly.

`status` in particular is the command an operator runs when something is wrong,
so the thing it must not do is print a green report for a runner that cannot
start a process. T-181's D3 was a diagnose() an operator could not use; the
same failure here would be a status that only reads files.
"""

from __future__ import annotations

import json

import pytest

from ticket_board.adapters.claude import Enrollment, save_enrollment
from ticket_board.runners.__main__ import EXIT_ERROR, EXIT_UNHEALTHY, main


@pytest.fixture()
def connected(tmp_path):
    project_dir = tmp_path / "scratch"
    project_dir.mkdir()
    save_enrollment(project_dir, Enrollment(
        project_id="prj_abcd1234", server_url="http://127.0.0.1:1",
        agent_id="agt_abcd1234", agent_name="runner-1",
        session_id="ses_abcd1234", token="tbk_test", created_at="2026-09-07T00:00:00Z",
        lease_version=1))
    return project_dir


def test_an_unconnected_project_is_an_error_with_the_next_step(tmp_path, capsys):
    assert main(["status", "--project-dir", str(tmp_path)]) == EXIT_ERROR
    err = capsys.readouterr().err
    assert "not connected" in err
    assert "connect" in err


def test_status_reports_an_unreachable_board_as_unhealthy(connected, capsys):
    """Port 1 on loopback answers nothing. The report says so and exits 2 --
    it does not print a green line because the config file parsed."""
    code = main(["status", "--project-dir", str(connected)])
    report = json.loads(capsys.readouterr().out)

    assert code == EXIT_UNHEALTHY
    assert report["board_reachable"] is False
    assert report["remediation"], "an unhealthy status must say what to do"
    assert report["agent_id"] == "agt_abcd1234"


def test_status_separates_configured_from_runnable(connected, monkeypatch,
                                                   capsys):
    """Installed and runnable are different facts (T-181 D1)."""
    monkeypatch.setenv("CLAUDE_BIN", "/nonexistent/claude")
    assert main(["status", "--project-dir", str(connected)]) == EXIT_UNHEALTHY
    report = json.loads(capsys.readouterr().out)
    assert report["claude_executable"] is None
    assert any("claude" in line for line in report["remediation"])
