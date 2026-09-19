"""Provider-native persistent session adapters (T-683).

Capability-based boundary for injecting one turn into an already-connected
interactive session when the harness supports it. Endpoints and credentials
live outside the git-tracked board (~/.cache/atman/sessions/<board-hash>/,
dirs 0700, files 0600). Falls back to supervised watch or the T-640 remote
bridge when native injection is unavailable.
"""

import base64
import fcntl
import hashlib
import json
import os
import re
import socket
import struct
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
        # Native inject needs a *managed* persist session that is still on the
        # cursor-agent tmux server, or an operator ACP control sock. A stored
        # session name whose pane is gone is identity, not reachability, and
        # identity-only CURSOR_CONVERSATION_ID stays supervised.
        persist = (ep.get("persist_session") or "").strip()
        acp = (ep.get("socket") or "").strip()
        acp_live = bool(acp and os.path.exists(acp))
        persist_live = bool(persist) and _cursor_managed_session(persist) is not None
        if ep.get("mode") == "native" and not persist_live and not acp_live:
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
    """Operator-asserted ACP control socket. Empty unless one is configured.

    T-861 evidence: cursor-agent 2026.09.10 ships `agent acp` as a *hidden*
    command that starts the agent as an ACP server on stdio
    (index.js: `command("acp", {hidden:!0})` -> `runAcp`), i.e. a new process.
    The bundle contains no "acp-control" string and opens no control socket, so
    the old default ~/.cursor/acp-control/acp-control.sock could never exist: it
    only made `native_inject` look reachable on a seat with no transport. A live
    ACP bridge is something an operator runs, so it must be named explicitly.
    """
    return (os.environ.get("CURSOR_ACP_CONTROL_SOCK") or "").strip()


# --- Cursor `agent persist` transport (T-861, verified against 2026.09.10) ---
# `agent persist` does NOT live on the user's default tmux server.
# src/persistence/persistent-session.ts runs every tmux call as
#   tmux -u -L <CURSOR_AGENT_TMUX_SERVER_NAME|cursor-agent> -f /dev/null ...
# with a scrubbed env (PATH/HOME/SHELL/USER/LOGNAME/LANG/TERM/COLORTERM, LC_*)
# and TMUX_TMPDIR=/tmp, and tags each managed session @cursor_managed=1,
# @cursor_session_version=1, @cursor_workspace_hash=<32 hex>, @cursor_chat_id.
# Inside a persist pane the CLI exports CURSOR_AGENT_PERSIST_SESSION=<name>.
# Reading $TMUX / `tmux display-message` (the pre-T-861 code) asked the default
# server instead: send-keys either failed or typed the board's mail into an
# unrelated user session that happened to share the name.
CURSOR_PERSIST_TMUX_SERVER = "cursor-agent"
CURSOR_PERSIST_TMUX_TMPDIR = "/tmp"
CURSOR_PERSIST_ENV = "CURSOR_AGENT_PERSIST_SESSION"
CURSOR_SESSIONS_TTL_SECS = float(os.environ.get("TICKETS_CURSOR_SESSIONS_TTL_SECS", "2"))
# Seconds to wait for the injected line to land in the chat store as a turn.
CURSOR_EVIDENCE_SECS = float(os.environ.get("TICKETS_CURSOR_EVIDENCE_SECS", "6"))
_CURSOR_SESSION_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_CURSOR_SERVER_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_CURSOR_LIST_FIELDS = ("#{session_name}", "#{session_attached}", "#{@cursor_managed}",
                       "#{@cursor_workspace_hash}", "#{@cursor_session_version}",
                       "#{@cursor_chat_id}")
_cursor_sessions_cache = {"at": 0.0, "rows": []}


def _cursor_tmux_binary():
    for var in ("CURSOR_AGENT_TMUX_PATH",):
        path = (os.environ.get(var) or "").strip()
        if path:
            return path
    root = (os.environ.get("AGENT_TMUX_ROOT_PATH") or "").strip()
    if root:
        return os.path.join(root, "bin", "tmux")
    return "tmux"


def _cursor_tmux_server():
    name = (os.environ.get("CURSOR_AGENT_TMUX_SERVER_NAME") or "").strip()
    if name and _CURSOR_SERVER_NAME_RE.match(name):
        return name
    return CURSOR_PERSIST_TMUX_SERVER


def _cursor_tmux_env():
    """The same scrubbed env the CLI hands tmux. $TMUX must not leak in:
    an inherited $TMUX would point the client at whatever server this process
    happens to run under instead of the managed one."""
    keep = ("PATH", "HOME", "SHELL", "USER", "LOGNAME", "LANG", "TERM", "COLORTERM")
    env = dict((k, os.environ[k]) for k in keep if os.environ.get(k) is not None)
    for key, value in os.environ.items():
        if key.startswith("LC_"):
            env[key] = value
    env["TMUX_TMPDIR"] = ((os.environ.get("CURSOR_AGENT_TMUX_TMPDIR") or "").strip()
                          or CURSOR_PERSIST_TMUX_TMPDIR)
    return env


