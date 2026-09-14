"""T-933: train build / verify / land."""

import json
import os
import subprocess
from pathlib import Path

from test_byoa import board, run  # noqa: F401

TOOL = Path(__file__).resolve().parents[1] / "tickets.py"


def git(repo, *args, check=True):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    return subprocess.run(["git", "-C", str(repo), *args], check=check,
                          capture_output=True, text=True, env=env)


def _repo(tmp_path):
    repo = tmp_path / "code"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "t@t")
    git(repo, "config", "user.name", "t")
    git(repo, "checkout", "-q", "-b", "main")
    (repo / "base.txt").write_text("base\n")
    git(repo, "add", "base.txt")
    git(repo, "commit", "-qm", "base")
    return repo


def _branch(repo, name, path, text):
    git(repo, "checkout", "-q", "main")
    git(repo, "checkout", "-q", "-b", name)
    (repo / path).write_text(text)
    git(repo, "add", path)
    git(repo, "commit", "-qm", name)
    git(repo, "checkout", "-q", "main")


def test_build_stacks_no_ff_and_stops_on_conflict(board, tmp_path):
    repo = _repo(tmp_path)
    _branch(repo, "pr-a", "a.txt", "A\n")
    _branch(repo, "pr-b", "b.txt", "B\n")
    _branch(repo, "pr-c1", "same.txt", "one\n")
    _branch(repo, "pr-c2", "same.txt", "two\n")
    ok = run(board, "train", "build", "--refs", "pr-a,pr-b",
             "--artifact", str(repo), "--trunk", "main")
    assert ok.returncode == 0, ok.stderr + ok.stdout
    assert "train/stack" in ok.stdout
    rec = json.loads((board / "train.json").read_text())
    assert len(rec["members"]) == 2
    assert rec["train_sha"]
    bad = run(board, "train", "build", "--refs", "pr-c1,pr-c2",
              "--artifact", str(repo), "--trunk", "main", "--branch", "train/conflict")
    assert bad.returncode != 0
    assert "conflict: pr-c1 then pr-c2" in bad.stderr + bad.stdout


def test_verify_accept_and_reject(board, tmp_path):
    repo = _repo(tmp_path)
    _branch(repo, "pr-a", "a.txt", "A\n")
    assert run(board, "train", "build", "--refs", "pr-a",
               "--artifact", str(repo), "--trunk", "main").returncode == 0
    main_f = tmp_path / "main.txt"
    train_f = tmp_path / "train.txt"
    main_f.write_text("# suite-ran\ntests/test_old.py::test_x\n")
    train_f.write_text("# suite-ran\ntests/test_old.py::test_x\n")
    accept = run(board, "train", "verify",
                 "--main-failures", str(main_f),
                 "--train-failures", str(train_f))
    assert accept.returncode == 0, accept.stderr
    assert "ACCEPT" in accept.stdout
    train_f.write_text("# suite-ran\ntests/test_old.py::test_x\ntests/test_new.py::test_y\n")
    reject = run(board, "train", "verify",
                 "--main-failures", str(main_f),
                 "--train-failures", str(train_f))
    assert reject.returncode != 0
    assert "REJECT" in reject.stderr + reject.stdout
    rec = json.loads((board / "train.json").read_text())
    assert rec["new_failures"] == ["tests/test_new.py::test_y"]


def test_land_refuses_without_accept_and_merges_at_exact_sha(board, tmp_path):
    repo = _repo(tmp_path)
    _branch(repo, "pr-a", "a.txt", "A\n")
    assert run(board, "train", "build", "--refs", "pr-a",
               "--artifact", str(repo), "--trunk", "main").returncode == 0
    rec = json.loads((board / "train.json").read_text())
    rec["members"][0]["pr"] = 42
    rec["verdict"] = ""
    (board / "train.json").write_text(json.dumps(rec))
    assert run(board, "join", "boss", "--roles", "master").returncode == 0
    run(board, "master", "take", agent="boss")
    refused = run(board, "train", "land", "--artifact", str(repo),
                 env={"TICKET_AGENT": "boss"})
    assert refused.returncode != 0
    assert "ACCEPT" in refused.stderr + refused.stdout
    rec["verdict"] = "ACCEPT"
    (board / "train.json").write_text(json.dumps(rec))
    gh = tmp_path / "gh"
    gh.write_text("#!/bin/sh\necho gh \"$@\" >> \"$TICKETS_GH_LOG\"\n")
    os.chmod(gh, 0o755)
    log = tmp_path / "gh.log"
    landed = run(board, "train", "land", "--artifact", str(repo),
                 env={"TICKET_AGENT": "boss", "TICKETS_GH": str(gh),
                      "TICKETS_GH_LOG": str(log)})
    assert landed.returncode == 0, landed.stderr + landed.stdout
    logged = log.read_text()
    assert "pr merge 42 --match-head-commit %s" % rec["members"][0]["sha"] in logged


