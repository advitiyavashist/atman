"""T-537: T-522 re-root as a second post-FLAG ancestry root, and a release
dir (no .git above scheduler.py) must never shell out to git in a non-repo.
"""

import importlib.util
import os
import shutil
import subprocess

import ticket_board.scheduler as sched
from ticket_board.scheduler import clear_sha_post_flag_cache

from test_t488_sha_ancestry_era import _git_env


def _init_dual_root_repo(tmp_path):
    """Old FLAG lineage, a re-rooted (T-522-style) new lineage, and a third
    lineage sharing no history with either -- three independent orphan
    branches in one repo so ancestry checks can't accidentally succeed."""
    repo = tmp_path / "gitrepo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    env = _git_env()

    def commit(msg):
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True, env=env)
        subprocess.run(
            ["git", "commit", "-q", "-m", msg, "--allow-empty"], cwd=repo, check=True, env=env)
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repo, text=True, env=env).strip()

    def orphan(branch):
        subprocess.run(["git", "checkout", "-q", "--orphan", branch], cwd=repo, check=True, env=env)

    orphan("old")
    pre_sha = commit("pre")
    flag_sha = commit("flag")

    orphan("new")
    new_root_sha = commit("new-root")
    new_post_sha = commit("new-post")

    orphan("other")
    divergent_sha = commit("divergent")

    return repo, pre_sha, flag_sha, new_root_sha, new_post_sha, divergent_sha


def _bind_dual_roots(monkeypatch, repo, flag_sha, new_root_sha):
    monkeypatch.setattr(sched, "FLAG_PIN", flag_sha)
    monkeypatch.setattr(sched, "T522_ROOT_PIN", new_root_sha)
    monkeypatch.setattr(sched, "_scheduler_repo_root", lambda: str(repo))
    clear_sha_post_flag_cache()


def test_second_root_descendant_is_post(tmp_path, monkeypatch):
    repo, _pre, flag_sha, new_root, new_post, _divergent = _init_dual_root_repo(tmp_path)
    _bind_dual_roots(monkeypatch, repo, flag_sha, new_root)
    # new_post descends only from new_root, not from the old FLAG_PIN lineage
    # at all -- pre-fix (single-root FLAG_PIN ancestry) this is None/unknown.
    assert sched._sha_is_post_flag(new_post) is True


def test_old_lineage_ancestor_of_flag_still_pre(tmp_path, monkeypatch):
    repo, pre_sha, flag_sha, new_root, _new_post, _divergent = _init_dual_root_repo(tmp_path)
    _bind_dual_roots(monkeypatch, repo, flag_sha, new_root)
    assert sched._sha_is_post_flag(pre_sha) is False


def test_divergent_lineage_stays_unknown(tmp_path, monkeypatch):
    """X2: a sha related to neither root and not an ancestor of FLAG_PIN
    must stay unknown -- the two-root fix must not widen this to a guess."""
    repo, _pre, flag_sha, new_root, _new_post, divergent = _init_dual_root_repo(tmp_path)
    _bind_dual_roots(monkeypatch, repo, flag_sha, new_root)
    assert sched._sha_is_post_flag(divergent) is None


def _load_module_from_path(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _copy_scheduler_to_release_dir(tmp_path, label):
    """A `tickets-releases/<sha>/src/ticket_board/scheduler.py` export has no
    .git anywhere above it -- only the file itself, unlike a checkout."""
    dest = tmp_path / label / "scheduler.py"
    dest.parent.mkdir(parents=True)
    shutil.copy(sched.__file__, dest)
    return dest


_ATMAN_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(sched.__file__))))


def test_release_dir_no_git_no_env_never_calls_git(tmp_path, monkeypatch):
    monkeypatch.delenv("TICKETS_ATMAN_REPO", raising=False)
    dest = _copy_scheduler_to_release_dir(tmp_path, "release_a")
    mod = _load_module_from_path("t537_release_copy_no_env", dest)

    def _fail_if_called(*args, **kwargs):
        raise AssertionError("git must not run when no repo can be resolved")

    monkeypatch.setattr(mod, "_git_run", _fail_if_called)
    assert mod._scheduler_repo_root() is None
    assert mod._sha_is_post_flag("8346c38") is None


def test_release_dir_no_git_with_env_repo_resolves_post(tmp_path, monkeypatch):
    monkeypatch.setenv("TICKETS_ATMAN_REPO", _ATMAN_REPO_ROOT)
    dest = _copy_scheduler_to_release_dir(tmp_path, "release_b")
    mod = _load_module_from_path("t537_release_copy_with_env", dest)
    assert mod._scheduler_repo_root() == _ATMAN_REPO_ROOT

    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=_ATMAN_REPO_ROOT, text=True).strip()
    assert mod._sha_is_post_flag(head) is True