def _cursor_tmux(args, timeout=5):
    """One tmux call against the managed cursor-agent server. None on failure."""
    binary = _cursor_tmux_binary()
    if binary == "tmux" and not _which("tmux"):
        return None
    cmd = [binary, "-u", "-L", _cursor_tmux_server(), "-f", "/dev/null"] + list(args)
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                              env=_cursor_tmux_env())
    except (OSError, subprocess.TimeoutExpired):
        return None


def cursor_persist_sessions(refresh=False):
    """Managed `agent persist` sessions, as the CLI itself enumerates them.

    Untagged sessions on the same server are other people's; they are dropped
    so a wake can never type into a session cursor-agent does not own.
    """
    now_ts = time.time()
    if not refresh and (now_ts - float(_cursor_sessions_cache["at"] or 0)) < CURSOR_SESSIONS_TTL_SECS:
        return list(_cursor_sessions_cache["rows"])
    r = _cursor_tmux(["list-sessions", "-F", "\t".join(_CURSOR_LIST_FIELDS)])
    rows = []
    if r is not None and r.returncode == 0:
        for line in (r.stdout or "").splitlines():
            parts = line.split("\t")
            if len(parts) < 6:
                continue
            name, attached, managed, ws_hash, version, chat_id = [p.strip() for p in parts[:6]]
            if managed != "1" or version != "1" or not ws_hash:
                continue
            if not _CURSOR_SESSION_NAME_RE.match(name):
                continue
            try:
                clients = int(attached or "0")
            except ValueError:
                clients = 0
            rows.append({"name": name, "attached_clients": clients,
                         "workspace_hash": ws_hash, "chat_id": chat_id})
    _cursor_sessions_cache["at"] = now_ts
    _cursor_sessions_cache["rows"] = rows
    return list(rows)


def _cursor_managed_session(name, refresh=False):
    name = (name or "").strip()
    if not name or not _CURSOR_SESSION_NAME_RE.match(name):
        return None
    for row in cursor_persist_sessions(refresh=refresh):
        if row.get("name") == name:
            return row
    return None


def _cursor_persist_target():
    """Managed persist session this process runs inside, if any.

    CURSOR_AGENT_PERSIST_SESSION is exported by cursor-agent itself, so it is
    the seat's own statement of which session it is. CURSOR_PERSIST_SESSION
    stays as the operator override for wiring a seat by hand.
    """
    for var in ("CURSOR_PERSIST_SESSION", CURSOR_PERSIST_ENV):
        named = (os.environ.get(var) or "").strip()
        if named and _CURSOR_SESSION_NAME_RE.match(named):
            return named
    return ""


def _cursor_chats_root():
    override = (os.environ.get("CURSOR_CHATS_DIR") or "").strip()
    if override:
        return override
    return os.path.join(os.path.expanduser("~"), ".cursor", "chats")


def _cursor_chat_store(ep):
    """~/.cursor/chats/<workspace hash>/<chat id>/store.db, or ''.

    The chat store is where a submitted prompt becomes a turn: each message is
    one plaintext-JSON row in `blobs`. It is the only local artifact that says
    a keystroke actually started a turn rather than landing in a dialog.
    The @cursor_workspace_hash tmux tag is NOT the chats directory name, so the
    chat id is what locates the store.
    """
    chat_id = ((ep or {}).get("chat_id") or (ep or {}).get("session_id") or "").strip()
    if not chat_id or "/" in chat_id or chat_id in (".", ".."):
        return ""
    root = _cursor_chats_root()
    names = []
    ws_hash = ((ep or {}).get("workspace_hash") or "").strip()
    if ws_hash and "/" not in ws_hash:
        names.append(ws_hash)
    try:
        names.extend(sorted(os.listdir(root)))
    except OSError:
        pass
    for name in names:
        path = os.path.join(root, name, chat_id, "store.db")
        if os.path.exists(path):
            return path
    return ""


def _cursor_blob_hits(store, needle):
    """Chat-store rows containing this line. -1 when the store is unreadable."""
    if not store or not needle:
        return -1
    import sqlite3
    from urllib.request import pathname2url
    try:
        con = sqlite3.connect("file:%s?mode=ro" % pathname2url(store), uri=True, timeout=1.0)
    except (sqlite3.Error, OSError, ValueError):
        return -1
    try:
        row = con.execute("select count(*) from blobs where instr(data, ?) > 0",
                          (needle.encode("utf-8"),)).fetchone()
        return int(row[0]) if row else 0
    except (sqlite3.Error, OSError, ValueError):
        return -1
    finally:
        try:
            con.close()
        except sqlite3.Error:
            pass


