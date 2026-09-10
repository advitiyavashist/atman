"""T-685 auth V2 contract: execution context, credential profiles, merge rules.

This module is the frozen schema and decision table for T-686. It does not
run provider CLIs, start models, or store secrets. T-610 `auth_check` remains
the on-disk blob; this file names the fields T-686 must add and the merge
rules that prevent a false `login_required`.
"""

import json
import os
import re

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
    "head",
    "agent_id",       # enrolled seat (tickets join / spawn name)
    "ticket_agent",   # process TICKET_AGENT; must equal agent_id
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


def normalize_git_origin(url):
    """Collapse GitHub HTTPS/SSH URLs to owner/repo. Other URLs stay stripped."""
    text = (url or "").strip()
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
    "agent_id",
)


def context_fingerprint(ctx):
    """Stable identity of where a probe ran. Host vs sandbox must differ.

    HEAD is excluded: it changes on ordinary commits and is not auth authority.
    Username, argv0, and env_fingerprint (credential-relevant env *names*,
    never values) are included so another OS user or env cannot overwrite the
    enrolled runner blob. Repo identity for spawn is origin, not path/HEAD.
    """
    ctx = ctx or {}
    parts = []
    for field in AUTH_CONTEXT_IDENTITY_FIELDS:
        val = ctx.get(field) or ""
        if field == "origin_url":
            val = normalize_git_origin(val)
        parts.append(str(val))
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
    if probe_ctx is not None and runner_ctx is not None:
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
    if not probe_ctx or not runner_ctx:
        return False
    return context_fingerprint(probe_ctx) == context_fingerprint(runner_ctx)


def is_authoritative(auth_check, runner_ctx):
    rec = auth_check or {}
    if rec.get("authoritative") is False:
        return False
    ctx = rec.get("execution_context") or {}
    if not ctx:
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
    return bool(rec.get("execution_context"))


def merge_auth_check(previous, incoming, runner_ctx):
    """Apply an incoming probe to the stored blob.

    A non-matching / non-authoritative probe never replaces *any*
    authoritative runner blob (`ready`, `quota`, `login_required`,
    `expired`, `network`, `unavailable`, `unsupported`). Matching-context
    probes may replace.

    Stored authority is the previous blob's execution_context. Comparing
    only against the caller's runner_ctx would let sandbox ready clobber
    host quota when the caller passes sandbox_ctx. Silent runner rebind
    is forbidden here; T-686 may add an explicit fenced rebind later.
    """
    previous = dict(previous or {})
    incoming = dict(incoming or {})
    prev_ctx = previous.get("execution_context") or {}
    inc_ctx = incoming.get("execution_context") or {}
    if _stored_authoritative_lineage(previous):
        if not contexts_match(prev_ctx, inc_ctx):
            return previous
        incoming_auth = is_authoritative(incoming, prev_ctx)
        if not incoming_auth:
            return previous
    else:
        incoming_auth = is_authoritative(incoming, runner_ctx)
        previous_auth = is_authoritative(previous, runner_ctx)
        if previous_auth and not incoming_auth:
            return previous
    incoming["authoritative"] = incoming_auth
    merged = dict(previous)
    merged.update(incoming)
    merged["authoritative"] = incoming_auth
    lifecycle = (
        (runner_ctx or {}).get("lifecycle")
        or merged.get("lifecycle")
        or "ephemeral"
    )
    if merged.get("state") in NO_SPEND_STATES:
        merged["pause"] = pause_policy(lifecycle, merged.get("state"))
        merged["alert_id"] = alert_id(
            (runner_ctx or {}).get("agent_id") or merged.get("agent_id") or "",
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


def redact_auth_check(record):
    """Drop secret-shaped keys; keep opaque profile refs and safe labels."""
    if not isinstance(record, dict):
        return {}
    out = {}
    for k, v in record.items():
        if _secret_key(k):
            continue
        if k == "execution_context" and isinstance(v, dict):
            out[k] = redact_auth_check(v)
            continue
        if isinstance(v, str) and _looks_secret_value(v):
            continue
        out[k] = v
    return out


def _looks_secret_value(text):
    if not text or len(text) < 24:
        return False
    if text.startswith("prf_"):
        return False
    if " " in text or "@" in text:
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9_\-./+=]{32,}", text))


def validate_auth_check(record):
    """Return a list of contract violations. Empty means the record is legal."""
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
    if ref and not str(ref).startswith("prf_"):
        errors.append("credential_profile_ref must be an opaque prf_ token")
    ctx = rec.get("execution_context")
    if ctx is None:
        errors.append("execution_context is required for a V2 record")
    elif not isinstance(ctx, dict):
        errors.append("execution_context must be an object")
    else:
        runner_kind = ctx.get("runner_kind")
        if runner_kind not in RUNNER_KINDS:
            errors.append("runner_kind must be host|sandbox|container")
        expected = ctx.get("expected_origin") or ""
        origin = ctx.get("origin_url") or ""
        if expected and origin and not repo_identity_matches(expected, origin):
            errors.append("origin_url does not match expected_origin")
        if not seat_identity_matches(ctx.get("agent_id"), ctx.get("ticket_agent")):
            errors.append("ticket_agent must equal agent_id")
    for k in rec:
        if _secret_key(k):
            errors.append("forbidden secret key %s" % k)
    return errors


def profile_store_path(cache_root, board_hash, profile_ref):
    """Metadata file only (0600). Secrets stay in the provider CLI/keychain."""
    ref = (profile_ref or "").strip()
    if not ref.startswith("prf_"):
        raise ValueError("credential_profile_ref must be opaque")
    return os.path.join(cache_root, "credentials", board_hash, ref + ".json")


def dumps_board_safe(record):
    return json.dumps(redact_auth_check(record), sort_keys=True)
