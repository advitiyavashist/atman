"""Shared Git environment policy used by path probes and worker launchers."""

from ticket_board.git_env import clean_git_env


def test_clean_git_env_preserves_unrelated_settings_without_mutating_source():
    inherited = {
        "GIT_DIR": "/another/repo/.git",
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "core.worktree",
        "GIT_CONFIG_VALUE_0": "/another/repo",
        "GIT_CEILING_DIRECTORIES": "/scratch",
        "GIT_FUTURE_OVERRIDE": "value",
        "PATH": "/bin",
        "HOME": "/synthetic/home",
        "TICKET_AGENT": "worker",
    }
    original = dict(inherited)
    assert clean_git_env(inherited) == {
        "PATH": "/bin", "HOME": "/synthetic/home", "TICKET_AGENT": "worker",
    }
    assert inherited == original
    assert clean_git_env({}) == {}
