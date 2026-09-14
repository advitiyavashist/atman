"""T-935 negative cases: completed-run evidence and landing fences."""

import subprocess

import pytest

import train_stack as train


@pytest.mark.parametrize("line", ["# suite-ran=false", "# not-suite-ran", "# suite-ran-aborted"])
def test_negative_or_aborted_suite_marker_is_not_execution(line):
    assert not train.suite_evidence(line)


@pytest.mark.parametrize("line", ["# suite-ran", "# suite-ran=true", "suite-ran=completed"])
def test_completed_run_marker_is_execution(line):
    assert train.suite_evidence(line)


@pytest.fixture
def landing(monkeypatch, tmp_path):
    calls = []
    binding = {"origin": "https://github.com/example/atman.git", "git_common_dir": str(tmp_path)}
    monkeypatch.setattr(train, "repo_bind", lambda root: binding)
    refs = {"main": "old-base", "origin/main": "old-base", "train/stack": "tested-train"}
    monkeypatch.setattr(train, "_rev", lambda root, ref: refs.get(ref, ""))

    def gh(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(train.subprocess, "run", gh)
    receipt = {"verdict": "ACCEPT", "repo": binding, "main_sha": "old-base",
               "train_sha": "tested-train", "train_branch": "train/stack", "strategy": "no-ff",
               "members": [{"pr": 42, "sha": "accepted-head", "repo": binding}]}
    return receipt, calls, str(tmp_path)


@pytest.mark.parametrize("field", ["repo", "main_sha", "train_sha", "strategy"])
def test_missing_landing_fence_refuses_before_gh_merge(landing, field):
    receipt, calls, root = landing
    del receipt[field]
    with pytest.raises(SystemExit):
        train.land_train(receipt, True, root=root)
    assert not calls


def test_gh_merge_is_bound_to_receipt_repo_not_caller_directory(landing):
    receipt, calls, root = landing
    train.land_train(receipt, True, root=root)
    assert calls, "expected a GH operation on the recorded member"
    for command, kwargs in calls:
        assert "--repo" in command or kwargs.get("cwd") == root
        assert "--repo" in command
        assert command[command.index("--repo") + 1] == "example/atman"
        assert kwargs.get("cwd") == root


def test_advanced_remote_base_refuses_even_if_local_main_is_stale(landing, monkeypatch):
    receipt, calls, root = landing
    refs = {"main": "old-base", "origin/main": "new-remote-base", "train/stack": "tested-train"}
    monkeypatch.setattr(train, "_rev", lambda root, ref: refs.get(ref, ""))
    with pytest.raises(SystemExit):
        train.land_train(receipt, True, root=root)
    assert not any(command[:3] == ["gh", "pr", "merge"] for command, kwargs in calls)


def test_literal_debt_policy_is_not_authority(landing):
    receipt, calls, root = landing
    receipt["verdict"] = "GO-WITH-DEBT"
    receipt["policy"] = "go-with-debt"
    with pytest.raises(SystemExit, match="debt_authority"):
        train.land_train(receipt, True, root=root)
    assert not calls


def test_named_debt_authority_and_baseline_can_land(landing):
    receipt, calls, root = landing
    receipt["verdict"] = "GO-WITH-DEBT"
    receipt["policy"] = "go-with-debt"
    receipt["debt_authority"] = "atman-ceo-opus-0913"
    receipt["debt_baseline"] = "old-base"
    train.land_train(receipt, True, root=root)
    assert calls
