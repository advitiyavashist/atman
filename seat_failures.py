"""Seat failure classification and backoff (T-1500).

Spawn/run failures use exponential backoff (1s→30s) that resets on success.
Errors are terminal (auth/permission/binary) or retryable (rate limit/5xx/network).
Three identical consecutive errors stop the seat; any different error or real
progress resets the counter. Credentials are always re-read at spawn time.
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone

TERMINAL = "terminal"
RETRYABLE = "retryable"
IDENTICAL_STOP = 3
BACKOFF_FLOOR_SECS = 1
BACKOFF_CAP_SECS = 30

_TERMINAL_PATTERNS = (
    re.compile(r"\bnot installed\b", re.I),
    re.compile(r"\bcommand not found\b", re.I),
    re.compile(r"\bno such file\b", re.I),
    re.compile(r"\bpermission denied\b", re.I),
    re.compile(r"\blogin[_ ]required\b", re.I),
    re.compile(r"\bnot logged in\b", re.I),
    re.compile(r"\bauthentication (required|failed)\b", re.I),
    re.compile(r"\bunauthorized\b", re.I),
    re.compile(r"\bforbidden\b", re.I),
    re.compile(r"\bbinary missing\b", re.I),
)

_RETRYABLE_PATTERNS = (
    re.compile(r"\brate.?limit", re.I),
    re.compile(r"\btoo many requests\b", re.I),
    re.compile(r"\b429\b"),
    re.compile(r"\b5\d\d\b"),
    re.compile(r"\binternal server error\b", re.I),
    re.compile(r"\bbad gateway\b", re.I),
    re.compile(r"\bservice unavailable\b", re.I),
    re.compile(r"\bgateway timeout\b", re.I),
    re.compile(r"\bconnection (refused|reset|timed out)\b", re.I),
    re.compile(r"\bnetwork (is )?unreachable\b", re.I),
    re.compile(r"\btemporary failure\b", re.I),
    re.compile(r"\bcould not resolve\b", re.I),
    re.compile(r"\btls handshake\b", re.I),
)

_AUTH_TERMINAL_STATES = frozenset({
    "login_required", "expired", "unavailable", "unsupported",
})
_AUTH_RETRYABLE_STATES = frozenset({"network", "quota"})


def classify_error(text="", *, rc=None, auth_state=""):
    """Return TERMINAL or RETRYABLE for a harness/spawn/run failure."""
    state = (auth_state or "").strip()
    if state in _AUTH_TERMINAL_STATES:
        return TERMINAL
    if state in _AUTH_RETRYABLE_STATES:
        return RETRYABLE
    blob = str(text or "")
    for pat in _TERMINAL_PATTERNS:
        if pat.search(blob):
            return TERMINAL
    for pat in _RETRYABLE_PATTERNS:
        if pat.search(blob):
            return RETRYABLE
    try:
        code = int(rc) if rc is not None else None
    except (TypeError, ValueError):
        code = None
    if code in (126, 127):
        return TERMINAL
    if code in (429,) or (code is not None and 500 <= code <= 599):
        return RETRYABLE
    # Unknown nonzero exits are retryable so flaky harnesses can recover.
    return RETRYABLE


def error_key(text="", *, rc=None, auth_state=""):
    """Stable fingerprint for 'identical consecutive error' counting."""
    detail = (str(text or "").strip().splitlines() or [""])[-1].strip()
    detail = re.sub(r"\s+", " ", detail)[:160]
    return "%s|%s|%s" % (auth_state or "", rc if rc is not None else "", detail)


def backoff_seconds(identical_count):
    """Exponential 1s → 30s from the identical-error streak length."""
    n = max(1, int(identical_count or 1))
    return min(BACKOFF_CAP_SECS, BACKOFF_FLOOR_SECS * (2 ** (n - 1)))


def _utc_iso(epoch=None):
    ts = time.time() if epoch is None else float(epoch)
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def next_failure_record(previous, *, reason, rc=None, auth_state="", trigger="",
                        provider="", harness="", clock=None):
    """Build the next adapter_failure record from a prior one + this error."""
    now_epoch = time.time() if clock is None else float(clock)
    kind = classify_error(reason, rc=rc, auth_state=auth_state)
    key = error_key(reason, rc=rc, auth_state=auth_state)
    prev = previous or {}
    if prev.get("error_key") == key:
        identical = int(prev.get("identical") or 0) + 1
    else:
        identical = 1
    stop = identical >= IDENTICAL_STOP or kind == TERMINAL
    delay = 0 if stop else backoff_seconds(identical)
    retry_epoch = (now_epoch + delay) if delay else 0
    return {
        "state": "failed" if stop else "retrying",
        "kind": kind,
        "error_key": key,
        "identical": identical,
        "identical_stop": IDENTICAL_STOP,
        "reason": (reason or "")[:240],
        "rc": rc,
        "auth_state": auth_state or "",
        "trigger": trigger or "",
        "attempts": identical,  # compat with older readers
        "max_attempts": IDENTICAL_STOP,
        "retry_epoch": retry_epoch,
        "retry_at": _utc_iso(retry_epoch) if retry_epoch else "",
        "at": _utc_iso(now_epoch),
        "provider": provider or "",
        "harness": harness or "",
    }


def plain_failure_status(failure, *, now_epoch=None):
    """One plain-language line: last error and when the next retry is."""
    failure = failure or {}
    if not failure:
        return ""
    reason = (failure.get("reason") or "error").strip()
    state = failure.get("state") or ""
    kind = failure.get("kind") or ""
    identical = int(failure.get("identical") or failure.get("attempts") or 0)
    if state == "failed":
        if identical >= IDENTICAL_STOP:
            return "stopped after %d identical errors: %s" % (identical, reason)
        if kind == TERMINAL:
            return "terminal failure (will re-check on next spawn): %s" % reason
        return "failed: %s" % reason
    retry_at = failure.get("retry_at") or ""
    retry_epoch = float(failure.get("retry_epoch") or 0)
    clock = time.time() if now_epoch is None else float(now_epoch)
    if retry_epoch and retry_epoch > clock:
        secs = int(max(0, retry_epoch - clock))
        return "last error: %s; next retry in %ds (%s)" % (reason, secs, retry_at)
    if retry_at:
        return "last error: %s; next retry at %s" % (reason, retry_at)
    return "last error: %s; retrying" % reason


def failure_blocks_retry(failure, *, now_epoch=None, force=False):
    """True when watch should not re-dispatch the same failed trigger yet."""
    if force or not failure:
        return False
    state = failure.get("state")
    if state == "failed":
        return True
    if state != "retrying":
        return False
    clock = time.time() if now_epoch is None else float(now_epoch)
    return float(failure.get("retry_epoch") or 0) > clock
