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


def merge_auth_check(previous, incoming, runner_ctx):
    """Apply an incoming probe to the stored blob.

    A non-matching / non-authoritative / malformed probe never replaces
    *any* authoritative runner blob. For stored lineage, stored,
    incoming, and enrolled caller contexts must all agree. Silent runner
    rebind is forbidden here; T-686 may add an explicit fenced rebind later.

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
        if not (
            contexts_match(prev_ctx, inc_ctx)
            and contexts_match(prev_ctx, runner_ctx)
            and contexts_match(inc_ctx, runner_ctx)
        ):
            return previous
        incoming_auth = is_authoritative(incoming, prev_ctx)
        if not incoming_auth:
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
