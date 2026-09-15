"""T-818 remote control: authenticated operator command with evidence-only states.

Frozen contract: Steer docs/product/atman-remote-control-contract.md (T-830).

An operator on a separate client (phone, laptop, curl) authenticates with a
board-scoped bearer token, selects a stable role, sees the runtime that role
resolves to today plus the permissions the token grants, sends a bounded task,
and watches ``sent -> queued -> delivered -> acknowledged -> working -> review``
advance only when the board holds evidence for each step.

This module owns policy and records. It does not read provider tokens, run a
shell, or touch ``.tickets`` files other than its own ``remote/`` directory
plus the shared message log through ``tickets.post_message``. Everything that
needs the board (posting, waking, reading evidence) goes through the ``tk``
handle -- the ``tickets`` module -- so that the wake path is exactly the one
``tickets msg`` uses and no second delivery mechanism can drift from it.

Secrets: a token exists in clear text once, in the response that mints it.
At rest only its SHA-256 lives in ``remote/state.json`` (mode 0600). Audit
rows carry an idempotency-key digest and a content digest, never bodies.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone

SCHEMA = 1

# Capabilities. Base is implicit on every token; grants are explicit; step-up
# grants are short-lived; denied is denied for every remote caller in V1.
BASE_GRANTS = ("read", "message")
TASK_GRANTS = ("dispatch", "retry")
STEP_UP_GRANTS = ("reassign", "revoke", "cancel")
REVIEWER_GRANTS = ("review",)
GRANTABLE = TASK_GRANTS + STEP_UP_GRANTS + REVIEWER_GRANTS
DENIED_V1 = ("merge", "destructive", "shell", "secrets")

TOKEN_PREFIX = "rop_"
TOKEN_DEFAULT_SECONDS = 12 * 3600
TOKEN_MAX_SECONDS = 30 * 24 * 3600
STEP_UP_DEFAULT_SECONDS = 15 * 60
STEP_UP_MAX_SECONDS = 3600

DISPATCH_DEFAULT_SECONDS = 30 * 60
DISPATCH_MIN_SECONDS = 60
DISPATCH_MAX_SECONDS = 24 * 3600

OBJECTIVE_MAX_CHARS = 4000
TEXT_MAX_CHARS = 8000
REASON_MAX_CHARS = 500
IDEMPOTENCY_KEEP = 500
BODY_MAX_BYTES = 65536

STATES = ("sent", "queued", "delivered", "acknowledged", "working", "review",
          "failed", "expired", "canceled")
TERMINAL = ("failed", "expired", "canceled")
RECEIPT_KINDS = ("ack", "working", "submitted", "failed", "cancel-ack", "canceled")

_SEAT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,100}\Z")
_ID_RE = re.compile(r"rc_[0-9a-f]{12}\Z")
_TOKEN_ID_RE = re.compile(r"rtk_[0-9a-f]{8}\Z")
_DURATION_RE = re.compile(r"(\d+)\s*([smhd]?)\Z")


class RemoteError(Exception):
    """A refusal the caller sees: HTTP status, stable code, human detail."""

    def __init__(self, status, code, detail="", recovery="", **extra):
        Exception.__init__(self, detail or code)
        self.status = int(status)
        self.code = code
        self.detail = detail
        self.recovery = recovery
        self.extra = extra

    def body(self):
        out = {"error": self.code, "detail": self.detail}
        if self.recovery:
            out["recovery"] = self.recovery
        out.update(self.extra)
        return out


# ---------------------------------------------------------------- time helpers

def utcnow():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def in_seconds(seconds, base=None):
    start = datetime.now(timezone.utc) if base is None else parse_ts(base)
    return (start + timedelta(seconds=int(seconds))).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(stamp):
    return datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def parse_duration(text, default):
    """'900', '15m', '2h', '1d' -> seconds. Empty -> default."""
    raw = str(text if text is not None else "").strip().lower()
    if not raw:
        return int(default)
    m = _DURATION_RE.match(raw)
    if not m:
        raise RemoteError(400, "bad_duration", "duration must look like 900, 15m, 2h or 1d")
    n, unit = int(m.group(1)), m.group(2) or "s"
    return n * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]


def digest(*parts):
    h = hashlib.sha256()
    for p in parts:
        h.update(str(p).encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


def content_digest(text):
    return {"digest": hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:24],
            "length": len(text or "")}


def _looks_secret(text):
    try:
        from auth_v2_contract import _EMBEDDED_TOKEN_RE, _looks_secret_value
    except ImportError:  # contract module absent: keep the conservative check
        return bool(re.search(r"(?i)(sk|ghp|xox[abp]|AKIA|rop|tbk)_[A-Za-z0-9]{16,}", text or ""))
    if not text:
        return False
    if _EMBEDDED_TOKEN_RE.search(text):
        return True
    for piece in str(text).split():
        if piece.startswith(TOKEN_PREFIX) and len(piece) > 20:
            return True
        if _looks_secret_value(piece):
            return True
    return False


# ---------------------------------------------------------------- the store

class RemoteControl:
    """Board-scoped remote command policy, records and evidence.

    ``tk`` is the tickets module (injected so the wake path, the message log
    and the board readers are the live ones, not copies).
    """

    def __init__(self, board, tk, clock=None):
        self.board = os.path.abspath(board)
        self.tk = tk
        self.clock = clock or utcnow
        self.board_hash = hashlib.sha256(self.board.encode("utf-8")).hexdigest()[:16]

    # ---- paths and locking -------------------------------------------------

    @property
    def dir(self):
        return os.path.join(self.board, "remote")

    def _ensure_dir(self):
        os.makedirs(self.dir, exist_ok=True)
        try:
            os.chmod(self.dir, 0o700)
        except OSError:
            pass

    def _state_path(self):
        return os.path.join(self.dir, "state.json")

    def _dispatch_path(self):
        return os.path.join(self.dir, "dispatches.json")

    def audit_path(self):
        return os.path.join(self.dir, "audit.jsonl")

    class _Lock:
        def __init__(self, path):
            self.path = path
            self.fd = None

        def __enter__(self):
            try:
                import fcntl
            except ImportError:
                return self
            self.fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
            fcntl.flock(self.fd, fcntl.LOCK_EX)
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

    def _lock(self):
        self._ensure_dir()
        return RemoteControl._Lock(os.path.join(self.dir, ".lock"))

    def _read_json(self, path, default):
        try:
            with open(path) as f:
                data = json.load(f)
            return data if isinstance(data, dict) else dict(default)
        except (IOError, ValueError):
            return dict(default)

    def _write_json(self, path, data, mode=0o600):
        self._ensure_dir()
        tmp = "%s.tmp.%d.%s" % (path, os.getpid(), uuid.uuid4().hex[:6])
        fd = os.open(tmp, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, mode)
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        try:
            os.chmod(path, mode)
        except OSError:
            pass

    def _state(self):
        st = self._read_json(self._state_path(), {})
        st.setdefault("schema", SCHEMA)
        st.setdefault("tokens", {})
        st.setdefault("revoked_seats", {})
        st.setdefault("idempotency", {})
        return st

    def _save_state(self, st):
        self._write_json(self._state_path(), st, mode=0o600)

    def _dispatches(self):
        d = self._read_json(self._dispatch_path(), {})
        d.setdefault("schema", SCHEMA)
        d.setdefault("items", {})
        return d

    def _save_dispatches(self, d):
        self._write_json(self._dispatch_path(), d, mode=0o600)

    def _audit(self, action, actor, outcome, **fields):
        """Append one receipt. Never a body, never a credential."""
        row = {"at": self.clock(), "board": self.board_hash, "action": action,
               "actor": actor, "outcome": outcome}
        for k, v in fields.items():
            if v not in (None, "", {}, []):
                row[k] = v
        self._ensure_dir()
        line = json.dumps(row, sort_keys=True) + "\n"
        fd = os.open(self.audit_path(), os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)
        return row

    def audit(self, limit=50):
        out = []
        try:
            with open(self.audit_path()) as f:
                for ln in f:
                    ln = ln.strip()
                    if ln:
                        try:
                            out.append(json.loads(ln))
                        except ValueError:
                            continue
        except IOError:
            return []
        return out[-int(limit):]

    # ---- tokens ------------------------------------------------------------

    @staticmethod
    def _actor_of(token):
        return {"operator": token.get("operator", ""), "token_id": token.get("id", ""),
                "credential_class": "operator"}

    def mint_token(self, operator, grants=(), step_up=(), expires_in=None, targets=(),
                   label="", by="local"):
        operator = str(operator or "").strip()
        if not operator or not _SEAT_RE.match(operator):
            raise RemoteError(400, "bad_operator", "operator name is required (letters, digits, . _ -)")
        wanted = [g.strip().lower() for g in grants if g and g.strip()]
        step = [g.strip().lower() for g in step_up if g and g.strip()]
        for g in wanted + step:
            if g in DENIED_V1:
                self._audit("token.mint", {"operator": operator, "credential_class": "local"},
                            "denied", grant=g, reason="denied_in_v1")
                raise RemoteError(403, "denied_in_v1",
                                  "%s is denied for every remote caller in V1" % g,
                                  recovery="use the reviewed local gate on the host")
            if g not in GRANTABLE:
                raise RemoteError(400, "unknown_grant", "unknown grant %r" % g,
                                  granted=list(GRANTABLE))
        for g in wanted:
            if g in STEP_UP_GRANTS:
                raise RemoteError(400, "step_up_required",
                                  "%s needs --step-up (short-lived, explicit)" % g)
        seconds = parse_duration(expires_in, TOKEN_DEFAULT_SECONDS)
        if seconds <= 0 or seconds > TOKEN_MAX_SECONDS:
            raise RemoteError(400, "bad_duration", "token expiry must be 1s..30d")
        step_seconds = min(STEP_UP_DEFAULT_SECONDS, seconds)
        now = self.clock()
        raw = TOKEN_PREFIX + "".join(secrets.choice(
            "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789") for _ in range(40))
        rec = {
            "id": "rtk_" + uuid.uuid4().hex[:8],
            "operator": operator,
            "label": str(label or "")[:80],
            "grants": sorted(set(wanted)),
            "step_up": {g: in_seconds(step_seconds, now) for g in sorted(set(step))},
            "targets": sorted({str(t).strip() for t in targets if str(t).strip()}),
            "created_at": now,
            "expires_at": in_seconds(seconds, now),
            "revoked_at": "",
            "created_by": by,
        }
        with self._lock():
            st = self._state()
            st["tokens"][digest(raw)] = rec
            self._save_state(st)
        self._audit("token.mint", {"operator": operator, "credential_class": "local", "by": by},
                    "ok", token_id=rec["id"], grants=rec["grants"],
                    step_up=sorted(rec["step_up"]), targets=rec["targets"],
                    requested_expiry=rec["expires_at"])
        return raw, self.public_token(rec)

    def public_token(self, rec):
        out = dict(rec)
        out["revoked"] = bool(rec.get("revoked_at"))
        out["expired"] = rec.get("expires_at", "") <= self.clock()
        return out

    def list_tokens(self):
        return [self.public_token(r) for r in self._state()["tokens"].values()]

    def authenticate(self, raw):
        raw = str(raw or "").strip()
        if not raw:
            raise RemoteError(401, "auth_missing", "Authorization: Bearer <token> is required")
        key = digest(raw)
        st = self._state()
        rec = None
        for k, v in st["tokens"].items():
            if hmac.compare_digest(k, key):
                rec = v
                break
        if rec is None:
            self._audit("auth", {"credential_class": "operator"}, "denied", reason="auth_invalid")
            raise RemoteError(401, "auth_invalid", "unknown token")
        if rec.get("revoked_at"):
            self._audit("auth", self._actor_of(rec), "denied", reason="auth_revoked")
            raise RemoteError(401, "auth_revoked", "token was revoked at %s" % rec["revoked_at"],
                              recovery="mint a new token on the host: tickets rc mint")
        if rec.get("expires_at", "") <= self.clock():
            self._audit("auth", self._actor_of(rec), "denied", reason="auth_expired")
            raise RemoteError(401, "auth_expired", "token expired at %s" % rec["expires_at"],
                              recovery="mint a new token on the host: tickets rc mint")
        return rec

    def revoke_token(self, token_id, actor=None, by="local"):
        with self._lock():
            st = self._state()
            hit = None
            for rec in st["tokens"].values():
                if rec.get("id") == token_id:
                    hit = rec
                    break
            if hit is None:
                raise RemoteError(404, "token_not_found", "no token %s" % token_id)
            if not hit.get("revoked_at"):
                hit["revoked_at"] = self.clock()
            self._save_state(st)
        self._audit("token.revoke", actor or {"credential_class": "local", "by": by}, "ok",
                    token_id=token_id)
        return self.public_token(hit)

    def permissions_for(self, token):
        now = self.clock()
        step = {g: exp for g, exp in (token.get("step_up") or {}).items() if exp > now}
        return {
            "granted": list(BASE_GRANTS) + list(token.get("grants") or []),
            "step_up": step,
            "denied": list(DENIED_V1),
            "targets": list(token.get("targets") or []),
            "expires_at": token.get("expires_at", ""),
        }

    def _require(self, token, grant, target=None):
        """Raise 403 with an audited, visible denial unless the token holds the grant."""
        perms = self.permissions_for(token)
        held = grant in perms["granted"] or grant in perms["step_up"]
        if grant in DENIED_V1:
            held = False
        if not held:
            self._audit("permission", self._actor_of(token), "denied", grant=grant,
                        target=target or "")
            if grant in DENIED_V1:
                raise RemoteError(403, "denied_in_v1", "%s is denied remotely in V1" % grant,
                                  recovery="use the reviewed local gate on the host")
            if grant in STEP_UP_GRANTS:
                raise RemoteError(403, "step_up_required",
                                  "%s needs a live step-up grant on this token" % grant,
                                  recovery="tickets rc mint --step-up %s (expires within %ds)"
                                  % (grant, STEP_UP_MAX_SECONDS), grant=grant)
            raise RemoteError(403, "forbidden_grant", "token lacks the %s grant" % grant,
                              recovery="tickets rc mint --grant %s" % grant, grant=grant)
        targets = perms["targets"]
        if targets and target and target not in targets:
            self._audit("permission", self._actor_of(token), "denied", grant=grant,
                        target=target, reason="target_out_of_scope")
            raise RemoteError(403, "target_out_of_scope",
                              "token is scoped to %s" % ", ".join(targets), grant=grant)

    # ---- idempotency -------------------------------------------------------

    def _idempotent(self, token, key, action, payload, fn):
        key = str(key or "").strip()
        if not key:
            raise RemoteError(400, "idempotency_key_required",
                              "every mutation needs Idempotency-Key (or idempotency_key)")
        if len(key) > 200:
            raise RemoteError(400, "idempotency_key_too_long", "key must be <= 200 chars")
        kd = digest(token.get("id", ""), self.board_hash, key)
        fp = digest(action, json.dumps(payload, sort_keys=True, separators=(",", ":")))
        with self._lock():
            st = self._state()
            prior = st["idempotency"].get(kd)
        if prior:
            if hmac.compare_digest(prior.get("fingerprint", ""), fp):
                return prior["status"], prior["body"], True
            self._audit(action, self._actor_of(token), "denied", reason="idempotency_conflict",
                        idempotency_digest=kd[:24])
            raise RemoteError(409, "idempotency_conflict",
                              "this key was already used for a different request")
        status, body = fn(kd[:24])
        with self._lock():
            st = self._state()
            st["idempotency"][kd] = {"fingerprint": fp, "status": status, "body": body,
                                     "at": self.clock()}
            if len(st["idempotency"]) > IDEMPOTENCY_KEEP:
                for old in sorted(st["idempotency"],
                                  key=lambda k: st["idempotency"][k].get("at", ""))[
                        :len(st["idempotency"]) - IDEMPOTENCY_KEEP]:
                    st["idempotency"].pop(old, None)
            self._save_state(st)
        return status, body, False

    # ---- role -> runtime ---------------------------------------------------

    def _known_seats(self):
        tk = self.tk
        seats = set()
        for rec in tk._safe(lambda: tk.load_agents(self.board), []) or []:
            if rec.get("owner"):
                seats.add(rec["owner"])
        seats.update((tk._safe(lambda: tk.load_workforce(self.board), {}) or {}).keys())
        return seats

    def roles(self):
        """Every name an operator may select, with what it resolves to today."""
        tk = self.tk
        out = []
        seen = set()
        aliases = tk._safe(lambda: tk.load_aliases(self.board), {}) or {}
        for alias, seat in sorted(aliases.items()):
            out.append({"role": alias, "source": "alias", "seat": seat})
            seen.add(alias)
        coord = self._coordination_roles()
        for name, rec in sorted(coord.items()):
            out.append({"role": name, "source": "coordination-role", "seat": rec.get("holder") or ""})
            seen.add(name)
        master = tk._safe(lambda: tk.current_master(self.board), {}) or {}
        for key in ("owner", "cos"):
            if master.get(key):
                name = "master" if key == "owner" else "cos"
                if name not in seen:
                    out.append({"role": name, "source": "master", "seat": master[key]})
                    seen.add(name)
        roles = tk._safe(lambda: tk.load_roles(self.board), {}) or {}
        by_role = {}
        for seat, names in roles.items():
            if seat not in self._known_seats():
                continue
            for n in names or []:
                by_role.setdefault(n, []).append(seat)
        for n, seats in sorted(by_role.items()):
            if n in seen:
                continue
            out.append({"role": n, "source": "workforce-role",
                        "seat": seats[0] if len(seats) == 1 else "",
                        "candidates": sorted(seats)})
        return out

    def _coordination_roles(self):
        path = os.path.join(self.board, "coordination", "state.json")
        try:
            with open(path) as f:
                st = json.load(f)
            roles = st.get("roles") if isinstance(st, dict) else {}
            return roles if isinstance(roles, dict) else {}
        except (IOError, ValueError):
            return {}

    def resolve_role(self, role):
        """Stable name -> the seat it means today. Never a hardcoded id."""
        tk = self.tk
        role = str(role or "").strip()
        if not role or not _SEAT_RE.match(role):
            raise RemoteError(400, "bad_role", "role must be a seat, alias, durable role, master or cos")
        key = role.lower()
        known = self._known_seats()
        res = None
        if role in known:
            res = {"role": role, "seat": role, "source": "seat"}
        if res is None:
            aliases = tk._safe(lambda: tk.load_aliases(self.board), {}) or {}
            if aliases.get(key):
                res = {"role": role, "seat": aliases[key], "source": "alias"}
        if res is None:
            coord = self._coordination_roles()
            hits = {}
            for name, rec in coord.items():
                if name.lower() == key or name.lower().rsplit(".", 1)[-1] == key:
                    if rec.get("holder"):
                        hits[name] = rec["holder"]
            holders = sorted(set(hits.values()))
            if len(holders) == 1:
                res = {"role": role, "seat": holders[0], "source": "coordination-role",
                       "role_ids": sorted(hits)}
            elif len(holders) > 1:
                raise RemoteError(409, "role_ambiguous",
                                  "%s maps to several holders" % role,
                                  candidates=[{"role": n, "seat": s} for n, s in sorted(hits.items())],
                                  recovery="select one seat explicitly")
        if res is None and key in ("master", "cos"):
            master = tk._safe(lambda: tk.current_master(self.board), {}) or {}
            seat = master.get("owner" if key == "master" else "cos")
            if seat:
                res = {"role": role, "seat": seat, "source": "master"}
        if res is None:
            roles = tk._safe(lambda: tk.load_roles(self.board), {}) or {}
            seats = sorted(s for s, names in roles.items()
                           if s in known and key in [str(n).lower() for n in (names or [])])
            if len(seats) == 1:
                res = {"role": role, "seat": seats[0], "source": "workforce-role"}
            elif len(seats) > 1:
                raise RemoteError(409, "role_ambiguous", "%d seats hold role %s" % (len(seats), role),
                                  candidates=[{"role": role, "seat": s} for s in seats],
                                  recovery="select one seat explicitly")
        if res is None:
            raise RemoteError(404, "role_unresolved", "nothing on this board answers to %s" % role,
                              recovery="tickets alias <role> <seat> or tickets role take on the host")
        if res["seat"] not in known:
            raise RemoteError(409, "role_unbound",
                              "%s points at %s, which is not a registered seat" % (role, res["seat"]),
                              recovery="rebind it on the host: tickets alias %s <live seat>" % role,
                              source=res["source"], seat=res["seat"])
        return res

    def runtime_for(self, seat):
        """The resolved runtime as the board sees it now: reachability, provider, auth."""
        tk = self.tk
        snap = tk._safe(lambda: tk.board_snapshot(self.board, messages=0), {}) or {}
        entry = None
        for a in snap.get("agents") or []:
            if a.get("name") == seat:
                entry = a
                break
        rec = tk._safe(lambda: tk._agent_rec(self.board, seat), {}) or {}
        wf = (tk._safe(lambda: tk.load_workforce(self.board), {}) or {}).get(seat, {}) or {}
        run = tk._safe(lambda: tk._read_run(self.board, seat), {}) or {}
        held = [t["id"] for t in tk._safe(lambda: tk.load_all(self.board), [])
                if t.get("owner") == seat and t.get("status") == "claimed"]
        revoked = self._state()["revoked_seats"].get(seat) or {}
        out = {
            "seat": seat,
            "provider": (entry or {}).get("adapter_provider") or wf.get("harness") or wf.get("tool") or "",
            "harness": wf.get("harness") or wf.get("tool") or "",
            "model": wf.get("model", ""),
            "lifecycle": (entry or {}).get("lifecycle") or tk.lifecycle_of(self.board, seat),
            "wake_mode": (entry or {}).get("wake_mode") or tk.wake_mode_of(self.board, seat),
            "reachable": bool((entry or {}).get("reachable")) and not revoked,
            "adapter_state": "revoked" if revoked else (entry or {}).get("adapter_state", ""),
            "adapter_reason": (revoked.get("reason") or "seat revoked for remote delivery")
            if revoked else (entry or {}).get("adapter_reason", ""),
            "adapter_mode": (entry or {}).get("adapter_mode", ""),
            "session": str((entry or {}).get("adapter_session") or ""),
            "native_online": bool((entry or {}).get("adapter_native_online")),
            "watcher_online": bool((entry or {}).get("watcher")),
            "state": (entry or {}).get("state", ""),
            "auth": (entry or {}).get("auth") or (rec.get("auth_check") or {}).get("state", ""),
            "auth_detail": (entry or {}).get("auth_detail", ""),
            "auth_login_cmd": (entry or {}).get("auth_login_cmd", ""),
            "limit": bool(rec.get("limit")),
            "limit_until": (rec.get("limit") or {}).get("until", "") if rec.get("limit") else "",
            "adapter_failure": (rec.get("adapter_failure") or {}).get("reason", ""),
            "wake_delivery": rec.get("wake_delivery") or {},
            "run_active": bool(run.get("active")),
            "holding": held,
            "busy": bool(run.get("active")) or bool(held),
            "revoked": bool(revoked),
            "revoked_at": revoked.get("at", ""),
            "roles": (entry or {}).get("roles") or [],
        }
        return out

    def resolve(self, token, role):
        res = self.resolve_role(role)
        rt = self.runtime_for(res["seat"])
        perms = self.permissions_for(token)
        confirm = self.confirm_digest(token, res["seat"], rt, perms)
        return {"resolution": res, "runtime": rt, "permissions": perms, "confirm": confirm,
                "denied": list(DENIED_V1)}

    def confirm_digest(self, token, seat, runtime, perms):
        """What the client must echo back: proof it saw target + scope before acting."""
        return digest(token.get("id", ""), seat, runtime.get("session", ""),
                      runtime.get("provider", ""), ",".join(sorted(perms["granted"])),
                      ",".join(sorted(perms["step_up"])))[:20]

    def _confirmed(self, token, role, confirm):
        res = self.resolve_role(role)
        rt = self.runtime_for(res["seat"])
        perms = self.permissions_for(token)
        expected = self.confirm_digest(token, res["seat"], rt, perms)
        if not confirm or not hmac.compare_digest(str(confirm), expected):
            raise RemoteError(409, "confirmation_required",
                              "resolve the role, show target and permissions, then echo confirm",
                              resolution=res, runtime=rt, permissions=perms, confirm=expected)
        return res, rt, perms

    # ---- posting through the live board ------------------------------------

    def _sender(self, token):
        return "remote:%s" % token.get("operator", "")

    def _post(self, sender, text, seat, ticket, kind, extra):
        tk = self.tk
        m = tk.post_message(self.board, sender, text, seat, ticket or "", kind=kind,
                            source="remote", extra=extra)
        for k in list(m):
            if k.startswith("_"):
                m.pop(k, None)
        return m

    def _wake(self, message):
        tk = self.tk
        results = tk._safe(lambda: tk.wake_recipients(self.board, message, echo=lambda *_: None), [])
        for r in results or []:
            return {"label": r.get("label", ""), "poked": bool(r.get("poked"))}
        return {"label": "not-attempted", "poked": False}

    def _check_seat_deliverable(self, seat, rt):
        """Refusals before anything is posted. Returns (reason_code, recovery) or None."""
        if rt.get("revoked"):
            return ("seat_revoked", "tickets rc revoke-seat %s --lift on the host" % seat)
        auth = rt.get("auth") or ""
        if auth and auth != "ready":
            return ("auth_failed",
                    rt.get("auth_login_cmd") or "on the host: tickets harness auth %s --login" % seat)
        if rt.get("limit"):
            return ("usage_limit", "wait until %s or transfer the role to another seat"
                    % (rt.get("limit_until") or "the limit clears"))
        return None

    def _text_ok(self, text, limit, field):
        text = str(text or "").strip()
        if not text:
            raise RemoteError(400, "missing_" + field, "%s is required" % field)
        if len(text) > limit:
            raise RemoteError(400, field + "_too_long", "%s must be <= %d chars" % (field, limit))
        if _looks_secret(text):
            raise RemoteError(400, "secret_in_payload",
                              "%s looks like it carries a credential; remote text never does" % field)
        return text

    def _ticket_ok(self, ticket):
        ticket = str(ticket or "").strip()
        if not ticket:
            return ""
        if not re.fullmatch(r"T-\d+", ticket):
            raise RemoteError(400, "bad_ticket", "ticket must look like T-123")
        if not os.path.isfile(self.tk.ticket_path(self.board, ticket)):
            raise RemoteError(404, "ticket_not_found", "no ticket %s on this board" % ticket)
        return ticket

    # ---- message (base capability) -----------------------------------------

    def message(self, token, role, text, ticket="", confirm="", key=""):
        text = self._text_ok(text, TEXT_MAX_CHARS, "text")
        ticket = self._ticket_ok(ticket)
        payload = {"role": role, "text": text, "ticket": ticket, "confirm": confirm}

        def run(kd):
            self._require(token, "message", target=role)
            res, rt, perms = self._confirmed(token, role, confirm)
            seat = res["seat"]
            if rt.get("revoked"):
                self._audit("message", self._actor_of(token), "denied", role=role, seat=seat,
                            reason="seat_revoked", idempotency_digest=kd)
                raise RemoteError(409, "seat_revoked", "%s is revoked for remote delivery" % seat,
                                  recovery="tickets rc revoke-seat %s --lift on the host" % seat)
            rid = "rc_" + uuid.uuid4().hex[:12]
            m = self._post(self._sender(token), text, seat, ticket, "message", {"dispatch": rid})
            wake = self._wake(m)
            rec = {
                "id": rid, "kind": "message", "role": role, "seat": seat, "resolved": res,
                "runtime_at_send": self._runtime_brief(rt), "ticket": ticket,
                "operator": token.get("operator", ""), "token_id": token.get("id", ""),
                "message_id": m.get("id", ""), "message_at": m.get("at", ""),
                "created_at": self.clock(), "expires_at": "", "wake": wake,
                "content": content_digest(text), "attempt": 1, "logical_task": rid,
                "idempotency_digest": kd,
            }
            with self._lock():
                d = self._dispatches()
                d["items"][rid] = rec
                self._save_dispatches(d)
            self._audit("message", self._actor_of(token), "ok", role=role, seat=seat,
                        runtime_session=rt.get("session", ""), dispatch=rid,
                        message_id=rec["message_id"], ticket=ticket, idempotency_digest=kd,
                        content=rec["content"], prior_state="", new_state="queued",
                        grant="message", wake=wake)
            return 202, self.status(rid)
        status, body, replayed = self._idempotent(token, key, "message", payload, run)
        body = dict(body, replayed=replayed)
        return status, body

    # ---- dispatch ----------------------------------------------------------

    def _runtime_brief(self, rt):
        keys = ("provider", "lifecycle", "wake_mode", "reachable", "adapter_state", "session",
                "auth", "busy", "native_online", "watcher_online")
        return {k: rt.get(k) for k in keys}

    def _dispatch_text(self, rid, objective, exit_criteria, expires_at, perms):
        parts = ["task %s: %s" % (rid, objective)]
        if exit_criteria:
            parts.append("exit: %s" % exit_criteria)
        parts.append("expires %s" % expires_at)
        parts.append("remote budget: %s" % ", ".join(perms.get("granted") or []))
        parts.append("reply: tickets rc receipt %s --ack (then --working / --submitted / --failed --reason ...)"
                     % rid)
        return " | ".join(parts)

    def dispatch(self, token, role, objective, exit_criteria="", ticket="", expires_in=None,
                 confirm="", key="", _link=None):
        objective = self._text_ok(objective, OBJECTIVE_MAX_CHARS, "objective")
        exit_criteria = str(exit_criteria or "").strip()
        if len(exit_criteria) > OBJECTIVE_MAX_CHARS or _looks_secret(exit_criteria):
            raise RemoteError(400, "bad_exit_criteria", "exit criteria too long or secret-shaped")
        ticket = self._ticket_ok(ticket)
        seconds = parse_duration(expires_in, DISPATCH_DEFAULT_SECONDS)
        if seconds < DISPATCH_MIN_SECONDS or seconds > DISPATCH_MAX_SECONDS:
            raise RemoteError(400, "bad_expiry", "expiry must be %ds..%ds"
                              % (DISPATCH_MIN_SECONDS, DISPATCH_MAX_SECONDS))
        payload = {"role": role, "objective": objective, "exit": exit_criteria, "ticket": ticket,
                   "expires_in": seconds, "confirm": confirm, "link": _link or {}}

        def run(kd):
            self._require(token, "dispatch", target=role)
            res, rt, perms = self._confirmed(token, role, confirm)
            seat = res["seat"]
            rid = "rc_" + uuid.uuid4().hex[:12]
            now = self.clock()
            expires_at = in_seconds(seconds, now)
            link = _link or {}
            rec = {
                "id": rid, "kind": "dispatch", "role": role, "seat": seat, "resolved": res,
                "runtime_at_send": self._runtime_brief(rt), "ticket": ticket,
                "operator": token.get("operator", ""), "token_id": token.get("id", ""),
                "objective": content_digest(objective), "exit_criteria": content_digest(exit_criteria),
                "objective_text": objective, "exit_text": exit_criteria,
                "permission_budget": list(perms.get("granted") or []),
                "created_at": now, "expires_at": expires_at, "expires_in": seconds,
                "attempt": int(link.get("attempt") or 1),
                "logical_task": link.get("logical_task") or rid,
                "prior_attempt": link.get("prior_attempt", ""),
                "reassigned_from": link.get("reassigned_from", ""),
                "reassign_reason": link.get("reason", ""),
                "idempotency_digest": kd, "message_id": "", "message_at": "", "wake": {},
            }
            refusal = self._check_seat_deliverable(seat, rt)
            if refusal:
                code, recovery = refusal
                rec["terminal"] = {"state": "failed", "reason_code": code, "recovery": recovery,
                                   "at": now, "detail": rt.get("auth_detail") or rt.get("adapter_reason") or ""}
                with self._lock():
                    d = self._dispatches()
                    d["items"][rid] = rec
                    self._save_dispatches(d)
                self._audit("dispatch", self._actor_of(token), "failed", role=role, seat=seat,
                            dispatch=rid, ticket=ticket, reason=code, idempotency_digest=kd,
                            requested_expiry=expires_at, grant="dispatch",
                            content=rec["objective"], prior_state="", new_state="failed",
                            linked=link or None)
                return 202, self.status(rid)
            text = self._dispatch_text(rid, objective, exit_criteria, expires_at, perms)
            m = self._post(self._sender(token), text, seat, ticket, "task", {"dispatch": rid})
            rec["message_id"] = m.get("id", "")
            rec["message_at"] = m.get("at", "")
            rec["wake"] = self._wake(m)
            with self._lock():
                d = self._dispatches()
                d["items"][rid] = rec
                self._save_dispatches(d)
            self._audit("dispatch", self._actor_of(token), "ok", role=role, seat=seat,
                        runtime_session=rt.get("session", ""), dispatch=rid,
                        message_id=rec["message_id"], ticket=ticket, idempotency_digest=kd,
                        requested_expiry=expires_at, grant="dispatch", content=rec["objective"],
                        prior_state="", new_state="queued", wake=rec["wake"], linked=link or None)
            return 202, self.status(rid)
        status, body, replayed = self._idempotent(token, key, "dispatch", payload, run)
        return status, dict(body, replayed=replayed)

    # ---- receipts (seat side, local credential) ----------------------------

    def receipt(self, seat, dispatch_id, kind, note="", reason="", artifact=""):
        seat = str(seat or "").strip()
        if not seat or not _SEAT_RE.match(seat):
            raise RemoteError(400, "bad_seat", "TICKET_AGENT must name the acting seat")
        kind = str(kind or "").strip().lower()
        if kind not in RECEIPT_KINDS:
            raise RemoteError(400, "bad_receipt", "receipt must be one of %s" % ", ".join(RECEIPT_KINDS))
        rec = self._get(dispatch_id)
        if rec.get("seat") != seat:
            self._audit("receipt", {"seat": seat, "credential_class": "agent"}, "denied",
                        dispatch=dispatch_id, reason="not_addressed")
            raise RemoteError(403, "not_addressed", "%s is addressed to %s, not %s"
                              % (dispatch_id, rec.get("seat"), seat))
        note = str(note or "").strip()[:REASON_MAX_CHARS]
        reason = str(reason or "").strip()[:REASON_MAX_CHARS]
        if kind == "failed" and not reason:
            raise RemoteError(400, "reason_required", "--failed needs --reason")
        if _looks_secret(note) or _looks_secret(reason) or _looks_secret(artifact):
            raise RemoteError(400, "secret_in_payload", "receipts never carry credentials")
        text = "%s %s" % (kind, dispatch_id)
        if reason:
            text += ": " + reason
        if note:
            text += " -- " + note
        if artifact:
            text += " [artifact %s]" % str(artifact)[:200]
        extra = {"dispatch": dispatch_id, "receipt": kind}
        m = self._post(seat, text, self._sender_for_receipt(rec), rec.get("ticket") or "",
                       "receipt", extra)
        self._audit("receipt", {"seat": seat, "credential_class": "agent"}, "ok",
                    dispatch=dispatch_id, receipt=kind, message_id=m.get("id", ""),
                    ticket=rec.get("ticket") or "", reason=reason and content_digest(reason) or None)
        return self.status(dispatch_id)

    def _sender_for_receipt(self, rec):
        # Receipts are attributable, durable and visible in the seat thread. They are
        # addressed to the remote operator handle so a bystander seat is not woken.
        return "remote:%s" % rec.get("operator", "")

    # ---- evidence -> state -------------------------------------------------

    def _get(self, dispatch_id):
        dispatch_id = str(dispatch_id or "").strip()
        if not _ID_RE.match(dispatch_id):
            raise RemoteError(400, "bad_dispatch_id", "dispatch ids look like rc_0123456789ab")
        rec = self._dispatches()["items"].get(dispatch_id)
        if not rec:
            raise RemoteError(404, "dispatch_not_found", "no dispatch %s" % dispatch_id)
        return rec

    def _messages(self):
        tk = self.tk
        return tk._safe(lambda: tk.load_messages(self.board), []) or []

    def _receipts_for(self, rec, msgs):
        out = []
        for m in msgs:
            if m.get("dispatch") != rec["id"] or m.get("from") != rec.get("seat"):
                continue
            kind = m.get("receipt") or ""
            if not kind and rec["id"] in str(m.get("text") or ""):
                kind = "ack"
            if kind:
                out.append({"kind": kind, "at": m.get("at", ""), "message_id": m.get("id", ""),
                            "from": m.get("from", "")})
        # A plain reply from the seat that names the dispatch id is an acknowledgement too.
        for m in msgs:
            if m.get("from") == rec.get("seat") and not m.get("dispatch") \
                    and rec["id"] in str(m.get("text") or "") and m.get("at", "") >= rec.get("message_at", ""):
                out.append({"kind": "ack", "at": m.get("at", ""), "message_id": m.get("id", ""),
                            "from": m.get("from", ""), "implicit": True})
        out.sort(key=lambda r: r["at"])
        return out

    def _consumed(self, rec):
        """Did the addressed seat's inbox consume the dispatch message? (delivered)"""
        tk = self.tk
        if not rec.get("message_id"):
            return False
        seat = rec.get("seat")
        agent = tk._safe(lambda: tk._agent_rec(self.board, seat), {}) or {}
        if not agent:
            return False
        unread = tk._safe(lambda: tk.unread(self.board, seat), None)
        if unread is None:
            return False
        for m in unread:
            if m.get("id") == rec["message_id"]:
                return False
        # Not unread: consumed, provided the watermark actually reached it.
        since = agent.get("inbox_seen", "")
        return bool(since) and (since >= rec.get("message_at", "") or
                                rec["message_id"] in (agent.get("inbox_seen_ids") or []))

    def _events_after(self, seat, since, ticket=""):
        tk = self.tk
        out = []
        for e in tk._safe(lambda: tk.load_trajectories(self.board), []) or []:
            if e.get("agent") != seat or e.get("at", "") < since:
                continue
            if ticket and e.get("kind") in ("claim", "update", "review", "done", "block") \
                    and e.get("ticket") != ticket:
                continue
            out.append(e)
        return out

    def _ticket_status(self, ticket):
        try:
            with open(self.tk.ticket_path(self.board, ticket)) as f:
                t = json.load(f)
            return t if isinstance(t, dict) else {}
        except (IOError, ValueError):
            return {}

    def status(self, dispatch_id):
        rec = self._get(dispatch_id)
        return self._derive(rec, persist=True)

    def _derive(self, rec, persist=False):
        now = self.clock()
        evidence = [{"state": "sent", "at": rec.get("created_at", ""), "evidence": "request accepted with id %s" % rec["id"]}]
        msgs = self._messages()
        receipts = self._receipts_for(rec, msgs)
        seat = rec.get("seat", "")
        since = rec.get("message_at") or rec.get("created_at", "")
        ticket = rec.get("ticket") or ""
        events = self._events_after(seat, since, ticket) if seat else []
        state = "sent"
        detail = ""
        recovery = ""
        rt = self.runtime_for(seat) if seat else {}
        if rec.get("message_id"):
            state = "queued"
            evidence.append({"state": "queued", "at": rec.get("message_at", ""),
                             "evidence": "durable message %s addressed to %s" % (rec["message_id"], seat)})
        delivered = bool(rec.get("delivered_at")) or self._consumed(rec)
        if delivered:
            state = "delivered"
            delivered_at = rec.get("delivered_at") or now
            if not rec.get("delivered_at") and persist:
                self._set(rec["id"], delivered_at=delivered_at)
            evidence.append({"state": "delivered", "at": delivered_at,
                             "evidence": "%s's inbox consumed message %s" % (seat, rec.get("message_id"))})
        acks = [r for r in receipts if r["kind"] in ("ack", "working", "submitted", "failed", "cancel-ack", "canceled")]
        if acks:
            state = "acknowledged"
            evidence.append({"state": "acknowledged", "at": acks[0]["at"],
                             "evidence": "receipt %s from %s (%s)" % (acks[0]["kind"], seat, acks[0]["message_id"])})
        working_ev = None
        for e in events:
            if e.get("kind") == "run_start" and (delivered or acks):
                working_ev = "run %s started by %s (trigger %s)" % (
                    e.get("run_id") or e.get("run_no"), seat, ",".join(e.get("trigger") or []))
                working_at = e.get("at", "")
                break
            if ticket and e.get("kind") in ("claim", "update", "block") and e.get("ticket") == ticket:
                working_ev = "%s event on %s by %s" % (e.get("kind"), ticket, seat)
                working_at = e.get("at", "")
                break
        for r in receipts:
            if r["kind"] in ("working", "submitted") and working_ev is None:
                working_ev = "receipt %s from %s" % (r["kind"], seat)
                working_at = r["at"]
        if working_ev:
            state = "working"
            evidence.append({"state": "working", "at": working_at, "evidence": working_ev})
        review_ev = None
        if ticket:
            t = self._ticket_status(ticket)
            if t.get("status") == "review" and t.get("owner") == seat and (
                    t.get("review_at", "") >= since or any(e.get("kind") == "review" for e in events)):
                review_ev = "%s entered review (%s)" % (ticket, t.get("commit") or t.get("pr") or "pinned")
                review_at = t.get("review_at", "") or now
        elif any(r["kind"] == "submitted" for r in receipts):
            r = [x for x in receipts if x["kind"] == "submitted"][0]
            review_ev = "receipt submitted from %s (no ticket-bound review gate)" % seat
            review_at = r["at"]
        if review_ev:
            state = "review"
            evidence.append({"state": "review", "at": review_at, "evidence": review_ev})
        # Terminal evaluation. Stored terminal wins; otherwise derive and persist.
        term = rec.get("terminal") or {}
        failed_receipt = [r for r in receipts if r["kind"] == "failed"]
        cancel = rec.get("cancel") or {}
        if not term:
            if failed_receipt:
                term = {"state": "failed", "reason_code": "seat_reported_failure",
                        "recovery": "read the seat's failure receipt, then retry or reassign",
                        "at": failed_receipt[0]["at"]}
            elif rec.get("kind") == "dispatch" and state in ("queued", "delivered", "acknowledged") \
                    and self._wake_failed(rec, rt):
                term = {"state": "failed", "reason_code": "wake_failed",
                        "recovery": "reconnect the seat on its host, then retry",
                        "at": now, "detail": rt.get("adapter_failure", "")}
            elif cancel:
                exited = self._child_exit_after(seat, cancel.get("requested_at", ""), events, receipts)
                if exited or not delivered and not acks and not working_ev:
                    term = {"state": "canceled", "reason_code": cancel.get("reason_code", "canceled_by_operator"),
                            "recovery": "", "at": exited or now,
                            "detail": "child-exit receipt" if exited else "canceled before delivery"}
            elif rec.get("expires_at") and rec["expires_at"] <= now and state in (
                    "sent", "queued", "delivered", "acknowledged"):
                term = {"state": "expired", "reason_code": "expired_before_work",
                        "recovery": "retry as a linked attempt: tickets rc retry %s" % rec["id"],
                        "at": rec["expires_at"]}
            if term and persist:
                self._set(rec["id"], terminal=term)
                self._audit("state", {"credential_class": "system"}, term["state"], dispatch=rec["id"],
                            seat=seat, reason=term.get("reason_code"), prior_state=state,
                            new_state=term["state"])
        if term:
            state = term["state"]
            detail = term.get("detail", "") or term.get("reason_code", "")
            recovery = term.get("recovery", "")
            evidence.append({"state": state, "at": term.get("at", ""),
                             "evidence": term.get("reason_code", ""), "recovery": recovery})
        cancel_view = {}
        if cancel:
            cancel_view = {"requested_at": cancel.get("requested_at", ""), "by": cancel.get("by", ""),
                           "acknowledged": any(r["kind"] in ("cancel-ack", "canceled") for r in receipts),
                           "stopped": state == "canceled"}
        if state in ("queued", "delivered") and rt:
            if rt.get("revoked"):
                detail = "seat revoked for remote delivery"
            elif rt.get("wake_mode") == "task-only" and rec.get("kind") == "message":
                detail = "task-only seat: an ordinary message waits for its next task trigger"
            elif not rt.get("reachable"):
                detail = "queued-offline: no live transport for %s (%s)" % (
                    seat, rt.get("adapter_state") or "offline")
            elif rt.get("busy"):
                detail = "busy: %s" % (", ".join(rt.get("holding") or []) or "active run")
        out = {
            "id": rec["id"], "kind": rec.get("kind"), "state": state, "detail": detail,
            "recovery": recovery, "role": rec.get("role"), "seat": seat, "ticket": ticket,
            "operator": rec.get("operator"), "created_at": rec.get("created_at"),
            "expires_at": rec.get("expires_at", ""), "message_id": rec.get("message_id", ""),
            "attempt": rec.get("attempt", 1), "logical_task": rec.get("logical_task", rec["id"]),
            "prior_attempt": rec.get("prior_attempt", ""), "reassigned_from": rec.get("reassigned_from", ""),
            "wake": rec.get("wake") or {}, "runtime": self._runtime_brief(rt) if rt else {},
            "busy": bool(rt.get("busy")) if rt else False,
            "reachable": bool(rt.get("reachable")) if rt else False,
            "receipts": receipts, "evidence": evidence, "cancel": cancel_view,
            "objective": rec.get("objective") or rec.get("content") or {},
            "terminal": bool(term),
        }
        return out

    def _wake_failed(self, rec, rt):
        tk = self.tk
        agent = tk._safe(lambda: tk._agent_rec(self.board, rec.get("seat")), {}) or {}
        failure = agent.get("adapter_failure") or {}
        return failure.get("state") == "failed" and failure.get("trigger") == rec.get("message_id")

    def _child_exit_after(self, seat, since, events, receipts):
        for r in receipts:
            if r["kind"] == "canceled" and r["at"] >= since:
                return r["at"]
        for e in events:
            if e.get("kind") == "run_end" and e.get("at", "") >= since:
                return e.get("at", "")
        return ""

    def _set(self, dispatch_id, **fields):
        with self._lock():
            d = self._dispatches()
            rec = d["items"].get(dispatch_id)
            if rec is None:
                raise RemoteError(404, "dispatch_not_found", "no dispatch %s" % dispatch_id)
            rec.update(fields)
            self._save_dispatches(d)
            return rec

    def list_dispatches(self, limit=50, seat=""):
        items = list(self._dispatches()["items"].values())
        if seat:
            items = [r for r in items if r.get("seat") == seat]
        items.sort(key=lambda r: r.get("created_at", ""), reverse=True)
        return [self._derive(r, persist=True) for r in items[:int(limit)]]

    # ---- retry / reassign / cancel / revoke ---------------------------------

    def retry(self, token, dispatch_id, key=""):
        payload = {"dispatch": dispatch_id}

        def run(kd):
            self._require(token, "retry")
            rec = self._get(dispatch_id)
            if rec.get("kind") != "dispatch":
                raise RemoteError(409, "not_retryable", "only dispatches retry")
            cur = self._derive(rec, persist=True)
            if cur["state"] not in ("failed", "expired"):
                self._audit("retry", self._actor_of(token), "denied", dispatch=dispatch_id,
                            reason="not_terminal", prior_state=cur["state"])
                raise RemoteError(409, "not_retryable",
                                  "retry only a failed or expired attempt (now %s)" % cur["state"])
            if any(e["state"] in ("working", "review") for e in cur["evidence"]):
                raise RemoteError(409, "not_retryable", "the prior attempt already did work; do not duplicate it")
            res = self.resolve_role(rec["role"])
            rt = self.runtime_for(res["seat"])
            perms = self.permissions_for(token)
            confirm = self.confirm_digest(token, res["seat"], rt, perms)
            self._require(token, "dispatch", target=rec["role"])
            link = {"attempt": int(rec.get("attempt") or 1) + 1,
                    "logical_task": rec.get("logical_task") or rec["id"],
                    "prior_attempt": rec["id"], "reason": "retry"}
            status, body = self.dispatch(
                token, rec["role"], rec.get("objective_text", ""), rec.get("exit_text", ""),
                rec.get("ticket", ""), rec.get("expires_in"), confirm=confirm,
                key="retry:" + kd + ":" + rec["id"], _link=link)
            self._set(rec["id"], retried_by=body["id"])
            self._audit("retry", self._actor_of(token), "ok", dispatch=rec["id"],
                        linked={"retry": body["id"]}, grant="retry", idempotency_digest=kd)
            return status, body
        status, body, replayed = self._idempotent(token, key, "retry", payload, run)
        return status, dict(body, replayed=replayed)

    def reassign(self, token, dispatch_id, role, reason="", confirm="", key=""):
        reason = str(reason or "").strip()
        if not reason:
            raise RemoteError(400, "reason_required", "reassign needs a reason")
        if len(reason) > REASON_MAX_CHARS or _looks_secret(reason):
            raise RemoteError(400, "bad_reason", "reason too long or secret-shaped")
        payload = {"dispatch": dispatch_id, "role": role, "reason": reason, "confirm": confirm}

        def run(kd):
            self._require(token, "reassign", target=role)
            rec = self._get(dispatch_id)
            if rec.get("kind") != "dispatch":
                raise RemoteError(409, "not_reassignable", "only dispatches reassign")
            cur = self._derive(rec, persist=True)
            if cur["state"] in ("working", "review"):
                self._audit("reassign", self._actor_of(token), "denied", dispatch=dispatch_id,
                            reason="active_work", prior_state=cur["state"])
                raise RemoteError(409, "active_work",
                                  "%s is %s on %s; cancel first, never steal active work"
                                  % (rec["seat"], cur["state"], rec["id"]))
            res, rt, perms = self._confirmed(token, role, confirm)
            if res["seat"] == rec["seat"]:
                raise RemoteError(409, "same_seat", "%s already resolves to %s" % (role, rec["seat"]))
            now = self.clock()
            if not cur["terminal"]:
                self._set(rec["id"], terminal={"state": "canceled", "reason_code": "reassigned",
                                               "recovery": "", "at": now,
                                               "detail": "reassigned to %s: %s" % (res["seat"], reason)})
                self._post(self._sender(token), "dispatch %s reassigned to %s: %s"
                           % (rec["id"], res["seat"], reason), rec["seat"], rec.get("ticket") or "",
                           "message", {"dispatch": rec["id"], "reassigned": True})
            link = {"attempt": int(rec.get("attempt") or 1) + 1,
                    "logical_task": rec.get("logical_task") or rec["id"],
                    "prior_attempt": rec["id"], "reassigned_from": rec["seat"], "reason": reason}
            self._require(token, "dispatch", target=role)
            status, body = self.dispatch(
                token, role, rec.get("objective_text", ""), rec.get("exit_text", ""),
                rec.get("ticket", ""), rec.get("expires_in"), confirm=confirm,
                key="reassign:" + kd + ":" + rec["id"], _link=link)
            self._set(rec["id"], reassigned_to=body["id"])
            self._audit("reassign", self._actor_of(token), "ok", dispatch=rec["id"],
                        prior_owner=rec["seat"], new_owner=res["seat"], reason=content_digest(reason),
                        linked={"reassign": body["id"]}, grant="reassign", idempotency_digest=kd,
                        prior_state=cur["state"], new_state="canceled")
            return status, body
        status, body, replayed = self._idempotent(token, key, "reassign", payload, run)
        return status, dict(body, replayed=replayed)

    def cancel(self, token, dispatch_id, reason="", key=""):
        reason = str(reason or "").strip()[:REASON_MAX_CHARS]
        if _looks_secret(reason):
            raise RemoteError(400, "bad_reason", "reason is secret-shaped")
        payload = {"dispatch": dispatch_id, "reason": reason}

        def run(kd):
            self._require(token, "cancel")
            rec = self._get(dispatch_id)
            cur = self._derive(rec, persist=True)
            if cur["terminal"]:
                raise RemoteError(409, "already_terminal", "%s is already %s" % (rec["id"], cur["state"]))
            if rec.get("cancel"):
                return 202, self.status(rec["id"])
            now = self.clock()
            text = "cancel %s%s | stop this task, then reply: tickets rc receipt %s --cancel-ack (and --canceled once the run has exited)" % (
                rec["id"], (": " + reason) if reason else "", rec["id"])
            m = self._post(self._sender(token), text, rec["seat"], rec.get("ticket") or "", "task",
                           {"dispatch": rec["id"], "cancel": True})
            wake = self._wake(m)
            self._set(rec["id"], cancel={"requested_at": now, "by": token.get("operator", ""),
                                         "reason_code": "canceled_by_operator",
                                         "message_id": m.get("id", ""), "wake": wake})
            self._audit("cancel", self._actor_of(token), "ok", dispatch=rec["id"], seat=rec["seat"],
                        message_id=m.get("id", ""), grant="cancel", idempotency_digest=kd,
                        prior_state=cur["state"], new_state="cancel_requested",
                        reason=content_digest(reason) if reason else None, wake=wake)
            return 202, self.status(rec["id"])
        status, body, replayed = self._idempotent(token, key, "cancel", payload, run)
        return status, dict(body, replayed=replayed)

    def revoke_seat(self, token, seat, reason="", lift=False, key=""):
        seat = str(seat or "").strip()
        if not seat or not _SEAT_RE.match(seat):
            raise RemoteError(400, "bad_seat", "seat name required")
        reason = str(reason or "").strip()[:REASON_MAX_CHARS]
        payload = {"seat": seat, "reason": reason, "lift": bool(lift)}

        def run(kd):
            self._require(token, "revoke", target=seat)
            if seat not in self._known_seats():
                raise RemoteError(404, "seat_not_found", "no seat %s" % seat)
            now = self.clock()
            with self._lock():
                st = self._state()
                if lift:
                    st["revoked_seats"].pop(seat, None)
                else:
                    st["revoked_seats"][seat] = {"at": now, "by": token.get("operator", ""),
                                                 "reason": reason}
                self._save_state(st)
            affected = []
            if not lift:
                with self._lock():
                    d = self._dispatches()
                    for rid, rec in d["items"].items():
                        if rec.get("seat") == seat and rec.get("kind") == "dispatch" and not rec.get("terminal"):
                            rec["terminal"] = {"state": "failed", "reason_code": "revoked",
                                               "recovery": "reassign: tickets rc reassign %s <role>" % rid,
                                               "at": now, "detail": "seat revoked for remote delivery"}
                            affected.append(rid)
                    self._save_dispatches(d)
            self._audit("revoke_seat" if not lift else "unrevoke_seat", self._actor_of(token), "ok",
                        seat=seat, reason=content_digest(reason) if reason else None, grant="revoke",
                        idempotency_digest=kd, linked={"failed": affected} if affected else None)
            return 200, {"seat": seat, "revoked": not lift, "at": now, "failed_dispatches": affected}
        status, body, replayed = self._idempotent(token, key, "revoke_seat", payload, run)
        return status, dict(body, replayed=replayed)

    def revoke_self(self, token, key=""):
        def run(kd):
            rec = self.revoke_token(token["id"], actor=self._actor_of(token))
            return 200, {"token_id": rec["id"], "revoked": True}
        status, body, replayed = self._idempotent(token, key, "revoke_self", {}, run)
        return status, dict(body, replayed=replayed)

    # ---- HTTP router (pure; the socket layer is thin) ----------------------

    def handle(self, method, path, headers, body=b""):
        """(status, body_dict) for one request. Never raises; never logs a body."""
        headers = {str(k).lower(): v for k, v in (headers or {}).items()}
        try:
            return self._route(method.upper(), path, headers, body)
        except RemoteError as e:
            return e.status, e.body()
        except Exception as e:  # noqa: BLE001 - a failure must answer, not hang the client
            return 500, {"error": "internal_error", "detail": e.__class__.__name__}

    def _json_body(self, headers, body):
        if not body:
            return {}
        if len(body) > BODY_MAX_BYTES:
            raise RemoteError(413, "body_too_large", "body must be <= %d bytes" % BODY_MAX_BYTES)
        ctype = (headers.get("content-type") or "").split(";", 1)[0].strip().lower()
        if ctype != "application/json":
            raise RemoteError(415, "json_required", "Content-Type must be application/json")
        try:
            data = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise RemoteError(400, "bad_json", "body is not valid JSON")
        if not isinstance(data, dict):
            raise RemoteError(400, "bad_json", "body must be a JSON object")
        for k in ("from", "sender", "actor", "operator"):
            if k in data:
                raise RemoteError(403, "sender_identity_rejected",
                                  "sender comes from the credential, never the body")
        return data

    def _origin_ok(self, headers):
        origin = (headers.get("origin") or "").strip()
        if not origin:
            return True
        host = (headers.get("host") or "").strip()
        if not host or origin.lower() == "null":
            return False
        from urllib.parse import urlparse
        p = urlparse(origin)
        return p.scheme in ("http", "https") and p.netloc.lower() == host.lower()

    def _bearer(self, headers):
        auth = (headers.get("authorization") or "").strip()
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        return ""

    def _route(self, method, path, headers, body):
        from urllib.parse import parse_qs, urlsplit
        parts = urlsplit(path)
        route = parts.path.rstrip("/") or "/"
        q = {k: v[0] for k, v in parse_qs(parts.query).items()}
        if not route.startswith("/remote/v1"):
            raise RemoteError(404, "not_found", "unknown route")
        route = route[len("/remote/v1"):] or "/"
        if method not in ("GET", "POST"):
            raise RemoteError(404, "not_found", "unknown route")
        if method == "POST" and not self._origin_ok(headers):
            raise RemoteError(403, "origin_mismatch", "Origin must match Host")
        token = self.authenticate(self._bearer(headers))
        data = self._json_body(headers, body) if method == "POST" else {}
        key = headers.get("idempotency-key") or data.get("idempotency_key") or ""
        if method == "GET":
            if route == "/whoami":
                return 200, {"operator": token["operator"], "token_id": token["id"],
                             "board": self.board_hash, "permissions": self.permissions_for(token)}
            if route == "/roles":
                return 200, {"roles": self.roles()}
            if route == "/resolve":
                return 200, self.resolve(token, q.get("role", ""))
            if route == "/permissions":
                return 200, self.permissions_for(token)
            if route == "/dispatches":
                self._require(token, "read")
                return 200, {"dispatches": self.list_dispatches(limit=q.get("limit", 50), seat=q.get("seat", ""))}
            m = re.fullmatch(r"/dispatches/(rc_[0-9a-f]{12})", route)
            if m:
                return 200, self.status(m.group(1))
            if route == "/audit":
                return 200, {"audit": self.audit(limit=q.get("limit", 50))}
            raise RemoteError(404, "not_found", "unknown route")
        if route == "/message":
            return self.message(token, data.get("role", ""), data.get("text", ""), data.get("ticket", ""),
                                confirm=data.get("confirm", ""), key=key)
        if route == "/dispatch":
            return self.dispatch(token, data.get("role", ""), data.get("objective", ""),
                                 data.get("exit_criteria", ""), data.get("ticket", ""),
                                 data.get("expires_in"), confirm=data.get("confirm", ""), key=key)
        m = re.fullmatch(r"/dispatches/(rc_[0-9a-f]{12})/(retry|reassign|cancel)", route)
        if m:
            rid, action = m.group(1), m.group(2)
            if action == "retry":
                return self.retry(token, rid, key=key)
            if action == "reassign":
                return self.reassign(token, rid, data.get("role", ""), data.get("reason", ""),
                                     confirm=data.get("confirm", ""), key=key)
            return self.cancel(token, rid, data.get("reason", ""), key=key)
        m = re.fullmatch(r"/seats/([A-Za-z0-9][A-Za-z0-9_.-]{0,100})/(revoke|unrevoke)", route)
        if m:
            return self.revoke_seat(token, m.group(1), data.get("reason", ""),
                                    lift=m.group(2) == "unrevoke", key=key)
        if route == "/tokens/self/revoke":
            return self.revoke_self(token, key=key)
        if route in ("/merge", "/shell", "/clear", "/delete", "/secrets"):
            self._audit("denied", self._actor_of(token), "denied", action_requested=route.strip("/"),
                        reason="denied_in_v1")
            raise RemoteError(403, "denied_in_v1", "%s is denied for every remote caller in V1" % route.strip("/"),
                              recovery="use the reviewed local gate on the host")
        raise RemoteError(404, "not_found", "unknown route")


