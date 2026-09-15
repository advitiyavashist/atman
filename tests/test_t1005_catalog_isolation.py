"""T-1005: isolate the generic harness catalog from board operator policy.

Public `harness available` / `harness usage` report capability, auth, usage,
and generic next actions only. Board-specific model bans, quota holds, and
staffing policy stay in that board's briefs and worker prompt inheritance.
Two isolated boards with different policies must not cross-contaminate
catalog output. No provider call.
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tickets.py"

CATALOG_IDS = ("cursor", "agy", "claude", "codex", "devin", "gemini", "grok")

# Unique tokens written only into each throwaway board's briefs.
POLICY_A = "POLICY-ALPHA-BAN-MODEL-ORION"
POLICY_B = "POLICY-BETA-QUOTA-HOLD-UNTIL-RESET"

# T-997 leak: these were hardcoded into catalog output on every board.
OPERATOR_POLICY_LEAKS = (
    "no new claude fable",
    "do not spawn a gemini product job",
    "do not spawn unless they say usage is back",
    "do not spawn gemini",
)


def clean_env(tmp_path, home_name="fake-home", **overrides):
    home = tmp_path / home_name
    home.mkdir(exist_ok=True)
    empty = tmp_path / "empty-bin"
    empty.mkdir(exist_ok=True)
    e = {
        "PATH": str(empty) + os.pathsep + "/usr/bin" + os.pathsep + "/bin",
        "HOME": str(home),
        "PYTEST_CURRENT_TEST": "tests/test_t1005_catalog_isolation.py::simulated (call)",
    }
    e.update(overrides)
    return e


def run(cwd, *args, env=None):
    return subprocess.run(
        [sys.executable, str(TOOL), *args],
        capture_output=True, text=True, cwd=str(cwd), env=env)


def git(cwd, *args):
    return subprocess.run(
        ["git", "-c", "user.email=t1005@test", "-c", "user.name=t1005", *args],
        cwd=str(cwd), capture_output=True, text=True, check=True)


def make_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q", "-b", "main")
    (path / "README").write_text("t1005\n")
    git(path, "add", "README")
    git(path, "commit", "-qm", "init")
    return path


def boot_board(repo, env, agent, policy):
    r = run(repo, "init", env=env)
    assert r.returncode == 0, r.stderr
    r = run(repo, "join", agent, "--roles", "backend", env=env)
    assert r.returncode == 0, r.stderr
    r = run(repo, "brief", agent, policy, env=env)
    assert r.returncode == 0, r.stderr + r.stdout
    briefs = repo / ".tickets" / "briefs"
    briefs.mkdir(parents=True, exist_ok=True)
    (briefs / "_shared.md").write_text("shared staffing: %s\n" % policy)
    roles = briefs / "roles"
    roles.mkdir(exist_ok=True)
    (roles / "backend.md").write_text("role staffing: %s\n" % policy)
    return repo / ".tickets"


def assert_catalog_is_generic(text):
    lowered = text.lower()
    for cid in CATALOG_IDS:
        assert cid in lowered, cid
    assert "usage unknown" in lowered or "unknown" in lowered
    assert "which of these do you want to use?" in lowered
    assert "if they say yes:" in lowered
    assert "unknown, not exhausted" in lowered
    assert "spawn only" in lowered
    for leak in OPERATOR_POLICY_LEAKS:
        assert leak not in lowered, leak
    assert POLICY_A not in text
    assert POLICY_B not in text


def test_catalog_rows_have_no_operator_policy():
    import importlib.util
    spec = importlib.util.spec_from_file_location("tickets_t1005", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    blob = " ".join(
        "%s %s" % (row.get("policy", ""), row.get("if_yes", ""))
        for row in mod.INTEGRATION_CATALOG).lower()
    for leak in OPERATOR_POLICY_LEAKS:
        assert leak not in blob, leak
    by_id = {row["id"]: row for row in mod.INTEGRATION_CATALOG}
    assert by_id["claude"]["policy"] == "ok to spawn if chosen"
    assert "unknown usage" in by_id["codex"]["policy"]
    assert "persist/hooks" in by_id["gemini"]["policy"]
    assert "product job" not in by_id["gemini"]["policy"]


def test_two_boards_catalog_does_not_cross_contaminate_prompts_do(tmp_path):
    repo_a = make_repo(tmp_path / "board-a")
    repo_b = make_repo(tmp_path / "board-b")
    env_a = clean_env(tmp_path, home_name="home-a")
    env_b = clean_env(tmp_path, home_name="home-b")
    boot_board(repo_a, env_a, "alice", POLICY_A)
    boot_board(repo_b, env_b, "bob", POLICY_B)

    avail_a = run(repo_a, "harness", "available", env=env_a)
    avail_b = run(repo_b, "harness", "available", env=env_b)
    assert avail_a.returncode == 0, avail_a.stderr
    assert avail_b.returncode == 0, avail_b.stderr
    assert_catalog_is_generic(avail_a.stdout)
    assert_catalog_is_generic(avail_b.stdout)

    usage_a = run(repo_a, "harness", "usage", env=env_a)
    usage_b = run(repo_b, "harness", "usage", env=env_b)
    assert usage_a.returncode == 0, usage_a.stderr
    assert usage_b.returncode == 0, usage_b.stderr
    for text in (usage_a.stdout, usage_b.stdout):
        lowered = text.lower()
        for leak in OPERATOR_POLICY_LEAKS:
            assert leak not in lowered, leak
        assert POLICY_A not in text
        assert POLICY_B not in text
        assert "unknown, not exhausted" in lowered
        assert "spawn only" in lowered

    prompt_a = run(repo_a, "prompt", "--agent", "alice", env=env_a)
    prompt_b = run(repo_b, "prompt", "--agent", "bob", env=env_b)
    assert prompt_a.returncode == 0, prompt_a.stderr
    assert prompt_b.returncode == 0, prompt_b.stderr
    assert POLICY_A in prompt_a.stdout
    assert POLICY_B not in prompt_a.stdout
    assert POLICY_B in prompt_b.stdout
    assert POLICY_A not in prompt_b.stdout
    assert "standing brief" in prompt_a.stdout.lower()
    assert "standing brief" in prompt_b.stdout.lower()


def test_fresh_init_master_brief_is_not_steer_operator_policy(tmp_path):
    repo = make_repo(tmp_path / "fresh")
    env = clean_env(tmp_path)
    r = run(repo, "init", env=env)
    assert r.returncode == 0, r.stderr
    body = (repo / ".tickets" / "MASTER.md").read_text().lower()
    for leak in OPERATOR_POLICY_LEAKS:
        assert leak not in body, leak
    assert "atm harness available" in body
    assert "unknown usage" in body
