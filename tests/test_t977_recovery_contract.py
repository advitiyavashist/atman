"""T-977 recovery contract.

Same-provider restart and cross-provider handoff are different cases.
Do not treat a successful same-provider continue as interchangeability.
"""
import json
from pathlib import Path

from ticket_coordination import parse_handoff_document, flag_missing_artifacts, apply_context_budget
from test_wakeup import board, run  # noqa: F401

HANDOFF = """\
## completion_criteria
mid.txt exists and review can be submitted
## work_completed
wrote mid.txt with hello
## decisions
keep the fixture board; do not add a database
## open_questions
none
## artifacts
mid.txt
## worktree
.
## checks
test_mid passed
## next_action
submit review from the persisted artifact
## facts
mid.txt contains hello
## assumptions
the next process can read mid.txt from this worktree
## context_budget
2000
## missing_artifacts
"""


def _ticket(board, tid="T-001"):
    return json.loads((board / ("%s.json" % tid)).read_text())


def _write_handoff(repo, name="handoff.md", extra_missing=""):
    text = HANDOFF
    if extra_missing:
        text = text + extra_missing + "\n"
    path = repo / name
    path.write_text(text)
    return path


def _run(board, *args, agent="", cwd=None, env=None):
    isolated = {
        "TICKET_SEAT": "",
        "TICKETS_RUN_ID": "",
        "TICKETS_RUN_NO": "",
        "TICKETS_PY": "",
    }
    if env:
        isolated.update(env)
    return run(board, *args, agent=agent, cwd=cwd, env=isolated)


def test_parse_separates_facts_from_assumptions_and_flags_missing_artifacts(tmp_path):
    parsed = parse_handoff_document(HANDOFF)
    assert parsed["facts"] == "mid.txt contains hello"
    assert parsed["assumptions"] == "the next process can read mid.txt from this worktree"
    assert parsed["facts"] != parsed["assumptions"]
    assert "completion_criteria" not in parsed["missing_fields"]
    flagged = flag_missing_artifacts(
        {"artifacts": "no-such.bin\nmid.txt", "worktree": str(tmp_path), "missing_artifacts": ""},
        cwd=str(tmp_path),
    )
    assert "no-such.bin" in flagged
    assert "mid.txt" in flagged
    (tmp_path / "mid.txt").write_text("hello")
    flagged = flag_missing_artifacts(
        {"artifacts": "no-such.bin\nmid.txt", "worktree": str(tmp_path), "missing_artifacts": ""},
        cwd=str(tmp_path),
    )
    assert flagged == ["no-such.bin"]
    budget = apply_context_budget("x" * 400, {"context_budget": "32"})
    assert budget["truncated"] is True
    assert budget["context_budget"] == 256  # floor
    assert budget["document_bytes"] == 400


def test_same_provider_restart_keeps_one_owner_and_the_same_lease(board):
    """INTERRUPTED / same-provider: the lease survives a new process of the same harness."""
    repo = board.parent
    assert _run(board, "join", "alice", "--roles", "docs", "--harness", "cursor",
               agent="alice", cwd=repo).returncode == 0
    assert _run(board, "objective", "recover mid.txt", "--exit", "mid.txt reviewed",
               agent="master", cwd=repo).returncode == 0
    r = _run(board, "next", "--role", "docs", agent="alice", cwd=repo)
    assert r.returncode == 0, r.stderr
    (repo / "mid.txt").write_text("hello")
    handoff = _write_handoff(repo)
    r = _run(board, "handover", "T-001", "--file", str(handoff), agent="alice", cwd=repo)
    assert r.returncode == 0, r.stderr + r.stdout
    before = _ticket(board)
    assert before["owner"] == "alice"
    assert before["owner_generation"] == 1
    assert before["owner_lease"]["harness"] == "cursor"
    assert before["handoff"]["facts"] == "mid.txt contains hello"
    assert before["handoff"]["assumptions"] == "the next process can read mid.txt from this worktree"
    assert before["handoff"]["missing_artifacts_flagged"] is False

    # New process, same agent, same harness: continue, do not claim again.
    r = _run(board, "mine", agent="alice", cwd=repo)
    assert r.returncode == 0
    assert "T-001" in r.stdout
    r = _run(board, "next", "--role", "docs", agent="alice", cwd=repo)
    assert r.returncode == 1
    assert "already hold" in r.stdout
    r = _run(board, "update", "T-001", "restarted same cursor seat", agent="alice", cwd=repo)
    assert r.returncode == 0, r.stderr
    r = _run(board, "review", "T-001", "--notes", "same-provider restart of mid.txt",
            "--force", agent="alice", cwd=repo)
    assert r.returncode == 0, r.stderr + r.stdout
    after = _ticket(board)
    assert after["owner"] == "alice"
    assert after["owner_generation"] == 1
    assert after["status"] == "review"
    owners = [t["owner"] for t in [_ticket(board)] if t.get("owner")]
    assert owners == ["alice"]