def _cursor_turn_started(ep, needle, before, wait_secs=None):
    """True only once the injected line shows up as a NEW chat-store row.

    `before` is the count taken before typing, so a retry of an already
    delivered line cannot be read as a fresh turn.
    """
    if before is None or before < 0:
        return False
    end = time.time() + max(0.0, CURSOR_EVIDENCE_SECS if wait_secs is None else wait_secs)
    store = _cursor_chat_store(ep)
    while True:
        if _cursor_blob_hits(store, needle) > before:
            return True
        if time.time() >= end:
            return False
        time.sleep(0.25)


def _cursor_single_line(text):
    """send-keys types literally: an embedded newline submits half a prompt."""
    return " ".join(str(text or "").split())


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


def _cursor_supervised_reason(persist=""):
    if persist:
        return ("supervised (%s is not a managed `agent persist` session on "
                "tmux -L %s; `agent -p --resume` is a new run, not pause-resume)"
                % (persist, _cursor_tmux_server()))
    return ("supervised (no managed `agent persist` session and no operator ACP "
            "bridge; `agent -p --resume` is a new run, not pause-resume)")


def _probe_cursor():
    if not _which("agent") and not _which("cursor-agent"):
        return {"ok": False, "reason": "cursor agent CLI not on PATH"}
    r = subprocess.run(["agent", "--help"], capture_output=True, text=True)
    help_text = r.stdout + r.stderr
    if r.returncode != 0 or "--resume" not in help_text:
        return {"ok": False, "reason": "cursor agent --resume unavailable"}
    persist = _cursor_persist_target()
    row = _cursor_managed_session(persist, refresh=True) if persist else None
    if row is not None:
        return {"ok": True, "capabilities": {
            "native_inject": True,
            "transport": ("tmux -L %s send-keys into the managed agent persist session"
                          % _cursor_tmux_server()),
            "persist_session": persist,
            "chat_id": row.get("chat_id", ""),
            "workspace_hash": row.get("workspace_hash", ""),
            "turn_evidence": "injected line appears in the cursor chat store",
        }}
    sock = _cursor_acp_sock()
    if sock and os.path.exists(sock):
        return {"ok": True, "capabilities": {
            "native_inject": True,
            "transport": ("ACP session/load + session/prompt on an operator-provided "
                          "control sock (unverified against a shipped Cursor build)"),
            "control_socket": sock,
            "control_socket_live": True,
        }}
    return {"ok": True, "capabilities": {
        "native_inject": False,
        "transport": _cursor_supervised_reason(persist),
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
        supervised = supervised_harness(provider)
        if supervised:
            return {"ok": True, "capabilities": {
                "native_inject": False, "supervised": True,
                "transport": "supervised: %s" % supervised[1],
            }}
        return {"ok": False, "reason": "no native adapter for %s" % provider}
    return fn()


# Harnesses with a board integration but no live-session injection. Evidence
# lives in docs/wake-recipients.md; the receipt must say supervised, not wake.
SUPERVISED_HARNESSES = {
    "agy": ("Antigravity (agy)",
            "agy 1.2.2 exposes no live-session injection: the binary opens no local "
            "control socket or RPC, `remote-control` is a cloud (WebRTC-signalled) "
            "daemon for the Antigravity app, and `-p/--prompt`, `-i` and "
            "`--conversation <id>` each start a new run. Board mail reaches the seat "
            "through the PreInvocation/Stop hooks (`atm hooks agy`) or a persist "
            "watcher"),
}
SUPERVISED_HARNESSES["antigravity"] = SUPERVISED_HARNESSES["agy"]


def supervised_harness(harness):
    """(name, why) for a harness that is supervised by design, else None."""
    return SUPERVISED_HARNESSES.get((harness or "").strip().lower())


def provider_for_harness(harness):
    if harness in ("claude", "cursor+claude"):
        return "claude"
    if harness in ("codex", "cursor", "remote"):
        return harness
    if harness in ("grok", "grokbots"):
        return "cursor"
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
        supervised = supervised_harness(harness)
        if supervised:
            return {"ok": False, "reason": "%s is a supervised seat: %s"
                    % (supervised[0], supervised[1])}
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
        hosted = bool(thread and _codex_thread_is_loaded(thread))
        live = bool(sock and os.path.exists(sock) and hosted)
        record["mode"] = "native" if live else "supervised"
        record["capabilities"] = {
            "native_inject": live,
            "transport": ("codex app-server turn/start" if live
                          else "codex queue mailbox only (thread not loaded on app-server)"),
        }
    elif provider == "cursor":
        session_id = (os.environ.get("CURSOR_CONVERSATION_ID") or os.environ.get("CURSOR_SESSION_ID") or "").strip()
        if not session_id:
            return {"ok": False, "reason": "CURSOR_CONVERSATION_ID not set"}
        record["session_id"] = session_id
        persist = _cursor_persist_target()
        # A name is not a transport: the session has to be on the managed
        # server and carry cursor-agent's own @cursor_managed tag.
        row = _cursor_managed_session(persist, refresh=True) if persist else None
        sock = _cursor_acp_sock()
        if row is not None:
            record["persist_session"] = persist
            record["workspace_hash"] = row.get("workspace_hash", "")
            chat_id = (row.get("chat_id") or "").strip()
            if chat_id:
                record["chat_id"] = chat_id
            record["mode"] = "native"
            record["capabilities"] = {
                "native_inject": True,
                "transport": ("tmux -L %s send-keys into the managed agent persist session"
                              % _cursor_tmux_server()),
            }
        elif sock and os.path.exists(sock):
            record["socket"] = sock
            record["mode"] = "native"
            record["capabilities"] = {
                "native_inject": True,
                "transport": ("ACP session/load + session/prompt on an operator-provided "
                              "control sock (unverified against a shipped Cursor build)"),
            }
        else:
            record["mode"] = "supervised"
            record["capabilities"] = {
                "native_inject": False,
                "transport": _cursor_supervised_reason(persist),
            }
    committed = commit_endpoint(board, seat, record)
    if not committed.get("ok"):
        return committed
    return {"ok": True, "provider": provider, "mode": record["mode"],
            "lease_id": committed.get("lease_id"),
            "record": committed["record"]}


def wake_payload(fmt_msg, message):
    return "atm board message -- %s\n(see `atm inbox` for the rest)" % fmt_msg(message)


def _claude_user_envelope(text, msg_id=""):
    """Claude Code UDS inbox is JSONL, not a raw text line.

    Verified frame (claude 2.1.263/2.1.266): auth frame, then one JSON object
    carrying msgV/msg_id/type/message/priority. A raw second line is ignored
    after auth, so write-success is not a wake. The receiver dedupes by
    msg_id, which is why a retry must reuse the id it already sent.
    """
    return {
        "msgV": 1,
        "msg_id": str(msg_id or uuid.uuid4()),
        "type": "user",
        "message": {"role": "user", "content": text},
        "priority": "next",
    }


CLAUDE_HELD_RECOVERY = (
    "approve in the recipient session or set crossSessionInbound accept"
)

# peer_message_status values are delivery outcomes, never turn-start evidence.
CLAUDE_PEER_STATUS_LABELS = {
    "held": "held",
    "delivered": "delivered-confirmed",
    "refused": "refused",
    "dropped": "dropped",
    "expired": "expired",
}


def _claude_ack_ok(obj):
    """True only for a generic write-ack. That is delivery, not a wake."""
    if not isinstance(obj, dict):
        return False
    if obj.get("type") == "control" and obj.get("action") == "peer_message_status":
        return False
    if obj.get("ok") is True:
        return True
    if obj.get("type") in ("ack", "ok"):
        return True
    if obj.get("status") in ("delivered", "ok", "accepted"):
        return True
    return False


def _claude_receipt_label(obj):
    """Map a Claude inbox object to a wake receipt.

    Write-acks and peer_message_status are delivery outcomes. woken requires
    verified evidence that the intended message started a recipient turn;
    no Claude inbox frame currently carries that proof, so do not invent one.
    """
    if not isinstance(obj, dict):
        return None
    if obj.get("type") == "control" and obj.get("action") == "peer_message_status":
        status = str(obj.get("status") or "").strip().lower()
        return CLAUDE_PEER_STATUS_LABELS.get(status)
    if _claude_ack_ok(obj):
        return "delivered-confirmed"
    return None


def _recv_json_line(sock, deadline):
    """One JSON line, waiting no longer than `deadline`.

    Always makes one attempt, non-blocking when the deadline has already
    passed, so a receipt that is already sitting in the buffer still counts.
    """
    buf = b""
    while True:
        remaining = deadline - time.time()
        sock.settimeout(max(0.0, remaining))
        try:
            chunk = sock.recv(4096)
        except (socket.timeout, BlockingIOError):
            return None
        except OSError:
            return None
        if not chunk:
            return None
        buf += chunk
        if b"\n" in buf:
            line = buf.split(b"\n", 1)[0].decode("utf-8", "replace").strip()
            if not line:
                return None
            try:
                return json.loads(line)
            except ValueError:
                return None
        if remaining <= 0:
            return None


# Claude Code sends no receipt on the injector socket, so blocking for one
# only stalls every wake. Read whatever already arrived and return. Tests
# raise this to exercise the receipt branch.
CLAUDE_ACK_WAIT_SECS = 0.0


def _poke_claude_wake(ep, text, msg_id=""):
    """Inject a Claude UDS user envelope. Write-ack is not a wake.

    Live Claude Code does not write an ack on the injector socket, so the
    honest receipt is delivered-unconfirmed: the bytes left here, nobody
    confirmed a turn. A generic write-ack, if one ever arrives, is
    delivered-confirmed and must not refresh the heartbeat. Raw text after
    auth is never a wake. Only a failure to connect or to finish the write
    is a connection error worth retrying -- a completed write must never be
    sent twice under a fresh id.
    """
    sock_path = ep.get("socket") or ""
    if not sock_path or not os.path.exists(sock_path):
        return "queued-offline"
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(2)
    wrote = False
    try:
        s.connect(sock_path)
        token = ep.get("token") or ""
        if token:
            s.sendall((json.dumps({"type": "auth", "token": token}) + "\n").encode("utf-8"))
        s.sendall((json.dumps(_claude_user_envelope(text, msg_id)) + "\n").encode("utf-8"))
        wrote = True
        try:
            s.shutdown(socket.SHUT_WR)
        except OSError:
            pass
        ack = _recv_json_line(s, time.time() + CLAUDE_ACK_WAIT_SECS)
        labeled = _claude_receipt_label(ack)
        if labeled:
            return labeled
        return "delivered-unconfirmed"
    except OSError:
        return "queued-offline" if not wrote else "delivered-unconfirmed"
    finally:
        try:
            s.close()
        except OSError:
            pass


def _poke_claude(ep, text, msg_id=""):
    return _poke_claude_wake(ep, text, msg_id)


# Only these mean "the bytes never left"; anything else is a delivery outcome.
CLAUDE_RETRYABLE = ("queued-offline",)


def _poke_claude_until(ep, text, attempts=None):
    """Retry one inject on connection errors only, reusing the same msg_id.

    The receiver dedupes by msg_id, so resending the id it may already hold is
    safe; minting a new one per attempt is what would double-post a turn.
    """
    attempts = NATIVE_POKE_ATTEMPTS if attempts is None else attempts
    msg_id = str(uuid.uuid4())
    label = "queued-offline"
    for _ in range(max(1, int(attempts))):
        poked = _poke_claude(ep, text, msg_id)
        # Older mocks answer True/False rather than a label.
        if poked is True:
            return "woken"
        if poked is False:
            return "inject-refused"
        label = str(poked or "queued-offline")
        if label not in CLAUDE_RETRYABLE:
            return label
    return label


_WS_GUID = "258EAFA5-E914-47DA-95AA-C5AB0DC85B11"


def _ws_mask_frame(payload):
    mask = os.urandom(4)
    n = len(payload)
    if n < 126:
        header = bytes([0x81, 0x80 | n])
    elif n < 65536:
        header = bytes([0x81, 0x80 | 126]) + struct.pack(">H", n)
    else:
        header = bytes([0x81, 0x80 | 127]) + struct.pack(">Q", n)
    return header + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload))


