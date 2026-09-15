"""T-946: automated worktree cleanup node + safe sweep (throwaway boards)."""
import json
import os
import subprocess
import sys
import time

import pytest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tickets.py"
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ticket_board import worktree_gc as gc
from ticket_board.work_view import work_payload


def clean_env(tmp_path, **overrides):
    e = {
        "PATH": str(tmp_path / "bin") + os.pathsep + "/usr/sbin" + os.pathsep
                + "/usr/bin" + os.pathsep + "/bin",
        "HOME": str(tmp_path / "fake-home"),
        "TICKET_AGENT": "boss",
        "TICKETS_GC_OPEN_PRS": "none",
        "PYTEST_CURRENT_TEST": "tests/test_t946_worktree_gc.py::simulated (call)",
    }
    (tmp_path / "fake-home").mkdir(exist_ok=True)
    (tmp_path / "bin").mkdir(exist_ok=True)
    e.update(overrides)
    return e


def run(cwd, *args, env=None, tmp_path=None, stdin=None):
    return subprocess.run(
        [sys.executable, str(TOOL), *args],
        capture_output=True, text=True, cwd=str(cwd),
        env=env or clean_env(tmp_path),
        input=stdin)


def git(cwd, *args):
    return subprocess.run(
        ["git", "-c", "user.email=t946@test", "-c", "user.name=t946", *args],
        cwd=str(cwd), capture_output=True, text=True, check=True)


def make_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.email", "t946@test")
    git(path, "config", "user.name", "t946")
    (path / "README").write_text("t946\n")
    git(path, "add", "README")
    git(path, "commit", "-qm", "init")
    return path


def make_origin_pair(tmp_path):
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", str(origin))
    repo = make_repo(tmp_path / "repo")
    git(repo, "remote", "add", "origin", str(origin))
    git(repo, "push", "-q", "-u", "origin", "main")
    git(repo, "remote", "set-head", "origin", "main")
    return repo, origin


def add_worktree(repo, name="feat"):
    wt = repo / ".worktrees" / name
    wt.parent.mkdir(exist_ok=True)
    git(repo, "worktree", "add", "-q", "-b", name, str(wt))
    return wt


def commit_on(wt, text="feat"):
    (wt / "f.txt").write_text(text + "\n")
    git(wt, "add", "f.txt")
    git(wt, "commit", "-qm", text)


def merge_to_origin(repo, wt, branch="feat"):
    git(repo, "merge", "--no-ff", "-m", "land %s" % branch, branch)
    git(repo, "push", "-q", "origin", "main")


def load_ticket(repo, tid):
    return json.loads((repo / ".tickets" / ("%s.json" % tid)).read_text())


def messages(repo):
    p = repo / ".tickets" / "messages.jsonl"
    if not p.is_file():
        return []
    return [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()]


