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
import socket
import subprocess
import time
import uuid


ADAPTER_MODES = ("native", "supervised", "remote")
PROVIDERS = ("claude", "codex", "cursor", "remote")
# PID-less Codex/Cursor endpoints cannot stay live forever and suppress recovery.
NATIVE_TTL_SECS = int(os.environ.get("TICKETS_NATIVE_TTL_SECS", "90"))


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
        return None
    try:
        return _pid_alive(int(pid))
    except (TypeError, ValueError):
        return None


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
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    return path


def endpoint_still_live(ep):
    """Whether a stored record is still live. Does not delete it."""
    if not ep:
        return False
    pid_ok = _endpoint_pid_ok(ep.get("pid"))
    if pid_ok is False:
        return False
    provider = ep.get("provider") or ""
    if provider == "claude":
        sock = ep.get("socket") or ""
        return bool(sock and os.path.exists(sock))
    if provider == "codex":
        if not (ep.get("thread") or "").strip():
            return False
        return True if pid_ok is True else _heartbeat_fresh(ep)
    if provider == "cursor":
        if not (ep.get("session_id") or "").strip():
            return False
        return True if pid_ok is True else _heartbeat_fresh(ep)
    return False


def commit_endpoint(board, seat, record, presented_lease=""):
    """Same-seat rebind increments fence; never steal another seat's session.

    A live same-seat endpoint is exclusive: rebind requires the current
    lease_id, the same session fingerprint, or waiting until the record is
    stale. Unconditional os.replace is not ownership.
    """
    taken = other_seat_for_session(board, seat, record)
    if taken:
        return {"ok": False, "reason": "session already bound to %s; refuse identity crosswire" % taken}
    existing = read_endpoint(board, seat)
    presented = (presented_lease or os.environ.get("TICKETS_SESSION_LEASE") or "").strip()
    if existing and endpoint_still_live(existing):
        same_session = bool(session_key(existing) and session_key(existing) == session_key(record))
        holds_lease = bool(presented and presented == (existing.get("lease_id") or ""))
        if not (holds_lease or same_session):
            return {"ok": False, "reason": (
                "active lease %s holds this seat; rebind with TICKETS_SESSION_LEASE "
                "or wait for the live session to expire" % (existing.get("lease_id") or "?"))}
        record["fence"] = int(existing.get("fence") or 0) + 1
        record["prev_lease_id"] = existing.get("lease_id") or ""
    elif existing:
        record["fence"] = int(existing.get("fence") or 0) + 1
        record["prev_lease_id"] = existing.get("lease_id") or ""
    else:
        record["fence"] = 1
    record["lease_id"] = uuid.uuid4().hex[:16]
    record["heartbeat_epoch"] = time.time()
    record.setdefault("heartbeat_at", record.get("at") or "")
    write_endpoint(board, seat, record)
    return {"ok": True, "fence": record["fence"], "lease_id": record["lease_id"],
            "record": redact_endpoint(record)}


def touch_endpoint(board, seat, **fields):
    ep = read_endpoint(board, seat)
    if not ep:
        return None
    ep.update(fields)
    ep["heartbeat_epoch"] = time.time()
    ep["heartbeat_at"] = fields.get("heartbeat_at") or ep.get("at") or ""
    write_endpoint(board, seat, ep)
    return ep


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


def list_endpoints(board):
    """Return (seat, record) pairs for this board's cache, skipping unreadable files."""
    try:
        names = os.listdir(endpoint_dir(board))
    except OSError:
        return []
    out = []
    for name in names:
        if not name.endswith(".json"):
            continue
        seat = name[:-5]
        ep = read_endpoint(board, seat)
        if ep:
            out.append((seat, ep))
    return out


def session_key(ep):
    """Provider + transport identity. Empty identities do not fence."""
    provider = (ep or {}).get("provider") or ""
    if provider == "claude":
        ident = (ep.get("socket") or "").strip()
    elif provider == "codex":
        ident = (ep.get("thread") or "").strip()
    elif provider == "cursor":
        ident = (ep.get("session_id") or "").strip()
    else:
        ident = ""
    if not ident:
        return None
    return (provider, ident)


def other_seat_for_session(board, seat, record):
    key = session_key(record)
    if not key:
        return None
    for other, ep in list_endpoints(board):
        if other == seat:
            continue
        if session_key(ep) == key:
            return other
    return None


def redact_endpoint(ep):
    if not ep:
        return {}
    out = dict(ep)
    if out.get("token"):
        out["token"] = "<redacted>"
    return out


def _heartbeat_fresh(ep, ttl=None):
    ttl = NATIVE_TTL_SECS if ttl is None else ttl
    try:
        beat = float(ep.get("heartbeat_epoch") or 0)
    except (TypeError, ValueError):
        beat = 0.0
    if beat <= 0:
        return False
    return (time.time() - beat) <= max(1, int(ttl))