def _ws_recv_json(sock, buf, deadline):
    """Read one WebSocket text frame. Returns (obj_or_None, remaining_buf)."""
    while time.time() < deadline:
        if len(buf) >= 2:
            opcode = buf[0] & 0x0F
            masked = bool(buf[1] & 0x80)
            ncode = buf[1] & 0x7F
            header_ok = (
                ncode < 126
                or (ncode == 126 and len(buf) >= 4)
                or (ncode == 127 and len(buf) >= 10)
            )
            if header_ok:
                n = ncode
                off = 2
                if n == 126:
                    n = struct.unpack(">H", buf[2:4])[0]
                    off = 4
                elif n == 127:
                    n = struct.unpack(">Q", buf[2:10])[0]
                    off = 10
                mask_len = 4 if masked else 0
                if len(buf) >= off + mask_len + n:
                    payload = buf[off + mask_len:off + mask_len + n]
                    if masked:
                        mask = buf[off:off + 4]
                        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
                    rest = buf[off + mask_len + n:]
                    if opcode == 0x8:
                        return None, rest
                    if opcode == 0x1:
                        return json.loads(payload.decode("utf-8")), rest
                    buf = rest
                    continue
        sock.settimeout(max(0.05, deadline - time.time()))
        try:
            chunk = sock.recv(65536)
        except (OSError, socket.timeout):
            break
        if not chunk:
            break
        buf += chunk
    return None, buf


