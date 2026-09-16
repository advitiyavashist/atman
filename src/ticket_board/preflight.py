"""T-1043: binary + account preflight, separate from usage."""
from __future__ import annotations

from datetime import datetime, timezone

CACHE_SECS = 90
DISPATCHABLE_STATES = frozenset(("ready", "quota"))
ACCOUNT_FAIL_STATES = frozenset(("login_required", "expired"))

INSTALL_HINT = {
    "cursor": "install the Cursor CLI (`agent`) and put it on PATH",
    "cursor+claude": "install the Cursor CLI (`agent`) and put it on PATH",
    "claude": "install Claude Code (`claude`) and put it on PATH",
    "codex": "install the Codex CLI (`codex`) and put it on PATH",
}

LOGIN_HINT = {
    "cursor": "agent login",
    "cursor+claude": "agent login",
    "claude": "claude auth login",
    "codex": "codex login",
}


def parse_iso(ts):
    raw = (ts or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def age_s(ts, now=None):
    at = parse_iso(ts)
    if at is None:
        return None
    if now is None:
        now = datetime.now(timezone.utc)
    elif isinstance(now, str):
        now = parse_iso(now)
        if now is None:
            return None
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return max(0.0, (now - at).total_seconds())


def cached_positive(rec, now=None, ttl=CACHE_SECS):
    """Fresh successful preflight, or a still-fresh dispatchable auth_check."""
    rec = rec or {}
    blob = rec.get("preflight") if isinstance(rec.get("preflight"), dict) else {}
    if blob.get("ok"):
        secs = age_s(blob.get("at"), now)
        if secs is not None and secs <= ttl:
            return blob
    auth = rec.get("auth_check") if isinstance(rec.get("auth_check"), dict) else {}
    if auth.get("state") in DISPATCHABLE_STATES:
        secs = age_s(auth.get("at"), now)
        if secs is not None and secs <= ttl:
            return {
                "ok": True,
                "at": auth.get("at"),
                "state": auth.get("state"),
                "harness": auth.get("harness") or "",
            }
    return None


def is_missing_binary(result):
    rec = result or {}
    if rec.get("exit") == 127:
        return True
    detail = (rec.get("detail") or "").lower()
    return "is not installed" in detail or "no such file" in detail


def dispatch_refuse(result, harness=""):
    """Why dispatch/spawn must not hand work, or empty to allow.

    Usage (quota / unreadable remaining) never refuses: that is not account state.
    """
    rec = result or {}
    hid = (rec.get("harness") or harness or "harness").strip() or "harness"
    state = rec.get("state") or ""
    if state in DISPATCHABLE_STATES or state == "unsupported":
        return ""
    if is_missing_binary(rec) or state == "unavailable":
        hint = INSTALL_HINT.get(hid, "install `%s` and put it on PATH" % hid)
        return "preflight: %s binary missing -- %s" % (hid, hint)
    if state in ACCOUNT_FAIL_STATES:
        login = rec.get("login_cmd") or LOGIN_HINT.get(hid, "re-login")
        return ("preflight: %s is logged out -- run `%s`, then "
                "`atm harness auth <seat>`" % (hid, login))
    if state == "network":
        return ("preflight: %s auth probe failed (network) -- retry when the "
                "host can reach the provider" % hid)
    return ""


def route_skip(result, harness=""):
    """Skip only a confirmed logged-out account. Missing binary may be another host."""
    rec = result or {}
    if rec.get("state") in ACCOUNT_FAIL_STATES:
        return dispatch_refuse(rec, harness)
    return ""


def snapshot(result, ok):
    rec = result or {}
    return {
        "ok": bool(ok),
        "at": rec.get("at") or "",
        "state": rec.get("state") or "",
        "harness": rec.get("harness") or "",
        "reason": "" if ok else dispatch_refuse(rec, rec.get("harness") or ""),
    }
