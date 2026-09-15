"""T-685 auth V2 contract: execution context, credential profiles, merge rules.

This module is the frozen schema and decision table for T-686. It does not
run provider CLIs, start models, or store secrets. T-610 `auth_check` remains
the on-disk blob; this file names the fields T-686 must add and the merge
rules that prevent a false `login_required`.
"""

import json
import os
import re
from urllib.parse import urlsplit, urlunsplit

AUTH_STATES = (
    "ready",
    "login_required",
    "expired",
    "quota",
    "network",
    "unavailable",
    "unsupported",
)

# Probe that ran somewhere other than the enrolled runner cannot become
# the seat's authoritative state (the observed false login_required).
NON_AUTHORITATIVE_STATES = AUTH_STATES

PROFILE_KINDS = {
    "cursor": ("browser", "api_key", "auth_token"),
    "claude": ("subscription", "api_key"),
    "codex": ("chatgpt", "api_key"),
    "remote": ("adapter",),
    "custom": ("adapter",),
}

RUNNER_KINDS = ("host", "sandbox", "container")

# Board/UI/CLI may store these auth_check keys. Anything secret-shaped is
# stripped by redact_auth_check before write.
AUTH_CHECK_FIELDS = (
    "state",
    "harness",
    "at",
    "exit",
    "detail",
    "identity",          # T-610; migrate to identity_label
    "identity_label",
    "status_cmd",
    "login_cmd",
    "credential_profile_ref",
    "profile_kind",
    "authoritative",
    "execution_context",
    "pause",
    "alert_id",
)

EXECUTION_CONTEXT_FIELDS = (
    "runner_id",
    "runner_kind",
    "hostname",
    "username",
    "binary",
    "argv0",
    "env_fingerprint",
    "worktree",
    "repo_root",
    "origin_url",
    "expected_origin",
    "head",           # observational only; not auth identity
    "agent_id",       # enrolled seat (tickets join / spawn name)
    "ticket_agent",   # process TICKET_AGENT; must equal agent_id
    "lifecycle",      # persistent|ephemeral; pause/alert derive from this
)

PAUSE_FIELDS = (
    "paused",
    "retry_model",
    "retain_queue",
    "dedupe_alert",
    "resume_once_on_ready",
    "operator_path",
)

# Required for a V2 authoritative record. `head` is observational and may
# be empty. `worktree` is display/path, not identity.
REQUIRED_CONTEXT_IDENTITY_FIELDS = (
    "runner_id",
    "runner_kind",
    "hostname",
    "username",
    "binary",
    "argv0",
    "env_fingerprint",
    "repo_root",
    "origin_url",
    "expected_origin",
    "agent_id",
    "ticket_agent",
)

LIFECYCLES = ("persistent", "ephemeral")

PROFILE_REF_RE = re.compile(r"^prf_[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
BOARD_HASH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_EMBEDDED_TOKEN_RE = re.compile(
    r"(?i)(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|"
    r"github_pat_[A-Za-z0-9_]{20,}|xox[baprs]-[A-Za-z0-9-]{20,}|"
    r"Bearer\s+\S{16,})"
)
_USERINFO_ORIGIN_RE = re.compile(
    r"(?i)^(?P<scheme>https?://|ssh://|git://)(?P<userinfo>[^/@]+@)"
)

# Never persist these names or values on the board, logs, or UI.
FORBIDDEN_SECRET_KEYS = (
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "auth_token",
    "refresh_token",
    "cookie",
    "authorization",
    "private_key",
)

PROFILE_STORE_MODE = 0o600
PROFILE_DIR_MODE = 0o700

_GITHUB_ORIGIN = re.compile(
    r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)"
    r"(?P<owner>[^/]+)/(?P<repo>[^/.]+?)(?:\.git)?$",
    re.I,
)


def _userinfo_is_credential(userinfo):
    """Keep the Git SSH user `git`; drop password-bearing or token userinfo."""
    user = (userinfo or "").rstrip("@")
    if not user:
        return False
    if ":" in user:
        return True
    return user.lower() != "git"