# Distinguish "never left the socket" from "sent, no reply".
RPC_REFUSED_BEFORE_SEND = -32001


def _codex_ws_rpc_ex(method, params, timeout=5):
    """JSON-RPC after HTTP Upgrade. Returns (kind, resp).

    kind is ok | error | timeout | refused. refused means the method was
    never sent (no sock, connect/handshake/initialize failed). timeout
    means the method left the socket and no matching reply arrived.
    """
    sock_path = _codex_control_sock()
    if not sock_path or not os.path.exists(sock_path):
        return "refused", None
    deadline = time.time() + max(1, float(timeout))
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sent = False
    try:
        s.settimeout(max(0.2, deadline - time.time()))
        s.connect(sock_path)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        s.sendall((
            "GET / HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\n"
            "Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n" % key
        ).encode("ascii"))
        buf = b""
        while b"\r\n\r\n" not in buf and time.time() < deadline:
            s.settimeout(max(0.05, deadline - time.time()))
            chunk = s.recv(4096)
            if not chunk:
                return "refused", None
            buf += chunk
        if b"\r\n\r\n" not in buf:
            return "refused", None
        header, buf = buf.split(b"\r\n\r\n", 1)
        first = header.split(b"\r\n", 1)[0].decode("ascii", "replace")
        if "101" not in first:
            return "refused", None

        def send_obj(obj):
            s.sendall(_ws_mask_frame(json.dumps(obj, separators=(",", ":")).encode("utf-8")))

        send_obj({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                  "params": {"clientInfo": {"name": "atman-tickets", "version": "0"}}})
        init_msg, buf = _ws_recv_json(s, buf, deadline)
        while init_msg is not None and init_msg.get("id") != 0 and time.time() < deadline:
            init_msg, buf = _ws_recv_json(s, buf, deadline)
        if not init_msg or init_msg.get("id") != 0 or init_msg.get("error"):
            return "refused", None
        send_obj({"jsonrpc": "2.0", "method": "initialized"})
        send_obj({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})
        sent = True
        while time.time() < deadline:
            msg, buf = _ws_recv_json(s, buf, deadline)
            if msg is None:
                return "timeout", None
            if msg.get("id") == 1:
                if msg.get("error"):
                    return "error", msg
                return "ok", msg
        return "timeout", None
    except (OSError, ValueError, socket.timeout):
        return ("timeout" if sent else "refused"), None
    finally:
        try:
            s.close()
        except OSError:
            pass