def test_create_worktree_adds_automated_child_visible_in_graph(tmp_path):
    repo, _ = make_origin_pair(tmp_path)
    wt = add_worktree(repo)
    env = clean_env(tmp_path)
    assert run(repo, "init", env=env, tmp_path=tmp_path).returncode == 0
    r = run(repo, "create", "impl", "--worktree", str(wt), env=env, tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr + r.stdout
    assert "created T-001" in r.stdout
    assert "cleanup node: T-002" in r.stdout

    parent = load_ticket(repo, "T-001")
    child = load_ticket(repo, "T-002")
    assert parent["worktree"] == str(wt.resolve())
    assert child["kind"] == "automated"
    assert child["deps"] == ["T-001"]
    assert child["automated"]["action"] == "cleanup_worktree"
    assert child["automated"]["target"] == "T-001"

    g = run(repo, "graph", env=env, tmp_path=tmp_path)
    assert g.returncode == 0, g.stderr
    assert "T-002" in g.stdout and "automated" in g.stdout

    # workflow_graph payload (Work view source)
    sys.path.insert(0, str(ROOT))
    import tickets as tool
    graph = tool.workflow_graph(tool.load_all(str(repo / ".tickets")), include_done=True)
    by = dict((n["id"], n) for n in graph["nodes"])
    assert by["T-002"]["kind"] == "automated"
    assert by["T-002"]["automated"] is True
    assert any(e["from"] == "T-001" and e["to"] == "T-002" for e in graph["edges"])

    payload = work_payload(tool.load_all(str(repo / ".tickets")), graph, [])
    n2 = dict((n["id"], n) for n in payload["nodes"])["T-002"]
    assert n2["kind"] == "automated"
    assert n2["who_kind"] == "automated"
    assert "no model" in n2["evidence"] or "no agent" in n2["evidence"]


def test_happy_path_removes_worktree_keeps_branch_no_task_message(tmp_path):
    repo, _ = make_origin_pair(tmp_path)
    wt = add_worktree(repo)
    commit_on(wt)
    merge_to_origin(repo, wt)
    env = clean_env(tmp_path)
    assert run(repo, "init", env=env, tmp_path=tmp_path).returncode == 0
    r = run(repo, "create", "impl", "--role", "backend", "--worktree", str(wt),
            env=env, tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr + r.stdout
    d = run(repo, "done", "T-001", "--notes", "landed", "--force",
            "--artifact", str(wt), env=env, tmp_path=tmp_path)
    assert d.returncode == 0, d.stderr + d.stdout
    assert "automated:removed" in d.stdout
    assert not wt.exists()
    # branch intact
    show = subprocess.run(
        ["git", "show-ref", "--verify", "--quiet", "refs/heads/feat"],
        cwd=str(repo))
    assert show.returncode == 0
    child = load_ticket(repo, "T-002")
    assert child["status"] == "done"
    assert child["kind"] == "automated"
    task_posts = [m for m in messages(repo)
                  if m.get("task") and m.get("re") == "T-002"]
    assert task_posts == []
    digest = (repo / ".tickets" / "gc-digest.jsonl").read_text()
    assert "remove" in digest and str(wt) in digest


def test_unmerged_done_escalates_to_agent(tmp_path):
    repo, _ = make_origin_pair(tmp_path)
    wt = add_worktree(repo)
    commit_on(wt, "unmerged")
    env = clean_env(tmp_path)
    assert run(repo, "init", env=env, tmp_path=tmp_path).returncode == 0
    run(repo, "create", "impl", "--worktree", str(wt), env=env, tmp_path=tmp_path)
    d = run(wt, "done", "T-001", "--notes", "closed without merge", "--force",
            env=env, tmp_path=tmp_path)
    assert d.returncode == 0, d.stderr + d.stdout
    child = load_ticket(repo, "T-002")
    assert child["kind"] == "agent"
    assert child["automated"]["escalated"] is True
    assert child["automated"]["escalate_reason"] == "unmerged"
    assert "keep --" in child["body"] and "remove --" in child["body"]
    assert wt.exists()


def test_next_and_claim_skip_automated(tmp_path):
    repo, _ = make_origin_pair(tmp_path)
    env = clean_env(tmp_path)
    assert run(repo, "init", env=env, tmp_path=tmp_path).returncode == 0
    run(repo, "join", "boss", "--roles", "backend,ops", env=env, tmp_path=tmp_path)
    run(repo, "create", "human work", "--role", "backend", env=env, tmp_path=tmp_path)
    # standalone automated node (no worktree) should still be unclaimable
    board = repo / ".tickets"
    child = json.loads((board / "T-001.json").read_text())
    # create automated sibling
    run(repo, "create", "cleanup worktree for T-001", "--role", "ops",
        env=env, tmp_path=tmp_path)
    t2 = load_ticket(repo, "T-002")
    t2["kind"] = "automated"
    t2["automated"] = {"action": "cleanup_worktree", "target": "T-001",
                       "worktree": str(repo), "escalated": False}
    (board / "T-002.json").write_text(json.dumps(t2, indent=2))
    nxt = run(repo, "next", env=env, tmp_path=tmp_path)
    assert nxt.returncode == 0, nxt.stderr + nxt.stdout
    assert "T-001" in nxt.stdout
    assert "T-002" not in nxt.stdout.split("IN PROGRESS")[0] or "T-001" in nxt.stdout
    claimed = load_ticket(repo, "T-001")
    assert claimed["status"] == "claimed"
    c = run(repo, "claim", "T-002", env=env, tmp_path=tmp_path)
    assert c.returncode != 0
    assert "automated" in (c.stderr + c.stdout)


def _eval_repo(tmp_path, merged=True):
    repo, _ = make_origin_pair(tmp_path)
    wt = add_worktree(repo)
    commit_on(wt)
    if merged:
        merge_to_origin(repo, wt)
    parent = {"id": "T-001", "status": "done"}
    probes = gc.Probes(gh_fn=lambda branch, cwd: [])
    return repo, wt, parent, probes


def test_evaluate_dirty_escalates(tmp_path):
    repo, wt, parent, probes = _eval_repo(tmp_path)
    (wt / "dirt.txt").write_text("nope\n")
    d = gc.evaluate_cleanup(str(wt), parent=parent, repo_root=str(repo), probes=probes)
    assert d["action"] == "escalate" and d["reason"] == "dirty"


def test_evaluate_custody_ignored_file(tmp_path):
    repo, _ = make_origin_pair(tmp_path)
    wt = add_worktree(repo)
    (wt / ".gitignore").write_text("secret.txt\n.venv/\n")
    git(wt, "add", ".gitignore")
    git(wt, "commit", "-qm", "ignore")
    merge_to_origin(repo, wt)
    (wt / "secret.txt").write_text("token\n")
    (wt / ".venv").mkdir()
    (wt / ".venv" / "x").write_text("ok\n")
    parent = {"id": "T-001", "status": "done"}
    probes = gc.Probes(gh_fn=lambda branch, cwd: [])
    d = gc.evaluate_cleanup(str(wt), parent=parent, repo_root=str(repo), probes=probes)
    assert d["action"] == "escalate" and d["reason"] == "custody"
    assert "secret.txt" in d["detail"]


def test_evaluate_live_cwd(tmp_path):
    repo, wt, parent, probes = _eval_repo(tmp_path)
    probes.lsof_fn = lambda path: [{"pid": 9, "cmd": "python"}]
    d = gc.evaluate_cleanup(str(wt), parent=parent, repo_root=str(repo), probes=probes)
    assert d["action"] == "escalate" and d["reason"] == "live_cwd"


def test_evaluate_live_cwd_real_process(tmp_path):
    repo, wt, parent, probes = _eval_repo(tmp_path)
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        cwd=str(wt))
    try:
        time.sleep(0.2)
        d = gc.evaluate_cleanup(str(wt), parent=parent, repo_root=str(repo),
                                probes=probes)
        assert d["action"] == "escalate" and d["reason"] == "live_cwd"
    finally:
        proc.kill()
        proc.wait()


def test_evaluate_runtime_symlink(tmp_path):
    repo, wt, parent, probes = _eval_repo(tmp_path)
    link = tmp_path / "atman-runtime-current"
    link.symlink_to(wt)
    probes.runtime_extra = [str(link)]
    d = gc.evaluate_cleanup(str(wt), parent=parent, repo_root=str(repo), probes=probes)
    assert d["action"] == "escalate" and d["reason"] == "runtime_symlink"


def test_evaluate_nested_main_checkout(tmp_path):
    repo, wt, parent, probes = _eval_repo(tmp_path)
    d = gc.evaluate_cleanup(str(repo), parent=parent, repo_root=str(repo), probes=probes)
    assert d["action"] == "escalate" and d["reason"] == "nested"


def test_evaluate_open_pr(tmp_path):
    repo, wt, parent, _ = _eval_repo(tmp_path)
    probes = gc.Probes(gh_fn=lambda branch, cwd: [{"number": 7, "url": "https://pr/7"}])
    d = gc.evaluate_cleanup(str(wt), parent=parent, repo_root=str(repo), probes=probes)
    assert d["action"] == "escalate" and d["reason"] == "open_pr"


def test_evaluate_not_done_waits(tmp_path):
    repo, wt, parent, probes = _eval_repo(tmp_path)
    parent["status"] = "claimed"
    d = gc.evaluate_cleanup(str(wt), parent=parent, repo_root=str(repo), probes=probes)
    assert d["action"] == "wait" and d["reason"] == "not_done"


@pytest.mark.parametrize("flags", [(), ("--dry-run",), ("--apply",)])
def test_gc_sweep_happy_path(tmp_path, flags):
    repo, _ = make_origin_pair(tmp_path)
    wt = add_worktree(repo)
    commit_on(wt)
    merge_to_origin(repo, wt)
    env = clean_env(tmp_path)
    assert run(repo, "init", env=env, tmp_path=tmp_path).returncode == 0
    run(repo, "create", "impl", "--worktree", str(wt), env=env, tmp_path=tmp_path)
    # mark parent done without triggering successors (write JSON)
    t = load_ticket(repo, "T-001")
    t["status"] = "done"
    t["done_at"] = "2026-09-14T00:00:00Z"
    t["branch"] = "feat"
    (repo / ".tickets" / "T-001.json").write_text(json.dumps(t, indent=2))
    dirty = add_worktree(repo, "dirty")
    (dirty / "untracked").write_text("keep me")
    run(repo, "create", "dirty impl", "--worktree", str(dirty), env=env, tmp_path=tmp_path)
    t = load_ticket(repo, "T-003")
    t["status"] = "done"
    (repo / ".tickets" / "T-003.json").write_text(json.dumps(t))
    before = {p.name: p.read_bytes() for p in (repo / ".tickets").glob("T-*.json")}
    r = run(repo, "gc", *flags, env=env, tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr + r.stdout
    assert dirty.exists()
    if flags == ("--apply",):
        assert "removed" in r.stdout
        assert not wt.exists()
        assert load_ticket(repo, "T-004")["kind"] == "agent"
    else:
        assert "would remove" in r.stdout
        assert "keep" in r.stdout and "dirty" in r.stdout
        assert wt.exists()
        assert before == {p.name: p.read_bytes() for p in (repo / ".tickets").glob("T-*.json")}
    show = subprocess.run(
        ["git", "show-ref", "--verify", "--quiet", "refs/heads/feat"],
        cwd=str(repo))
    assert show.returncode == 0


def test_work_payload_escalated_label(tmp_path):
    tickets = [
        {"id": "T-001", "title": "impl", "status": "done", "deps": [],
         "kind": "agent", "notes": []},
        {"id": "T-002", "title": "cleanup worktree for T-001", "status": "open",
         "deps": ["T-001"], "kind": "agent",
         "automated": {"action": "cleanup_worktree", "target": "T-001",
                       "escalated": True, "escalate_reason": "custody"},
         "notes": []},
    ]
    graph = {"nodes": [{"id": "T-001"}, {"id": "T-002"}],
             "edges": [{"from": "T-001", "to": "T-002"}],
             "roots": ["T-001"]}
    payload = work_payload(tickets, graph, [])
    n2 = dict((n["id"], n) for n in payload["nodes"])["T-002"]
    assert n2["escalated"] is True
    assert "keep/remove" in n2["evidence"]


@pytest.mark.parametrize("status,stdout,stderr", [
    (2, "", "probe failed"),
    (2, "", ""),
    (1, "", "permission denied"),
    (0, "garbage\n", ""),
    (0, "", ""),
    (0, "p123\ncsleep\nfcwd\n", ""),
])
def test_lsof_error_preserves_and_escalates(tmp_path, monkeypatch, status, stdout, stderr):
    repo, wt, parent, probes = _eval_repo(tmp_path)
    env = clean_env(tmp_path)
    assert run(repo, "init", env=env, tmp_path=tmp_path).returncode == 0
    assert run(repo, "create", "impl", "--worktree", str(wt),
               env=env, tmp_path=tmp_path).returncode == 0
    import tickets as tool
    board = str(repo / ".tickets")
    t = load_ticket(repo, "T-001")
    t["status"] = "done"
    tool.save(board, t)
    # Force the BSD/macOS probe on every test host; Git remains real.
    isdir = gc.os.path.isdir
    monkeypatch.setattr(gc.os.path, "isdir", lambda p: False if p == "/proc" else isdir(p))
    monkeypatch.setattr(gc, "_find_lsof", lambda: "lsof")
    real_run = gc._run
    monkeypatch.setattr(gc, "_run", lambda argv, **kw:
                        subprocess.CompletedProcess(argv, status, stdout, stderr)
                        if argv[0] == "lsof" else real_run(argv, **kw))
    proc = subprocess.Popen(["sleep", "300"], cwd=str(wt))
    try:
        rows = gc.sweep(board, tool._gc_hooks(), probes=probes, repo_root=str(repo), apply=True)
        assert wt.exists()
        assert any(r["status"] == "escalate" for r in rows)
        child = load_ticket(repo, "T-002")
        assert child["kind"] == "agent"
        assert child["automated"]["escalate_reason"] == "live_cwd"
        git(repo, "show-ref", "--verify", "refs/heads/feat")
    finally:
        proc.terminate()
        proc.wait()


@pytest.mark.parametrize("failure", ["missing", "exception", "no_matches", "spaces"])
def test_lsof_probe_protocol(tmp_path, monkeypatch, failure):
    path = tmp_path / "checkout with spaces"
    path.mkdir()
    monkeypatch.setattr(gc.os.path, "isdir", lambda p: False)
    monkeypatch.setattr(gc, "_find_lsof", lambda: None if failure == "missing" else "lsof")
    def probe(argv):
        if failure == "exception":
            raise OSError("cannot execute")
        if failure == "no_matches":
            return subprocess.CompletedProcess(argv, 1, "", "")
        return subprocess.CompletedProcess(argv, 0, "p123\ncsleep\nfcwd\nn%s\n" % path, "")
    monkeypatch.setattr(gc, "_run", probe)
    result = gc.live_cwds(str(path))
    if failure in ("missing", "exception"):
        assert result and result[0]["unknown"]
    elif failure == "no_matches":
        assert result == []
    else:
        assert result == [{"pid": 123, "cmd": "sleep"}]