def strip_url_userinfo(url):
    """Drop credential userinfo from any URL-shaped origin before storage."""
    text = (url or "").strip()
    if not text:
        return ""

    def _drop_scheme_userinfo(match):
        if _userinfo_is_credential(match.group("userinfo")):
            return match.group("scheme")
        return match.group(0)

    text = _USERINFO_ORIGIN_RE.sub(_drop_scheme_userinfo, text)
    try:
        parts = urlsplit(text)
    except ValueError:
        return text
    if parts.scheme and parts.netloc:
        if parts.password or (parts.username and parts.username.lower() != "git"):
            host = parts.hostname or ""
            if parts.port:
                host = "%s:%s" % (host, parts.port)
            text = urlunsplit(
                (parts.scheme, host, parts.path, parts.query, parts.fragment))
    return text


def normalize_git_origin(url):
    """Collapse GitHub HTTPS/SSH URLs to owner/repo. Strip credentials first."""
    text = strip_url_userinfo((url or "").strip())
    if not text:
        return ""
    m = _GITHUB_ORIGIN.match(text.rstrip("/"))
    if m:
        return "%s/%s" % (m.group("owner").lower(), m.group("repo").lower())
    return text.rstrip("/").lower()


def repo_identity_matches(expected_origin, actual_origin):
    """True when the worktree git remote is the ticket's expected repository."""
    exp = normalize_git_origin(expected_origin)
    act = normalize_git_origin(actual_origin)
    return bool(exp) and exp == act


def spawn_repo_identity_ok(expected_origin, worktree_origin, spawn_git_root_origin,
                           worktree_exists=True):
    """The worktree and the git root used for `worktree add` must be the ticket repo.

    Shared boards may live in another checkout (Steer `.tickets` driving Atman
    seats). Path prefixes are not identity. The 2026-09-10 incident created a
    Steer object database at an Atman-looking `--worktree` because spawn used
    `root = dirname(board)` instead of the enrolled repo origin.

    T-686 must `git worktree add` from a root whose origin matches
    `expected_origin`, and refuse when an existing worktree origin does not.
    """
    if not repo_identity_matches(expected_origin, spawn_git_root_origin):
        return False
    if worktree_exists:
        return repo_identity_matches(expected_origin, worktree_origin)
    return True


# Auth authority is *where* the probe ran, not which commit the worktree
# currently points at. Ordinary `git commit` / `git checkout` must not flip
# `runner_mismatch` or demote an authoritative blob. `head` remains an
# observational field on the record; spawn may pin a required SHA separately.
AUTH_CONTEXT_IDENTITY_FIELDS = (
    "runner_id",
    "runner_kind",
    "hostname",
    "username",
    "binary",
    "argv0",
    "env_fingerprint",
    "repo_root",
    "origin_url",
    "expected_origin",
    "agent_id",
)

# Exact fingerprint includes resolved `binary` and the `runner_id` that
# hashes it. Those two fields drift when the enrolled CLI disappears
# (`_which_binary` falls back to argv0). Same-seat fence keeps host, user,
# argv0, env, repo, origin, and agent — never a cross-seat overwrite.
SEAT_FENCE_IDENTITY_FIELDS = (
    "runner_kind",
    "hostname",
    "username",
    "argv0",
    "env_fingerprint",
    "repo_root",
    "origin_url",
    "expected_origin",
    "agent_id",
)


def _nonempty_text(value):
    return bool(str(value or "").strip())


def context_is_complete(ctx):
    """True when every identity field is present, valid, and seat-bound."""
    if not isinstance(ctx, dict) or not ctx:
        return False
    for field in REQUIRED_CONTEXT_IDENTITY_FIELDS:
        if not _nonempty_text(ctx.get(field)):
            return False
    if ctx.get("runner_kind") not in RUNNER_KINDS:
        return False
    origin = normalize_git_origin(ctx.get("origin_url"))
    expected = normalize_git_origin(ctx.get("expected_origin"))
    if not origin or not expected or origin != expected:
        return False
    if not seat_identity_matches(ctx.get("agent_id"), ctx.get("ticket_agent")):
        return False
    lifecycle = ctx.get("lifecycle")
    if lifecycle not in (None, "") and lifecycle not in LIFECYCLES:
        return False
    return True