def _codex_ws_rpc(method, params, timeout=5):
    """JSON-RPC after HTTP Upgrade on the Codex app-server Unix socket."""
    kind, resp = _codex_ws_rpc_ex(method, params, timeout=timeout)
    if kind in ("ok", "error"):
        return resp
    return None


def _codex_app_server_rpc(method, params, timeout=5):
    """JSON-RPC one-shot on the control socket. WebSocket handshake required.

    Connection-refused / never-sent is an explicit error (code
    RPC_REFUSED_BEFORE_SEND). Timeout after send is None.
    """
    kind, resp = _codex_ws_rpc_ex(method, params, timeout=timeout)
    if kind in ("ok", "error"):
        return resp
    if kind == "refused":
        return {"error": {
            "code": RPC_REFUSED_BEFORE_SEND,
            "message": "connection refused before send",
        }}
    return None


def _collect_loaded_thread_ids(items, ids):
    for item in items or []:
        if isinstance(item, str) and item.strip():
            ids.append(item.strip())
        elif isinstance(item, dict):
            for key in ("id", "threadId", "thread_id"):
                val = item.get(key)
                if isinstance(val, str) and val.strip():
                    ids.append(val.strip())
                    break


def _thread_ids_from_loaded_result(resp):
    """Thread ids from thread/loaded/list.

    Production (codex-cli 0.154+): ``result: {data: ["<id>", ...], nextCursor}``.
    Walking only ``id``/``threadId`` keys misses that shape, so loaded checks
    were always false and wake never called turn/start.
    """
    ids = []
    result = (resp or {}).get("result")
    if isinstance(result, list):
        _collect_loaded_thread_ids(result, ids)
        return ids
    if not isinstance(result, dict):
        return ids
    data = result.get("data")
    if isinstance(data, list):
        _collect_loaded_thread_ids(data, ids)
        return ids
    # compat: older fixtures nested {id: ...} anywhere under result
    def walk(obj):
        if isinstance(obj, dict):
            for key, val in obj.items():
                if key in ("id", "threadId", "thread_id") and isinstance(val, str):
                    ids.append(val)
                else:
                    walk(val)
        elif isinstance(obj, list):
            _collect_loaded_thread_ids(obj, ids)
            for item in obj:
                if isinstance(item, (dict, list)):
                    walk(item)

    walk(result)
    return ids


def _loaded_next_cursor(resp):
    result = (resp or {}).get("result")
    if not isinstance(result, dict):
        return ""
    return (result.get("nextCursor") or result.get("next_cursor") or "").strip()


def _codex_loaded_thread_ids():
    """All loaded thread ids, following nextCursor."""
    ids = []
    cursor = ""
    seen = set()
    while True:
        params = {"cursor": cursor} if cursor else {}
        resp = _codex_app_server_rpc("thread/loaded/list", params)
        if not resp or resp.get("error"):
            break
        ids.extend(_thread_ids_from_loaded_result(resp))
        nxt = _loaded_next_cursor(resp)
        if not nxt or nxt in seen:
            break
        seen.add(nxt)
        cursor = nxt
    return ids


def _codex_thread_is_loaded(thread):
    thread = (thread or "").strip()
    if not thread:
        return False
    return thread in _codex_loaded_thread_ids()


_ACTIVE_TURN = ("inprogress", "in_progress", "active", "running", "busy")


