#!/usr/bin/env python3
"""Install a pinned CLI snapshot; never overwrite unknown live bytes silently."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

FILES = ("tickets.py", "ticket_coordination.py", "board_backup.py", "session_adapters.py")
PACKAGE_PREFIX = "src/ticket_board"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def clean_env():
    return {k: v for k, v in os.environ.items()
            if not k.startswith("GIT_") and k not in ("TICKETS_DIR", "TICKET_BOARD_DB", "TICKET_AGENT")}


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], env=clean_env())


def export_paths(repo, sha):
    """Pinned paths exported into every immutable release snapshot."""
    paths = list(FILES)
    pkg = git(repo, "ls-tree", "-r", "--name-only", sha, "--", PACKAGE_PREFIX).decode().strip()
    if pkg:
        paths.extend(line for line in pkg.splitlines() if line)
    return paths


def seed_fixture_repo(repo_dir, source_repo):
    """Copy exportable release bytes into a throwaway git repo for tests."""
    repo_dir = Path(repo_dir)
    source_repo = Path(source_repo)
    for name in FILES:
        shutil.copy2(source_repo / name, repo_dir / name)
    shutil.copytree(source_repo / PACKAGE_PREFIX, repo_dir / PACKAGE_PREFIX)


def _write_release_file(stage, rel_path, data):
    dest = stage / rel_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    dest.chmod(0o555 if rel_path == "tickets.py" else 0o444)


def _seed_smoke_board(board):
    board.mkdir()
    for name, content in (("tickets.json", "[]"), ("sprints.json", "[]"),
                          ("roles.json", "{}"), ("agents.json", "[]")):
        (board / name).write_text(content)


def smoke(script, sha):
    # Explicit board override: no discovery and no reads/writes to the live board.
    # -S + PYTHONNOUSERSITE: never let a dev checkout's site-packages hide a
    # missing src/ticket_board/ in the staged release.
    with tempfile.TemporaryDirectory(prefix="tickets-install-smoke-") as scratch:
        board = Path(scratch) / ".tickets"
        _seed_smoke_board(board)
        env = dict(clean_env(), TICKETS_DIR=str(board), HOME=scratch,
                   TICKET_AGENT="installer", PYTHONNOUSERSITE="1")
        env.pop("PYTHONPATH", None)
        arbitrary_cwd = Path(scratch) / "elsewhere"
        arbitrary_cwd.mkdir()
        py = [sys.executable, "-S", str(script)]

        def run(*args):
            result = subprocess.run([*py, *args], cwd=str(arbitrary_cwd),
                                    env=env, text=True, capture_output=True, timeout=30)
            if result.returncode:
                raise RuntimeError("smoke failed (%s): %s" % (" ".join(args), result.stderr))
            return result.stdout

        if run("--version").strip() != "tickets commit %s (verified release)" % sha:
            raise RuntimeError("smoke failed: version does not match pinned commit")
        run("create", "Installer smoke fixture")
        if "Installer smoke fixture" not in run("show", "T-001"):
            raise RuntimeError("smoke failed: show did not read isolated fixture")
        # Persistent join imports session_adapters.py from the staged snapshot.
        # Missing that file used to crash ModuleNotFoundError outside a checkout.
        persist = run("join", "persist-seat", "--roles", "backend", "--persistent")
        if "ModuleNotFoundError" in persist:
            raise RuntimeError("smoke failed: persistent join missing session_adapters.py")
        if "joined as persist-seat" not in persist:
            raise RuntimeError("smoke failed: persistent join did not complete")
        turns = json.loads(run("turns", "--json"))
        if turns.get("v") != 1:
            raise RuntimeError("smoke failed: turns --json missing v=1")
        util = json.loads(run("util", "--json"))
        if "agents" not in util:
            raise RuntimeError("smoke failed: util --json missing agents")
        run("route")
        snap = json.loads(run("ui", "--json"))
        if snap.get("turns", {}).get("v") != 1:
            raise RuntimeError("smoke failed: ui --json turns snapshot missing v=1")
        # Generated hooks are part of the live release contract. Prove that a
        # staged launcher can install and execute one from an unrelated cwd,
        # and that a hostile ambient identity cannot replace the baked one.
        settings = Path(scratch) / "hook-settings.json"
        run("hooks", "claude", "--agent", "release-hook", "--settings", str(settings))
        hook = json.loads(settings.read_text())["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        hook_env = dict(env, TICKET_AGENT="wrong-ambient", TICKETS_DIR=str(Path(scratch) / "wrong-board"))
        result = subprocess.run(hook, shell=True, cwd=str(arbitrary_cwd), env=hook_env,
                                input=json.dumps({"hook_event_name": "SessionStart"}),
                                text=True, capture_output=True, timeout=30)
        if result.returncode or "Ticket board" not in result.stdout:
            raise RuntimeError("smoke failed: identity-pinned SessionStart hook did not run: %s" % result.stderr)
        coordination = json.loads((board / "coordination" / "state.json").read_text())
        if "release-hook" not in coordination.get("agents", {}) or "wrong-ambient" in coordination.get("agents", {}):
            raise RuntimeError("smoke failed: hook inherited the ambient agent identity")


def install(repo, ref, live, activate=False, expected=None):
    sha = git(repo, "rev-parse", "--verify", ref + "^{commit}").decode().strip()
    paths = export_paths(repo, sha)
    payload = {name: git(repo, "show", sha + ":" + name) for name in paths}
    manifest = {"commit": sha, "files": {name: {"sha256": digest(data), "size": len(data)}
                                          for name, data in payload.items()}}
    releases = live.parent / "tickets-releases"
    releases.mkdir(parents=True, exist_ok=True)
    with (releases / ".install.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        release = releases / sha
        if not release.exists():
            stage = Path(tempfile.mkdtemp(prefix=".stage-", dir=releases))
            try:
                for name, data in payload.items():
                    _write_release_file(stage, name, data)
                (stage / "release.json").write_text(json.dumps(manifest, sort_keys=True) + "\n")
                (stage / "release.json").chmod(0o444)
                smoke(stage / "tickets.py", sha)
                os.replace(stage, release)
            finally:
                if stage.exists():
                    shutil.rmtree(stage)
        if json.loads((release / "release.json").read_text()) != manifest:
            raise RuntimeError("existing release manifest differs from pinned source")
        for name, data in payload.items():
            if (release / name).read_bytes() != data:
                raise RuntimeError("existing release has drifted: " + name)
        smoke(release / "tickets.py", sha)
        if not activate:
            print("staged %s at %s; live tool unchanged" % (sha, release))
            return release
        exists = live.exists() or live.is_symlink()
        previous = live.read_bytes() if exists else None
        mode = (live.stat().st_mode & 0o777) | 0o100 if exists else 0o755
        if exists and (not expected or digest(previous) != expected):
            raise RuntimeError("live bytes differ or are unacknowledged; inspect live-only fixes, "
                               "then supply --expected-live-sha256 of the approved current file")
        if not exists and expected:
            raise RuntimeError("expected a live file but it is missing")
        # A literal absolute target keeps already-running processes on their old
        # snapshot, including late imports and child process launches.
        launcher = ("#!/usr/bin/env python3\nimport os, sys\n"
                    "os.execv(sys.executable, [sys.executable, %r] + sys.argv[1:])\n"
                    % str(release / "tickets.py"))
        backup = None
        if exists:
            backup = releases / ("previous-" + digest(previous))
            if not backup.exists() and not backup.is_symlink():
                shutil.copy2(live, backup, follow_symlinks=False)
        fd, temporary = tempfile.mkstemp(prefix=".tickets-launcher-", dir=live.parent)
        temporary = Path(temporary)
        try:
            with os.fdopen(fd, "w") as out:
                out.write(launcher)
                out.flush()
                os.fsync(out.fileno())
            temporary.chmod(mode)
            # Catch changes by non-installer writers during staging/backup.
            if exists and live.read_bytes() != previous:
                raise RuntimeError("live tool changed during installation; retry after coordination")
            os.replace(temporary, live)
            try:
                smoke(live, sha)
            except Exception:
                if backup:
                    shutil.copy2(backup, temporary, follow_symlinks=False)
                    os.replace(temporary, live)
                else:
                    live.unlink()
                raise
        finally:
            if temporary.exists() or temporary.is_symlink():
                temporary.unlink()
        print("activated %s via %s; previous launcher: %s" % (sha, live, backup))
        return release


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--ref", required=True, help="reviewed commit to export (not working-tree bytes)")
    parser.add_argument("--live", type=Path, default=Path.home() / ".claude/tools/tickets.py")
    parser.add_argument("--activate", action="store_true", help="otherwise only stage and smoke-test")
    parser.add_argument("--expected-live-sha256", help="acknowledge the exact existing live bytes")
    args = parser.parse_args()
    try:
        install(args.repo.resolve(), args.ref, args.live.absolute(), args.activate, args.expected_live_sha256)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        parser.exit(1, "install refused: %s\n" % exc)


if __name__ == "__main__":
    main()