def context_fingerprint(ctx):
    """Stable identity of where a probe ran. Host vs sandbox must differ.

    Incomplete contexts never fingerprint-match (empty fields are not
    identity). HEAD is excluded: it changes on ordinary commits and is not
    auth authority. Username, argv0, env_fingerprint, normalized
    credential-free origin, and expected_origin are included. Repo identity
    for spawn is origin, not path/HEAD.
    """
    if not context_is_complete(ctx):
        return ""
    parts = []
    for field in AUTH_CONTEXT_IDENTITY_FIELDS:
        val = ctx.get(field) or ""
        if field in ("origin_url", "expected_origin"):
            val = normalize_git_origin(val)
        parts.append(str(val).strip())
    return "|".join(parts)


def seat_identity_matches(enrolled_agent, ticket_agent):
    """The process claiming work must be the enrolled seat, not a generic harness name."""
    enrolled = (enrolled_agent or "").strip()
    process = (ticket_agent or "").strip()
    return bool(enrolled) and enrolled == process


def preflight_failures(enrolled_agent, ticket_agent, expected_origin,
                        worktree_origin, spawn_git_root_origin,
                        probe_ctx, runner_ctx, worktree_exists=True):
    """Reasons a zero-model preflight must fail closed. Empty means proceed.

    Live 2026-09-10: Atman worktree origin/main@920644c was correct, but the
    Cursor child claimed T-685 as generic `cursor` while the seat was
    `atman-auth-v2`. That is a seat identity mismatch, not `login_required`.
    """
    reasons = []
    if not seat_identity_matches(enrolled_agent, ticket_agent):
        reasons.append("seat_mismatch")
    if not spawn_repo_identity_ok(
            expected_origin, worktree_origin, spawn_git_root_origin,
            worktree_exists=worktree_exists):
        reasons.append("repo_mismatch")
    if not contexts_match(probe_ctx, runner_ctx):
        reasons.append("runner_mismatch")
    return reasons


def mismatch_auth_check(reasons):
    """Non-ready result for identity/repo/runner mismatch. Never login_required."""
    why = ",".join(reasons) or "mismatch"
    return {
        "state": "unavailable",
        "authoritative": False,
        "detail": "preflight mismatch: %s" % why,
        "login_cmd": "",
        "pause": pause_policy("persistent", "unavailable"),
    }


def contexts_match(probe_ctx, runner_ctx):
    if not context_is_complete(probe_ctx) or not context_is_complete(runner_ctx):
        return False
    left = context_fingerprint(probe_ctx)
    right = context_fingerprint(runner_ctx)
    return bool(left) and left == right


def seat_fence_matches(left, right):
    """True when both contexts are the same fenced seat, ignoring binary drift.

    Resolved binary path and runner_id are not fence identity. A missing
    CLI must still be the same host, user, argv0, env, repo, origin, and
    agent. Incomplete or seat-mismatched contexts never match.
    """
    if not context_is_complete(left) or not context_is_complete(right):
        return False
    if not seat_identity_matches(left.get("agent_id"), left.get("ticket_agent")):
        return False
    if not seat_identity_matches(right.get("agent_id"), right.get("ticket_agent")):
        return False
    if not seat_identity_matches(left.get("agent_id"), right.get("agent_id")):
        return False
    for field in SEAT_FENCE_IDENTITY_FIELDS:
        lv = left.get(field) or ""
        rv = right.get(field) or ""
        if field in ("origin_url", "expected_origin"):
            lv = normalize_git_origin(lv)
            rv = normalize_git_origin(rv)
        if str(lv).strip() != str(rv).strip():
            return False
    return True


def is_authoritative(auth_check, runner_ctx):
    rec = auth_check or {}
    if rec.get("authoritative") is False:
        return False
    if rec.get("state") not in AUTH_STATES:
        return False
    ctx = rec.get("execution_context") or {}
    if not context_is_complete(ctx):
        return False
    if not context_is_complete(runner_ctx):
        return False
    return contexts_match(ctx, runner_ctx)