def _result_shows_active_turn(result):
    if not isinstance(result, dict):
        return False
    for key in ("status", "state"):
        if str(result.get(key) or "").lower().replace("-", "_") in _ACTIVE_TURN:
            return True
    turn = result.get("turn") or result.get("activeTurn") or result.get("active_turn")
    if isinstance(turn, dict):
        return _result_shows_active_turn(turn)
    return False


def _codex_thread_is_busy(thread):
    """True unless thread/read succeeds and proves no active turn.

    Unknown state (read error, timeout, missing result) is treated as busy:
    turn/start on an unproven-idle thread can steer an active turn.
    """
    thread = (thread or "").strip()
    if not thread:
        return True
    resp = _codex_app_server_rpc("thread/read", {"threadId": thread})
    if not resp or resp.get("error"):
        return True
    return _result_shows_active_turn(resp.get("result") or {})


def _codex_queue_start(thread):
    """Ask the managed app-server to start the next queued turn (pause→resume)."""
    resp = _codex_app_server_rpc("thread/queue/start", {"threadId": thread})
    if not resp or resp.get("error"):
        return False
    return True


def _classify_turn_start(resp):
    """ok | error | timeout | refused. True/False kept for older mocks."""
    if resp is True or resp == "ok":
        return "ok"
    if resp is False or resp == "error":
        return "error"
    if resp == "timeout":
        return "timeout"
    if resp == "refused":
        return "refused"
    if resp is None:
        return "timeout"
    if not isinstance(resp, dict):
        return "timeout"
    err = resp.get("error")
    if not err:
        return "ok"
    if isinstance(err, dict):
        code = err.get("code")
        msg = str(err.get("message") or "").lower()
        if code == RPC_REFUSED_BEFORE_SEND or "connection refused before send" in msg:
            return "refused"
    return "error"


def _codex_turn_start(thread, text, message_id=""):
    """Direct turn inject via app-server when the thread is loaded and idle.

    Returns ok | error | timeout | refused (True still means ok for old mocks).
    """
    params = {
        "threadId": thread,
        "input": [{"type": "text", "text": text}],
    }
    if message_id:
        params["clientUserMessageId"] = str(message_id)
    resp = _codex_app_server_rpc("turn/start", params)
    return _classify_turn_start(resp)


def _codex_queue_cli(thread, text):
    cmd = ["codex", "queue", "--thread", thread, "--message", text]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


CODEX_OFFLINE_HINT = "run the thread in terminal Codex to enable native wake"


def _poke_codex_wake(ep, text, message_id=""):
    """Wake a Codex thread.

    Returns woken | queued-busy | queued-offline | delivery-unknown | refused.

    Idle + loaded in the reachable daemon: turn/start only (do not also queue;
    the daemon would drain the queue and double-deliver). Busy or unknown
    busy-state: queue and return queued-busy (never turn/start — that steers).
    Not loaded (VS Code private app-server, etc.): queue and return
    queued-offline with a recovery hint. turn/start timeout / no response:
    delivery-unknown with no queue fallback (the turn may have started).
    Retry turn/start only on connection-refused before send. Sqlite-only
    success is not a native wake.
    """
    thread = (ep.get("thread") or "").strip()
    if not thread:
        return "refused"
    loaded = _codex_thread_is_loaded(thread)
    busy = loaded and _codex_thread_is_busy(thread)
    if loaded and not busy:
        for _ in range(max(1, int(NATIVE_POKE_ATTEMPTS))):
            outcome = _classify_turn_start(
                _codex_turn_start(thread, text, message_id=message_id))
            if outcome == "ok":
                return "woken"
            if outcome == "timeout":
                return "delivery-unknown"
            if outcome == "refused":
                continue
            break
        if _codex_queue_cli(thread, text):
            return "queued-offline"
        return "refused"
    if not _codex_queue_cli(thread, text):
        return "refused"
    if busy:
        return "queued-busy"
    return "queued-offline"


def _poke_codex(ep, text):
    """Bool wrapper for older call sites/tests: True only on live wake."""
    return _poke_codex_wake(ep, text) == "woken"


def _poke_cursor(ep, text):
    """Type one line into the managed persist pane. Never `agent -p --resume`.

    Keys go to the cursor-agent tmux server only, and only to a session that
    server reports as managed, so the board can never type its mail into an
    unrelated tmux session that happens to share the name.
    """
    session = (ep.get("persist_session") or "").strip()
    if _cursor_managed_session(session) is None:
        return False
    target = session + ":"
    typed = _cursor_tmux(["send-keys", "-t", target, "-l", _cursor_single_line(text)])
    if typed is None or typed.returncode != 0:
        return False
    enter = _cursor_tmux(["send-keys", "-t", target, "Enter"])
    return enter is not None and enter.returncode == 0


