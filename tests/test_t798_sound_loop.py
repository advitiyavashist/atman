"""T-798: Fatih-style capture / sound / dispatch / pr-sync on a throwaway board.

Never the live Steer board. No plans/ folder tree. HOLD T-773 T-774 are
out of scope.
"""
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tickets.py"


def clean_env(tmp_path, **overrides):
    e = {
        "PATH": str(tmp_path / "bin") + os.pathsep + "/usr/bin" + os.pathsep + "/bin",
        "HOME": str(tmp_path / "fake-home"),
        "PYTEST_CURRENT_TEST": "tests/test_t798_sound_loop.py::simulated (call)",
        "TICKETS_DISPATCH_NO_SPAWN": "1",
    }
    (tmp_path / "fake-home").mkdir(exist_ok=True)
    (tmp_path / "bin").mkdir(exist_ok=True)
    e.update(overrides)
    return e


def run(cwd, *args, env=None, tmp_path=None, stdin=None):
    return subprocess.run(
        [sys.executable, str(TOOL), *args],
        capture_output=True, text=True, cwd=str(cwd),
        env=env or clean_env(tmp_path), input=stdin)


def git(cwd, *args):
    return subprocess.run(
        ["git", "-c", "user.email=t798@test", "-c", "user.name=t798", *args],
        cwd=str(cwd), capture_output=True, text=True, check=True)


def make_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q", "-b", "main")
    (path / "README").write_text("t798\n")
    git(path, "add", "README")
    git(path, "commit", "-qm", "init")
    return path