# Failed preflight never spends a model turn, including adapters with no
# declared zero-model check (`unsupported`).
NO_SPEND_STATES = (
    "login_required",
    "expired",
    "quota",
    "network",
    "unavailable",
    "unsupported",
)

# Operator recovery is keyed by state, not by "try login" for every pause.
OPERATOR_PATH = {
    "login_required": "login",
    "expired": "login",
    "quota": "quota",
    "network": "network",
    "unavailable": "unavailable",
    "unsupported": "unsupported",
}


def pause_policy(lifecycle, state):
    """Persistent seats pause without model retries; queued work is kept.

    `unsupported` is no-spend: do not retry a model and do not send the
    operator down the login path. Recovery is declare-an-adapter-check
    (or switch provider); queued work stays until that happens.
    """
    no_spend = state in NO_SPEND_STATES
    persistent = lifecycle == "persistent"
    paused = bool(no_spend and persistent)
    return {
        "paused": paused,
        "retry_model": False if no_spend else True,
        "retain_queue": True,
        "dedupe_alert": no_spend,
        "resume_once_on_ready": persistent and no_spend,
        "operator_path": OPERATOR_PATH.get(state) or "ready",
    }


def alert_id(agent_id, state, profile_ref=""):
    """One operator alert per seat+state+profile; not per poll."""
    return "auth:%s:%s:%s" % (agent_id or "", state or "", profile_ref or "")


def _stored_authoritative_lineage(previous):
    """True when the blob already claims authority for its own execution_context.

    Authority is the stored runner, not the merge caller's runner_ctx. A
    later sandbox (or other) caller must not silently rebind that lineage.
    """
    rec = previous or {}
    if rec.get("authoritative") is not True:
        return False
    return context_is_complete(rec.get("execution_context"))


def _incoming_is_legal_probe(incoming):
    rec = incoming or {}
    if rec.get("state") not in AUTH_STATES:
        return False
    if validate_auth_check(rec):
        return False
    return context_is_complete(rec.get("execution_context"))


def _trusted_lifecycle(previous, incoming, runner_ctx):
    """Pause/alert lifecycle comes from enrolled stored/incoming context.

    Arbitrary caller `runner_ctx.lifecycle` is ignored unless that caller
    matches the stored/incoming lineage (three-way agreement).
    """
    for ctx in (
        (previous or {}).get("execution_context"),
        (incoming or {}).get("execution_context"),
    ):
        life = (ctx or {}).get("lifecycle") if isinstance(ctx, dict) else None
        if life in LIFECYCLES:
            return life
    if context_is_complete(runner_ctx) and (runner_ctx or {}).get("lifecycle") in LIFECYCLES:
        if contexts_match((incoming or {}).get("execution_context"), runner_ctx):
            return runner_ctx.get("lifecycle")
        prev_ctx = (previous or {}).get("execution_context")
        if contexts_match(prev_ctx, runner_ctx):
            return runner_ctx.get("lifecycle")
    return "ephemeral"


def _trusted_agent_id(previous, incoming, runner_ctx):
    for ctx in (
        (previous or {}).get("execution_context"),
        (incoming or {}).get("execution_context"),
    ):
        agent = (ctx or {}).get("agent_id") if isinstance(ctx, dict) else None
        if _nonempty_text(agent):
            return str(agent).strip()
    if context_is_complete(runner_ctx) and _nonempty_text((runner_ctx or {}).get("agent_id")):
        if contexts_match((incoming or {}).get("execution_context"), runner_ctx) or contexts_match(
                (previous or {}).get("execution_context"), runner_ctx):
            return str(runner_ctx.get("agent_id")).strip()
    return ""


def _same_seat_unavailable_reprobe(previous, incoming, runner_ctx):
    """Authoritative unavailable re-probe for the same fenced seat.

    When the previously resolved CLI disappears, the new probe's
    execution_context has a different `binary` (argv0 fallback) and
    `runner_id` (hash includes that path). Exact fingerprint match then
    discards a correct unavailable result and freezes stale Ready. The
    fence accepts only `unavailable`, never Ready or a cross-seat write.
    """
    rec = incoming or {}
    if rec.get("state") != "unavailable":
        return False
    if rec.get("authoritative") is False:
        return False
    if not _incoming_is_legal_probe(rec):
        return False
    if not _stored_authoritative_lineage(previous):
        return False
    prev_ctx = (previous or {}).get("execution_context") or {}
    inc_ctx = rec.get("execution_context") or {}
    return (
        seat_fence_matches(prev_ctx, inc_ctx)
        and seat_fence_matches(prev_ctx, runner_ctx)
        and seat_fence_matches(inc_ctx, runner_ctx)
    )