def _cursor_acp_rpc(method, params, sock=None, timeout=5):
    """JSON-RPC one-shot to a live ACP control sock. None if unavailable."""
    sock = (sock or _cursor_acp_sock() or "").strip()
    if not sock or not os.path.exists(sock):
        return None
    rid = uuid.uuid4().hex
    req = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(sock)
        s.sendall((json.dumps(req) + "\n").encode("utf-8"))
        buf = b""
        deadline = time.time() + timeout
        while time.time() < deadline:
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                # ACP is bidirectional: session/update notifications and
                # permission requests share the stream, so only an id match is
                # this call's answer.
                if isinstance(msg, dict) and msg.get("id") == rid:
                    return msg
            chunk = s.recv(4096)
            if not chunk:
                return None
            buf += chunk
        return None
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
    """Receipt follows evidence: `woken` needs the line to become a turn.

    send-keys exits 0 as soon as tmux writes to the pane's tty. That says
    nothing about what the agent did with it -- a modal, a dropped keystroke or
    a dead pane all still exit 0 -- so the label is `delivered-unconfirmed`
    until the injected line shows up in the chat store as a new message.
    """
    line = _cursor_single_line(text)
    persist = (ep.get("persist_session") or "").strip()
    if persist and _cursor_managed_session(persist, refresh=True) is not None:
        before = _cursor_blob_hits(_cursor_chat_store(ep), line)
        if not _poke_until(_poke_cursor, ep, line):
            return "refused (persist session %s did not take keys)" % persist
        if _cursor_turn_started(ep, line, before):
            return "woken"
        return "delivered-unconfirmed"
    sock = _cursor_acp_sock()
    if sock and os.path.exists(sock) and _poke_until(_poke_cursor_acp, ep, line):
        return "woken"
    return _cursor_supervised_reason(persist)


def native_wake_online(board, seat):
    """True when a native poke can resume this seat now.

    Shared by `atm who` and `atm msg` so a Codex thread record or a
    PID-less session is not reported reachable=yes while wake returns
    queued-offline. Persistent lifecycle is not a transport.
    """
    ep, _ = live_endpoint(board, seat)
    if ep is None or ep.get("mode") != "native":
        return False
    provider = ep.get("provider") or ""
    if provider == "codex":
        return _codex_thread_is_loaded((ep.get("thread") or "").strip())
    if provider == "cursor":
        persist = (ep.get("persist_session") or "").strip()
        acp = (ep.get("socket") or "").strip()
        return bool(persist) or bool(acp and os.path.exists(acp))
    if provider == "claude":
        sock = (ep.get("socket") or "").strip()
        return bool(sock and os.path.exists(sock))
    return True


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
        unlink_refused = label == "inject-refused"
        if unlink_refused:
            label = "refused"
        delivered = (
            label in (
                "woken", "queued-offline", "queued-busy",
                "delivered-unconfirmed", "delivery-unknown",
                "delivered-confirmed", "held", "dropped", "expired", "refused",
            ) or str(label).startswith("queued-offline")
        ) and not unlink_refused
        if mid and delivered:
            ep["last_delivery_id"] = mid
            ep["last_delivery_status"] = label
        if ok:
            ep["heartbeat_epoch"] = time.time()
            ep["heartbeat_at"] = ep.get("at") or ""
            write_endpoint(board, seat, ep)
            return label
        if not delivered:
            ep["last_attempt_id"] = mid
            ep["last_attempt_status"] = label
        write_endpoint(board, seat, ep)
        if unlink_refused or (label == "refused" and not delivered):
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
    if not expected and supervised_harness(harness):
        # Never "no live endpoint": the seat has no injection path by design,
        # and the receipt has to say which delivery it actually got.
        return ("supervised (%s has no live-session injection; mail waits for its "
                "next hook or persist run)" % (harness or "").strip().lower())
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
        label = _poke_claude_until(ep, text)
        ok = label == "woken"
    elif provider == "cursor":
        label = _cursor_pause_resume(ep, text)
        ok = label == "woken"
    else:
        label = _poke_codex_wake(ep, text, message_id=mid)
        ok = label == "woken"
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
        return _codex_thread_is_loaded((ep.get("thread") or "").strip())
    if provider == "cursor":
        # Same proof as live_endpoint: the managed session has to still exist.
        persist = (ep.get("persist_session") or "").strip()
        acp = (ep.get("socket") or "").strip()
        if persist and _cursor_managed_session(persist) is not None:
            return True
        if acp and os.path.exists(acp):
            return True
        return False
    if provider == "claude":
        sock = (ep.get("socket") or "").strip()
        return bool(sock and os.path.exists(sock))
    return True


def public_adapter_state(board, seat, harness, adapter_online, wake_pending):
    """UI/API fields: provider, mode, online, last receipt hints."""
    harness_name = (harness or "").strip()
    # Empty / unknown is not a provider. "custom" is a real --harness value.
    if not harness_name or harness_name == "unknown":
        provider = "unknown"
    else:
        provider = provider_for_harness(harness_name) or "custom"
    mode = adapter_mode_for(board, seat, harness)
    ep, _ = live_endpoint(board, seat)
    native_online = native_wake_online(board, seat)
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
