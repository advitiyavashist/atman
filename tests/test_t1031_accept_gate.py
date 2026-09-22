"""T-1031: dependents release only after ACCEPT (or a recorded override).

Throwaway boards. Two-copy: tickets.py and src/ticket_board/cli.py.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from test_wakeup import board  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from ticket_board import work_view  # noqa: E402
TOOLS = [ROOT / "tickets.py", ROOT / "src" / "ticket_board" / "cli.py"]
TOOL_IDS = ["tickets.py", "cli.py"]
REASON = "T-002 marked done without verification; accept it or reopen"


def run(tool, board, *args, agent="", stdin=None):
    e = dict(os.environ, TICKETS_DIR=str(board), TICKET_AGENT=agent or "",
             HOME=str(board.parent.parent / "home"),
             TICKETS_GC_OPEN_PRS="none")
    e.pop("TICKETS_STOP_HOOK", None)
    e.pop("TICKET_SEAT", None)
    for var in ("CLAUDE_CODE_SESSION_ID", "CODEX_SESSION_ID",
                "CURSOR_SESSION_ID", "TERM_SESSION_ID", "TICKET_SESSION_ID"):
        e.pop(var, None)
    if agent:
        e["TICKET_SESSION_ID"] = "test-session-" + agent
    e["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + e.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, str(tool), *args], capture_output=True, text=True,
        env=e, cwd=str(board.parent), input=stdin)


def load_ticket(board, tid):
    return json.loads((board / (tid + ".json")).read_text())


def save_ticket(board, t):
    (board / (t["id"] + ".json")).write_text(json.dumps(t, indent=2) + "\n")


def messages(board):
    p = board / "messages.jsonl"
    if not p.exists():
        return []
    return [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()]


def repo_shas(board):
    repo = board.parent
    full = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    short = subprocess.check_output(
        ["git", "rev-parse", "--short", "HEAD"], cwd=repo, text=True).strip()
    return full, short


def setup_backend(tool, board):
    for name in ("alice", "bob", "reviewer"):
        r = run(tool, board, "join", name, "--roles", "backend", agent=name)
        assert r.returncode == 0, r.stderr + r.stdout
    parent = run(tool, board, "create", "impl work", "--role", "backend", agent="alice")
    assert parent.returncode == 0, parent.stderr
    child = run(tool, board, "create", "follow on", "--role", "backend",
                "--deps", "T-002", agent="alice")
    assert child.returncode == 0, child.stderr


def put_in_review(board, tid="T-002", owner="alice"):
    t = load_ticket(board, tid)
    full, short = repo_shas(board)
    t["status"] = "review"
    t["owner"] = owner
    t["role"] = "backend"
    t["review_at"] = "2026-09-15T12:00:00Z"
    t["review_head"] = full
    t["commit"] = "fix/t1031@%s" % short
    t["branch"] = "fix/t1031"
    t.setdefault("notes", []).append({
        "by": owner, "at": "2026-09-15T12:00:00Z",
        "text": "REVIEW: %s -- paths" % t["commit"],
    })
    save_ticket(board, t)
    return full, short


def test_pure_gate_helpers():
    reviewed = {"id": "T-001", "status": "done", "role": "backend",
                "review_at": "2026-09-15T00:00:00Z", "review_head": "a" * 40,
                "commit": "br@" + "a" * 7}
    accepted = dict(reviewed, review_events=[{
        "kind": "accept", "by": "rev", "at": "2026-09-15T00:01:00Z",
        "sha": "a" * 40, "notes": "ok"}])
    docs = {"id": "T-009", "status": "done", "role": "docs"}
    assert work_view.went_through_review(reviewed) is True
    assert work_view.is_docs_exempt(reviewed) is False
    assert work_view.dep_released(reviewed) is False
    assert work_view.dep_released(accepted) is True
    assert work_view.accepted_release_sha(accepted) == "a" * 40
    assert work_view.is_docs_exempt(docs) is True
    assert work_view.dep_released(docs) is False
    docs["release_override"] = work_view.make_release_override(
        "docs-exempt", "alice", "2026-09-15T00:02:00Z", "docs-exempt")
    assert work_view.dep_released(docs) is True
    assert work_view.unverified_block_reason("T-002") == REASON
    assert work_view.accepted_release_note("T-002", "a" * 40, "ceo") == (
        "T-002 accepted at %s by ceo -- unblocked" % ("a" * 40))
    leftover = {"notes": [{"by": "alice", "text": REASON},
                          {"by": "alice", "text": "other"}]}
    work_view.drop_unverified_gate_notes(leftover, "T-002")
    assert [n["text"] for n in leftover["notes"]] == ["other"]
    assert work_view.is_live_unverified_gate_note(REASON, "T-002") is True
    assert work_view.is_live_unverified_gate_note("resolved: " + REASON, "T-002") is False
    prose = dict(reviewed, notes=[{
        "by": "alice", "at": "2026-09-15T00:03:00Z",
        "text": "Merged into main as " + ("b" * 40)}])
    assert work_view.review_of(prose)["verified"] is False  # prose MERGED is not evidence (T-1111)
    assert work_view.dep_released(prose) is False  # release never trusts prose
    assert work_view.structured_accept(prose) is False
    assert work_view.structured_merge(prose) is False
    merged = dict(reviewed, merge_record=work_view.make_merge_record(
        "master", "2026-09-15T00:03:00Z", "b" * 40, pin="a" * 40, trunk="main"))
    assert work_view.dep_released(merged) is True
    stale_merge = dict(merged, review_head="c" * 40)
    assert work_view.dep_released(stale_merge) is False
    abbreviated = dict(reviewed, review_events=[{
        "kind": "accept", "sha": "a" * 7}])
    assert work_view.dep_released(abbreviated) is False
    work_view.supersede_release_evidence(accepted)
    assert work_view.dep_released(accepted) is False
    accepted["review_events"].append({
        "kind": "accept", "by": "rev", "sha": "a" * 40})
    assert work_view.dep_released(accepted) is True
    assert work_view.unreleased_dep_id(
        {"id": "T-003", "deps": ["T-001"]}, [prose]) == "T-001"
    assert work_view.unreleased_dep_id(
        {"id": "T-003", "deps": ["T-001"]}, [accepted]) == ""


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_done_without_accept_keeps_successor_blocked(tool, board):
    setup_backend(tool, board)
    put_in_review(board)
    done = run(tool, board, "done", "T-002", "--notes", "paths", "--force",
               agent="alice")
    assert done.returncode == 0, done.stderr + done.stdout
    assert "unblocked" not in done.stdout
    assert "started:" not in done.stdout
    assert "blocked: T-003 -- %s" % REASON in done.stdout
    child = load_ticket(board, "T-003")
    assert child["status"] == "blocked"
    assert child.get("unverified_block") == "T-002"
    assert any(REASON in (n.get("text") or "") for n in child.get("notes") or [])
    nxt = run(tool, board, "next", agent="bob")
    assert nxt.returncode != 0, nxt.stdout + nxt.stderr
    assert "T-003" not in nxt.stdout
    claim = run(tool, board, "claim", "T-003", agent="bob")
    assert claim.returncode != 0
    assert load_ticket(board, "T-003")["status"] == "blocked"


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_accept_then_done_releases_handoff_sha(tool, board):
    setup_backend(tool, board)
    full, _ = put_in_review(board)
    acc = run(tool, board, "accept", "T-002", "--sha", full,
              "--notes", "verified artifact", agent="reviewer")
    assert acc.returncode == 0, acc.stderr + acc.stdout
    done = run(tool, board, "done", "T-002", "--notes", "handoff", "--force",
               agent="alice")
    assert done.returncode == 0, done.stderr + done.stdout
    assert "unblocked: T-003" in done.stdout
    assert REASON not in done.stdout
    child = load_ticket(board, "T-003")
    assert child["status"] == "open"
    handoff = " ".join(n.get("text") or "" for n in child.get("notes") or [])
    assert full in handoff
    assert "success trigger" in handoff
    assert "accepted %s" % full in handoff
    assert REASON not in handoff
    shown = run(tool, board, "show", "T-003", agent="bob")
    assert shown.returncode == 0, shown.stderr + shown.stdout
    assert full in shown.stdout
    assert REASON not in shown.stdout


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_operator_override_is_recorded(tool, board):
    setup_backend(tool, board)
    put_in_review(board)
    done = run(tool, board, "done", "T-002", "--notes", "emergency",
               "--force", "--release-unverified", agent="alice")
    assert done.returncode == 0, done.stderr + done.stdout
    assert "unblocked: T-003" in done.stdout
    assert "released (operator override)" in done.stdout
    parent = load_ticket(board, "T-002")
    ov = parent.get("release_override") or {}
    assert ov.get("kind") == "operator"
    assert ov.get("by") == "alice"
    assert "release-unverified" in (ov.get("reason") or "")
    child = load_ticket(board, "T-003")
    assert child["status"] == "open"


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_docs_exempt_override_is_recorded(tool, board):
    assert run(tool, board, "join", "alice", "--roles", "docs",
               agent="alice").returncode == 0
    child = run(tool, board, "create", "next doc", "--role", "docs",
                "--deps", "T-001", agent="alice")
    assert child.returncode == 0, child.stderr
    done = run(tool, board, "done", "T-001", "--notes", "docs closed",
               "--force", agent="alice")
    assert done.returncode == 0, done.stderr + done.stdout
    assert "unblocked: T-002" in done.stdout
    assert "released (docs-exempt override)" in done.stdout
    parent = load_ticket(board, "T-001")
    ov = parent.get("release_override") or {}
    assert ov.get("kind") == "docs-exempt"
    assert ov.get("by") == "alice"


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_done_then_accept_releases_successor(tool, board):
    setup_backend(tool, board)
    full, _ = put_in_review(board)
    done = run(tool, board, "done", "T-002", "--notes", "closed early",
               "--force", agent="alice")
    assert done.returncode == 0, done.stderr + done.stdout
    assert load_ticket(board, "T-003")["status"] == "blocked"
    acc = run(tool, board, "accept", "T-002", "--sha", full,
              "--notes", "verified after close", agent="reviewer")
    assert acc.returncode == 0, acc.stderr + acc.stdout
    assert "unblocked: T-003" in acc.stdout
    child = load_ticket(board, "T-003")
    assert child["status"] == "open"
    assert "unverified_block" not in child
    notes = [n.get("text") or "" for n in child.get("notes") or []]
    handoff = " ".join(notes)
    release = "T-002 accepted at %s by reviewer -- unblocked" % full
    assert full in handoff
    assert release in notes
    assert "accepted %s" % full in handoff
    assert REASON not in handoff
    assert not any(work_view.is_live_unverified_gate_note(t, "T-002") for t in notes)
    shown = run(tool, board, "show", "T-003", agent="bob")
    assert shown.returncode == 0, shown.stderr + shown.stdout
    assert full in shown.stdout
    assert release in shown.stdout
    assert REASON not in shown.stdout


# Work-entry points that must share dep_released (CEO T-1036 RULE A).
# Each refuses a child whose parent is done-unverified.
WORK_ENTRY_POINTS = (
    ("status", ("status", "T-003", "in-progress"), "bob"),
    ("review owner", ("review", "T-003", "--notes", "submit", "--force"), "bob"),
    ("reserve", ("reserve", "T-003", "--for", "bob"), "planner"),
    ("next", ("next",), "bob"),
    ("claim", ("claim", "T-003"), "bob"),
    ("claim --another", ("claim", "T-003", "--another"), "bob"),
    ("assign", ("assign", "T-003", "--owner", "bob"), "reviewer"),
    ("reserve-then-claim", None, "bob"),  # special: reserve then claim
    ("route --claim", ("route", "--claim"), "reviewer"),
    ("reopen", ("reopen", "T-003"), "bob"),
    ("dispatch", ("dispatch", "T-003", "--to", "bob"), "reviewer"),
)


def _force_open_child(board, tid="T-003"):
    child = load_ticket(board, tid)
    child["status"] = "open"
    child.pop("unverified_block", None)
    save_ticket(board, child)
    lock = board / (tid + ".lock")
    if lock.exists():
        lock.unlink()


def _assert_child_not_started(board, name):
    child = load_ticket(board, "T-003")
    assert child["status"] != "claimed", "%s claimed T-003: %s" % (name, child)
    assert not child.get("owner"), "%s set an owner: %s" % (name, child)


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_work_entry_points_refuse_unverified_parent(tool, board):
    """Every start path refuses a child whose parent is done-unverified."""
    setup_backend(tool, board)
    put_in_review(board)
    done = run(tool, board, "done", "T-002", "--notes", "unverified", "--force",
               agent="alice")
    assert done.returncode == 0, done.stderr + done.stdout
    assert load_ticket(board, "T-003")["status"] == "blocked"

    reopen = run(tool, board, "reopen", "T-003", agent="bob")
    out = reopen.stdout + reopen.stderr
    assert reopen.returncode != 0, out
    assert REASON in out
    assert load_ticket(board, "T-003")["status"] == "blocked"

    for name, args, agent in WORK_ENTRY_POINTS:
        _force_open_child(board)
        if name == "claim --another":
            extra = run(tool, board, "create", "unrelated", "--role", "backend",
                        agent="alice")
            assert extra.returncode == 0, extra.stderr
            held = run(tool, board, "claim", "T-004", agent="bob")
            assert held.returncode == 0, held.stderr + held.stdout
            r = run(tool, board, *args, agent=agent)
            out = r.stdout + r.stderr
            assert r.returncode != 0, out
            assert REASON in out
            _assert_child_not_started(board, name)
            done_extra = run(tool, board, "done", "T-004", "--notes", "park",
                             "--force", agent="bob")
            assert done_extra.returncode == 0, done_extra.stderr + done_extra.stdout
            continue
        if name == "reserve-then-claim":
            child = load_ticket(board, "T-003")
            child["reserved_for"] = "bob"
            save_ticket(board, child)
            r = run(tool, board, "claim", "T-003", agent="bob")
            out = r.stdout + r.stderr
            assert r.returncode != 0, out
            assert REASON in out
            _assert_child_not_started(board, name)
            continue
        if name == "dispatch" and tool.name == "cli.py":
            continue
        r = run(tool, board, *args, agent=agent)
        out = r.stdout + r.stderr
        _assert_child_not_started(board, name)
        if name == "next":
            assert "T-003" not in r.stdout
            continue
        if name == "route --claim":
            continue
        assert r.returncode != 0, "%s should refuse: %s" % (name, out)
        assert REASON in out or name == "dispatch"
        if name == "reserve":
            assert not load_ticket(board, "T-003").get("reserved_for")


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_merge_prose_does_not_release_child(tool, board):
    """RULE B: a 'Merged into main as <sha>' note is not a release record."""
    setup_backend(tool, board)
    full, _ = put_in_review(board)
    parent = load_ticket(board, "T-002")
    parent.setdefault("notes", []).append({
        "by": "alice", "at": "2026-09-15T12:05:00Z",
        "text": "Merged into main as " + full,
    })
    save_ticket(board, parent)
    done = run(tool, board, "done", "T-002", "--notes", "unverified", "--force",
               agent="alice")
    assert done.returncode == 0, done.stderr + done.stdout
    parent = load_ticket(board, "T-002")
    assert not parent.get("review_events")
    assert not parent.get("release_override")
    assert not parent.get("merge_record")
    assert work_view.dep_released(parent) is False
    child = load_ticket(board, "T-003")
    assert child["status"] == "blocked"
    nxt = run(tool, board, "next", "--another", agent="bob")
    assert nxt.returncode != 0, nxt.stdout + nxt.stderr
    assert load_ticket(board, "T-003")["status"] != "claimed"


def test_entry_points_call_shared_dep_released():
    """No start path carries its own copy of the release check."""
    fns = {
        "try_claim": ("_refuse_unreleased_deps", "refuse_unreleased_reason"),
        "try_claim_one_active": ("try_claim",),
        "save": ("_refuse_unreleased_deps", "_reopen_unverified_successors"),
        "cmd_claim": ("_refuse_unreleased_deps",),
        "cmd_status": ("_refuse_unreleased_deps",),
        "cmd_review": ("_refuse_unreleased_deps",),
        "cmd_assign": ("_refuse_unreleased_deps",),
        "cmd_reopen": ("_refuse_unreleased_deps",),
        "cmd_reserve": ("_refuse_unreleased_deps",),
        "cmd_next": ("unblocked", "try_claim"),
        "cmd_route": ("released_ids", "try_claim"),
        "cmd_plan": ("_plan_keep_gated_unstarted",),
        "_plan_keep_gated_unstarted": ("unreleased_dep_id",),
        "_start_successors": ("unblocked",),
        "cmd_done": ("_reopen_unverified_successors", "_maybe_record_release_override"),
        "_reopen_unverified_successors": ("dep_released", "_retire_stale_gated_start"),
    }
    siblings = list(ROOT.glob("tickets_*.py"))
    for path in TOOLS:
        src = path.read_text()
        for fn, needles in fns.items():
            start = src.find("def %s(" % fn)
            body_path = path
            if start == -1 and path.name == "tickets.py":
                for sib in siblings:
                    sib_src = sib.read_text()
                    start = sib_src.find("def %s(" % fn)
                    if start != -1:
                        src_fn, body_path = sib_src, sib
                        break
                else:
                    src_fn = src
            else:
                src_fn = src
            assert start != -1, "%s missing %s" % (path.name, fn)
            nxt = src_fn.find("\ndef ", start + 4)
            body = src_fn[start:nxt if nxt != -1 else None]
            assert any(n in body for n in needles), (
                "%s %s does not call shared gate %s" % (body_path.name, fn, needles))
        if path.name == "tickets.py":
            start = src.find("def cmd_dispatch(")
            assert start != -1
            nxt = src.find("\ndef ", start + 4)
            body = src[start:nxt]
            assert "_refuse_unreleased_deps" in body
            start = src.find("def pending_work(")
            assert start != -1
            nxt = src.find("\ndef ", start + 4)
            body = src[start:nxt]
            assert "unreleased_dep_id" in body
            start = src.find("def _watch_bind_ticket(")
            assert start != -1
            nxt = src.find("\ndef ", start + 4)
            body = src[start:nxt]
            assert "unreleased_dep_id" in body


def submit_review_without_pr(tool, board):
    r = run(tool, board, "review", "T-002", "--notes", "review current work",
            "--force", agent="alice")
    assert r.returncode == 0, r.stdout + r.stderr
    return repo_shas(board)[0]


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_reopened_accept_cannot_release_new_review(tool, board):
    setup_backend(tool, board)
    sha_a = submit_review_without_pr(tool, board)
    accepted = run(tool, board, "accept", "T-002", "--sha", sha_a,
                   "--notes", "verified A", agent="reviewer")
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    reopened = run(tool, board, "reopen", "T-002", "--notes", "revise A",
                   agent="alice")
    assert reopened.returncode == 0, reopened.stdout + reopened.stderr
    parent = load_ticket(board, "T-002")
    assert parent["review_events"][0]["superseded"] is True
    assert not work_view.structured_accept(parent)
    subprocess.run(
        ["git", "-c", "user.name=test", "-c", "user.email=test@example.com",
         "commit", "--allow-empty", "-m", "revision B"],
        cwd=board.parent, check=True, capture_output=True)
    sha_b = submit_review_without_pr(tool, board)
    assert sha_b != sha_a
    done = run(tool, board, "done", "T-002", "--notes", "closed B",
               "--force", agent="alice")
    assert done.returncode == 0, done.stdout + done.stderr
    claim = run(tool, board, "claim", "T-003", agent="bob")
    assert claim.returncode != 0, claim.stdout + claim.stderr
    assert load_ticket(board, "T-003")["status"] == "blocked"
    parent = load_ticket(board, "T-002")
    assert parent["review_head"] == sha_b
    assert not work_view.dep_released(parent)
    accepted = run(tool, board, "accept", "T-002", "--sha", sha_b,
                   "--notes", "verified B", agent="reviewer")
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    claim = run(tool, board, "claim", "T-003", agent="bob")
    assert claim.returncode == 0, claim.stdout + claim.stderr
    assert sha_b in claim.stdout


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_full_sha_accept_without_pr_releases_child(tool, board):
    setup_backend(tool, board)
    full = submit_review_without_pr(tool, board)
    accepted = run(tool, board, "accept", "T-002", "--sha", full,
                   "--notes", "verified local submission", agent="reviewer")
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    done = run(tool, board, "done", "T-002", "--notes", "handoff",
               "--force", agent="alice")
    assert done.returncode == 0, done.stdout + done.stderr
    claim = run(tool, board, "claim", "T-003", agent="bob")
    assert claim.returncode == 0, claim.stdout + claim.stderr
    assert full in claim.stdout


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_status_blocked_child_refused(tool, board):
    setup_backend(tool, board)
    put_in_review(board)
    assert run(tool, board, "done", "T-002", "--notes", "early", "--force",
               agent="alice").returncode == 0
    before = load_ticket(board, "T-003")
    r = run(tool, board, "status", "T-003", "in-progress", agent="bob")
    assert r.returncode != 0, r.stdout + r.stderr
    assert REASON in r.stderr
    assert load_ticket(board, "T-003") == before


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
@pytest.mark.parametrize("recovery", ["reopen-review-accept-done", "late-override"])
@pytest.mark.parametrize("stale_assignment", [False, True])
def test_release_recovers_claimable_child(tool, board, recovery, stale_assignment):
    setup_backend(tool, board)
    full = submit_review_without_pr(tool, board)
    assert run(tool, board, "done", "T-002", "--notes", "early", "--force",
               agent="alice").returncode == 0
    if stale_assignment:
        child = load_ticket(board, "T-003")
        child["owner"] = "alice"
        child["claimed_at"] = "2026-09-15T00:00:00Z"
        child["owner_generation"] = 1
        child["owner_lease"] = {"owner": "alice", "generation": 1}
        save_ticket(board, child)
        (board / "T-003.lock").write_text("alice")
    if recovery == "reopen-review-accept-done":
        r = run(tool, board, "reopen", "T-002", "--notes", "recover", agent="alice")
        assert r.returncode == 0, r.stdout + r.stderr
        full = submit_review_without_pr(tool, board)
        r = run(tool, board, "accept", "T-002", "--sha", full,
                "--notes", "verified", agent="reviewer")
        assert r.returncode == 0, r.stdout + r.stderr
        assert load_ticket(board, "T-003")["status"] == "blocked"
        r = run(tool, board, "done", "T-002", "--notes", "handoff", "--force", agent="alice")
    else:
        r = run(tool, board, "done", "T-002", "--notes", "override recovery",
                "--force", "--release-unverified", agent="alice")
        ov = load_ticket(board, "T-002")["release_override"]
        assert ov["kind"] == "operator"
        assert ov["by"] == "alice"
        assert "--release-unverified" in ov["reason"]
    assert r.returncode == 0, r.stdout + r.stderr
    child = load_ticket(board, "T-003")
    assert child["status"] == "open"
    assert not child.get("owner")
    assert not child.get("unverified_block")
    assert not (board / "T-003.lock").exists()
    assert REASON not in json.dumps(child["notes"])
    r = run(tool, board, "claim", "T-003", agent="bob")
    assert r.returncode == 0, r.stdout + r.stderr
    if recovery == "reopen-review-accept-done":
        assert full in r.stdout
    else:
        assert "operator" in r.stdout and "--release-unverified" in r.stdout


def import_cli(tool):
    import importlib.util
    spec = importlib.util.spec_from_file_location("gate_cli_" + tool.parent.name, tool)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_internal_entry_points_refuse_gated_child(tool, board, monkeypatch):
    """Exercise publication, transfers, scheduling and auto-start entry points."""
    setup_backend(tool, board)
    put_in_review(board)
    assert run(tool, board, "done", "T-002", "--notes", "early", "--force",
               agent="alice").returncode == 0
    cli = import_cli(tool)
    monkeypatch.setenv("TICKET_AGENT", "bob")
    from ticket_board.scheduler import ready_tickets
    for name in ("try_claim", "try_claim_one_active", "save status", "save owner",
                 "update", "assign transfer", "success trigger", "scheduler",
                 "watch/spawn/remote pending", "plan"):
        _force_open_child(board)
        child = load_ticket(board, "T-003")
        child["owner"] = ""
        child.pop("reserved_for", None)
        save_ticket(board, child)
        if name in ("try_claim", "try_claim_one_active"):
            with pytest.raises(SystemExit, match="without verification"):
                getattr(cli, name)(str(board), "T-003", "bob")
        elif name.startswith("save"):
            if name == "save status":
                child["status"] = "claimed"
            child["owner"] = "bob"
            with pytest.raises(SystemExit, match="without verification"):
                cli.save(str(board), child)
        elif name in ("update", "assign transfer"):
            # Recover a historical bypass: even an already-claimed child
            # cannot be updated as active or transferred while still gated.
            child.update(status="claimed", owner="alice")
            save_ticket(board, child)
            args = (("update", "T-003", "progress") if name == "update" else
                    ("assign", "T-003", "--owner", "bob"))
            r = run(tool, board, *args, agent="alice")
            assert r.returncode != 0 and REASON in r.stderr, r.stdout + r.stderr
            assert load_ticket(board, "T-003") == child
            _force_open_child(board)
            child.update(status="open", owner="")
            save_ticket(board, child)
        elif name == "success trigger":
            assert not any(cli._start_successors(str(board), "T-002"))
        elif name == "scheduler":
            assert "T-003" not in [t["id"] for t in ready_tickets(cli.load_all(str(board)))]
        elif name == "watch/spawn/remote pending":
            if hasattr(cli, "pending_work"):
                pending = cli.pending_work(str(board), "bob")
                assert "T-003" not in json.dumps(pending)
        elif name == "plan":
            r = run(tool, board, "plan", agent="bob", stdin=json.dumps([{
                "title": "planned gated child", "deps": ["T-002"],
                "owner": "bob", "status": "claimed"}]))
            assert r.returncode == 0, r.stdout + r.stderr
            planned = load_ticket(board, "T-004")
            assert planned["status"] == "open" and not planned.get("owner")
            assert planned not in cli.unblocked(str(board), cli.load_all(str(board)))
        _assert_child_not_started(board, name)


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_non_cleanup_automated_successor_stays_gated(tool, board):
    """kind=automated is not enough; only cleanup_worktree is exempt."""
    setup_backend(tool, board)
    put_in_review(board)
    child = load_ticket(board, "T-003")
    child["kind"] = "automated"
    child["automated"] = {"action": "notify", "target": "T-002"}
    save_ticket(board, child)
    done = run(tool, board, "done", "T-002", "--notes", "unverified", "--force",
               agent="alice")
    assert done.returncode == 0, done.stderr + done.stdout
    assert "blocked: T-003 -- %s" % REASON in done.stdout
    assert "cleanup:" not in done.stdout
    child = load_ticket(board, "T-003")
    assert child["status"] == "blocked"
    assert child.get("unverified_block") == "T-002"
    assert child["kind"] == "automated"
    assert child["automated"]["action"] == "notify"
    _force_open_child(board)
    child = load_ticket(board, "T-003")
    child["kind"] = "automated"
    child["automated"] = {"action": "notify", "target": "T-002"}
    child["status"] = "claimed"
    child["owner"] = "bob"
    cli = import_cli(tool)
    with pytest.raises(SystemExit, match="without verification"):
        cli.save(str(board), child)
    assert load_ticket(board, "T-003")["status"] == "open"
    assert not any(cli._start_successors(str(board), "T-002"))


@pytest.mark.parametrize("tool", TOOLS, ids=TOOL_IDS)
def test_cleanup_worktree_runs_on_unverified_done(tool, board):
    """T-946 x T-1031: cleanup_worktree still runs when the parent is unverified."""
    setup_backend(tool, board)
    extra = run(tool, board, "create", "cleanup worktree for T-002",
                "--role", "ops", "--deps", "T-002", agent="alice")
    assert extra.returncode == 0, extra.stderr + extra.stdout
    child = load_ticket(board, "T-004")
    child["kind"] = "automated"
    child["automated"] = {
        "action": "cleanup_worktree",
        "target": "T-002",
        "worktree": str(board.parent / "missing-wt"),
        "escalated": False,
    }
    save_ticket(board, child)
    put_in_review(board)
    done = run(tool, board, "done", "T-002", "--notes", "unverified", "--force",
               agent="alice")
    assert done.returncode == 0, done.stderr + done.stdout
    assert "blocked: T-003 -- %s" % REASON in done.stdout
    assert "cleanup: T-004 [automated:removed]" in done.stdout
    assert load_ticket(board, "T-003")["status"] == "blocked"
    cleanup = load_ticket(board, "T-004")
    assert cleanup["status"] == "done"
    assert "unverified_block" not in cleanup
    claim = run(tool, board, "claim", "T-003", agent="bob")
    assert claim.returncode != 0