def merge_auth_check(previous, incoming, runner_ctx):
    """Apply an incoming probe to the stored blob.

    A non-matching / non-authoritative / malformed probe never replaces
    *any* authoritative runner blob. For stored lineage, stored,
    incoming, and enrolled caller contexts must all agree. Silent runner
    rebind is forbidden here; T-686 may add an explicit fenced rebind later.

    Exception: an authoritative `unavailable` re-probe for the same fenced
    seat may replace stored lineage when only resolved binary / runner_id
    drifted (CLI disappeared). Cross-seat, sandbox, repo, argv0, and Ready
    claims still cannot overwrite. Supported preview install is
    source-prefix `install.sh`: atm and tickets are the same tickets.py.

    Pause and alert identity are derived from trusted stored/incoming
    enrolled context, never from an arbitrary caller.
    """
    previous = load_auth_check(previous)
    incoming = redact_auth_check(dict(incoming or {}))
    prev_ctx = previous.get("execution_context") or {}
    inc_ctx = incoming.get("execution_context") or {}
    legal_incoming = _incoming_is_legal_probe(incoming)
    if _stored_authoritative_lineage(previous):
        if not legal_incoming:
            return previous
        exact = (
            contexts_match(prev_ctx, inc_ctx)
            and contexts_match(prev_ctx, runner_ctx)
            and contexts_match(inc_ctx, runner_ctx)
        )
        if exact:
            incoming_auth = is_authoritative(incoming, prev_ctx)
            if not incoming_auth:
                return previous
        elif _same_seat_unavailable_reprobe(previous, incoming, runner_ctx):
            incoming_auth = True
        else:
            return previous
    else:
        if not legal_incoming:
            return previous
        incoming_auth = is_authoritative(incoming, runner_ctx)
        if not incoming_auth:
            return previous
    incoming["authoritative"] = incoming_auth
    merged = dict(previous)
    merged.update(incoming)
    merged["authoritative"] = incoming_auth
    merged = redact_auth_check(merged)
    lifecycle = _trusted_lifecycle(previous, incoming, runner_ctx)
    agent = _trusted_agent_id(previous, incoming, runner_ctx)
    if merged.get("state") in NO_SPEND_STATES:
        merged["pause"] = pause_policy(lifecycle, merged.get("state"))
        merged["alert_id"] = alert_id(
            agent,
            merged.get("state"),
            merged.get("credential_profile_ref") or "")
    else:
        # Matching quota/login/expired → ready must resume once: drop stale
        # auth pause and the paused-state alert so the seat is not stuck.
        merged["pause"] = pause_policy(lifecycle, merged.get("state") or "ready")
        merged["alert_id"] = ""
    return merged


def _secret_key(name):
    n = (name or "").lower().replace("-", "_")
    if n in FORBIDDEN_SECRET_KEYS:
        return True
    return any(n.endswith("_" + k) or n.startswith(k + "_") for k in FORBIDDEN_SECRET_KEYS)


def _looks_secret_value(text):
    if not text or not isinstance(text, str):
        return False
    if text.startswith("prf_") and PROFILE_REF_RE.fullmatch(text):
        return False
    if _EMBEDDED_TOKEN_RE.search(text):
        return True
    if len(text) < 24:
        return False
    if " " in text or "@" in text:
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9_\-./+=]{32,}", text))


