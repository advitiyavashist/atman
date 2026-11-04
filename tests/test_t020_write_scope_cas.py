"""T-020: ticket write_scope + compare-and-set revisions.

Two-copy: tickets.py and src/ticket_board/cli.py.

Before the change, 20 concurrent `atm update` calls on one ticket kept 4
notes; the other 16 were overwritten without an error. A write loaded before
someone else's save now refuses instead of overwriting, and notes re-read
inside the ticket lock so they never conflict.
"""
import importlib.util
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from ticket_board import ticket_coordination as tc  # noqa: E402
from ticket_board import ticket_scope as ts  # noqa: E402

TOOLS = [ROOT / "tickets.py", ROOT / "src" / "ticket_board" / "cli.py"]
TOOL_IDS = ["tickets.py", "cli.py"]


def run(tool, board, *args, agent="", stdin=None):
    e = dict(os.environ, TICKETS_DIR=str(board), TICKET_AGENT=agent or "",
             HOME=str(board.parent.parent / "home"), TICKETS_GC_OPEN_PRS="none")
    for var in ("TICKETS_STOP_HOOK", "TICKET_SEAT", "TICKETS_RUN_ID", "TICKETS_RUN_NO",
                "CLAUDE_CODE_SESSION_ID", "CODEX_SESSION_ID", "CURSOR_SESSION_ID",
                "TERM_SESSION_ID", "TICKET_SESSION_ID", "TICKET_OWNER_GENERATION"):
        e.pop(var, None)
    if agent:
        e["TICKET_SESSION_ID"] = "test-session-" + agent
    e["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + e.get("PYTHONPATH", "")
    return subprocess.run([sys.executable, str(tool), *args], capture_output=True, text=True,
                          env=e, cwd=str(board.parent), input=stdin)


def ticket(board, tid):
    return json.loads((board / (tid + ".json")).read_text())


@pytest.fixture
def board(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (tmp_path / "home").mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    return repo / ".tickets"


# --- pure functions --------------------------------------------------------


def test_prefixes_overlap_only_on_path_component_boundaries():
    assert ts.overlap("src/foo", "src/foo/bar.py")
    assert ts.overlap("src/foo/", "src/foo")
    assert ts.overlap("src/foo/bar.py", "src/")
    assert not ts.overlap("src/foo", "src/foobar")
    assert not ts.overlap("src/foo/", "tests/")
    assert ts.overlap("", "anything/at/all")  # empty prefix = whole repo


def test_normalize_refuses_paths_outside_the_repo():
    assert ts.normalize("./src//foo/") == "src/foo/"
    assert ts.normalize(".") == ""
    for bad in ("/etc", "../other", "src/../../x", "C:\\x"):
        with pytest.raises(ts.ScopeError):
            ts.normalize(bad)


def test_outside_lists_only_uncovered_paths():
    assert ts.outside(["src/a/", "tests/test_a.py"], [
        "src/a/x.py", "tests/test_a.py", "README.md", "src/ab.py"]) == ["README.md", "src/ab.py"]
    assert ts.outside([], ["README.md"]) == []  # no scope declares nothing


def test_stale_revision_is_refused_and_revision_never_moves_back():
    assert tc.stale_revision_error({"id": "T-1", "rev": 3}, {"id": "T-1", "rev": 2})
    assert tc.stale_revision_error({"id": "T-1", "rev": 3}, {"id": "T-1"})
    assert tc.stale_revision_error({"id": "T-1", "rev": 3}, {"id": "T-1", "rev": 3}) is None
    assert tc.stale_revision_error({"id": "T-1"}, {"id": "T-1"}) is None  # legacy ticket
    assert tc.next_revision({"rev": 2}, {"rev": 5}) == 6
    assert tc.next_revision({"rev": 4}, {}) == 5


# --- CAS through the real save() ---------------------------------------------


def _load_tickets_module():
    spec = importlib.util.spec_from_file_location("t020_tickets", ROOT / "tickets.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_save_refuses_a_ticket_read_before_another_writers_save(board, monkeypatch):
    tool = TOOLS[0]
    assert run(tool, board, "create", "target").returncode == 0
    tk = _load_tickets_module()
    monkeypatch.delenv("TICKET_OWNER_GENERATION", raising=False)
    stale = tk.load(str(board), "T-001")
    before = stale.get("rev")
    r = run(tool, board, "update", "T-001", "landed first", agent="bob")
    assert r.returncode == 0, r.stderr
    stale["title"] = "overwrites bob"
    with pytest.raises(SystemExit) as exc:
        tk.save(str(board), stale)
    assert "changed since you read it" in str(exc.value)
    on_disk = ticket(board, "T-001")
    assert on_disk["title"] == "target"
    assert [n["text"] for n in on_disk["notes"]] == ["landed first"]
    assert on_disk["rev"] == (before or 0) + 1
    tk.save(str(board), tk.load(str(board), "T-001"))  # a fresh read publishes fine


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_concurrent_updates_keep_every_note(board, tool):
    assert run(tool, board, "create", "race target").returncode == 0
    n = 12

    def one(i):
        return run(tool, board, "update", "T-001", "note %d" % i, agent="seat%d" % i)

    with ThreadPoolExecutor(max_workers=n) as pool:
        results = list(pool.map(one, range(n)))
    assert all(r.returncode == 0 for r in results), [r.stderr for r in results if r.returncode]
    t = ticket(board, "T-001")
    assert sorted(x["text"] for x in t["notes"]) == sorted("note %d" % i for i in range(n))
    assert t["rev"] == n  # one bump per note (create writes the file directly)


# --- write_scope through the CLI --------------------------------------------


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_claiming_an_overlapping_scope_warns_with_both_owners(board, tool):
    assert run(tool, board, "create", "parser", "--scope", "src/parser/").returncode == 0
    assert run(tool, board, "create", "lexer", "--scope", "src/parser/lexer.py",
               "--scope", "tests/").returncode == 0
    assert run(tool, board, "create", "docs", "--scope", "docs/").returncode == 0
    assert ticket(board, "T-001")["write_scope"] == ["src/parser/"]

    first = run(tool, board, "claim", "T-001", agent="alice")
    assert first.returncode == 0, first.stderr
    assert "write_scope: src/parser/" in first.stdout
    assert "warning: write_scope" not in first.stdout

    second = run(tool, board, "claim", "T-002", agent="bob")
    assert second.returncode == 0, second.stderr
    assert ("warning: write_scope of T-002 overlaps T-001 (in progress, owner=alice): "
            "src/parser/lexer.py ~ src/parser/") in second.stdout

    third = run(tool, board, "claim", "T-003", agent="carol")
    assert third.returncode == 0 and "warning: write_scope" not in third.stdout

    check = run(tool, board, "scope", "--check")
    assert check.returncode == 1
    assert "T-001 (alice) ~ T-002 (bob)" in check.stdout
    assert "T-003" not in check.stdout


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_scope_edit_records_a_note_and_clears_the_overlap(board, tool):
    run(tool, board, "create", "a", "--scope", "src/")
    run(tool, board, "create", "b", "--scope", "src/b/")
    run(tool, board, "claim", "T-001", agent="alice")
    run(tool, board, "claim", "T-002", agent="bob")
    assert run(tool, board, "scope", "--check").returncode == 1

    r = run(tool, board, "scope", "T-001", "--drop", "src/", "--add", "src/a/", agent="alice")
    assert r.returncode == 0, r.stderr
    assert "T-001 write_scope: src/a/" in r.stdout and "warning" not in r.stdout
    t = ticket(board, "T-001")
    assert t["write_scope"] == ["src/a/"]
    assert t["notes"][-1]["text"] == "write_scope -> src/a/"
    assert run(tool, board, "scope", "--check").returncode == 0

    bad = run(tool, board, "scope", "T-001", "--add", "../outside")
    assert bad.returncode != 0 and "may not contain '..'" in bad.stderr
    r = run(tool, board, "scope", "T-001", "--clear")
    assert r.returncode == 0 and "write_scope" not in ticket(board, "T-001")


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_plan_carries_write_scope_and_refuses_a_bad_one_before_creating(board, tool):
    bad = json.dumps([{"key": "a", "title": "ok", "write_scope": ["src/"]},
                      {"key": "b", "title": "bad", "write_scope": ["/abs"]}])
    r = run(tool, board, "plan", stdin=bad)
    assert r.returncode != 0 and "relative to the repo root" in r.stderr
    assert not list(board.glob("T-*.json")) if board.exists() else True

    good = json.dumps([{"key": "a", "title": "ok", "write_scope": ["src/", "./tests//"]}])
    r = run(tool, board, "plan", stdin=good)
    assert r.returncode == 0, r.stderr
    assert ticket(board, "T-001")["write_scope"] == ["src/", "tests/"]


def test_tickets_without_scope_are_unchanged(board):
    for tool in TOOLS:
        run(tool, board, "create", "plain")
    for t in board.glob("T-*.json"):
        assert "write_scope" not in json.loads(t.read_text())


def test_review_warns_about_paths_outside_scope(capsys):
    tk = _load_tickets_module()
    t = {"id": "T-009", "write_scope": ["src/parser/"]}
    calls = []

    def fake_git(*args):
        calls.append(args)
        return "src/parser/a.py\nREADME.md\n"

    tk._warn_outside_scope(t, fake_git, "origin/main")
    out = capsys.readouterr().out
    assert calls == [("diff", "--name-only", "origin/main...HEAD")]
    assert "1 changed path(s) outside T-009 write_scope [src/parser/]: README.md" in out
    tk._warn_outside_scope({"id": "T-010"}, fake_git, "origin/main")
    assert capsys.readouterr().out == ""
