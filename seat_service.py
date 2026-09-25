"""Per-user OS seat service: desired state on the board, one reconciler.

T-1499: `atm spawn --persist` records active/inactive/stopped on the board and
returns. One launchd/systemd user service reconciles every few seconds so each
active seat has exactly one watcher. Parent-session death must not orphan seats.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import time
from datetime import datetime, timezone

DESIRED_STATES = ("active", "inactive", "stopped")
RECONCILE_INTERVAL_SECS = 5
DEFAULT_BACKOFF_SECS = 15
SERVICE_LABEL = "com.atman.seat-service"
SYSTEMD_UNIT = "atman-seat-service.service"


def _utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def service_installed(home=None):
    home_root = home if home is not None else (
        os.environ.get("ATMAN_SERVICE_HOME") or os.path.expanduser("~"))
    # Explicit marker wins (tests + unsupported OS).
    if os.path.isfile(os.path.join(service_home(home_root), "installed")):
        return True
    system = platform.system()
    if system == "Darwin":
        return os.path.isfile(plist_path(home_root))
    if system == "Linux":
        return os.path.isfile(systemd_unit_path(home_root))
    return False


def service_home(home=None):
    """Per-user service state directory (~/.atman/service)."""
    if home is None:
        home = os.environ.get("ATMAN_SERVICE_HOME") or os.path.expanduser("~")
    return os.path.join(home, ".atman", "service")


def desired_path(board, owner):
    # Not *.json: load_agents globs agents/*.json and would treat this as a seat.
    return os.path.join(board, "agents", owner + ".desired")


def runner_lock_path(board, owner):
    return os.path.join(board, "agents", owner + ".runner.lock")


def load_desired(board, owner):
    path = desired_path(board, owner)
    try:
        with open(path) as f:
            data = json.load(f)
    except (IOError, OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    state = data.get("state")
    if state not in DESIRED_STATES:
        return None
    return data


def save_desired(board, owner, state, *, spec=None, backoff_until=None,
                 updated_by="", keep_spec=True):
    """Write desired state. Spec is replaced when provided; else prior spec kept."""
    if state not in DESIRED_STATES:
        raise ValueError("desired state must be one of %s" % (", ".join(DESIRED_STATES),))
    os.makedirs(os.path.join(board, "agents"), exist_ok=True)
    prev = load_desired(board, owner) or {}
    rec = {
        "state": state,
        "backoff_until": backoff_until if backoff_until is not None else prev.get("backoff_until"),
        "updated_at": _utc_now(),
        "updated_by": updated_by or prev.get("updated_by") or "",
        "spec": dict(spec) if spec is not None else (
            dict(prev.get("spec") or {}) if keep_spec else {}),
    }
    if backoff_until is None and "backoff_until" not in prev:
        rec["backoff_until"] = None
    path = desired_path(board, owner)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(rec, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)
    return rec


def clear_backoff(board, owner):
    rec = load_desired(board, owner)
    if not rec:
        return None
    return save_desired(
        board, owner, rec["state"],
        spec=rec.get("spec"), backoff_until=None,
        updated_by=rec.get("updated_by") or "")


def set_backoff(board, owner, seconds=DEFAULT_BACKOFF_SECS):
    rec = load_desired(board, owner)
    if not rec:
        return None
    until = time.time() + max(0, int(seconds))
    return save_desired(
        board, owner, rec["state"],
        spec=rec.get("spec"), backoff_until=until,
        updated_by=rec.get("updated_by") or "")


def parse_backoff_deadline(value):
    """Return unix seconds, or None when no deadline applies."""
    if value in (None, "", 0, "0"):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def seat_may_spawn(desired, live_count, now=None):
    """Deterministic: active + no live runner + past backoff => may spawn.

    Exactly one loop per seat: any live runner blocks another start. Active
    with no process is wakeable (not an error) -- the caller starts one.
    """
    if not desired or desired.get("state") != "active":
        return False
    try:
        live = int(live_count or 0)
    except (TypeError, ValueError):
        live = 0
    if live > 0:
        return False
    deadline = parse_backoff_deadline(desired.get("backoff_until"))
    if deadline is None:
        return True
    clock = time.time() if now is None else float(now)
    return clock >= deadline


def seat_should_stop(desired, live_count):
    """True when desired is stopped and at least one runner is live."""
    if not desired or desired.get("state") != "stopped":
        return False
    try:
        return int(live_count or 0) > 0
    except (TypeError, ValueError):
        return False


class RunnerLock:
    """Exclusive lock so two loops cannot start one seat concurrently."""

    def __init__(self, board, owner, *, blocking=False):
        self.path = runner_lock_path(board, owner)
        self.blocking = blocking
        self.fd = None
        self.held = False

    def __enter__(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        try:
            import fcntl
        except ImportError:
            self.held = True
            return self
        self.fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o644)
        flags = fcntl.LOCK_EX if self.blocking else (fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            fcntl.flock(self.fd, flags)
            self.held = True
        except OSError:
            os.close(self.fd)
            self.fd = None
            self.held = False
        return self

    def __exit__(self, *exc):
        if self.fd is None:
            return
        try:
            import fcntl
            fcntl.flock(self.fd, fcntl.LOCK_UN)
        finally:
            os.close(self.fd)
            self.fd = None


def _service_home_root(home=None):
    if home is not None:
        return home
    return os.environ.get("ATMAN_SERVICE_HOME") or os.path.expanduser("~")


def register_board(board, home=None):
    """Remember a board so the user service reconciles its active seats."""
    home_root = _service_home_root(home)
    os.makedirs(service_home(home_root), exist_ok=True)
    real = os.path.realpath(board)
    boards = load_boards(home_root)
    if real not in boards:
        boards.append(real)
    path = boards_registry_path(home_root)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"boards": boards, "updated_at": _utc_now()}, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)
    return boards


def unregister_board(board, home=None):
    home_root = _service_home_root(home)
    real = os.path.realpath(board)
    boards = [b for b in load_boards(home_root) if b != real]
    path = boards_registry_path(home_root)
    if not os.path.isdir(service_home(home_root)):
        return boards
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"boards": boards, "updated_at": _utc_now()}, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)
    return boards


def load_boards(home=None):
    path = boards_registry_path(_service_home_root(home))
    try:
        with open(path) as f:
            data = json.load(f)
    except (IOError, OSError, ValueError):
        return []
    boards = data.get("boards") if isinstance(data, dict) else data
    if not isinstance(boards, list):
        return []
    out = []
    seen = set()
    for item in boards:
        if not isinstance(item, str):
            continue
        real = os.path.realpath(os.path.expanduser(item))
        if real in seen or not os.path.isdir(real):
            continue
        seen.add(real)
        out.append(real)
    return out


def boards_registry_path(home=None):
    return os.path.join(service_home(_service_home_root(home)), "boards.json")


def env_snapshot_path(home=None):
    return os.path.join(service_home(_service_home_root(home)), "login-env.json")

def list_desired_seats(board):
    """Yield (owner, desired_rec) for every agents/<owner>.desired file."""
    agents = os.path.join(board, "agents")
    if not os.path.isdir(agents):
        return
    for name in sorted(os.listdir(agents)):
        if not name.endswith(".desired"):
            continue
        if name.endswith(".desired.json"):
            # Legacy mistake from an early T-1499 draft; skip.
            continue
        owner = name[: -len(".desired")]
        rec = load_desired(board, owner)
        if rec:
            yield owner, rec


def capture_login_env(shell=None):
    """Capture the user's login-shell environment (PATH, provider CLIs).

    launchd's default PATH is bare; without this, reconciler-started watchers
    often fail with 'X is not installed'.
    """
    shell = shell or os.environ.get("SHELL") or "/bin/zsh"
    # Prefer a login interactive env dump; fall back to current process.
    try:
        proc = subprocess.run(
            [shell, "-l", "-c", "env -0"],
            capture_output=True, timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return dict(os.environ)
    if proc.returncode != 0 or not proc.stdout:
        return dict(os.environ)
    env = {}
    for chunk in proc.stdout.split(b"\0"):
        if not chunk or b"=" not in chunk:
            continue
        key, _, val = chunk.partition(b"=")
        try:
            env[key.decode("utf-8", "replace")] = val.decode("utf-8", "replace")
        except Exception:
            continue
    return env or dict(os.environ)


def save_login_env(env, home=None):
    home_root = home if home is not None else os.path.expanduser("~")
    os.makedirs(service_home(home_root), exist_ok=True)
    path = env_snapshot_path(home_root)
    tmp = path + ".tmp"
    # Drop empty keys; keep strings only.
    clean = {str(k): str(v) for k, v in (env or {}).items()
             if k and isinstance(k, str) and isinstance(v, str)}
    with open(tmp, "w") as f:
        json.dump({"captured_at": _utc_now(), "env": clean}, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)
    return path


def load_login_env(home=None):
    path = env_snapshot_path(home)
    try:
        with open(path) as f:
            data = json.load(f)
    except (IOError, OSError, ValueError):
        return {}
    env = data.get("env") if isinstance(data, dict) else {}
    if not isinstance(env, dict):
        return {}
    return {str(k): str(v) for k, v in env.items() if k}


def merge_launch_env(base, login_env):
    """Overlay login-shell PATH and provider-relevant vars onto a launch env."""
    out = dict(base or {})
    login = login_env or {}
    # Login PATH first so provider CLIs resolve; keep base extras after.
    login_path = login.get("PATH") or ""
    base_path = out.get("PATH") or ""
    if login_path:
        parts = []
        seen = set()
        for chunk in (login_path + os.pathsep + base_path).split(os.pathsep):
            if chunk and chunk not in seen:
                seen.add(chunk)
                parts.append(chunk)
        out["PATH"] = os.pathsep.join(parts)
    keep_prefixes = (
        "ANTHROPIC", "CLAUDE", "CODEX", "CURSOR", "OPENAI", "OPENROUTER", "XAI",
        "HOME", "USER", "LOGNAME", "USERNAME", "SHELL", "LANG", "LC_",
        "XDG_", "SSH_", "TERM",
    )
    for key, val in login.items():
        if key in ("PATH", "TICKET_AGENT", "TICKET_SEAT", "TICKETS_DIR",
                   "TICKETS_PY", "TICKET_SESSION_ID", "TICKETS_WATCH_PINNED",
                   "PYTHONHOME", "PYTHONPATH"):
            continue
        if any(key == p or key.startswith(p) for p in keep_prefixes):
            out.setdefault(key, val)
    return out


def plist_path(home=None):
    home_root = home if home is not None else (
        os.environ.get("ATMAN_SERVICE_HOME") or os.path.expanduser("~"))
    return os.path.join(home_root, "Library", "LaunchAgents", SERVICE_LABEL + ".plist")


def systemd_unit_path(home=None):
    home_root = home if home is not None else (
        os.environ.get("ATMAN_SERVICE_HOME") or os.path.expanduser("~"))
    return os.path.join(home_root, ".config", "systemd", "user", SYSTEMD_UNIT)


def render_launchd_plist(python_exe, tickets_py, log_path):
    """Minimal KeepAlive + RunAtLoad plist for the seat reconciler."""
    def esc(s):
        return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                .replace('"', "&quot;"))
    return """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>%s</string>
  <key>ProgramArguments</key>
  <array>
    <string>%s</string>
    <string>%s</string>
    <string>service</string>
    <string>run</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>%s</string>
  <key>StandardErrorPath</key>
  <string>%s</string>