def _sanitize_string(text, field=""):
    if not isinstance(text, str):
        return text
    if field in ("origin_url", "expected_origin"):
        return normalize_git_origin(text)
    if field == "credential_profile_ref":
        ref = text.strip()
        return ref if PROFILE_REF_RE.fullmatch(ref) else ""
    if field == "login_cmd":
        cleaned = _EMBEDDED_TOKEN_RE.sub("", text)
        cleaned = strip_url_userinfo(cleaned)
        parts = []
        for piece in cleaned.split():
            key = piece.split("=", 1)[0] if "=" in piece else ""
            if _secret_key(key) or _looks_secret_value(piece):
                continue
            parts.append(piece)
        return " ".join(parts)
    if field in (
        "binary", "argv0", "worktree", "repo_root", "hostname", "username",
        "runner_id", "head", "agent_id", "ticket_agent", "status_cmd", "detail",
        "identity", "identity_label",
    ):
        if _EMBEDDED_TOKEN_RE.search(text):
            return ""
        return text
    if _looks_secret_value(text) or _EMBEDDED_TOKEN_RE.search(text):
        return ""
    return text


def _sanitize_nested(value, field=""):
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if _secret_key(k):
                continue
            cleaned = _sanitize_nested(v, field=k)
            if cleaned is None:
                continue
            if isinstance(cleaned, str) and cleaned == "" and _looks_secret_value(v if isinstance(v, str) else ""):
                continue
            out[k] = cleaned
        return out
    if isinstance(value, list):
        return [_sanitize_nested(item, field=field) for item in value]
    if isinstance(value, str):
        cleaned = _sanitize_string(value, field=field)
        return cleaned
    return value


def redact_auth_check(record):
    """Allowlist persisted fields; recurse dict/list; drop secret-shaped data."""
    if not isinstance(record, dict):
        return {}
    nested = _sanitize_nested(record)
    out = {}
    allowed = set(AUTH_CHECK_FIELDS) | {"lifecycle"}
    for k, v in nested.items():
        if k not in allowed:
            continue
        if k == "execution_context" and isinstance(v, dict):
            ctx = {}
            for ck in EXECUTION_CONTEXT_FIELDS:
                if ck in v:
                    ctx[ck] = v[ck]
            if "origin_url" in ctx:
                ctx["origin_url"] = normalize_git_origin(ctx.get("origin_url"))
            if "expected_origin" in ctx:
                ctx["expected_origin"] = normalize_git_origin(ctx.get("expected_origin"))
            out[k] = ctx
            continue
        if k == "pause" and isinstance(v, dict):
            out[k] = {pk: v[pk] for pk in PAUSE_FIELDS if pk in v}
            continue
        out[k] = v
    return out


def is_legacy_t610(record):
    """T-610 blobs have display fields but no complete V2 execution_context."""
    rec = record or {}
    return not context_is_complete(rec.get("execution_context"))


def load_auth_check(record):
    """Read a stored blob. Legacy T-610 remains display-readable, non-authoritative."""
    rec = redact_auth_check(dict(record or {}))
    if is_legacy_t610(rec):
        rec["authoritative"] = False
        return rec
    rec["authoritative"] = rec.get("authoritative") is True
    return rec


def validate_auth_check(record):
    """Return a list of contract violations for a V2 record. Empty means legal.

    Legacy T-610 blobs are not V2-legal; use load_auth_check for display.
    """
    errors = []
    rec = record or {}
    state = rec.get("state")
    if state not in AUTH_STATES:
        errors.append("state must be one of %s" % ",".join(AUTH_STATES))
    harness = rec.get("harness") or ""
    kinds = PROFILE_KINDS.get(harness)
    kind = rec.get("profile_kind")
    if kind:
        if not kinds:
            if harness not in ("remote", "custom"):
                errors.append("unknown harness %s" % harness)
        elif kind not in kinds:
            errors.append("profile_kind %s not allowed for %s" % (kind, harness))
    ref = rec.get("credential_profile_ref") or ""
    if ref and not PROFILE_REF_RE.fullmatch(str(ref)):
        errors.append("credential_profile_ref must be a strict opaque prf_ token")
    ctx = rec.get("execution_context")
    if ctx is None:
        errors.append("execution_context is required for a V2 record")
    elif not isinstance(ctx, dict):
        errors.append("execution_context must be an object")
    else:
        for field in REQUIRED_CONTEXT_IDENTITY_FIELDS:
            if not _nonempty_text(ctx.get(field)):
                errors.append("execution_context.%s is required" % field)
        runner_kind = ctx.get("runner_kind")
        if runner_kind not in RUNNER_KINDS:
            errors.append("runner_kind must be host|sandbox|container")
        expected = ctx.get("expected_origin") or ""
        origin = ctx.get("origin_url") or ""
        if expected and origin and not repo_identity_matches(expected, origin):
            errors.append("origin_url does not match expected_origin")
        if not seat_identity_matches(ctx.get("agent_id"), ctx.get("ticket_agent")):
            errors.append("ticket_agent must equal agent_id")
        lifecycle = ctx.get("lifecycle")
        if lifecycle not in (None, "") and lifecycle not in LIFECYCLES:
            errors.append("lifecycle must be persistent|ephemeral")
    for k in rec:
        if _secret_key(k):
            errors.append("forbidden secret key %s" % k)
    return errors


