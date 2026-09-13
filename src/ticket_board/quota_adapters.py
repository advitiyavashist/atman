"""Subscription quota parsers and honest availability states (T-862).

Not a second provider registry. Callers keep using INTEGRATION_CATALOG.
Unsupported or missing remaining/reset is unknown, never exhausted/FAIL.
"""
from __future__ import annotations

import json
import re

# Personal Cursor / Grok / Devin / Gemini have no documented remaining/reset
# contract. Catalog probes must not treat a missing meter as FAIL/exhausted.
UNSUPPORTED_QUOTA = frozenset({"cursor", "grok", "devin", "gemini"})
SUPPORTED_QUOTA = frozenset({"codex", "claude", "agy"})
_AUTH_MARKERS = (
    "login required", "not logged", "authentication required",
    "unauthenticated", "auth failed", "please log in", "please login",
)


def detect_auth_error(text):
    """Account/auth errors stay FAIL and stay separate from missing quota."""
    low = (text or "").lower()
    for marker in _AUTH_MARKERS:
        if marker in low:
            return "auth/login error"
    return ""


def _as_float(value):
    if value is None or value is False:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().rstrip("%")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def remaining_from_used_percent(used):
    pct = _as_float(used)
    if pct is None or pct < 0 or pct > 100:
        return None
    return "%g%%" % (100.0 - pct)


def remaining_from_fraction(frac):
    val = _as_float(frac)
    if val is None or val < 0 or val > 1:
        return None
    return "%g%%" % (100.0 * val)


def _first_reset(*values):
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _json_objects(text):
    raw = (text or "").strip()
    if not raw:
        return
    try:
        rec = json.loads(raw)
        if isinstance(rec, dict):
            yield rec
            return
    except ValueError:
        pass
    for m in re.finditer(r"\{[^{}]*\}", raw):
        try:
            rec = json.loads(m.group(0))
        except ValueError:
            continue
        if isinstance(rec, dict):
            yield rec


def parse_codex_rate_limits(text):
    """Codex account/rateLimits/read or rateLimits/updated fixture."""
    remaining, reset = None, None
    for rec in _json_objects(text):
        windows = rec.get("rateLimits") or rec.get("rate_limits") or rec.get("windows")
        if isinstance(windows, dict):
            items = windows.values()
        elif isinstance(windows, list):
            items = windows
        else:
            items = [rec]
        for item in items:
            if not isinstance(item, dict):
                continue
            used = item.get("used_percent")
            if used is None:
                used = item.get("used_percentage")
            if used is None:
                used = item.get("usedPercent")
            got = remaining_from_used_percent(used)
            rst = _first_reset(
                item.get("resets_at"), item.get("reset_at"), item.get("resetsAt"),
                item.get("resetAt"), item.get("reset"))
            if got is not None:
                remaining = got
            if rst:
                reset = rst
            if remaining is not None and reset is not None:
                return remaining, reset
    return remaining, reset


def parse_claude_statusline(text):
    """Claude Pro/Max status-line rate_limits.five_hour / seven_day."""
    remaining, reset = None, None
    for rec in _json_objects(text):
        limits = rec.get("rate_limits") or rec.get("rateLimits") or {}
        if not isinstance(limits, dict):
            continue
        for key in ("five_hour", "seven_day", "fiveHour", "sevenDay"):
            window = limits.get(key)
            if not isinstance(window, dict):
                continue
            got = remaining_from_used_percent(
                window.get("used_percentage") or window.get("used_percent"))
            rst = _first_reset(window.get("resets_at"), window.get("reset_at"))
            if got is not None:
                remaining = got
            if rst:
                reset = rst
            if remaining is not None:
                break
    return remaining, reset


def parse_agy_quota(text):
    """Agy status-line quota[bucket].remaining_fraction + reset_time."""
    remaining, reset = None, None
    for rec in _json_objects(text):
        quota = rec.get("quota")
        if not isinstance(quota, dict):
            continue
        for window in quota.values():
            if not isinstance(window, dict):
                continue
            got = remaining_from_fraction(window.get("remaining_fraction"))
            rst = _first_reset(
                window.get("reset_time"), window.get("resets_at"),
                window.get("reset_in_seconds"))
            if got is not None:
                remaining = got
            if rst:
                reset = rst
            if remaining is not None:
                break
    return remaining, reset


def parse_cursor_admin(text):
    """Read-only Cursor team/org pooled-usage fixture. Not personal about/status."""
    remaining, reset = None, None
    for rec in _json_objects(text):
        pooled = rec.get("pooled_usage") or rec.get("pooledUsage") or rec
        if not isinstance(pooled, dict):
            continue
        if "remaining_percent" in pooled or "remainingPercent" in pooled:
            pct = _as_float(pooled.get("remaining_percent") or pooled.get("remainingPercent"))
            if pct is not None and 0 <= pct <= 100:
                remaining = "%g%%" % pct
        elif "used_percent" in pooled or "usedPercent" in pooled:
            remaining = remaining_from_used_percent(
                pooled.get("used_percent") or pooled.get("usedPercent"))
        reset = _first_reset(
            pooled.get("resets_at"), pooled.get("reset_at"), pooled.get("resetsAt"))
        if remaining is not None:
            return remaining, reset
    return remaining, reset


def parse_provider_quota(provider, text, allow_generic=True):
    """Return (remaining, reset) from a supported adapter. Unknown stays (None, None)."""
    hid = (provider or "").strip().lower()
    if hid == "codex":
        remaining, reset = parse_codex_rate_limits(text)
        if remaining is not None or reset is not None:
            return remaining, reset
    elif hid == "claude":
        remaining, reset = parse_claude_statusline(text)
        if remaining is not None or reset is not None:
            return remaining, reset
    elif hid in ("agy", "antigravity"):
        remaining, reset = parse_agy_quota(text)
        if remaining is not None or reset is not None:
            return remaining, reset
    elif hid == "cursor-admin":
        return parse_cursor_admin(text)
    if hid in UNSUPPORTED_QUOTA:
        return None, None
    if allow_generic and hid in SUPPORTED_QUOTA:
        return None, None  # caller may apply the T-793 generic parse
    return None, None


def is_exhausted(remaining):
    if remaining is None:
        return False
    text = str(remaining).strip().rstrip("%")
    try:
        return float(text) == 0.0
    except ValueError:
        return text in ("0", "0.0", "0%")


def classify_catalog_usage(provider, remaining, reset, reason="", on_disk=True,
                           cursor_admin=False):
    """Honest availability: unknown ≠ exhausted ≠ auth FAIL.

    Installation/auth/quota/wake stay separate. Missing remaining/reset on an
    unsupported or incomplete snapshot is unknown.
    """
    hid = (provider or "").strip().lower()
    reason = (reason or "").strip()
    if not on_disk:
        return "unknown", "not installed"
    low = reason.lower()
    if "auth" in low or "login required" in low or "not logged" in low:
        return "FAIL", reason
    if hid in UNSUPPORTED_QUOTA and not (hid == "cursor" and cursor_admin):
        return "unknown", reason or "unsupported quota adapter"
    if remaining is not None and is_exhausted(remaining):
        return "exhausted", reason or "remaining is 0"
    if remaining is not None and reset is not None:
        return "ok", reason
    if remaining is None and reset is None:
        return "unknown", reason or "no remaining/reset in probe"
    return "unknown", reason or "incomplete quota snapshot"