</dict>
</plist>
""" % (SERVICE_LABEL, esc(python_exe), esc(tickets_py), esc(log_path), esc(log_path))


def render_systemd_unit(python_exe, tickets_py):
    return """[Unit]
Description=Atman seat service (persistent watchers)
[Service]
ExecStart=%s %s service run
Restart=always
RestartSec=2
[Install]
WantedBy=default.target
""" % (python_exe, tickets_py)


def mark_installed(home=None):
    home_root = home if home is not None else (
        os.environ.get("ATMAN_SERVICE_HOME") or os.path.expanduser("~"))
    os.makedirs(service_home(home_root), exist_ok=True)
    path = os.path.join(service_home(home_root), "installed")
    with open(path, "w") as f:
        f.write(_utc_now() + "\n")
    return path


def clear_installed(home=None):
    home_root = home if home is not None else (
        os.environ.get("ATMAN_SERVICE_HOME") or os.path.expanduser("~"))
    path = os.path.join(service_home(home_root), "installed")
    try:
        os.unlink(path)
    except OSError:
        pass


def install_service(python_exe, tickets_py, home=None, *, load=True):
    """Install launchd or systemd --user unit. Captures login env at install."""
    home_root = home if home is not None else (
        os.environ.get("ATMAN_SERVICE_HOME") or os.path.expanduser("~"))
    os.makedirs(service_home(home_root), exist_ok=True)
    save_login_env(capture_login_env(), home=home_root)
    mark_installed(home_root)
    system = platform.system()
    log_path = os.path.join(service_home(home_root), "seat-service.log")
    if system == "Darwin":
        path = plist_path(home_root)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        body = render_launchd_plist(python_exe, tickets_py, log_path)
        with open(path, "w") as f:
            f.write(body)
        if load:
            subprocess.run(["launchctl", "bootout", "gui/%d" % os.getuid(), path],
                           capture_output=True)
            r = subprocess.run(["launchctl", "bootstrap", "gui/%d" % os.getuid(), path],
                               capture_output=True, text=True)
            if r.returncode != 0:
                # Older macOS: load -w
                r2 = subprocess.run(["launchctl", "load", "-w", path],
                                    capture_output=True, text=True)
                if r2.returncode != 0:
                    return path, (r.stderr or r2.stderr or "launchctl load failed").strip()
        return path, None
    if system == "Linux":
        path = systemd_unit_path(home_root)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(render_systemd_unit(python_exe, tickets_py))
        if load:
            subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
            r = subprocess.run(["systemctl", "--user", "enable", "--now", SYSTEMD_UNIT],
                               capture_output=True, text=True)
            if r.returncode != 0:
                return path, (r.stderr or r.stdout or "systemctl enable failed").strip()
        return path, None
    # Unsupported OS: marker file only (tests use this).
    return os.path.join(service_home(home_root), "installed"), None


def uninstall_service(home=None, *, unload=True):
    home_root = home if home is not None else (
        os.environ.get("ATMAN_SERVICE_HOME") or os.path.expanduser("~"))
    system = platform.system()
    err = None
    if system == "Darwin":
        path = plist_path(home_root)
        if unload and os.path.isfile(path):
            subprocess.run(["launchctl", "bootout", "gui/%d" % os.getuid(), path],
                           capture_output=True)
            subprocess.run(["launchctl", "unload", "-w", path], capture_output=True)
        try:
            os.unlink(path)
        except OSError as exc:
            err = str(exc)
    elif system == "Linux":
        path = systemd_unit_path(home_root)
        if unload:
            subprocess.run(["systemctl", "--user", "disable", "--now", SYSTEMD_UNIT],
                           capture_output=True)
        try:
            os.unlink(path)
        except OSError as exc:
            err = str(exc)
    clear_installed(home_root)
    return err


def tickets_argv(tickets_py=None):
    py = sys.executable
    script = tickets_py or os.path.realpath(
        os.path.join(os.path.dirname(__file__), "tickets.py"))
    return py, script