def profile_ref_ok(profile_ref):
    return bool(PROFILE_REF_RE.fullmatch((profile_ref or "").strip()))


def _contained_path(root, candidate):
    root_real = os.path.realpath(root)
    cand_real = os.path.realpath(candidate)
    return cand_real == root_real or cand_real.startswith(root_real + os.sep)


def profile_store_path(cache_root, board_hash, profile_ref):
    """Metadata file only (0600). Secrets stay in the provider CLI/keychain."""
    ref = (profile_ref or "").strip()
    if not profile_ref_ok(ref):
        raise ValueError("credential_profile_ref must be opaque")
    board = (board_hash or "").strip()
    if not BOARD_HASH_RE.fullmatch(board):
        raise ValueError("board hash must be a safe token")
    cache = os.path.realpath(os.path.abspath(cache_root))
    store_root = os.path.join(cache, "credentials", board)
    path = os.path.join(store_root, ref + ".json")
    if not _contained_path(store_root, path) or not _contained_path(cache, path):
        raise ValueError("credential_profile_ref escapes store")
    return path


def dumps_board_safe(record):
    return json.dumps(redact_auth_check(record), sort_keys=True)


AUTH_STATE_LABELS = {
    "ready": "Ready",
    "login_required": "Login required",
    "expired": "Credential expired",
    "quota": "Usage quota",
    "network": "Network",
    "unavailable": "Unavailable",
    "unsupported": "Unsupported",
}


def execution_context_line(ctx):
    """One-line host/binary/origin for Team/Connect. No secrets."""
    ctx = ctx or {}
    if not (ctx.get("hostname") or ctx.get("username") or ctx.get("origin_url")):
        return ""
    origin = ctx.get("origin_url") or ctx.get("expected_origin") or ""
    return ("%s@%s %s %s" % (
        ctx.get("username") or "?",
        ctx.get("hostname") or "?",
        ctx.get("runner_kind") or "?",
        origin,
    )).strip()


def on_enrolled_runner_host(enrolled_ctx, local_ctx):
    """True when this process may run a reconnect *probe* (never a secret form).

    Dashboard argv0 is `tickets`, the provider CLI is `agent`/`claude`/`codex`,
    so this is hostname + username + runner_kind — not the full fingerprint.
    A sandbox UI never executes host reconnect (T-830: login stays local/host).
    """
    enrolled = enrolled_ctx or {}
    local = local_ctx or {}
    if not enrolled.get("hostname") or not enrolled.get("username"):
        return False
    if enrolled.get("hostname") != local.get("hostname"):
        return False
    if enrolled.get("username") != local.get("username"):
        return False
    ek = enrolled.get("runner_kind") or ""
    lk = local.get("runner_kind") or ""
    if ek == "sandbox" or lk == "sandbox":
        return False
    if ek and lk and ek != lk:
        return False
    return True