def live_endpoint(board, seat):
    """Return (record, was_stale). Native live requires transport proof, not lifecycle."""
    ep = read_endpoint(board, seat)
    if not ep:
        return None, False
    pid_ok = _endpoint_pid_ok(ep.get("pid"))
    if pid_ok is False:
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
        if pid_ok is None and not _heartbeat_fresh(ep):
            remove_endpoint(board, seat)
            return None, True
    elif provider == "cursor":
        if not (ep.get("session_id") or "").strip():
            remove_endpoint(board, seat)
            return None, True
        if pid_ok is None and not _heartbeat_fresh(ep):
            remove_endpoint(board, seat)
            return None, True
        # Cursor resume is not an enqueue primitive; a recorded session is identity only.
        if ep.get("mode") == "native":
            ep = dict(ep)
            ep["mode"] = "supervised"
    else:
        return None, False
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
    help_text = r.stdout + r.stderr
    if r.returncode != 0 or "--resume" not in help_text:
        return {"ok": False, "reason": "cursor agent --resume unavailable"}
    return {"ok": True, "capabilities": {
        "native_inject": False,
        "transport": "supervised watch (agent -p --resume is a paid foreground run, not enqueue)",
    }}


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
    caps = probe.get("capabilities") or {}
    record = {"seat": seat, "agent_id": seat, "provider": provider,
              "mode": "native" if caps.get("native_inject") else "supervised",
              "pid": session_pid(), "at": at_iso, "capabilities": caps}
    if provider == "claude":
        sock = (os.environ.get("CLAUDE_CODE_MESSAGING_SOCKET") or "").strip()
        token = (os.environ.get("CLAUDE_CODE_MESSAGING_TOKEN") or "").strip()
        if not sock:
            return {"ok": False, "reason": "CLAUDE_CODE_MESSAGING_SOCKET not set"}
        record["socket"] = sock
        record["token"] = token
        record["mode"] = "native"
    elif provider == "codex":
        thread = ((os.environ.get("CODEX_THREAD_ID") or os.environ.get("CODEX_SESSION_ID") or "").strip())
        if not thread:
            return {"ok": False, "reason": "CODEX_THREAD_ID / CODEX_SESSION_ID not set"}
        record["thread"] = thread
        record["mode"] = "native"
    elif provider == "cursor":
        session_id = (os.environ.get("CURSOR_CONVERSATION_ID") or os.environ.get("CURSOR_SESSION_ID") or "").strip()
        if not session_id:
            return {"ok": False, "reason": "CURSOR_CONVERSATION_ID not set"}
        record["session_id"] = session_id
        record["mode"] = "supervised"
        record["capabilities"] = {
            "native_inject": False,
            "transport": "supervised watch (agent -p --resume is a paid foreground run, not enqueue)",
        }
    committed = commit_endpoint(board, seat, record)
    if not committed.get("ok"):
        return committed
    return {"ok": True, "provider": provider, "mode": record["mode"],
            "lease_id": committed.get("lease_id"),
            "record": committed["record"]}


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
    """Never enqueue via agent -p --resume: that is a paid foreground model run."""
    return False


def is_reachable(native_online=False, watcher_online=False, remote_online=False):
    """Persistent is identity, not reachability. Need a live transport."""
    return bool(native_online or watcher_online or remote_online)


def wake_seat(board, seat, text, harness=None, message_id=""):
    """Best-effort native wake. Returns a short label; never raises."""
    expected = provider_for_harness(harness) if harness else ""
    if expected == "remote" or harness == "remote":
        return "remote bridge required"
    ep, was_stale = live_endpoint(board, seat)
    if ep is None:
        return "endpoint stale (removed)" if was_stale else "no live endpoint"
    mid = str(message_id or "")
    if mid and ep.get("last_delivery_id") == mid:
        return "deduped"
    provider = ep.get("provider") or ""
    if expected and provider and expected != provider:
        remove_endpoint(board, seat)
        return "refused (harness %s != provider %s; removed stale endpoint)" % (harness, provider)
    ok = False
    if provider == "claude":
        ok = _poke_claude(ep, text)
        label = "woken" if ok else "refused"
    elif provider == "codex":
        ok = _poke_codex(ep, text)
        label = "queued" if ok else "refused"
    elif provider == "cursor":
        label = "supervised (cursor agent -p --resume is a paid foreground run, not enqueue)"
    else:
        label = "unsupported provider"
    if ok:
        touch_endpoint(board, seat, last_delivery_id=mid, last_delivery_status=label)
    return label


def has_live_native_session(board, seat):
    ep, _ = live_endpoint(board, seat)
    return ep is not None and ep.get("mode") == "native"


def public_adapter_state(board, seat, harness, adapter_online, wake_pending):
    """UI/API fields: provider, mode, online, last receipt hints."""
    provider = provider_for_harness(harness) or "custom"
    mode = adapter_mode_for(board, seat, harness)
    ep, _ = live_endpoint(board, seat)
    native_online = bool(ep) and ep.get("mode") == "native"
    if mode == "remote":
        online = bool(adapter_online)
    else:
        online = native_online or bool(adapter_online)
    state = {
        "adapter_provider": provider,
        "adapter_mode": mode,
        "adapter_native_online": native_online,
    }
    if ep:
        state["adapter_registered_at"] = ep.get("at", "")
        state["adapter_capabilities"] = ep.get("capabilities") or {}
        state["adapter_session"] = ep.get("session_id") or ep.get("thread") or ep.get("pid") or ""
        state["adapter_pid"] = ep.get("pid")
        state["adapter_fence"] = int(ep.get("fence") or 0)
        state["adapter_heartbeat_at"] = ep.get("heartbeat_at") or ""
        if ep.get("last_delivery_id"):
            state["adapter_last_delivery_id"] = ep.get("last_delivery_id")
            state["adapter_last_delivery"] = ep.get("last_delivery_status") or ""
    state["adapter_usage"] = "unmeasured"
    if wake_pending and not online:
        state["adapter_delivery"] = "queued-offline"
    elif wake_pending:
        state["adapter_delivery"] = "queued"
    elif online:
        state["adapter_delivery"] = "online"
    else:
        state["adapter_delivery"] = "offline"
    return state
