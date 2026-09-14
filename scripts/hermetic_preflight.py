#!/usr/bin/env python3
"""T-909 hermetic dependency and child-process preflight (zero-model).

Builds a pinned isolated venv, then proves representative source CLI, installed
wheel, child pytest collection, and import provenance for one or more git SHAs
or a live tree. Child processes use the venv interpreter and must not resolve
operator user-site or a different SHA.

This is not a full-suite runner. Do not treat a pass here as suite ACCEPT.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import site
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


PINNED_PYTEST = "8.4.2"
PINNED_INSTALL = (
    "pip>=24,<26",
    "setuptools>=68,<76",
    "wheel>=0.41,<0.46",
    "pytest==%s" % PINNED_PYTEST,
    "jsonschema>=4.18,<5",
    "pyyaml>=6,<7",
)

STRIP_PREFIXES = (
    "TICKET_",
    "TICKETS_",
    "CURSOR_",
    "CODEX_",
    "CLAUDE_",
    "ANTHROPIC_",
    "OPENAI_",
    "VSCODE_",
    "TERM_PROGRAM",
)
STRIP_EXACT = {
    "PYTHONPATH",
    "PYTHONHOME",
    "PYTHONSTARTUP",
    "STEER_API_KEY",
    "SSH_AUTH_SOCK",
    "SSH_AGENT_PID",
}

REPRESENTATIVE_TESTS = (
    "tests/test_t263_init_isolation.py",
    "tests/test_t836_clean_wheel.py",
)

T891_RAW_SHA = "f19bdab093f14930e990141eaf5c8eed4858de93"
T891_RAW_DIGEST = "55b014fde225dcbc4af1c8e9bf12c25492f18aeb631cb6d1066a1d29e4b767ce"
BAC7_POST_RUN_PIN = "bac7cb8b00c14237db2fb23d41961f7f285c881c"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(
    argv: Sequence[str],
    *,
    cwd: Optional[Path] = None,
    env: Optional[Dict[str, str]] = None,
    timeout: int = 180,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(argv),
        cwd=str(cwd) if cwd else None,
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
    )


def fail(message: str, proc: Optional[subprocess.CompletedProcess] = None) -> None:
    extra = ""
    if proc is not None:
        extra = "\ncmd=%s\nexit=%s\nstdout:\n%s\nstderr:\n%s" % (
            proc.args,
            proc.returncode,
            proc.stdout,
            proc.stderr,
        )
    raise SystemExit("preflight FAIL: %s%s" % (message, extra))


def classify_import_path(
    path: str,
    *,
    selected_src: Optional[Path] = None,
    venv_root: Optional[Path] = None,
    operator_user_site: Optional[Path] = None,
) -> str:
    resolved = str(Path(path).resolve())
    if operator_user_site is not None:
        user_site = str(operator_user_site.resolve())
        if resolved == user_site or resolved.startswith(user_site + os.sep):
            return "operator_user_site"
    if "/Library/Python/" in resolved and "site-packages" in resolved:
        return "operator_user_site"
    if venv_root is not None:
        root = str(venv_root.resolve())
        if resolved == root or resolved.startswith(root + os.sep):
            return "venv"
    if selected_src is not None:
        src = str(selected_src.resolve())
        if resolved == src or resolved.startswith(src + os.sep):
            return "selected_src"
    return "other"


def parent_env_presence(names_or_prefixes: Iterable[str]) -> List[str]:
    present = []
    for key in os.environ:
        for item in names_or_prefixes:
            if key == item or key.startswith(item):
                present.append(key)
                break
    return sorted(present)


def sanitized_env(home: Path, tmp: Path, venv: Path) -> Dict[str, str]:
    home.mkdir(parents=True, exist_ok=True)
    tmp.mkdir(parents=True, exist_ok=True)
    path = os.pathsep.join(
        [
            str(venv / "bin"),
            "/usr/bin",
            "/bin",
            "/usr/sbin",
            "/sbin",
        ]
    )
    return {
        "HOME": str(home),
        "TMPDIR": str(tmp),
        "TMP": str(tmp),
        "TEMP": str(tmp),
        "PATH": path,
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "LC_CTYPE": "C.UTF-8",
        "TERM": "dumb",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
    }


def child_cli_env(home: Path, tmp: Path, venv: Path) -> Dict[str, str]:
    env = sanitized_env(home, tmp, venv)
    env["PYTEST_CURRENT_TEST"] = "scripts/hermetic_preflight.py::child (call)"
    return env


def public_path(path: Path, workdir: Path) -> str:
    text = str(path.resolve())
    work = str(workdir.resolve())
    if text == work or text.startswith(work + os.sep):
        return "$WORKDIR" + text[len(work):]
    home = str(Path.home())
    if text == home or text.startswith(home + os.sep):
        return "$HOME" + text[len(home):]
    return text


def git_rev_parse(repo: Path, ref: str) -> str:
    proc = run(["git", "rev-parse", ref], cwd=repo)
    if proc.returncode != 0:
        fail("cannot resolve %s" % ref, proc)
    return proc.stdout.strip()


def extract_sha(repo: Path, sha: str, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    archive = subprocess.run(
        ["git", "archive", "--format=tar", sha],
        cwd=str(repo),
        capture_output=True,
        timeout=120,
    )
    if archive.returncode != 0:
        raise SystemExit(
            "preflight FAIL: git archive %s\n%s"
            % (sha, archive.stderr.decode("utf-8", "replace"))
        )
    extract = subprocess.run(
        ["tar", "-x", "-C", str(dest)],
        input=archive.stdout,
        capture_output=True,
    )
    if extract.returncode != 0:
        raise SystemExit(
            "preflight FAIL: tar extract %s\n%s"
            % (sha, extract.stderr.decode("utf-8", "replace"))
        )


def make_venv(python: str, dest: Path) -> Path:
    if dest.exists():
        shutil.rmtree(dest)
    proc = run([python, "-m", "venv", str(dest)], timeout=120)
    if proc.returncode != 0:
        fail("venv create %s" % dest, proc)
    return dest / "bin" / "python"


def pip_install(venv_python: Path, args: Sequence[str], env: Dict[str, str]) -> None:
    proc = run(
        [str(venv_python), "-m", "pip", "install", "--disable-pip-version-check", *args],
        env=env,
        timeout=300,
    )
    if proc.returncode != 0:
        fail("pip install %s" % " ".join(args), proc)


def probe_imports(venv_python: Path, env: Dict[str, str], cwd: Path) -> Dict[str, str]:
    script = (
        "import json, pytest, sys, ticket_board\n"
        "print(json.dumps({"
        "'executable': sys.executable,"
        "'prefix': sys.prefix,"
        "'pytest_version': pytest.__version__,"
        "'pytest_file': pytest.__file__,"
        "'ticket_board_file': ticket_board.__file__,"
        "'usersite': __import__('site').getusersitepackages(),"
        "'enable_usersite': __import__('site').ENABLE_USER_SITE,"
        "'sys_path': sys.path,"
        "}))\n"
    )
    proc = run([str(venv_python), "-c", script], cwd=cwd, env=env)
    if proc.returncode != 0:
        fail("import probe", proc)
    return json.loads(proc.stdout)


def collect_tests(
    venv_python: Path,
    src: Path,
    env: Dict[str, str],
    files: Sequence[str],
    *,
    override_pythonpath: bool,
) -> Dict[str, Any]:
    missing = [name for name in files if not (src / name).is_file()]
    argv = [str(venv_python), "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"]
    if override_pythonpath:
        argv.extend(["-o", "pythonpath="])
    argv.extend(files)
    proc = run(argv, cwd=src, env=env, timeout=180)
    blob = proc.stdout + proc.stderr
    collected = 0
    match = re.search(r"(\d+) tests? collected", blob)
    if match:
        collected = int(match.group(1))
    return {
        "exit": proc.returncode,
        "collected": collected,
        "missing_files": missing,
        "override_pythonpath": override_pythonpath,
        "ok": proc.returncode == 0 and not missing and collected > 0,
        "tail": blob[-800:],
    }


def help_probe(argv: Sequence[str], cwd: Path, env: Dict[str, str], workdir: Path) -> Dict[str, Any]:
    proc = run(list(argv), cwd=cwd, env=env, timeout=60)
    text = proc.stdout + proc.stderr
    return {
        "argv0": public_path(Path(argv[0]), workdir),
        "exit": proc.returncode,
        "ok": proc.returncode == 0 and "join" in text and "usage" in text.lower(),
        "has_join": "join" in text,
    }


def origin_label(sha: str) -> str:
    if sha.startswith("f19bdab"):
        return "baseline-t891-raw"
    if sha.startswith("c2f7dd5"):
        return "candidate-app-t896"
    if sha.startswith("26ff79f"):
        return "candidate-cli-t877"
    return "sha-%s" % sha[:12]


def redact_probe(probe: Dict[str, Any], workdir: Path) -> Dict[str, Any]:
    out = dict(probe)
    for key in ("executable", "prefix", "pytest_file", "ticket_board_file", "usersite"):
        if key in out and out[key]:
            out[key] = public_path(Path(out[key]), workdir)
    if "sys_path" in out:
        out["sys_path"] = [public_path(Path(item), workdir) if item else item for item in out["sys_path"]]
    return out


def preflight_origin(
    *,
    repo: Path,
    workdir: Path,
    python: str,
    sha: Optional[str],
    src_override: Optional[Path],
    operator_user_site: Path,
    host_presence: List[str],
) -> Dict[str, Any]:
    if sha:
        sha = git_rev_parse(repo, sha)
        label = origin_label(sha)
        src = workdir / "origins" / label / "src"
        if src.exists():
            shutil.rmtree(src)
        extract_sha(repo, sha, src)
    else:
        assert src_override is not None
        src = src_override.resolve()
        sha = git_rev_parse(src, "HEAD")
        label = origin_label(sha)

    origin_dir = workdir / "origins" / label
    origin_dir.mkdir(parents=True, exist_ok=True)
    home = origin_dir / "home"
    tmp = origin_dir / "tmp"
    source_venv = origin_dir / "source-venv"
    wheel_venv = origin_dir / "wheel-venv"
    dist = origin_dir / "dist"
    if dist.exists():
        shutil.rmtree(dist)
    dist.mkdir(parents=True)

    source_python = make_venv(python, source_venv)
    source_env = sanitized_env(home / "source", tmp / "source", source_venv)
    pip_install(source_python, ["-U", "pip", "setuptools", "wheel"], source_env)
    pip_install(source_python, PINNED_INSTALL, source_env)
    pip_install(source_python, ["-e", str(src), "--no-deps"], source_env)

    source_probe = probe_imports(source_python, source_env, src)
    tb_class = classify_import_path(
        source_probe["ticket_board_file"],
        selected_src=src,
        venv_root=source_venv,
        operator_user_site=operator_user_site,
    )
    pytest_class = classify_import_path(
        source_probe["pytest_file"],
        selected_src=src,
        venv_root=source_venv,
        operator_user_site=operator_user_site,
    )
    if source_probe["pytest_version"] != PINNED_PYTEST:
        fail("source pytest %s != %s" % (source_probe["pytest_version"], PINNED_PYTEST))
    if tb_class not in {"selected_src", "venv"}:
        fail("source ticket_board origin is %s: %s" % (tb_class, source_probe["ticket_board_file"]))
    if pytest_class != "venv":
        fail("source pytest origin is %s: %s" % (pytest_class, source_probe["pytest_file"]))
    if source_probe["enable_usersite"]:
        fail("source venv ENABLE_USER_SITE is true")

    cli_env = child_cli_env(home / "cli", tmp / "cli", source_venv)
    source_cli = {
        "tickets_py": help_probe([str(source_python), str(src / "tickets.py"), "--help"], tmp / "cli", cli_env, workdir),
        "cli_py": help_probe(
            [str(source_python), str(src / "src" / "ticket_board" / "cli.py"), "--help"],
            tmp / "cli",
            cli_env,
            workdir,
        ),
        "console_tickets": help_probe([str(source_venv / "bin" / "tickets"), "--help"], tmp / "cli", cli_env, workdir),
    }
    if not all(item["ok"] for item in source_cli.values()):
        fail("source CLI help probe failed: %s" % source_cli)

    child_import = run(
        [
            str(source_python),
            "-c",
            "import pytest, ticket_board, sys; print(sys.executable); print(ticket_board.__file__); print(pytest.__file__)",
        ],
        cwd=tmp / "cli",
        env=cli_env,
    )
    if child_import.returncode != 0:
        fail("scrubbed child import", child_import)

    source_collect = collect_tests(
        source_python, src, source_env, REPRESENTATIVE_TESTS, override_pythonpath=False
    )
    if not source_collect["ok"]:
        fail("source child pytest collect", None)

    wheel_proc = run(
        [str(source_python), "-m", "pip", "wheel", str(src), "--no-deps", "-w", str(dist)],
        cwd=origin_dir,
        env=source_env,
        timeout=180,
    )
    if wheel_proc.returncode != 0:
        fail("wheel build", wheel_proc)
    wheels = sorted(set(dist.glob("ticket_board-*.whl")) | set(dist.glob("ticket-board-*.whl")))
    unknown = list(dist.glob("UNKNOWN-*.whl"))
    if unknown:
        fail("wheel named UNKNOWN: %s" % unknown)
    if not wheels:
        fail("no ticket-board wheel in %s: %s" % (dist, list(dist.iterdir())))
    wheel = wheels[0]
    wheel_hash = sha256_file(wheel)

    wheel_python = make_venv(python, wheel_venv)
    wheel_env = sanitized_env(home / "wheel", tmp / "wheel", wheel_venv)
    pip_install(wheel_python, ["-U", "pip", "setuptools", "wheel"], wheel_env)
    pip_install(wheel_python, PINNED_INSTALL, wheel_env)
    pip_install(wheel_python, [str(wheel), "--no-deps", "--no-cache-dir"], wheel_env)

    wheel_probe = probe_imports(wheel_python, wheel_env, tmp / "wheel")
    wheel_tb_class = classify_import_path(
        wheel_probe["ticket_board_file"],
        selected_src=src,
        venv_root=wheel_venv,
        operator_user_site=operator_user_site,
    )
    wheel_pytest_class = classify_import_path(
        wheel_probe["pytest_file"],
        selected_src=src,
        venv_root=wheel_venv,
        operator_user_site=operator_user_site,
    )
    if wheel_tb_class != "venv":
        fail("wheel ticket_board origin is %s: %s" % (wheel_tb_class, wheel_probe["ticket_board_file"]))
    if wheel_pytest_class != "venv":
        fail("wheel pytest origin is %s: %s" % (wheel_pytest_class, wheel_probe["pytest_file"]))
    if src.resolve().as_posix() in Path(wheel_probe["ticket_board_file"]).resolve().as_posix():
        fail("wheel import still resolved selected src: %s" % wheel_probe["ticket_board_file"])

    wheel_cli_env = child_cli_env(home / "wheel-cli", tmp / "wheel-cli", wheel_venv)
    wheel_cli = {
        "console_tickets": help_probe([str(wheel_venv / "bin" / "tickets"), "--help"], tmp / "wheel-cli", wheel_cli_env, workdir),
    }
    atm = wheel_venv / "bin" / "atm"
    if atm.is_file():
        wheel_cli["console_atm"] = help_probe([str(atm), "--help"], tmp / "wheel-cli", wheel_cli_env, workdir)
        tickets_text = (wheel_venv / "bin" / "tickets").read_text(encoding="utf-8", errors="replace")
        atm_text = atm.read_text(encoding="utf-8", errors="replace")
        wheel_cli["atm_same_entry"] = (
            "from ticket_board.cli import main" in atm_text
            and "from ticket_board.cli import main" in tickets_text
        )
    if not wheel_cli["console_tickets"]["ok"]:
        fail("wheel tickets --help", None)

    wheel_collect = collect_tests(
        wheel_python, src, wheel_env, REPRESENTATIVE_TESTS, override_pythonpath=True
    )
    if not wheel_collect["ok"]:
        fail("wheel child pytest collect (pythonpath overridden)", None)

    freeze = run([str(source_python), "-m", "pip", "freeze"], env=source_env)
    pinned = []
    if freeze.returncode == 0:
        pinned = sorted(
            line.strip()
            for line in freeze.stdout.splitlines()
            if line.strip()
            and not line.startswith("#")
            and not line.startswith("-e ")
            and not line.startswith("ticket-board==")
            and " @ " not in line
        )

    fixture_note = (
        "tests/test_t544_conftest_shadow.py sets PYTHONPATH=src:. on purpose so a "
        "combined runners+server collect uses the checkout, not a wheel. That is a "
        "fixture contract, not a packaging defect. Wheel provenance in this preflight "
        "overrides pytest.ini pythonpath so child collection cannot shadow the wheel."
    )

    return {
        "label": label,
        "sha": sha,
        "source_tree": public_path(src, workdir),
        "source": {
            "ticket_board_class": tb_class,
            "pytest_class": pytest_class,
            "pytest_version": source_probe["pytest_version"],
            "cli": source_cli,
            "child_import_exit": child_import.returncode,
            "collect": {k: v for k, v in source_collect.items() if k != "tail"},
            "probe": redact_probe(source_probe, workdir),
        },
        "wheel": {
            "filename": wheel.name,
            "sha256": wheel_hash,
            "bytes": wheel.stat().st_size,
            "ticket_board_class": wheel_tb_class,
            "pytest_class": wheel_pytest_class,
            "cli": wheel_cli,
            "collect": {k: v for k, v in wheel_collect.items() if k != "tail"},
            "probe": redact_probe(wheel_probe, workdir),
            "atm_present": atm.is_file(),
        },
        "pins": pinned,
        "fixture_note": fixture_note,
        "ok": True,
    }


def self_test() -> None:
    selected = Path("/tmp/t909-src/src/ticket_board/__init__.py")
    venv = Path("/tmp/t909-venv/lib/python3.9/site-packages/ticket_board/__init__.py")
    user = Path("/Users/someone/Library/Python/3.9/lib/python/site-packages/ticket_board/__init__.py")
    assert classify_import_path(
        str(user),
        selected_src=Path("/tmp/t909-src"),
        venv_root=Path("/tmp/t909-venv"),
        operator_user_site=Path("/Users/someone/Library/Python/3.9/lib/python/site-packages"),
    ) == "operator_user_site"
    assert classify_import_path(
        str(venv),
        selected_src=Path("/tmp/t909-src"),
        venv_root=Path("/tmp/t909-venv"),
        operator_user_site=Path("/Users/someone/Library/Python/3.9/lib/python/site-packages"),
    ) == "venv"
    assert classify_import_path(
        str(selected),
        selected_src=Path("/tmp/t909-src"),
        venv_root=Path("/tmp/t909-venv"),
        operator_user_site=Path("/Users/someone/Library/Python/3.9/lib/python/site-packages"),
    ) == "selected_src"
    print("self-test ok")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", default="/usr/bin/python3", help="pinned interpreter (prefer 3.9.6)")
    parser.add_argument("--repo", default=".", help="git repo that can resolve --sha")
    parser.add_argument("--sha", action="append", dest="shas", default=[], help="repeatable origin SHA or ref")
    parser.add_argument("--src", help="live tree instead of git archive (single origin)")
    parser.add_argument("--workdir", default="", help="scratch dir for venvs and private receipts")
    parser.add_argument("--manifest-out", default="", help="public-safe JSON manifest path")
    parser.add_argument("--self-test", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.self_test:
        self_test()
        return 0

    python = args.python
    version_proc = run([python, "--version"])
    if version_proc.returncode != 0:
        fail("python --version", version_proc)
    version = (version_proc.stdout or version_proc.stderr).strip()
    if "3.9.6" not in version:
        print("warning: preferred Python is 3.9.6, got %s" % version, file=sys.stderr)

    repo = Path(args.repo).resolve()
    if args.workdir:
        workdir = Path(args.workdir).resolve()
        workdir.mkdir(parents=True, exist_ok=True)
        cleanup = False
    else:
        workdir = Path(tempfile.mkdtemp(prefix="atman-hermetic-preflight-"))
        cleanup = False  # keep for the private receipt; caller owns deletion

    operator_user_site = Path(site.getusersitepackages())
    host_presence = parent_env_presence(list(STRIP_EXACT) + list(STRIP_PREFIXES))

    shas = list(args.shas)
    if not shas and not args.src:
        fail("pass --sha (repeatable) or --src")

    origins: List[Dict[str, Any]] = []
    if args.src:
        origins.append(
            preflight_origin(
                repo=repo,
                workdir=workdir,
                python=python,
                sha=None,
                src_override=Path(args.src),
                operator_user_site=operator_user_site,
                host_presence=host_presence,
            )
        )
    for sha in shas:
        origins.append(
            preflight_origin(
                repo=repo,
                workdir=workdir,
                python=python,
                sha=sha,
                src_override=None,
                operator_user_site=operator_user_site,
                host_presence=host_presence,
            )
        )

    manifest = {
        "ticket": "T-909",
        "purpose": "hermetic dependency and child-process preflight",
        "full_suite": False,
        "python": {
            "requested": python,
            "version": version.replace("Python ", ""),
        },
        "pins_requested": list(PINNED_INSTALL),
        "child_env": {
            "PYTHONNOUSERSITE": "1",
            "PYTHONPATH": "unset",
            "HOME": "temporary under $WORKDIR",
            "TMPDIR": "temporary under $WORKDIR",
            "stripped_prefixes": list(STRIP_PREFIXES),
            "stripped_exact": sorted(STRIP_EXACT),
            "host_names_present_not_values": host_presence,
        },
        "representative_tests": list(REPRESENTATIVE_TESTS),
        "t891_distinction": {
            "raw_receipt_sha": T891_RAW_SHA,
            "raw_receipt_digest": T891_RAW_DIGEST,
            "post_run_pin": BAC7_POST_RUN_PIN,
            "post_run_pin_is_fullsuite_evidence": False,
            "t891_used_operator_user_site_pytest": True,
        },
        "origins": origins,
        "verdict": "PASS" if all(item.get("ok") for item in origins) else "FAIL",
    }

    receipt = workdir / "RECEIPT.json"
    receipt.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest_out = Path(args.manifest_out).resolve() if args.manifest_out else workdir / "hermetic-preflight.manifest.json"
    manifest_out.parent.mkdir(parents=True, exist_ok=True)
    manifest_out.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "verdict": manifest["verdict"],
        "origins": [item["sha"] for item in origins],
        "manifest": public_path(manifest_out, workdir),
        "private_receipt": public_path(receipt, workdir),
        "workdir": public_path(workdir, workdir),
    }, indent=2))
    if cleanup:
        shutil.rmtree(workdir)
    return 0 if manifest["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
