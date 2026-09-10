"""Provider-native persistent session adapters (T-683).

Capability-based boundary for injecting one turn into an already-connected
interactive session when the harness supports it. Endpoints and credentials
live outside the git-tracked board (~/.cache/atman/sessions/<board-hash>/,
dirs 0700, files 0600). Falls back to supervised watch or the T-640 remote
bridge when native injection is unavailable.
"""

import fcntl
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
# Crash during poke must not suppress the durable message_id forever.
INFLIGHT_TTL_SECS = int(os.environ.get("TICKETS_NATIVE_INFLIGHT_TTL_SECS", "30"))


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


def _seat_lock_path(board, seat):
    return os.path.join(endpoint_dir(board), seat + ".lock")


def acquire_named_lock(board, name):
    """Exclusive lock file. Created once with O_EXCL, then flocked."""
    d = endpoint_dir(board)
    root = cache_root()
    sessions = os.path.join(root, "sessions")
    for p in (root, sessions, d):
        _ensure_private_dir(p)
    path = os.path.join(d, name + ".lock")
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
    except FileExistsError:
        fd = os.open(path, os.O_RDWR, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def acquire_seat_lock(board, seat):
    return acquire_named_lock(board, seat)


def acquire_fingerprint_lock(board, key):
    ident = hashlib.sha256(("%s:%s" % key).encode("utf-8")).hexdigest()[:16]
    return acquire_named_lock(board, "fp-" + ident)


def release_seat_lock(fd):
    if fd is None:
        return
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass
    try:
        os.close(fd)
    except OSError:
        pass


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
    stale. Unconditional os.replace is not ownership. Register/rebind is
    serialized on a per-seat exclusive lock so two first binds cannot both
    stamp fence=1. Cross-seat same-session binds share a fingerprint lock so
    two seats cannot both scan-then-write the same provider identity.
    """
    key = session_key(record)
    fp_fd = acquire_fingerprint_lock(board, key) if key else None
    fd = acquire_seat_lock(board, seat)
    try:
        return _commit_endpoint_locked(board, seat, record, presented_lease)
    finally:
        release_seat_lock(fd)
        release_seat_lock(fp_fd)


def _commit_endpoint_locked(board, seat, record, presented_lease=""):
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


def touch_endpoint(board, seat, expected_lease="", expected_fence=None, **fields):
    """Update a live endpoint only when lease/fence still match the caller."""
    fd = acquire_seat_lock(board, seat)
    try:
        ep = read_endpoint(board, seat)
        if not ep:
            return None
        if expected_lease and (ep.get("lease_id") or "") != expected_lease:
            return None
        if expected_fence is not None and int(ep.get("fence") or 0) != int(expected_fence):
            return None
        ep.update(fields)
        ep["heartbeat_epoch"] = time.time()
        ep["heartbeat_at"] = fields.get("heartbeat_at") or _iso_now()
        write_endpoint(board, seat, ep)
        return ep
    finally:
        release_seat_lock(fd)


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


def remove_endpoint_if_match(board, seat, expected_lease="", expected_fence=None):
    """Delete only the endpoint this caller observed. Never drop a rebound lease."""
    fd = acquire_seat_lock(board, seat)
    try:
        ep = read_endpoint(board, seat)
        if not ep:
            return False
        if expected_lease and (ep.get("lease_id") or "") != expected_lease:
            return False
        if expected_fence is not None and int(ep.get("fence") or 0) != int(expected_fence):
            return False
        try:
            os.unlink(endpoint_path(board, seat))
        except OSError:
            return False
        return True
    finally:
        release_seat_lock(fd)


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

    def _drop_observed():
        lease = ep.get("lease_id") or ""
        try:
            fence = int(ep.get("fence") or 0)
        except (TypeError, ValueError):
            fence = 0
        remove_endpoint_if_match(board, seat, expected_lease=lease, expected_fence=fence)
        return None, True

    pid_ok = _endpoint_pid_ok(ep.get("pid"))
    if pid_ok is False:
        return _drop_observed()
    provider = ep.get("provider") or ""
    if provider == "claude":
        sock = ep.get("socket") or ""
        if not sock or not os.path.exists(sock):
            return _drop_observed()
    elif provider == "codex":
        if not (ep.get("thread") or "").strip():
            return _drop_observed()
        if pid_ok is None and not _heartbeat_fresh(ep):
            # Keep the thread identity. TTL means not-online, not "delete the seat".
            return None, True
    elif provider == "cursor":
        if not (ep.get("session_id") or "").strip():
            return _drop_observed()
        if pid_ok is None and not _heartbeat_fresh(ep):
            return None, True
        # Native inject needs persist+tmux or a live ACP control sock.
        # Identity-only CURSOR_CONVERSATION_ID stays supervised.
        persist = (ep.get("persist_session") or "").strip()
        acp = (ep.get("socket") or "").strip()
        acp_live = bool(acp and os.path.exists(acp))
        if ep.get("mode") == "native" and not persist and not acp_live:
            ep = dict(ep)
            ep["mode"] = "supervised"
    else:
        return None, False
    return ep, False


def _which(cmd):
    from shutil import which
    return which(cmd)


def default_codex_control_socket():
    return _codex_control_sock()


def _codex_control_sock():
    """Managed app-server control socket (not the IDE in-process server)."""
    override = (os.environ.get("CODEX_APP_SERVER_CONTROL_SOCK")
                or os.environ.get("CODEX_APP_SERVER_SOCKET") or "").strip()
    if override:
        return override
    home = (os.environ.get("CODEX_HOME") or "").strip() or os.path.join(
        os.path.expanduser("~"), ".codex")
    return os.path.join(home, "app-server-control", "app-server-control.sock")


def default_cursor_acp_socket():
    return _cursor_acp_sock()


def _cursor_acp_sock():
    """Managed ACP control socket (not `agent -p --resume`, not worker.sock)."""
    override = (os.environ.get("CURSOR_ACP_CONTROL_SOCK") or "").strip()
    if override:
        return override
    return os.path.join(os.path.expanduser("~"), ".cursor", "acp-control",
                        "acp-control.sock")


def _cursor_persist_target():
    named = (os.environ.get("CURSOR_PERSIST_SESSION") or "").strip()
    if named:
        return named
    if not os.environ.get("TMUX") or not _which("tmux"):
        return ""
    try:
        r = subprocess.run(
            ["tmux", "display-message", "-p", "#{session_name}"],
            capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if r.returncode != 0:
        return ""
    return (r.stdout or "").strip()


def _probe_codex():
    if not _which("codex"):
        return {"ok": False, "reason": "codex CLI not on PATH"}
    r = subprocess.run(["codex", "queue", "--help"], capture_output=True, text=True)
    if r.returncode != 0:
        return {"ok": False, "reason": "codex queue subcommand unavailable"}
    sock = _codex_control_sock()
    if os.path.exists(sock):
        transport = "codex queue --thread + app-server thread/queue/start"
        native = True
    else:
        transport = "codex queue --thread (queued-offline without app-server control sock)"
        native = False
    return {"ok": True, "capabilities": {
        "native_inject": native, "transport": transport,
        "control_socket": sock, "control_socket_live": bool(native),
    }}


def _probe_cursor():
    if not _which("agent"):
        return {"ok": False, "reason": "cursor agent CLI not on PATH"}
    r = subprocess.run(["agent", "--help"], capture_output=True, text=True)
    help_text = r.stdout + r.stderr
    if r.returncode != 0 or "--resume" not in help_text:
        return {"ok": False, "reason": "cursor agent --resume unavailable"}
    persist = _cursor_persist_target()
    if persist and _which("tmux"):
        return {"ok": True, "capabilities": {
            "native_inject": True,
            "transport": "tmux send-keys into agent persist session",
            "persist_session": persist,
        }}
    sock = _cursor_acp_sock()
    if sock and os.path.exists(sock):
        return {"ok": True, "capabilities": {
            "native_inject": True,
            "transport": "ACP session/load + session/prompt on live control sock",
            "control_socket": sock,
            "control_socket_live": True,
        }}
    return {"ok": True, "capabilities": {
        "native_inject": False,
        "transport": ("supervised (need agent persist + tmux, or a live ACP "
                      "control sock; agent -p --resume is a new paid run)"),
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
        sock = default_codex_control_socket()
        record["socket"] = sock
        live = bool(sock and os.path.exists(sock))
        record["mode"] = "native" if live else "supervised"
        record["capabilities"] = {
            "native_inject": live,
            "transport": ("codex app-server turn/start" if live
                          else "codex queue mailbox only (poll unless app-server socket is live)"),
        }
    elif provider == "cursor":
        session_id = (os.environ.get("CURSOR_CONVERSATION_ID") or os.environ.get("CURSOR_SESSION_ID") or "").strip()
        if not session_id:
            return {"ok": False, "reason": "CURSOR_CONVERSATION_ID not set"}
        record["session_id"] = session_id
        persist = _cursor_persist_target()
        if persist:
            record["persist_session"] = persist
        sock = _cursor_acp_sock()
        if persist and _which("tmux"):
            record["mode"] = "native"
            record["capabilities"] = {
                "native_inject": True,
                "transport": "tmux send-keys into agent persist session",
            }
        elif sock and os.path.exists(sock):
            record["socket"] = sock
            record["mode"] = "native"
            record["capabilities"] = {
                "native_inject": True,
                "transport": "ACP session/load + session/prompt on live control sock",
            }
        else:
            record["mode"] = "supervised"
            record["capabilities"] = {
                "native_inject": False,
                "transport": ("supervised (need agent persist + tmux, or a live ACP "
                              "control sock; agent -p --resume is a new paid run)"),
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


def _codex_app_server_rpc(method, params, timeout=5):
    """JSON-RPC one-shot via `codex app-server proxy`. None if unavailable."""
    sock = _codex_control_sock()
    if not sock or not os.path.exists(sock):
        return None
    if not _which("codex"):
        return None
    req = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    try:
        r = subprocess.run(
            ["codex", "app-server", "proxy", "--sock", sock],
            input=json.dumps(req) + "\n",
            capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    for line in (r.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if msg.get("id") == 1:
            return msg
    return None


def _codex_queue_start(thread):
    """Ask the managed app-server to start the next queued turn (pause→resume)."""
    resp = _codex_app_server_rpc("thread/queue/start", {"threadId": thread})
    if not resp or resp.get("error"):
        return False
    return True


def _codex_turn_start(thread, text):
    """Direct turn inject via app-server when the thread is loaded."""
    params = {
        "threadId": thread,
        "input": [{"type": "text", "text": text}],
    }
    resp = _codex_app_server_rpc("turn/start", params)
    if not resp or resp.get("error"):
        return False
    return True


def _poke_codex_wake(ep, text):
    """Enqueue then start. Returns woken | queued-offline | refused.

    `codex queue` alone writes ~/.codex/queue_1.sqlite and does not resume a
    paused session. Managed app-server `thread/queue/start` or `turn/start` is
    required for Claude-parity pause→resume. Sqlite-only success is
    queued-offline (durable, not a native wake).
    """
    thread = (ep.get("thread") or "").strip()
    if not thread:
        return "refused"
    cmd = ["codex", "queue", "--thread", thread, "--message", text]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        queued = r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        queued = False
    if not queued:
        return "refused"
    for _ in range(max(1, int(NATIVE_POKE_ATTEMPTS))):
        if _codex_queue_start(thread) or _codex_turn_start(thread, text):
            return "woken"
    return "queued-offline"


def _poke_codex(ep, text):
    """Bool wrapper for older call sites/tests: True only on live wake."""
    return _poke_codex_wake(ep, text) == "woken"


def _poke_cursor(ep, text):
    """Inject into a paused persist session. Never spawn agent -p --resume."""
    session = (ep.get("persist_session") or "").strip()
    if not session or not _which("tmux"):
        return False
    try:
        typed = subprocess.run(
            ["tmux", "send-keys", "-t", session, "-l", text],
            capture_output=True, text=True, timeout=5)
        if typed.returncode != 0:
            return False
        enter = subprocess.run(
            ["tmux", "send-keys", "-t", session, "Enter"],
            capture_output=True, text=True, timeout=5)
        return enter.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _cursor_acp_rpc(method, params, sock=None, timeout=5):
    """JSON-RPC one-shot to a live ACP control sock. None if unavailable."""
    sock = (sock or _cursor_acp_sock() or "").strip()
    if not sock or not os.path.exists(sock):
        return None
    req = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(sock)
        s.sendall((json.dumps(req) + "\n").encode("utf-8"))
        buf = b""
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
            if b"\n" in buf:
                break
        line = buf.split(b"\n", 1)[0].decode("utf-8", "replace").strip()
        if not line:
            return None
        return json.loads(line)
    except (OSError, ValueError):
        return None
    finally:
        try:
            s.close()
        except OSError:
            pass


def _poke_cursor_acp(ep, text):
    """Inject via ACP session/load + session/prompt. Never spawn agent acp or -p."""
    session_id = (ep.get("session_id") or "").strip()
    sock = (ep.get("socket") or "").strip() or _cursor_acp_sock()
    if not session_id or not sock or not os.path.exists(sock):
        return False
    # Load may error if the conversation is already the active ACP session.
    _cursor_acp_rpc("session/load", {"sessionId": session_id}, sock=sock)
    prompt = _cursor_acp_rpc(
        "session/prompt",
        {"sessionId": session_id, "prompt": [{"type": "text", "text": text}]},
        sock=sock)
    if not prompt or prompt.get("error"):
        return False
    return True


def _cursor_pause_resume(ep, text):
    if _poke_until(_poke_cursor, ep, text):
        return "woken"
    if _poke_until(_poke_cursor_acp, ep, text):
        return "woken"
    return ("supervised (no persist/tmux or ACP control sock; agent -p --resume "
            "is a new paid run, not pause-resume)")


def is_reachable(native_online=False, watcher_online=False, remote_online=False):
    """Persistent is identity, not reachability. Need a live transport."""
    return bool(native_online or watcher_online or remote_online)


NATIVE_POKE_ATTEMPTS = 3


def _poke_until(fn, ep, text, attempts=NATIVE_POKE_ATTEMPTS):
    for _ in range(max(1, int(attempts))):
        if fn(ep, text):
            return True
    return False


def _iso_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _inflight_fresh(ep, ttl=None):
    ttl = INFLIGHT_TTL_SECS if ttl is None else ttl
    try:
        beat = float((ep or {}).get("last_inflight_epoch") or 0)
    except (TypeError, ValueError):
        beat = 0.0
    if beat <= 0:
        return False
    return (time.time() - beat) <= max(1, int(ttl))


def _endpoint_lease_fence(ep):
    lease = (ep or {}).get("lease_id") or ""
    try:
        fence = int((ep or {}).get("fence") or 0)
    except (TypeError, ValueError):
        fence = 0
    return lease, fence


def heartbeat_session(board, seat, presented_lease="", thread="", session_id=""):
    """Keep-alive for a stored native identity. Does not create or steal a seat.

    Requires the current lease or a matching session fingerprint
    (`CODEX_THREAD_ID` / Cursor session id). Absent or mismatched identity is
    rejected so a new session cannot keep an old thread online.
    """
    presented = (presented_lease or os.environ.get("TICKETS_SESSION_LEASE") or "").strip()
    thread = (thread or os.environ.get("CODEX_THREAD_ID")
              or os.environ.get("CODEX_SESSION_ID") or "").strip()
    session_id = (session_id or os.environ.get("CURSOR_CONVERSATION_ID")
                  or os.environ.get("CURSOR_SESSION_ID") or "").strip()
    if not presented and not thread and not session_id:
        return None
    fd = acquire_seat_lock(board, seat)
    try:
        ep = read_endpoint(board, seat)
        if not ep:
            return None
        lease_ok = bool(presented and presented == (ep.get("lease_id") or ""))
        thread_ok = bool(thread and thread == (ep.get("thread") or "").strip())
        session_ok = bool(session_id and session_id == (ep.get("session_id") or "").strip())
        if not (lease_ok or thread_ok or session_ok):
            return None
        if _endpoint_pid_ok(ep.get("pid")) is False:
            return None
        ep["heartbeat_epoch"] = time.time()
        ep["heartbeat_at"] = _iso_now()
        write_endpoint(board, seat, ep)
        return ep
    finally:
        release_seat_lock(fd)


def _can_inject_retained(ep, expected):
    """PID-less Codex identity may still be queued after TTL; do not claim online."""
    if not ep or (ep.get("mode") or "") != "native":
        return False
    if (ep.get("provider") or "") != "codex":
        return False
    if not (ep.get("thread") or "").strip():
        return False
    if expected and expected != "codex":
        return False
    return True


def _reserve_wake(board, seat, mid, lease, fence):
    """Mark message_id in-flight under the seat lock before any poke."""
    fd = acquire_seat_lock(board, seat)
    try:
        ep = read_endpoint(board, seat)
        if not ep:
            return "gone", None
        if lease and (ep.get("lease_id") or "") != lease:
            return "stale (rebound before delivery)", ep
        if fence and int(ep.get("fence") or 0) != int(fence):
            return "stale (rebound before delivery)", ep
        if mid and ep.get("last_delivery_id") == mid:
            return "deduped", ep
        if mid and ep.get("last_inflight_id") == mid and _inflight_fresh(ep):
            return "deduped", ep
        if mid:
            ep["last_inflight_id"] = mid
            ep["last_inflight_epoch"] = time.time()
            write_endpoint(board, seat, ep)
        return "reserved", ep
    finally:
        release_seat_lock(fd)


def _commit_wake(board, seat, mid, lease, fence, label, ok):
    fd = acquire_seat_lock(board, seat)
    try:
        ep = read_endpoint(board, seat)
        if not ep:
            return "stale (rebound before delivery)" if mid else label
        if lease and (ep.get("lease_id") or "") != lease:
            return "stale (rebound before delivery)"
        if fence and int(ep.get("fence") or 0) != int(fence):
            return "stale (rebound before delivery)"
        ep.pop("last_inflight_id", None)
        ep.pop("last_inflight_epoch", None)
        if ok:
            if mid:
                ep["last_delivery_id"] = mid
                ep["last_delivery_status"] = label
            ep["heartbeat_epoch"] = time.time()
            ep["heartbeat_at"] = ep.get("at") or ""
            write_endpoint(board, seat, ep)
            return label
        ep["last_attempt_id"] = mid
        ep["last_attempt_status"] = label
        write_endpoint(board, seat, ep)
        if label == "refused":
            try:
                os.unlink(endpoint_path(board, seat))
            except OSError:
                pass
        return label
    finally:
        release_seat_lock(fd)


def wake_seat(board, seat, text, harness=None, message_id=""):
    """Best-effort native wake. Returns a short label; never raises."""
    expected = provider_for_harness(harness) if harness else ""
    if expected == "remote" or harness == "remote":
        leftover = read_endpoint(board, seat)
        if leftover:
            lease, fence = _endpoint_lease_fence(leftover)
            remove_endpoint_if_match(board, seat, expected_lease=lease, expected_fence=fence)
        return "remote bridge required"
    ep, was_stale = live_endpoint(board, seat)
    if ep is None:
        stored = read_endpoint(board, seat)
        if _can_inject_retained(stored, expected):
            ep = stored
        elif stored:
            return "no live endpoint"
        else:
            return "endpoint stale (removed)" if was_stale else "no live endpoint"
    mid = str(message_id or "")
    provider = ep.get("provider") or ""
    lease, fence = _endpoint_lease_fence(ep)
    if expected and provider and expected != provider:
        remove_endpoint_if_match(board, seat, expected_lease=lease, expected_fence=fence)
        return "refused (harness %s != provider %s; removed stale endpoint)" % (harness, provider)
    if provider not in ("claude", "codex", "cursor"):
        return "unsupported provider"
    reserved, ep = _reserve_wake(board, seat, mid, lease, fence)
    if reserved != "reserved":
        return reserved
    if ep is None:
        return "no live endpoint"
    lease, fence = _endpoint_lease_fence(ep)
    if provider == "claude":
        ok = _poke_until(_poke_claude, ep, text)
        label = "woken" if ok else "refused"
    elif provider == "cursor":
        label = _cursor_pause_resume(ep, text)
        ok = label == "woken"
    else:
        label = _poke_codex_wake(ep, text)
        ok = label in ("woken", "queued-offline")
    return _commit_wake(board, seat, mid, lease, fence, label, ok)


def has_live_native_session(board, seat):
    """True only when a native poke can resume the session now.

    Retained PID-less Codex thread identity alone must NOT suppress watch:
    `codex queue` sqlite writes are not Claude-parity wake, and claiming live
    here left seats stranded until an unrelated poll loop noticed mail.
    """
    ep, _ = live_endpoint(board, seat)
    if ep is None or ep.get("mode") != "native":
        return False
    provider = ep.get("provider") or ""
    if provider == "codex":
        # Live wake requires managed app-server control sock (queue/start) or
        # a still-alive session pid that owns the endpoint.
        if os.path.exists(_codex_control_sock()):
            return True
        return _endpoint_pid_ok(ep.get("pid")) is True
    if provider == "cursor":
        persist = (ep.get("persist_session") or "").strip()
        acp = (ep.get("socket") or "").strip()
        return bool(persist) or bool(acp and os.path.exists(acp))
    return True


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
