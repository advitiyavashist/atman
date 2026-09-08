"""T-530: tickets brief --role updates the T-529 inject source.

Contract (shared with T-529): .tickets/briefs/roles/<role>.md plus
.tickets/briefs/_shared.md. This ticket owns the write/update path.
T-529 inject reads only those board files (repo-root roles/ is not a source).
"""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[1] / "tickets.py"


def _load_tickets():
    spec = importlib.util.spec_from_file_location("tickets_t530", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run(board, *args, agent="", cwd=None):
    e = dict(
        os.environ,
        TICKETS_DIR=str(board),
        TICKET_AGENT=agent or "",
        HOME=str(board.parent.parent / "home"),
    )
    e.pop("TICKETS_STOP_HOOK", None)
    where = cwd or (board.parent if board.parent.is_dir() else Path("/"))
    return subprocess.run(
        [sys.executable, str(TOOL), *args],
        capture_output=True,
        text=True,
        env=e,
        cwd=where,
    )


@pytest.fixture
def board(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (tmp_path / "home").mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "init"],
        check=True,
        env=dict(
            os.environ,
            GIT_AUTHOR_NAME="t",
            GIT_AUTHOR_EMAIL="t@t",
            GIT_COMMITTER_NAME="t",
            GIT_COMMITTER_EMAIL="t@t",
        ),
    )
    b = repo / ".tickets"
    r = run(b, "create", "Write docs", "--role", "docs", cwd=repo)
    assert r.returncode == 0, r.stderr
    return b


def test_brief_role_append_creates_contract_file(board):
    r = run(board, "brief", "--role", "backend", "Prefer one file per change.", agent="master")
    assert r.returncode == 0, r.stderr + r.stdout
    path = board / "briefs" / "roles" / "backend.md"
    assert path.is_file()
    text = path.read_text()
    assert text.startswith("# Role context: backend\n")
    assert "Prefer one file per change." in text
    assert "added to" in r.stdout and str(path) in r.stdout
    # Same '- <ts> [by] text' line style as the agent brief.
    assert "[master] Prefer one file per change." in text
    # Nobody has joined with --roles backend yet: stored, but say so.
    assert "no seat on this board has role 'backend'" in r.stdout
    # Shared baseline is a sibling, not briefs/roles/_shared.md
    assert not (board / "briefs" / "roles" / "_shared.md").exists()


def test_brief_role_append_then_replace_and_show(board, tmp_path):
    run(board, "brief", "--role", "docs", "old standing line", agent="master")
    path = board / "briefs" / "roles" / "docs.md"
    first = path.read_text()
    r = run(board, "brief", "--role", "docs", "second standing line", agent="master")
    assert r.returncode == 0, r.stderr
    second = path.read_text()
    assert first in second
    assert "second standing line" in second
    assert second.count("# Role context: docs") == 1

    src = tmp_path / "role.md"
    src.write_text("# Role context: docs\n\nreplace body\n")
    r = run(board, "brief", "--role", "docs", "--file", str(src), agent="master")
    assert r.returncode == 0, r.stderr
    assert path.read_text() == "# Role context: docs\n\nreplace body\n"
    assert "role context for docs replaced from" in r.stdout

    shown = run(board, "brief", "--role", "docs", "--show")
    assert shown.returncode == 0
    assert shown.stdout.strip().endswith("replace body")


def test_brief_role_show_missing(board):
    r = run(board, "brief", "--role", "console", "--show")
    assert r.returncode == 0
    assert "(no role context for console)" in r.stdout


def test_brief_role_rejects_traversal_and_empty(board):
    for bad in ("../agent", "backend/../agent", "foo/bar", ".", "..", "_hidden", ""):
        r = run(board, "brief", "--role", bad, "nope", agent="master")
        assert r.returncode != 0, bad
        assert "brief --role" in r.stderr, bad
    assert not (board / "briefs" / "roles").exists()
    # Traversal must not write outside the roles/ directory either.
    assert not (board.parent / "agent.md").exists()
    # Refusals are explained, not just exit codes.
    r = run(board, "brief", "--role", "", "nope", agent="master")
    assert "needs a role name" in r.stderr
    r = run(board, "brief", "--role", "foo/bar", "nope", agent="master")
    assert "not a role name" in r.stderr and "join --roles" in r.stderr


def test_brief_role_shared_addresses_lane_wide_file(board, tmp_path):
    """--role _shared is the lane-wide baseline T-529 injects for every seat."""
    run(board, "join", "alice", "--roles", "backend")
    run(board, "join", "bob", "--roles", "docs")
    r = run(board, "brief", "--role", "_shared", "Board-first updates.", agent="master")
    assert r.returncode == 0, r.stderr + r.stdout
    shared = board / "briefs" / "_shared.md"
    assert shared.is_file()
    assert shared.read_text().startswith("# Role context: _shared\n")
    assert "Board-first updates." in shared.read_text()
    assert not (board / "briefs" / "roles" / "_shared.md").exists()
    # Every seat is on the shared lane.
    assert "messaged 2 seats (alice, bob)" in r.stdout
    for who in ("alice", "bob"):
        inbox = run(board, "inbox", agent=who).stdout
        assert "role context for _shared updated by master" in inbox, who
        assert "Board-first updates." in run(board, "prompt", "--agent", who).stdout

    src = tmp_path / "shared.md"
    src.write_text("# Role context: _shared\n\nnew baseline\n")
    r = run(board, "brief", "--role", "_shared", "--file", str(src), agent="master")
    assert r.returncode == 0, r.stderr
    assert shared.read_text() == "# Role context: _shared\n\nnew baseline\n"
    shown = run(board, "brief", "--role", "_shared", "--show")
    assert shown.stdout.strip().endswith("new baseline")


