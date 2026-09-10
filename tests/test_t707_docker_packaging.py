"""T-707: versioned Python-oracle image and installable wheel. No Go rewrite."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _pyproject_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version = "([^"]+)"', text, re.M)
    assert match is not None
    return match.group(1)


def test_dockerfile_is_versioned_and_pins_python():
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "FROM python:3.11.13-slim-bookworm" in text
    assert "ARG ATMAN_VERSION" in text
    assert "org.opencontainers.image.version" in text
    assert "org.opencontainers.image.revision" in text
    assert "pip install --no-cache-dir --constraint packaging/constraints.txt ." in text
    assert "ENTRYPOINT [\"tickets\"]" in text
    assert _pyproject_version() == "0.2.0"


def test_constraints_file_exists_for_reproducible_pip():
    path = ROOT / "packaging" / "constraints.txt"
    assert path.is_file()
    assert "stdlib" in path.read_text(encoding="utf-8")


def test_build_script_is_executable_and_reads_pyproject_version():
    script = ROOT / "packaging" / "build_release.sh"
    assert script.is_file()
    text = script.read_text(encoding="utf-8")
    assert "python3 -m build --wheel" in text
    assert "ATMAN_VERSION" in text
    assert "ATMAN_REVISION" in text


def test_wheel_installs_tickets_console_script(tmp_path):
    if shutil.which("python3") is None:
        raise AssertionError("python3 is required to prove the installable artifact")
    dist = tmp_path / "dist"
    dist.mkdir()
    subprocess.run(
        ["python3", "-m", "pip", "install", "--quiet", "build"],
        check=True,
        cwd=ROOT,
    )
    subprocess.run(
        ["python3", "-m", "build", "--wheel", "--outdir", str(dist)],
        check=True,
        cwd=ROOT,
    )
    wheels = list(dist.glob("ticket_board-*.whl"))
    assert len(wheels) == 1
    venv = tmp_path / "venv"
    subprocess.run(["python3", "-m", "venv", str(venv)], check=True)
    pip = venv / "bin" / "pip"
    tickets = venv / "bin" / "tickets"
    subprocess.run([str(pip), "install", "--quiet", str(wheels[0])], check=True)
    help_out = subprocess.run(
        [str(tickets), "--help"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "usage:" in help_out.stdout.lower() or help_out.stdout.strip()


def _docker_daemon_ok(docker: str) -> bool:
    probe = subprocess.run(
        [docker, "info"],
        capture_output=True,
        text=True,
    )
    return probe.returncode == 0


def test_docker_image_labels_and_tickets_help():
    docker = shutil.which("docker")
    if docker is None or not _docker_daemon_ok(docker):
        return
    version = _pyproject_version()
    tag = f"atman-tickets-t707-test:{os.getpid()}"
    subprocess.run(
        [
            docker,
            "build",
            "--build-arg",
            f"ATMAN_VERSION={version}",
            "--build-arg",
            "ATMAN_REVISION=testhash",
            "-t",
            tag,
            str(ROOT),
        ],
        check=True,
    )
    inspect = subprocess.run(
        [
            docker,
            "inspect",
            "--format",
            "{{index .Config.Labels \"org.opencontainers.image.version\"}}"
            " {{index .Config.Labels \"org.opencontainers.image.revision\"}}",
            tag,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert inspect.stdout.strip() == f"{version} testhash"
    help_out = subprocess.run(
        [docker, "run", "--rm", tag, "--help"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert help_out.returncode == 0
    subprocess.run([docker, "rmi", "-f", tag], check=False)