def test_build_refuses_preexisting_branch(board, tmp_path):
    repo = _repo(tmp_path)
    _branch(repo, "pr-a", "a.txt", "A\n")
    git(repo, "branch", "train/stack")
    bad = run(board, "train", "build", "--refs", "pr-a",
              "--artifact", str(repo), "--trunk", "main")
    assert bad.returncode != 0
    assert "already exists" in bad.stderr + bad.stdout


def test_verify_unknown_without_suite_evidence(board, tmp_path):
    repo = _repo(tmp_path)
    _branch(repo, "pr-a", "a.txt", "A\n")
    assert run(board, "train", "build", "--refs", "pr-a",
               "--artifact", str(repo), "--trunk", "main").returncode == 0
    main_f = tmp_path / "main.txt"
    train_f = tmp_path / "train.txt"
    main_f.write_text("")
    train_f.write_text("")
    unknown = run(board, "train", "verify",
                  "--main-failures", str(main_f),
                  "--train-failures", str(train_f))
    assert unknown.returncode != 0
    assert "UNKNOWN" in unknown.stderr + unknown.stdout
    rec = json.loads((board / "train.json").read_text())
    assert rec["verdict"] == "UNKNOWN"


def test_land_refuses_moved_trunk_or_train(board, tmp_path):
    repo = _repo(tmp_path)
    _branch(repo, "pr-a", "a.txt", "A\n")
    assert run(board, "train", "build", "--refs", "pr-a",
               "--artifact", str(repo), "--trunk", "main").returncode == 0
    rec = json.loads((board / "train.json").read_text())
    rec["members"][0]["pr"] = 42
    rec["verdict"] = "ACCEPT"
    (board / "train.json").write_text(json.dumps(rec))
    assert run(board, "join", "boss", "--roles", "master").returncode == 0
    run(board, "master", "take", agent="boss")
    (repo / "moved.txt").write_text("moved\n")
    git(repo, "add", "moved.txt")
    git(repo, "commit", "-qm", "trunk moved")
    moved = run(board, "train", "land", "--artifact", str(repo),
                env={"TICKET_AGENT": "boss"})
    assert moved.returncode != 0
    assert "trunk moved" in moved.stderr + moved.stdout


def test_land_refuses_moved_train_head(board, tmp_path):
    repo = _repo(tmp_path)
    _branch(repo, "pr-a", "a.txt", "A\n")
    assert run(board, "train", "build", "--refs", "pr-a",
               "--artifact", str(repo), "--trunk", "main").returncode == 0
    rec = json.loads((board / "train.json").read_text())
    rec["members"][0]["pr"] = 42
    rec["verdict"] = "ACCEPT"
    (board / "train.json").write_text(json.dumps(rec))
    assert run(board, "join", "boss", "--roles", "master").returncode == 0
    run(board, "master", "take", agent="boss")
    git(repo, "checkout", "-q", rec["train_branch"])
    (repo / "extra.txt").write_text("extra\n")
    git(repo, "add", "extra.txt")
    git(repo, "commit", "-qm", "train moved")
    git(repo, "checkout", "-q", "main")
    moved = run(board, "train", "land", "--artifact", str(repo),
                env={"TICKET_AGENT": "boss"})
    assert moved.returncode != 0
    assert "train branch moved" in moved.stderr + moved.stdout


def test_land_refuses_foreign_repo_and_unnamed_debt(board, tmp_path):
    repo = _repo(tmp_path)
    other_root = tmp_path / "other-root"
    other_root.mkdir()
    other = _repo(other_root)
    _branch(repo, "pr-a", "a.txt", "A\n")
    assert run(board, "train", "build", "--refs", "pr-a",
               "--artifact", str(repo), "--trunk", "main").returncode == 0
    rec = json.loads((board / "train.json").read_text())
    rec["members"][0]["pr"] = 42
    rec["verdict"] = "ACCEPT"
    (board / "train.json").write_text(json.dumps(rec))
    assert run(board, "join", "boss", "--roles", "master").returncode == 0
    run(board, "master", "take", agent="boss")
    foreign = run(board, "train", "land", "--artifact", str(other),
                  env={"TICKET_AGENT": "boss"})
    assert foreign.returncode != 0
    out = foreign.stderr + foreign.stdout
    assert "not the repo" in out or "bound to another repo" in out
    rec["verdict"] = "GO-WITH-DEBT"
    rec.pop("policy", None)
    (board / "train.json").write_text(json.dumps(rec))
    debt = run(board, "train", "land", "--artifact", str(repo),
               env={"TICKET_AGENT": "boss"})
    assert debt.returncode != 0
    assert "GO-WITH-DEBT" in debt.stderr + debt.stdout
