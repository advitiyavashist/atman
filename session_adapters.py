"""Provider-native persistent session adapters (T-683).

Capability-based boundary for injecting one turn into an already-connected
interactive session when the harness supports it. Endpoints and credentials
live outside the git-tracked board (~/.cache/atman/sessions/<board-hash>/,
dirs 0700, files 0600). Falls back to supervised watch or the T-640 remote
bridge when native injection is unavailable.
"""

import hashlib
import json
import os
import shlex
import socket
import subprocess
import sys


ADAPTER_MODES = ("native", "supervised", "remote")
PROVIDERS = ("claude", "codex", "cursor", "remote")


def cache_root():
    return os.environ.get("TICKETS_CACHE_DIR") or os.path.join(
        os.path.expanduser("~"), ".cache", "atman")


def board_hash(board):
    return hashlib.sha256(os.path.abspath(board).encode("utf-8")).hexdigest()[:16]


def endpoint_dir(board):
    return os.path.join(cache_root(), "sessions", board_hash(board))


def endpoint_path(board, seat):
    return os.path.join(endpoint_dir(board), seat + ".json")


def _ensure_private_dir(path):
    os.makedirs(path, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass


def session_pid():
    """Session pid from the harness, not this short-lived CLI process."""
    for var in ("TICKET_SESSION_PID", "CLAUDE_PID", "CODEX_SESSION_PID", "CURSOR_SESSION_PID"):
        raw = (os.environ.get(var) or "").strip()
        if raw.isdigit():
            return int(raw)
    return None


def _pid_alive(pid):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _endpoint_pid_ok(pid):
    if pid in (None, "", 0):
        return True
    try:
        return _pid_alive(int(pid))
    except (TypeError, ValueError):
        return True


def write_endpoint(board, seat, record):
    root = cache_root()
    sessions = os.path.join(root, "sessions")
    d = endpoint_dir(board)
    for p in (root, sessions, d):
        _ensure_private_dir(p)
    path = endpoint_path(board, seat)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(record, f)
    os.replace(tmp, path)
    return path


def read_endpoint(board, seat):
    try:
        with open(endpoint_path(board, seat)) as f:
            return json.load(f)
    except (IOError, ValueError):
        return None


def remove_endpoint(board, seat):
    try:
        os.unlink(endpoint_path(board, seat))
    except OSError:
        pass


def redact_endpoint(ep):
    if not ep:
        return {}
    out = dict(ep)
    if out.get("token"):
        out["token"] = "<redacted>"
    return out


def live_endpoint(board, seat):
    """Return (record, was_stale). Liveness is local pid + transport file checks."""
    ep = read_endpoint(board, seat)
    if not ep:
        return None, False
    if not _endpoint_pid_ok(ep.get("pid")):
        remove_endpoint(board, seat)
        return None, True
    provider = ep.get("provider") or ""
    if provider == "claude":
        sock = ep.get("socket") or ""
        if not sock or not os.path.exists(sock):
            remove_endpoint(board, seat)
            return None, True
    elif provider == "codex":
        if not (ep.get("thread") or "").strip():
            remove_endpoint(board, seat)
            return None, True
    elif provider == "cursor":
        if not (ep.get("session_id") or "").strip():
            remove_endpoint(board, seat)
            return None, True
    return ep, False


def _which(cmd):
    from shutil import which
    return which(cmd)


def _probe_codex():
    if not _which("codex"):
        return {"ok": False, "reason": "codex CLI not on PATH"}
    r = subprocess.run(["codex", "queue", "--help"], capture_output=True, text=True)
    if r.returncode != 0:
        return {"ok": False, "reason": "codex queue subcommand unavailable"}
    return {"ok": True, "capabilities": {"native_inject": True, "transport": "codex queue --thread"}}


def _probe_cursor():
    if not _which("agent"):
        return {"ok": False, "reason": "cursor agent CLI not on PATH"}
    r = subprocess.run(["agent", "--help"], capture_output=True, text=True)
    if r.returncode != 0 or "--resume" not in (r.stdout + r.stderr):
        return {"ok": False, "reason": "cursor agent --resume unavailable"}
    return {"ok": True, "capabilities": {"native_inject": True, "transport": "agent -p --resume"}}


def _probe_claude():
    sock = (os.environ.get("CLAUDE_CODE_MESSAGING_SOCKET") or "").strip()
    if not sock:
        return {"ok": False, "reason": "CLAUDE_CODE_MESSAGING_SOCKET not set"}
    return {"ok": True, "capabilities": {"native_inject": True, "transport": "AF_UNIX inbox socket"}}


def _probe_remote():
    return {"ok": True, "capabilities": {"native_inject": False, "transport": "schema-2 remote bridge"}}


def probe_provider(provider):
    probes = {
        "claude": _probe_claude,
        "codex": _probe_codex,
        "cursor": _probe_cursor,
        "remote": _probe_remote,
    }
    fn = probes.get(provider)
    if not fn:
        return {"ok": False, "reason": "no native adapter for %s" % provider}
    return fn()


def provider_for_harness(harness):
    if harness in ("claude", "cursor+claude"):
        return "claude"
    if harness in ("codex", "cursor", "remote"):
        return harness
    return ""


def adapter_mode_for(board, seat, harness):
    provider = provider_for_harness(harness)
    if provider == "remote":
        return "remote"
    ep, _ = live_endpoint(board, seat)
    if ep and ep.get("mode") == "native":
        return "native"
    if provider in PROVIDERS:
        probe = probe_provider(provider)
        if probe.get("ok"):
            return "native"
    return "supervised"


def register_persistent(board, seat, harness, at_iso):
    """Register a native session endpoint from the current harness environment."""
    provider = provider_for_harness(harness)
    if not provider:
        return {"ok": False, "reason": "harness %r has no native session adapter" % harness}
    if provider == "remote":
        return {"ok": False, "reason": "remote seats use the schema-2 bridge, not join --persistent"}
    probe = probe_provider(provider)
    if not probe.get("ok"):
        return {"ok": False, "reason": probe.get("reason", "native transport unavailable")}
    record = {"seat": seat, "provider": provider, "mode": "native",
              "pid": session_pid(), "at": at_iso,
              "capabilities": probe.get("capabilities") or {}}
    if provider == "claude":
        sock = (os.environ.get("CLAUDE_CODE_MESSAGING_SOCKET") or "").strip()
        token = (os.environ.get("CLAUDE_CODE_MESSAGING_TOKEN") or "").strip()
        if not sock:
            return {"ok": False, "reason": "CLAUDE_CODE_MESSAGING_SOCKET not set"}
        record["socket"] = sock
        record["token"] = token
    elif provider == "codex":
        thread = ((os.environ.get("CODEX_THREAD_ID") or os.environ.get("CODEX_SESSION_ID") or "").strip())
        if not thread:
            return {"ok": False, "reason": "CODEX_THREAD_ID / CODEX_SESSION_ID not set"}
        record["thread"] = thread
    elif provider == "cursor":
        session_id = (os.environ.get("CURSOR_CONVERSATION_ID") or os.environ.get("CURSOR_SESSION_ID") or "").strip()
        if not session_id:
            return {"ok": False, "reason": "CURSOR_CONVERSATION_ID not set"}
        record["session_id"] = session_id
    write_endpoint(board, seat, record)
    return {"ok": True, "provider": provider, "mode": "native", "record": redact_endpoint(record)}


def wake_payload(fmt_msg, message):
    return "tickets board message -- %s\n(see `tickets inbox` for the rest)" % fmt_msg(message)


def _poke_claude(ep, text):
    sock_path = ep.get("socket") or ""
    if not sock_path:
        return False
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(2)
    try:
        s.connect(sock_path)
        token = ep.get("token") or ""
        if token:
            s.sendall((json.dumps({"type": "auth", "token": token}) + "\n").encode("utf-8"))
        s.sendall((text + "\n").encode("utf-8"))
        return True
    except OSError:
        return False
    finally:
        try:
            s.close()
        except OSError:
            pass


def _poke_codex(ep, text):
    thread = (ep.get("thread") or "").strip()
    if not thread:
        return False
    cmd = ["codex", "queue", "--thread", thread, "--message", text]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _poke_cursor(ep, text):
    session_id = (ep.get("session_id") or "").strip()
    if not session_id:
        return False
    cmd = ["agent", "-p", "--resume", session_id, "--output-format", "text", "--force", text]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def wake_seat(board, seat, text, harness=None):
    """Best-effort native wake. Returns a short label; never raises."""
    ep, was_stale = live_endpoint(board, seat)
    if ep is None:
        if harness == "remote":
            return "remote bridge required"
        return "endpoint stale (removed)" if was_stale else "no live endpoint"
    provider = ep.get("provider") or ""
    if provider == "claude":
        return "woken" if _poke_claude(ep, text) else "refused"
    if provider == "codex":
        return "queued" if _poke_codex(ep, text) else "refused"
    if provider == "cursor":
        return "woken" if _poke_cursor(ep, text) else "refused"
    return "unsupported provider"


def has_live_native_session(board, seat):
    ep, _ = live_endpoint(board, seat)
    return ep is not None and ep.get("mode") == "native"


def public_adapter_state(board, seat, harness, adapter_online, wake_pending):
    """UI/API fields: provider, mode, online, last receipt hints."""
    provider = provider_for_harness(harness) or "custom"
    mode = adapter_mode_for(board, seat, harness)
    ep, _ = live_endpoint(board, seat)
    native_online = ep is not None
    if mode == "remote":
        online = adapter_online
    elif mode == "native":
        online = native_online or adapter_online
    else:
        online = adapter_online
    state = {
        "adapter_provider": provider,
        "adapter_mode": mode,
        "adapter_native_online": native_online,
    }
    if ep:
        state["adapter_registered_at"] = ep.get("at", "")
        state["adapter_capabilities"] = ep.get("capabilities") or {}
    if wake_pending and not online:
        state["adapter_delivery"] = "queued-offline"
    elif wake_pending:
        state["adapter_delivery"] = "queued"
    elif online:
        state["adapter_delivery"] = "online"
    else:
        state["adapter_delivery"] = "offline"
    return state