# ---------------------------------------------------------------- HTTP server

def serve(rc, host="127.0.0.1", port=8766, behind_tls_proxy=False, page_html="", block=True,
          on_bound=None):
    """Bind the router. Non-loopback needs an explicit TLS-proxy acknowledgement.

    ``on_bound(host, port)`` runs once the socket is actually listening, so a
    banner never precedes a refusal.
    """
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    loopback = host in ("127.0.0.1", "localhost", "::1")
    if not loopback and not behind_tls_proxy:
        raise RemoteError(400, "insecure_bind",
                          "binding %s exposes bearer tokens in clear text" % host,
                          recovery="keep 127.0.0.1 and reach it through an authenticated TLS tunnel, "
                                   "or pass --behind-tls-proxy when a TLS terminator fronts this port")

    class H(BaseHTTPRequestHandler):
        server_version = "atman-remote/1"

        def _send(self, status, payload, ctype="application/json"):
            body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path in ("/", "/remote", "/remote/"):
                self._send(200, (page_html or PAGE_HTML).encode("utf-8"), "text/html; charset=utf-8")
                return
            status, out = rc.handle("GET", self.path, dict(self.headers.items()), b"")
            self._send(status, out)

        def do_POST(self):
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if length < 0 or length > BODY_MAX_BYTES:
                self._send(413, {"error": "body_too_large"})
                return
            raw = self.rfile.read(length) if length else b""
            status, out = rc.handle("POST", self.path, dict(self.headers.items()), raw)
            self._send(status, out)

        def log_message(self, *args):  # never a body, never a header, never a token
            pass

    srv = ThreadingHTTPServer((host, port), H)
    srv.daemon_threads = True
    if on_bound:
        on_bound(srv.server_address[0], srv.server_address[1])
    if block:
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            srv.server_close()
        return None
    import threading
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv


PAGE_HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Atman remote</title>
<style>
body{font:15px/1.4 -apple-system,system-ui,sans-serif;margin:0;padding:12px;background:#f6f6f4;color:#111}
h1{font-size:18px;margin:0 0 8px}section{background:#fff;border:1px solid #ddd;border-radius:8px;padding:12px;margin:0 0 12px}
label{display:block;font-size:12px;color:#555;margin:8px 0 2px}input,select,textarea,button{font:inherit;width:100%;box-sizing:border-box;padding:8px;border:1px solid #bbb;border-radius:6px}
button{background:#111;color:#fff;border:0;margin-top:8px}button:disabled{background:#999}
.deny{background:#eee;color:#666;border:1px dashed #999}.state{font-weight:600}.ev{font-size:12px;color:#444}
pre{white-space:pre-wrap;font-size:12px;background:#fafafa;border:1px solid #eee;padding:6px;border-radius:6px}
.bad{color:#a00}.ok{color:#070}
</style></head><body>
<h1>Atman remote</h1>
<section><label>Operator token (kept in this page's memory only)</label><input id="tok" type="password" autocomplete="off">
<button id="who">Connect</button><div id="whoami" class="ev"></div></section>
<section><label>Role</label><select id="role"></select><button id="resolve">Resolve</button>
<pre id="res" hidden></pre>
<div id="perm" class="ev"></div></section>
<section><label>Bounded task</label><textarea id="obj" rows="3" placeholder="objective"></textarea>
<label>Exit criteria (optional)</label><input id="exit"><label>Ticket (optional)</label><input id="ticket" placeholder="T-123">
<label>Expires in</label><input id="exp" value="30m">
<button id="send" disabled>Dispatch task</button><button id="msg" disabled>Send message only</button>
<button class="deny" disabled>Merge (denied in V1)</button><button class="deny" disabled>Delete / clear (denied in V1)</button>
</section>
<section><div>State: <span id="state" class="state">-</span> <span id="detail" class="ev"></span></div>
<div id="rec" class="ev"></div><pre id="evid" hidden></pre>
<button id="cancel" disabled>Cancel</button><button id="retry" disabled>Retry as linked attempt</button>
<div id="err" class="bad"></div></section>
<script>
(function(){
var S={tok:"",confirm:"",id:"",perms:null,timer:null};
function $(i){return document.getElementById(i)}
function key(){return (crypto.randomUUID?crypto.randomUUID():String(Date.now())+Math.random())}
function api(m,p,b){var h={"Authorization":"Bearer "+S.tok};if(b){h["Content-Type"]="application/json";h["Idempotency-Key"]=key()}
 return fetch("/remote/v1"+p,{method:m,headers:h,body:b?JSON.stringify(b):undefined}).then(function(r){return r.json().then(function(j){j._status=r.status;return j})})}
function err(j){$("err").textContent=j&&j.error?(j.error+": "+(j.detail||"")+(j.recovery?" -> "+j.recovery:"")):""}
$("who").onclick=function(){S.tok=$("tok").value.trim();$("tok").value="";api("GET","/whoami").then(function(j){err(j);if(j.error)return;S.perms=j.permissions;
 $("whoami").textContent="operator "+j.operator+" | granted: "+j.permissions.granted.join(", ")+" | step-up: "+Object.keys(j.permissions.step_up).join(", ")+" | denied: "+j.permissions.denied.join(", ");
 return api("GET","/roles")}).then(function(j){if(!j||j.error)return;var s=$("role");s.innerHTML="";j.roles.forEach(function(r){var o=document.createElement("option");o.value=r.role;o.textContent=r.role+" -> "+(r.seat||"(ambiguous)")+" ["+r.source+"]";s.appendChild(o)})})};
$("resolve").onclick=function(){api("GET","/resolve?role="+encodeURIComponent($("role").value)).then(function(j){err(j);if(j.error)return;S.confirm=j.confirm;var r=j.runtime;
 $("res").hidden=false;$("res").textContent="seat "+r.seat+" via "+j.resolution.source+"\nprovider "+r.provider+" lifecycle "+r.lifecycle+" wake "+r.wake_mode+"\nreachable "+r.reachable+" ("+r.adapter_state+") session "+(r.session||"-")+"\nauth "+(r.auth||"unknown")+(r.busy?" busy":"")+(r.revoked?" REVOKED":"");
 $("perm").textContent="this action may use: "+j.permissions.granted.join(", ")+" | denied: "+j.denied.join(", ");
 $("send").disabled=j.permissions.granted.indexOf("dispatch")<0;$("msg").disabled=false})};
function track(j){err(j);if(j.error)return;S.id=j.id;show(j);if(S.timer)clearInterval(S.timer);S.timer=setInterval(function(){api("GET","/dispatches/"+S.id).then(show)},3000)}
function show(j){if(j.error){err(j);return}$("state").textContent=j.state;$("detail").textContent=j.detail||"";
 $("rec").textContent=j.id+" -> "+j.seat+" attempt "+j.attempt+(j.expires_at?" expires "+j.expires_at:"")+" wake "+(j.wake&&j.wake.label||"-");
 $("evid").hidden=false;$("evid").textContent=j.evidence.map(function(e){return e.state+" @ "+e.at+" : "+e.evidence+(e.recovery?" -> "+e.recovery:"")}).join("\n");
 var p=S.perms||{granted:[],step_up:{}};$("cancel").disabled=j.terminal||!(p.step_up.cancel);$("retry").disabled=!(j.state=="failed"||j.state=="expired")||p.granted.indexOf("retry")<0}
$("send").onclick=function(){api("POST","/dispatch",{role:$("role").value,objective:$("obj").value,exit_criteria:$("exit").value,ticket:$("ticket").value,expires_in:$("exp").value,confirm:S.confirm}).then(track)};
$("msg").onclick=function(){api("POST","/message",{role:$("role").value,text:$("obj").value,ticket:$("ticket").value,confirm:S.confirm}).then(track)};
$("cancel").onclick=function(){api("POST","/dispatches/"+S.id+"/cancel",{reason:"operator cancel from remote page"}).then(track)};
$("retry").onclick=function(){api("POST","/dispatches/"+S.id+"/retry",{}).then(track)};
})();
</script></body></html>
"""