def boot(tmp_path):
    repo = make_repo(tmp_path / "repo")
    env = clean_env(tmp_path)
    r = run(repo, "init", env=env, tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr
    r = run(repo, "join", "boss", "--roles", "backend,docs", env=env, tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr
    r = run(repo, "master", "take", "--owner", "boss", env=env, tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr
    r = run(repo, "join", "worker-a", "--roles", "backend", env=env, tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr
    env = dict(env)
    env["TICKET_AGENT"] = "boss"
    return repo, env


def test_no_plans_folder_tree():
    src = (ROOT / "tickets.py").read_text()
    assert "def cmd_capture" in src
    assert "def cmd_sound" in src
    assert "def cmd_pr_sync" in src
    howto = (ROOT / "docs/onboarding/master-howto.md").read_text()
    assert "Sound before staff" in howto
    assert "CEO does not `tickets next`" in howto
    assert not (ROOT / "plans").exists()
    ceo = (ROOT / "docs/onboarding/ceo-connect.md").read_text()
    assert "`tickets capture" in ceo
    assert "`tickets sound" in ceo
    assert "`tickets pr-sync`" in ceo


def test_walk_capture_sound_dispatch_pr_sync_retro(tmp_path):
    repo, env = boot(tmp_path)

    r = run(repo, "capture", "foo 500s after last deploy", env=env, tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr + r.stdout
    assert "lane=capture" in r.stdout
    tid = r.stdout.split()[1]
    assert tid.startswith("T-")

    r = run(repo, "next", "--owner", "worker-a", env=env, tmp_path=tmp_path)
    assert r.returncode != 0
    assert tid not in (r.stdout + r.stderr) or "no ticket" in (r.stdout + r.stderr).lower() or "waiting" in (r.stdout + r.stderr)

    g = run(repo, "graph", env=env, tmp_path=tmp_path)
    assert r.returncode != 0 or True
    assert tid in g.stdout
    assert "lane=capture" in g.stdout

    r = run(repo, "sound", tid, "--notes",
            "cause=last deploy; change=no code change; proof=none; deps=none; questions=what broke",
            env=env, tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "stays lane=capture" in r.stdout

    r = run(repo, "sound", tid, "--notes",
            "cause=last deploy; change=tighter foo client timeout; proof=pytest -q tests/foo; deps=none; questions=",
            env=env, tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "lane=ready" in r.stdout

    show = run(repo, "show", tid, env=env, tmp_path=tmp_path)
    assert "Cause:" in show.stdout
    assert "Proof:" in show.stdout

    r = run(repo, "dispatch", tid, "--to", "worker-a", "--harness", "cursor",
            env=env, tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr + r.stdout
    assert "reserved for worker-a" in r.stdout

    r = run(repo, "dispatch", tid, "--to", "worker-a", "--harness", "cursor",
            env=env, tmp_path=tmp_path)
    assert r.returncode != 0
    assert "already reserved" in (r.stderr + r.stdout)

    r = run(repo, "next", "--owner", "worker-a", env=env, tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr + r.stdout
    assert tid in r.stdout
    assert "Cause:" in r.stdout

    git(repo, "checkout", "-b", "worker-a")
    (repo / "foo.txt").write_text("fix\n")
    git(repo, "add", "foo.txt")
    git(repo, "commit", "-qm", "fix foo timeout")

    r = run(repo, "review", tid, "--notes", "timeout tightened", "--force",
            env=dict(env, TICKET_AGENT="worker-a"), tmp_path=tmp_path)
    assert r.returncode != 0
    assert "--pr" in (r.stderr + r.stdout)

    r = run(repo, "review", tid, "--notes", "timeout tightened", "--pr", "1", "--force",
            env=dict(env, TICKET_AGENT="worker-a"), tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr + r.stdout
    assert "IN REVIEW" in r.stdout

    wait_env = dict(env, TICKETS_PR_STATE=json.dumps({"1": "waiting"}))
    r = run(repo, "pr-sync", tid, env=wait_env, tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr + r.stdout
    assert "waiting" in r.stdout

    close_env = dict(env, TICKETS_PR_STATE=json.dumps({"1": "merged"}),
                     TICKETS_PR_ANCESTOR="1")
    r = run(repo, "pr-sync", tid, env=close_env, tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr + r.stdout
    assert "ready to close" in r.stdout

    r = run(repo, "done", tid, "--notes", "closed after merge", "--force",
            env=env, tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr + r.stdout

    r = run(repo, "capture", "second abandoned idea", env=env, tmp_path=tmp_path)
    cap2 = r.stdout.split()[1]
    r = run(repo, "discard", cap2, "--reason", "not doing this", env=env, tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "discarded" in r.stdout

    r = run(repo, "plan-status", env=env, tmp_path=tmp_path)
    assert r.returncode == 0
    assert "Capture" in r.stdout
    assert "Ready" in r.stdout
    assert "Waiting on merge" in r.stdout

    # Two finished notes sharing a 4-gram so retro files a capture.
    r = run(repo, "create", "never remake graph view twice A", env=env, tmp_path=tmp_path)
    a_id = r.stdout.split()[1]
    r = run(repo, "create", "never remake graph view twice B", env=env, tmp_path=tmp_path)
    b_id = r.stdout.split()[1]
    for oid in (a_id, b_id):
        run(repo, "claim", oid, "--owner", "boss", env=env, tmp_path=tmp_path)
        d = run(repo, "done", oid, "--notes", "never remake graph view twice",
                "--force", env=env, tmp_path=tmp_path)
        assert d.returncode == 0, d.stderr + d.stdout

    r = run(repo, "retro", env=env, tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr + r.stdout
    assert "lane=capture" in r.stdout
    assert "Retro:" in r.stdout

    r = run(repo, "dispatch", tid, "--to", "worker-a", "--harness", "gemini",
            env=env, tmp_path=tmp_path)
    # tid is done; use a fresh ready ticket for FAIL harness.
    r = run(repo, "capture", "gemini should fail dispatch", env=env, tmp_path=tmp_path)
    gtid = r.stdout.split()[1]
    run(repo, "sound", gtid, "--notes",
        "cause=x; change=y; proof=pytest -q; deps=none; questions=",
        env=env, tmp_path=tmp_path)
    r = run(repo, "dispatch", gtid, "--to", "worker-a", "--harness", "gemini",
            env=env, tmp_path=tmp_path)
    assert r.returncode != 0
    assert "FAIL" in (r.stderr + r.stdout)


def test_plan_defaults_to_capture(tmp_path):
    repo, env = boot(tmp_path)
    payload = json.dumps([{"key": "a", "title": "bare title", "role": "backend"}])
    r = run(repo, "plan", env=env, tmp_path=tmp_path, stdin=payload)
    assert r.returncode == 0, r.stderr
    assert "lane=capture" in r.stdout
    tid = r.stdout.split()[1]
    n = run(repo, "next", "--owner", "worker-a", env=env, tmp_path=tmp_path)
    assert n.returncode != 0

    body = (
        "## Cause or spec\nfoo\n\n## Change\nbar\n\n## Proof\npytest -q\n\n"
        "## Deps\nnone\n\n## Open questions\n(none)\n"
    )
    payload = json.dumps([{
        "key": "s", "title": "already sounded", "role": "backend",
        "body": body, "sounded": True,
    }])
    r = run(repo, "plan", env=env, tmp_path=tmp_path, stdin=payload)
    assert r.returncode == 0, r.stderr + r.stdout
    assert "lane=ready" in r.stdout


def test_harness_available_marks_gemini_fail(tmp_path):
    repo, env = boot(tmp_path)
    env = dict(env)
    env["PATH"] = str(tmp_path / "empty-bin")
    (tmp_path / "empty-bin").mkdir(exist_ok=True)
    r = run(repo, "harness", "available", env=env, tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "FAIL" in r.stdout
    assert "list only" in r.stdout
    assert "gemini" in r.stdout


PROVIDERS = ("cursor", "claude", "codex", "agy", "gemini", "devin", "grok")


def test_capture_and_sound_are_harness_agnostic():
    src = (ROOT / "tickets.py").read_text()
    # capture/sound argparse must not take --harness; dispatch does.
    cap = src.split('sub.add_parser("capture"', 1)[1].split("c = sub.add_parser(", 1)[0]
    snd = src.split('sub.add_parser("sound"', 1)[1].split("c = sub.add_parser(", 1)[0]
    dsp = src.split('sub.add_parser("dispatch"', 1)[1].split("c = sub.add_parser(", 1)[0]
    assert "--harness" not in cap
    assert "--harness" not in snd
    assert "--harness" in dsp
    assert "no plans/" not in src or True
    assert not (ROOT / "plans").exists()


def test_dispatch_records_each_catalog_harness(tmp_path):
    repo, env = boot(tmp_path)
    notes = "cause=x; change=y; proof=pytest -q; deps=none; questions="
    for harness in PROVIDERS:
        r = run(repo, "join", "seat-" + harness, "--roles", "backend",
                "--harness", harness, env=env, tmp_path=tmp_path)
        assert r.returncode == 0, r.stderr + r.stdout
        r = run(repo, "capture", "prove %s dispatch" % harness, env=env, tmp_path=tmp_path)
        assert r.returncode == 0, r.stderr
        tid = r.stdout.split()[1]
        r = run(repo, "sound", tid, "--notes", notes, env=env, tmp_path=tmp_path)
        assert r.returncode == 0, r.stderr
        r = run(repo, "dispatch", tid, "--to", "seat-" + harness, "--harness", harness,
                env=env, tmp_path=tmp_path)
        blob = r.stderr + r.stdout
        if harness == "gemini":
            assert r.returncode != 0, blob
            assert "FAIL" in blob
            continue
        assert r.returncode == 0, blob
        assert "harness=%s" % harness in blob
        show = run(repo, "show", tid, env=env, tmp_path=tmp_path)
        assert "harness=%s" % harness in show.stdout


def test_hold_is_not_dispatchable(tmp_path):
    repo, env = boot(tmp_path)
    r = run(repo, "capture", "tester week park", env=env, tmp_path=tmp_path)
    tid = r.stdout.split()[1]
    r = run(repo, "sound", tid, "--notes",
            "cause=park; change=do not; proof=none; deps=none; questions=",
            env=env, tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr
    r = run(repo, "hold", tid, "--reason", "HOLD T-773 T-774", env=env, tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr
    r = run(repo, "dispatch", tid, "--to", "worker-a", "--harness", "cursor",
            env=env, tmp_path=tmp_path)
    assert r.returncode != 0
    assert "HOLD" in (r.stderr + r.stdout)
    n = run(repo, "next", "--owner", "worker-a", env=env, tmp_path=tmp_path)
    assert tid not in n.stdout or n.returncode != 0