def test_cross_provider_handoff_is_not_a_same_provider_restart(board):
    """INTERCHANGEABILITY: a second harness continues only after a lease bump.

    A same-provider restart (test above) must not be reported as this case.
    """
    repo = board.parent
    assert _run(board, "join", "alice", "--roles", "docs", "--harness", "cursor",
               agent="alice", cwd=repo).returncode == 0
    assert _run(board, "join", "bob", "--roles", "docs", "--harness", "codex",
               agent="bob", cwd=repo).returncode == 0
    assert _run(board, "objective", "recover mid.txt", "--exit", "mid.txt reviewed",
               agent="master", cwd=repo).returncode == 0
    assert _run(board, "next", "--role", "docs", agent="alice", cwd=repo).returncode == 0
    (repo / "mid.txt").write_text("hello")
    (repo / "gone.bin").write_text("")  # named, then removed so handover flags it
    (repo / "gone.bin").unlink()
    handoff = _write_handoff(repo, extra_missing="gone.bin")
    r = _run(board, "handover", "T-001", "--file", str(handoff), agent="alice", cwd=repo)
    assert r.returncode == 0, r.stderr + r.stdout
    before = _ticket(board)
    assert before["owner_generation"] == 1
    assert before["handoff"]["missing_artifacts_flagged"] is True
    assert "gone.bin" in before["handoff"]["missing_artifacts"]

    r = _run(board, "assign", "T-001", "--owner", "bob", agent="master", cwd=repo)
    assert r.returncode == 0, r.stderr
    after = _ticket(board)
    assert after["owner"] == "bob"
    assert after["owner_generation"] == 2
    assert after["owner_lease"]["previous_owner"] == "alice"
    assert after["owner_lease"]["reason"] == "reassign"
    assert after["owner_lease"]["harness"] == "codex"
    assert (board / "T-001.lock").read_text() == "bob"
    assert after["handoff"]["id"] == before["handoff"]["id"]
    assert (repo / "mid.txt").read_text() == "hello"

    r = _run(board, "review", "T-001", "--notes", "stale alice result", "--force",
             agent="alice", cwd=repo)
    assert r.returncode != 0
    assert "stale ownership" in (r.stderr + r.stdout)
    r = _run(board, "done", "T-001", "--notes", "stale alice close", "--force",
             agent="alice", cwd=repo)
    assert r.returncode != 0
    assert "stale ownership" in (r.stderr + r.stdout)
    still = _ticket(board)
    assert still["status"] == "claimed"
    assert still["owner"] == "bob"
    assert still["owner_generation"] == 2

    r = _run(board, "review", "T-001", "--notes", "cross-provider continue of mid.txt",
             "--force", agent="bob", env={"TICKET_OWNER_GENERATION": "1"}, cwd=repo)
    assert r.returncode != 0
    assert "stale ownership generation" in (r.stderr + r.stdout)

    r = _run(board, "pulse", agent="bob", cwd=repo)
    assert r.returncode == 0, r.stderr
    pulse = json.loads(r.stdout)
    active = [row for row in pulse["active"] if row["ticket"] == "T-001"]
    assert active and active[0]["owner"] == "bob"
    assert active[0]["handoff"]["next_action"]
    assert "alice" not in [row["owner"] for row in pulse["active"] if row["ticket"] == "T-001"]

    r = _run(board, "review", "T-001", "--notes", "cross-provider continue of mid.txt",
             "--force", agent="bob", cwd=repo)
    assert r.returncode == 0, r.stderr + r.stdout
    final = _ticket(board)
    assert final["owner"] == "bob"
    assert final["status"] == "review"
    assert final["owner_generation"] == 2


def test_packaged_cli_bumps_lease_on_reassign(board):
    import os
    import subprocess
    import sys
    cli = Path(__file__).resolve().parents[1] / "src" / "ticket_board" / "cli.py"
    repo = board.parent
    env = dict(os.environ, TICKETS_DIR=str(board), PYTHONPATH=str(cli.parent.parent),
               TICKET_SEAT="", TICKETS_RUN_ID="", TICKETS_RUN_NO="", TICKETS_PY="")
    env.pop("TICKETS_STOP_HOOK", None)

    def cli_run(*args, agent=""):
        e = dict(env, TICKET_AGENT=agent, TICKET_SESSION_ID="test-session-" + (agent or "anon"))
        return subprocess.run([sys.executable, str(cli), *args], capture_output=True,
                              text=True, env=e, cwd=repo)

    assert cli_run("join", "alice", "--roles", "docs", "--harness", "cursor", agent="alice").returncode == 0
    assert cli_run("join", "bob", "--roles", "docs", "--harness", "codex", agent="bob").returncode == 0
    assert cli_run("next", "--role", "docs", agent="alice").returncode == 0
    r = cli_run("assign", "T-001", "--owner", "bob", agent="master")
    assert r.returncode == 0, r.stderr
    ticket = _ticket(board)
    assert ticket["owner"] == "bob"
    assert ticket["owner_generation"] == 2
    r = cli_run("review", "T-001", "--notes", "stale", "--force", agent="alice")
    assert r.returncode != 0
    assert "stale ownership" in (r.stderr + r.stdout)
