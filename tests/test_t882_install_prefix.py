"""T-882 (QA T-868): install.sh must not silently clobber an existing CLI.

Before this fix install.sh always `ln -sf`'d over $HOME/.local/bin/tickets,
so a machine already running a pinned live release (install_live.py
--activate) or any unrelated `tickets` on PATH would be silently replaced.
Fixed with an opt-in --prefix/PREFIX destination and a clobber guard that
only proceeds without --force when the existing target already links to
this same checkout.
"""
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
INSTALL_SH = ROOT / "install.sh"


def _run_install(home, *args):
    env = dict(os.environ, HOME=str(home))
    env.pop("PREFIX", None)
    return subprocess.run(["sh", str(INSTALL_SH), *args], cwd=str(ROOT),
                          capture_output=True, text=True, env=env)


def test_fresh_install_links_into_default_prefix(tmp_path):
    result = _run_install(tmp_path)
    assert result.returncode == 0, result.stderr
    link = tmp_path / ".local/bin/tickets"
    assert link.is_symlink()
    assert os.readlink(link) == str(ROOT / "tickets.py")


def test_rerunning_from_the_same_checkout_is_idempotent(tmp_path):
    assert _run_install(tmp_path).returncode == 0
    result = _run_install(tmp_path)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / ".local/bin/tickets").is_symlink()


def test_prefix_flag_installs_to_an_isolated_directory(tmp_path):
    isolated = tmp_path / "isolated-bin"
    result = _run_install(tmp_path, "--prefix", str(isolated))
    assert result.returncode == 0, result.stderr
    assert (isolated / "tickets").is_symlink()
    assert not (tmp_path / ".local/bin/tickets").exists()


def test_prefix_env_var_is_honored(tmp_path):
    isolated = tmp_path / "env-bin"
    env = dict(os.environ, HOME=str(tmp_path), PREFIX=str(isolated))
    result = subprocess.run(["sh", str(INSTALL_SH)], cwd=str(ROOT),
                            capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr
    assert (isolated / "tickets").is_symlink()


def test_refuses_to_clobber_an_unrelated_existing_tickets_binary(tmp_path):
    bin_dir = tmp_path / ".local/bin"
    bin_dir.mkdir(parents=True)
    foreign = bin_dir / "tickets"
    foreign.write_text("#!/bin/sh\necho unrelated tool\n")
    foreign.chmod(0o755)

    result = _run_install(tmp_path)
    assert result.returncode != 0
    assert "refusing to overwrite" in result.stderr
    assert foreign.read_text() == "#!/bin/sh\necho unrelated tool\n"


def test_refuses_to_clobber_a_live_release_launcher_without_force(tmp_path):
    live = tmp_path / ".local/bin/tickets"
    live.parent.mkdir(parents=True)
    subprocess.run([sys.executable, str(ROOT / "scripts/install_live.py"),
                    "--repo", str(ROOT), "--ref", "HEAD", "--live", str(live), "--activate"],
                   check=True, capture_output=True, text=True)
    before = live.read_bytes()

    result = _run_install(tmp_path)
    assert result.returncode != 0
    assert "live-release launcher" in result.stderr
    assert live.read_bytes() == before


def test_force_overrides_the_clobber_guard(tmp_path):
    bin_dir = tmp_path / ".local/bin"
    bin_dir.mkdir(parents=True)
    foreign = bin_dir / "tickets"
    foreign.write_text("#!/bin/sh\necho unrelated tool\n")
    foreign.chmod(0o755)

    result = _run_install(tmp_path, "--force")
    assert result.returncode == 0, result.stderr
    link = bin_dir / "tickets"
    assert link.is_symlink()
    assert os.readlink(link) == str(ROOT / "tickets.py")


def test_unknown_argument_is_rejected(tmp_path):
    result = _run_install(tmp_path, "--bogus")
    assert result.returncode == 2
    assert "unknown argument" in result.stderr