def auth_readiness_surface(auth, enrolled_ctx=None, local_ctx=None, resume_at="",
                           harness="", agent_id=""):
    """T-687 Team/Connect payload. Never reports Ready unless authoritative."""
    rec = redact_auth_check(auth or {})
    ctx = rec.get("execution_context") or {}
    enrolled = enrolled_ctx or {}
    agent = agent_id or enrolled.get("agent_id") or ctx.get("agent_id") or ""
    stored_state = rec.get("state") if rec.get("state") in AUTH_STATES else ""
    authoritative = rec.get("authoritative") is True and context_is_complete(ctx)
    if enrolled and context_is_complete(enrolled):
        if context_is_complete(ctx):
            same = contexts_match(ctx, enrolled) or on_enrolled_runner_host(enrolled, ctx)
            authoritative = authoritative and same
        else:
            authoritative = False
    ready = stored_state == "ready" and authoritative
    display_state = stored_state if not (stored_state == "ready" and not authoritative) else ""
    pause = rec.get("pause") or {}
    paused = bool(pause.get("paused")) and not ready
    operator_path = "ready" if ready else (pause.get("operator_path") or OPERATOR_PATH.get(display_state, ""))
    on_host = on_enrolled_runner_host(enrolled or ctx, local_ctx or {})
    login_cmd = rec.get("login_cmd") or ""
    probe_cmd = ("tickets harness auth %s" % agent) if agent else "tickets harness auth <agent>"
    if display_state in ("login_required", "expired"):
        recovery_kind = "login"
        recovery_cmd = login_cmd or probe_cmd
        recovery_copy = (
            "Run this on the enrolled runner host. Atman never collects provider secrets.")
    elif display_state == "quota":
        recovery_kind = "quota"
        recovery_cmd = probe_cmd
        recovery_copy = "Usage quota is not a login failure. Recheck after the provider window resets."
    elif display_state == "network":
        recovery_kind = "network"
        recovery_cmd = probe_cmd
        recovery_copy = "Retry the status check on the enrolled runner host."
    elif display_state == "unavailable":
        recovery_kind = "unavailable"
        recovery_cmd = probe_cmd
        recovery_copy = "Fix runner, binary, or seat identity on the enrolled host, then recheck."
    elif display_state == "unsupported":
        recovery_kind = "unsupported"
        recovery_cmd = ""
        recovery_copy = "This adapter has no zero-model auth check. Do not treat as login."
    elif ready:
        recovery_kind = "ready"
        recovery_cmd = probe_cmd
        recovery_copy = "Auth is ready on the enrolled runner. Recheck does not start a model."
    else:
        recovery_kind = "unchecked"
        recovery_cmd = probe_cmd
        recovery_copy = "No authoritative probe yet. Recheck on the enrolled runner host."
    execute_here = bool(on_host and agent and recovery_kind != "unsupported")
    retained = bool(pause.get("retain_queue")) and (paused or display_state in NO_SPEND_STATES)
    if ready and resume_at:
        queue_copy = (
            "Queued work is eligible to resume (auth recovered at %s). "
            "That is not proof a model turn already ran." % resume_at)
        retained = False
    elif ready:
        queue_copy = (
            "Auth is ready. Queued work may resume; nothing here confirms it already ran.")
    elif retained:
        queue_copy = "Queued work is retained and has not run."
    else:
        queue_copy = ""
    return {
        "state": display_state,
        "stored_state": stored_state,
        "label": AUTH_STATE_LABELS.get(display_state) if display_state else "Not checked",
        "ready": ready,
        "authoritative": bool(authoritative),
        "paused": paused,
        "provider": rec.get("harness") or harness or "",
        "profile_kind": rec.get("profile_kind") or "",
        "identity_label": rec.get("identity_label") or "",
        "checked_at": rec.get("at") or "",
        "detail": rec.get("detail") or "",
        "context": execution_context_line(ctx) or execution_context_line(enrolled),
        "runner_id": ctx.get("runner_id") or enrolled.get("runner_id") or "",
        "hostname": ctx.get("hostname") or enrolled.get("hostname") or "",
        "login_cmd": login_cmd if operator_path == "login" else "",
        "recovery": {
            "kind": recovery_kind,
            "cmd": recovery_cmd,
            "copy": recovery_copy,
            "execute_here": execute_here,
            "on_enrolled_host": on_host,
        },
        "queue": {
            "retained": retained,
            "resumed_at": resume_at or "",
            "ran": False,
            "copy": queue_copy,
        },
        "credential_profile_ref": rec.get("credential_profile_ref") or "",
        "operator_path": operator_path,
    }