def test_brief_role_messages_only_seats_on_that_lane(board, tmp_path):
    """Seats whose roles include <role> hear about the update; others do not.
    No re-join is needed: the next prompt render re-reads the file."""
    run(board, "join", "alice", "--roles", "backend")
    run(board, "join", "bob", "--roles", "backend,docs")
    run(board, "join", "carol", "--roles", "console")
    r = run(board, "brief", "--role", "backend", "Pin torch==2.5.1.", agent="master")
    assert r.returncode == 0, r.stderr + r.stdout
    assert "messaged 2 seats (alice, bob)" in r.stdout
    assert "next wake" in r.stdout
    for who in ("alice", "bob"):
        inbox = run(board, "inbox", agent=who).stdout
        assert "role context for backend updated by master: Pin torch==2.5.1." in inbox, who
    assert "role context for backend" not in run(board, "inbox", agent="carol").stdout
    # Replace notifies too (the whole file changed under them).
    src = tmp_path / "backend.md"
    src.write_text("# Role context: backend\n\nfresh\n")
    r = run(board, "brief", "--role", "backend", "--file", str(src), agent="master")
    assert "messaged 2 seats (alice, bob)" in r.stdout
    assert "replaced from" in run(board, "inbox", agent="alice").stdout
    # Retired seats drop out of the lane.
    run(board, "retire", "alice", agent="master")
    r = run(board, "brief", "--role", "backend", "after retire", agent="master")
    assert "messaged 1 seat (bob)" in r.stdout


def test_brief_role_rejects_agent_or_ticket_combo(board):
    # Same shape as `brief <agent> <text>` plus --role: both targets named.
    r = run(board, "brief", "alice", "some text", "--role", "backend", agent="master")
    assert r.returncode != 0
    assert "not both" in (r.stderr + r.stdout)
    r = run(board, "brief", "--role", "backend", "--ticket", "T-001", "text", agent="master")
    assert r.returncode != 0
    assert "not both" in (r.stderr + r.stdout)


def test_brief_role_does_not_touch_agent_brief(board):
    run(board, "join", "doc", "--roles", "docs")
    run(board, "brief", "doc", "agent standing", agent="master")
    run(board, "brief", "--role", "docs", "lane standing", agent="master")
    assert "agent standing" in (board / "briefs" / "doc.md").read_text()
    assert "lane standing" in (board / "briefs" / "roles" / "docs.md").read_text()
    assert "lane standing" not in (board / "briefs" / "doc.md").read_text()
    # T-529 inject: next prompt/watch reads the board role file first.
    p = run(board, "prompt", "--agent", "doc").stdout
    assert "agent standing" in p
    assert "lane standing" in p
    assert str(board / "briefs" / "roles" / "docs.md") in p


def test_brief_role_is_seen_on_next_prompt_not_repo_root(board):
    """wake/spawn must see `brief --role` — one store, no repo-root roles/."""
    run(board, "join", "alice", "--roles", "backend")
    decoy = board.parent / "roles"
    decoy.mkdir()
    (decoy / "backend.md").write_text("T530-ROOT-DECOY\n")
    r = run(board, "brief", "--role", "backend", "Prefer one file per change.", agent="master")
    assert r.returncode == 0, r.stderr + r.stdout
    p = run(board, "prompt", "--agent", "alice").stdout
    assert "Prefer one file per change." in p
    assert "Role context" in p
    assert "T530-ROOT-DECOY" not in p
    assert str(board / "briefs" / "roles" / "backend.md") in p


def test_role_brief_path_contract_helpers():
    t = _load_tickets()
    board = "/tmp/board"
    assert t.role_brief_path(board, "backend") == "/tmp/board/briefs/roles/backend.md"
    assert t.shared_brief_path(board) == "/tmp/board/briefs/_shared.md"
    assert t.role_brief_path(board, "../x") is None
    assert t.role_brief_path(board, "_shared") is None
    assert t.role_context_path(board, "_shared") == "/tmp/board/briefs/_shared.md"
    assert t.role_context_path(board, "backend") == "/tmp/board/briefs/roles/backend.md"
    assert t._safe_role_slug("docs") == "docs"
    assert t._safe_role_slug("_shared") == "_shared"
    with pytest.raises(SystemExit):
        t._safe_role_slug("../x")
    with pytest.raises(SystemExit):
        t._safe_role_slug("")
