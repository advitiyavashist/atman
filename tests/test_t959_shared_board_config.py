"""T-959: a repo with no TICKETS_DIR silently writes to whatever local
.tickets/ directory happens to already exist, even when that repo has a
DIFFERENT board configured as its shared board of record. That is how
/Users/<operator>/Downloads/atman/.tickets ended up holding ~48 stale agent
registrations and messages that never reached the real shared board
(steer/.tickets): sessions cd'd into the atman repo without TICKETS_DIR,
found a plain local .tickets dir, and board_dir() happily used it.

Fix: a machine-level config (ATMAN_BOARD_CONFIG, defaulting to
~/.config/atman/board.json) maps a repo root to its configured shared
board. Resolution order becomes:
  TICKETS_DIR > configured shared board > cwd .tickets (only if empty, or
  explicitly marked primary via `tickets board-mark-primary`)
A non-empty, unmarked local .tickets that disagrees with the configured
board is a shadow: refuse instead of silently using it. `tickets doctor`
reports shadows found (read-only); `tickets board-archive-shadow <path>
--yes` moves one aside (never deletes).

Same subprocess-CLI, two-copy pattern as test_t424_join_isolation.py: both
tickets.py and src/ticket_board/cli.py are exercised (T-243).
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = [ROOT / "tickets.py", ROOT / "src" / "ticket_board" / "cli.py"]
TOOL_IDS = ["tickets.py", "cli.py"]


def clean_env(tmp_path, **overrides):
    e = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(tmp_path / "fake-home"),
        "PYTEST_CURRENT_TEST": "tests/test_t959_shared_board_config.py::simulated (call)",
    }
    for passthrough in ("TMPDIR", "TMP", "TEMP"):
        if os.environ.get(passthrough):
            e[passthrough] = os.environ[passthrough]
    (tmp_path / "fake-home").mkdir(exist_ok=True)
    e.update(overrides)
    return e


def run(tool, cwd, *args, env=None, tmp_path=None):
    return subprocess.run([sys.executable, str(tool), *args], capture_output=True,
                          text=True, cwd=str(cwd), env=env or clean_env(tmp_path))


def git_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=str(path), check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=str(path), check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=str(path), check=True)
    return path


def make_shared_board(repo, seed_ticket_id="T-001"):
    tickets = repo / ".tickets"
    tickets.mkdir()
    (tickets / (seed_ticket_id + ".json")).write_text(json.dumps({"id": seed_ticket_id, "status": "open"}))
    return repo


def make_shadow_board(repo):
    """A local .tickets with real content but NO ticket files -- the exact
    T-959 shape (messages/workforce/agents, zero T-*.json)."""
    tickets = repo / ".tickets"
    (tickets / "agents").mkdir(parents=True)
    (tickets / "agents" / "someone.json").write_text("{}")
    (tickets / "messages.jsonl").write_text(
        json.dumps({"at": "2026-09-08T15:46:55Z", "from": "someone", "to": "bob",
                     "re": "", "text": "joined the board"}) + "\n"
    )
    (tickets / "workforce.json").write_text(json.dumps({"someone": {"harness": "claude"}}))
    return repo


def write_config(tmp_path, mapping):
    conf = tmp_path / "board-config.json"
    conf.write_text(json.dumps({"boards": {str(k): str(v) for k, v in mapping.items()}}))
    return conf


# --------------------------------------------------------------------------
# Test 1 (required): no TICKETS_DIR, repo has an uninitialized (not marked
# primary) .tickets that already holds real content, and a shared board is
# configured elsewhere for this repo -> refuse instead of silently using it.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_shadow_board_refuses_when_shared_board_configured(tool, tmp_path):
    shared = make_shared_board(git_repo(tmp_path / "shared_repo"))
    local = make_shadow_board(git_repo(tmp_path / "local_repo"))
    conf = write_config(tmp_path, {local: shared / ".tickets"})

    r = run(tool, local, "board",
            env=clean_env(tmp_path, ATMAN_BOARD_CONFIG=str(conf)))

    assert r.returncode != 0, "must REFUSE, not silently write to the shadow:\n" + r.stdout + r.stderr
    out = r.stdout + r.stderr
    assert "REFUSING" in out
    assert str(local / ".tickets") in out
    assert str(shared / ".tickets") in out
    # the exact fix must be named, not just "refused"
    assert "TICKETS_DIR" in out
    assert "board-mark-primary" in out
    assert "board-archive-shadow" in out


# --------------------------------------------------------------------------
# Test 2 (required): configured shared board wins -- an EMPTY local
# .tickets (or none at all) is not at risk, so resolution silently and
# safely redirects to the configured board rather than forking a new one.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_configured_shared_board_wins_over_empty_local(tool, tmp_path):
    shared = make_shared_board(git_repo(tmp_path / "shared_repo"), seed_ticket_id="T-200")
    local = git_repo(tmp_path / "local_repo")  # no .tickets at all
    conf = write_config(tmp_path, {local: shared / ".tickets"})

    r = run(tool, local, "where",
            env=clean_env(tmp_path, ATMAN_BOARD_CONFIG=str(conf)))

    assert r.returncode == 0, r.stdout + r.stderr
    assert str((shared / ".tickets").resolve()) in r.stdout


# --------------------------------------------------------------------------
# Ordering: TICKETS_DIR always wins, even over a configured shared board.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_tickets_dir_still_wins_over_configured_board(tool, tmp_path):
    shared = make_shared_board(git_repo(tmp_path / "shared_repo"), seed_ticket_id="T-300")
    local = make_shadow_board(git_repo(tmp_path / "local_repo"))
    other = make_shared_board(git_repo(tmp_path / "other_repo"), seed_ticket_id="T-400")
    conf = write_config(tmp_path, {local: shared / ".tickets"})

    r = run(tool, local, "where",
            env=clean_env(tmp_path, TICKETS_DIR=str(other / ".tickets"), ATMAN_BOARD_CONFIG=str(conf)))

    assert r.returncode == 0, r.stdout + r.stderr
    assert str((other / ".tickets").resolve()) in r.stdout


# --------------------------------------------------------------------------
# A repo with no configured shared board at all keeps today's behavior --
# this is an opt-in mechanism, not a new requirement on every repo.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_no_configured_board_is_unaffected(tool, tmp_path):
    local = make_shadow_board(git_repo(tmp_path / "local_repo"))
    conf = write_config(tmp_path, {})  # no entries at all

    r = run(tool, local, "where",
            env=clean_env(tmp_path, ATMAN_BOARD_CONFIG=str(conf)))

    assert r.returncode == 0, r.stdout + r.stderr
    assert str((local / ".tickets").resolve()) in r.stdout


# --------------------------------------------------------------------------
# board-mark-primary is the escape hatch: once marked, the local board wins
# even though a different shared board is configured for this repo.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_mark_primary_overrides_configured_board(tool, tmp_path):
    shared = make_shared_board(git_repo(tmp_path / "shared_repo"), seed_ticket_id="T-500")
    local = make_shadow_board(git_repo(tmp_path / "local_repo"))
    conf = write_config(tmp_path, {local: shared / ".tickets"})
    env = clean_env(tmp_path, ATMAN_BOARD_CONFIG=str(conf))

    r_mark = run(tool, local, "board-mark-primary", env=env)
    assert r_mark.returncode == 0, r_mark.stdout + r_mark.stderr
    assert (local / ".tickets" / ".primary").is_file()

    r_where = run(tool, local, "where", env=env)
    assert r_where.returncode == 0, r_where.stdout + r_where.stderr
    assert str((local / ".tickets").resolve()) in r_where.stdout


# --------------------------------------------------------------------------
# Test 3 (required): `tickets doctor` reports the shadow -- read-only, and
# must never itself trigger the refusal (it is the diagnostic FOR the
# refusal, and would be useless if it refused too).
# --------------------------------------------------------------------------

@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_doctor_reports_shadow_board_read_only(tool, tmp_path):
    shared = make_shared_board(git_repo(tmp_path / "shared_repo"), seed_ticket_id="T-600")
    local = make_shadow_board(git_repo(tmp_path / "local_repo"))
    conf = write_config(tmp_path, {local: shared / ".tickets"})

    before_messages = (local / ".tickets" / "messages.jsonl").read_text()
    before_agents = sorted(p.name for p in (local / ".tickets" / "agents").iterdir())

    r = run(tool, local, "doctor",
            env=clean_env(tmp_path, ATMAN_BOARD_CONFIG=str(conf)))

    assert r.returncode == 0, "doctor must never refuse -- it is the diagnostic:\n" + r.stdout + r.stderr
    out = r.stdout
    assert "shadow board" in out.lower()
    assert str(local / ".tickets") in out
    assert str(shared / ".tickets") in out
    assert "messages: 1" in out
    assert "agents: 1" in out
    assert "board-archive-shadow" in out

    # Read-only: nothing in the shadow changed.
    assert (local / ".tickets" / "messages.jsonl").read_text() == before_messages
    assert sorted(p.name for p in (local / ".tickets" / "agents").iterdir()) == before_agents
    assert not (local / ".tickets" / ".primary").exists()


# --------------------------------------------------------------------------
# board-archive-shadow: dry-run by default, --yes moves (never deletes).
# --------------------------------------------------------------------------

@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_archive_shadow_is_dry_run_without_yes(tool, tmp_path):
    shared = make_shared_board(git_repo(tmp_path / "shared_repo"), seed_ticket_id="T-700")
    local = make_shadow_board(git_repo(tmp_path / "local_repo"))
    conf = write_config(tmp_path, {local: shared / ".tickets"})
    env = clean_env(tmp_path, ATMAN_BOARD_CONFIG=str(conf))

    r = run(tool, local, "board-archive-shadow", str(local / ".tickets"), env=env)

    assert r.returncode != 0
    assert "DRY RUN" in (r.stdout + r.stderr)
    assert (local / ".tickets").is_dir(), "dry run must not move anything"
    assert (local / ".tickets" / "messages.jsonl").is_file()


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_archive_shadow_moves_aside_never_deletes(tool, tmp_path):
    shared = make_shared_board(git_repo(tmp_path / "shared_repo"), seed_ticket_id="T-800")
    local = make_shadow_board(git_repo(tmp_path / "local_repo"))
    conf = write_config(tmp_path, {local: shared / ".tickets"})
    env = clean_env(tmp_path, ATMAN_BOARD_CONFIG=str(conf))
    shadow_path = local / ".tickets"

    r = run(tool, local, "board-archive-shadow", str(shadow_path), "--yes", env=env)

    assert r.returncode == 0, r.stdout + r.stderr
    assert not shadow_path.exists(), "moved aside, so the original path is gone"
    archived = list(local.glob(".tickets.archived-*"))
    assert len(archived) == 1, "must move aside (rename), not delete"
    assert (archived[0] / "messages.jsonl").is_file(), "content survives the move -- nothing deleted"


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_archive_shadow_refuses_to_archive_the_live_board(tool, tmp_path):
    shared = make_shared_board(git_repo(tmp_path / "shared_repo"), seed_ticket_id="T-900")

    r = run(tool, shared, "board-archive-shadow", str(shared / ".tickets"), "--yes",
            env=clean_env(tmp_path))

    assert r.returncode != 0
    assert "REFUSING" in (r.stdout + r.stderr)
    assert (shared / ".tickets").is_dir()
