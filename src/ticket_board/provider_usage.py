"""T-1040: per-provider usage ledger. Observed limits + credentialed reads.

Honesty: unknown remaining is unknown (never zero, never 'fine'/'available').
No reset unless the provider gave one. Token counts are the harness's own
report. Cursor and Antigravity stay 'no data'. Credentials are never stored
on the board, logged, or returned in readings.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone

LEDGER_NAME = "provider_usage.json"
NO_DATA_PROVIDERS = frozenset({"cursor", "agy", "antigravity"})
HTTP_PROVIDERS = frozenset({"claude", "codex"})
CLAUDE_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
CODEX_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
_CODEX_TOKENS = re.compile(r"tokens used\s*[\r\n]+\s*(\d+)\b", re.I)
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T")


def utcnow():
    return datetime.now(timezone.utc)


def iso_now(when=None):
    return (when or utcnow()).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(text):
    text = (text or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def age_label(checked_at, now=None):
    """Human age for a persisted reading. Empty checked_at is not labelled."""
    dt = parse_iso(checked_at)
    if dt is None:
        return ""
    now = now or utcnow()
    secs = max(0, int((now - dt).total_seconds()))
    if secs < 60:
        return "last read %ss ago" % secs
    mins = secs // 60
    if mins < 60:
        return "last read %sm ago" % mins
    hours = mins // 60
    if hours < 48:
        return "last read %sh ago" % hours
    return "last read %sd ago" % (hours // 24)


def empty_reading(provider, status="unknown", hint="", checked_at="",
                  source="", account_state="unknown"):
    hid = (provider or "").strip().lower() or "unknown"
    if hid in ("antigravity",):
        hid = "agy"
    if hid in NO_DATA_PROVIDERS:
        status = "no_data"
        hint = hint or "no usage source (checked: CLI and Orca)"
        source = source or "none"
    return {
        "provider": hid,
        "status": status,
        "account_state": account_state,
        "windows": [],
        "remaining": None,
        "reset_at": None,
        "limit_message": "",
        "tokens_reported": None,
        "tokens_label": "",
        "source": source,
        "checked_at": checked_at or "",
        "hint": hint,
    }


def parse_codex_tokens_used(text):
    """Harness-reported Codex line: 'tokens used' then a number."""
    m = _CODEX_TOKENS.search(text or "")
    if not m:
        return None
    return int(m.group(1))


def parse_claude_session_usage(record):
    """Harness-reported Claude session JSONL usage object. None if absent."""
    if not isinstance(record, dict):
        return None
    usage = record.get("usage")
    if not isinstance(usage, dict):
        return None
    keys = ("input_tokens", "output_tokens", "cache_read_input_tokens",
            "cache_creation_input_tokens")
    if not any(k in usage for k in keys):
        return None
    total = 0
    found = False
    for k in keys:
        try:
            total += int(usage.get(k) or 0)
            found = True
        except (TypeError, ValueError):
            return None
    return total if found else None


def _as_float(value):
    if value is None or isinstance(value, bool):
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


def _used_percent(item):
    if not isinstance(item, dict):
        return None
    for key in ("used_percent", "used_percentage", "usedPercent",
                "percent_used", "percentUsed", "used"):
        pct = _as_float(item.get(key))
        if pct is None:
            continue
        if 0 <= pct <= 1 and key in ("used",):
            pct = pct * 100.0
        if 0 <= pct <= 100:
            return pct
    return None


def _reset_of(item):
    if not isinstance(item, dict):
        return None
    for key in ("resets_at", "reset_at", "resetsAt", "resetAt", "reset"):
        text = item.get(key)
        if text is None:
            continue
        text = str(text).strip()
        if text:
            return text
    return None


def _window(name, item):
    used = _used_percent(item)
    if used is None:
        return None
    remaining = 100.0 - used
    return {
        "name": name,
        "used_percent": used,
        "remaining_percent": remaining,
        "reset_at": _reset_of(item),
    }


def _reading_from_windows(provider, windows, checked_at, source):
    rec = empty_reading(provider, status="unknown", checked_at=checked_at,
                        source=source)
    rec["windows"] = windows
    if not windows:
        rec["hint"] = "unparseable usage body"
        return rec
    worst = max(windows, key=lambda w: w["used_percent"])
    rec["remaining"] = "%g%%" % worst["remaining_percent"]
    rec["reset_at"] = worst.get("reset_at")
    if worst["used_percent"] >= 100.0:
        rec["status"] = "limited"
        rec["hint"] = ""
    else:
        rec["status"] = "ok"
        rec["hint"] = ""
    return rec


def parse_claude_oauth_usage(body, checked_at=""):
    """Map GET /api/oauth/usage. Unparseable body → unknown, never available."""
    rec = empty_reading("claude", status="unknown",
                        checked_at=checked_at, source="oauth_usage")
    try:
        data = json.loads(body) if isinstance(body, str) else body
    except (TypeError, ValueError):
        rec["hint"] = "unparseable usage body"
        return rec
    if not isinstance(data, dict):
        rec["hint"] = "unparseable usage body"
        return rec
    limits = data.get("rate_limits") or data.get("rateLimits") or data
    if not isinstance(limits, dict):
        rec["hint"] = "unparseable usage body"
        return rec
    windows = []
    for key in ("five_hour", "seven_day", "fiveHour", "sevenDay"):
        item = limits.get(key)
        win = _window(key, item) if isinstance(item, dict) else None
        if win:
            windows.append(win)
    return _reading_from_windows("claude", windows, checked_at, "oauth_usage")


def parse_codex_wham_usage(body, checked_at=""):
    """Map GET /backend-api/wham/usage. Unparseable body → unknown."""
    rec = empty_reading("codex", status="unknown",
                        checked_at=checked_at, source="wham_usage")
    try:
        data = json.loads(body) if isinstance(body, str) else body
    except (TypeError, ValueError):
        rec["hint"] = "unparseable usage body"
        return rec
    if not isinstance(data, dict):
        rec["hint"] = "unparseable usage body"
        return rec
    raw = (data.get("rateLimits") or data.get("rate_limits")
           or data.get("windows") or data)
    items = []
    if isinstance(raw, dict):
        items = [(k, v) for k, v in raw.items() if isinstance(v, dict)
                 and _used_percent(v) is not None]
    elif isinstance(raw, list):
        items = [(str(i), v) for i, v in enumerate(raw) if isinstance(v, dict)]
    windows = []
    for name, item in items:
        win = _window(name, item)
        if win:
            windows.append(win)
    return _reading_from_windows("codex", windows, checked_at, "wham_usage")


def record_observed_limit(provider, message, observed_at, reset_at=""):
    """Fallback from a provider rejection. Reset only if the provider gave one."""
    rec = empty_reading(provider, status="limited", checked_at=observed_at,
                        source="watch_rejection")
    rec["limit_message"] = (message or "").strip()
    rec["reset_at"] = (reset_at or "").strip() or None
    rec["hint"] = ""
    return rec


def expire_stale_resets(reading, now=None):
    """A past reset_at drops limited → unknown (not available)."""
    if not reading or reading.get("status") != "limited":
        return reading
    reset = parse_iso(reading.get("reset_at"))
    if reset is None:
        return reading
    now = now or utcnow()
    if reset <= now:
        out = dict(reading)
        out["status"] = "unknown"
        out["hint"] = "reset elapsed"
        return out
    return reading


def ledger_path(board):
    return os.path.join(board, LEDGER_NAME)


def load_ledger(board):
    path = ledger_path(board)
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {"providers": {}, "updated": ""}
    if not isinstance(data, dict):
        return {"providers": {}, "updated": ""}
    providers = data.get("providers")
    if not isinstance(providers, dict):
        providers = {}
    return {"providers": providers, "updated": data.get("updated") or ""}


def save_ledger(board, ledger):
    os.makedirs(board, exist_ok=True)
    path = ledger_path(board)
    tmp = path + ".tmp"
    payload = {
        "providers": ledger.get("providers") or {},
        "updated": ledger.get("updated") or iso_now(),
    }
    with open(tmp, "w") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return payload


def put_reading(board, reading, now=None):
    ledger = load_ledger(board)
    hid = reading["provider"]
    ledger["providers"][hid] = expire_stale_resets(reading, now)
    ledger["updated"] = iso_now(now)
    save_ledger(board, ledger)
    return ledger["providers"][hid]


def get_reading(board, provider, now=None):
    hid = (provider or "").strip().lower()
    if hid in ("antigravity",):
        hid = "agy"
    if hid in NO_DATA_PROVIDERS:
        return empty_reading(hid, status="no_data")
    rec = (load_ledger(board).get("providers") or {}).get(hid)
    if not rec:
        return empty_reading(hid, status="unknown", hint="no data")
    return expire_stale_resets(rec, now)


def public_reading(reading, now=None):
    """Board/UI snapshot. Never includes credentials or raw HTTP bodies."""
    rec = expire_stale_resets(dict(reading or {}), now)
    return {
        "provider": rec.get("provider") or "",
        "status": rec.get("status") or "unknown",
        "account_state": rec.get("account_state") or "unknown",
        "remaining": rec.get("remaining"),
        "reset_at": rec.get("reset_at"),
        "limit_message": rec.get("limit_message") or "",
        "tokens_reported": rec.get("tokens_reported"),
        "tokens_label": rec.get("tokens_label") or "",
        "source": rec.get("source") or "",
        "checked_at": rec.get("checked_at") or "",
        "age": age_label(rec.get("checked_at"), now),
        "hint": rec.get("hint") or "",
        "windows": list(rec.get("windows") or []),
    }


def format_usage_line(reading, now=None):
    rec = public_reading(reading, now)
    hid = rec["provider"] or "?"
    status = rec["status"]
    bits = ["%s %s" % (hid, status)]
    if status == "no_data":
        bits.append("no data")
    elif rec["remaining"] is not None:
        bits.append("remaining %s (harness/provider report)" % rec["remaining"])
    elif status == "unknown":
        bits.append("UNKNOWN")
    if rec["reset_at"]:
        bits.append("resets %s" % rec["reset_at"])
    if rec["age"]:
        bits.append(rec["age"])
    if rec["hint"]:
        bits.append(rec["hint"])
    if rec["limit_message"]:
        bits.append(rec["limit_message"][:160])
    if rec["tokens_reported"] is not None:
        label = rec["tokens_label"] or "harness report"
        bits.append("tokens %s (%s)" % (rec["tokens_reported"], label))
    return "  " + " · ".join(bits)


def format_ledger_lines(board, now=None, providers=("claude", "codex", "cursor", "agy")):
    lines = ["USAGE (observed + credentialed read; unknown stays unknown)"]
    for hid in providers:
        lines.append(format_usage_line(get_reading(board, hid, now), now))
    return lines


def reading_from_http(provider, status_code, body, checked_at="",
                      account_state="unknown"):
    hid = (provider or "").strip().lower()
    if status_code == 401:
        rec = empty_reading(hid, status="unknown", checked_at=checked_at,
                            source="http_401", account_state="signed_out")
        rec["hint"] = "re-login required"
        return rec
    if hid == "claude":
        rec = parse_claude_oauth_usage(body, checked_at)
    elif hid == "codex":
        rec = parse_codex_wham_usage(body, checked_at)
    else:
        rec = empty_reading(hid, status="unknown", checked_at=checked_at,
                            hint="no usage source")
    rec["account_state"] = account_state
    return rec


def empty_credential_reading(provider, checked_at=""):
    rec = empty_reading(provider, status="unknown", checked_at=checked_at,
                        source="empty_credential", account_state="signed_out")
    rec["hint"] = "re-login required"
    return rec


def timeout_reading(provider, checked_at=""):
    rec = empty_reading(provider, status="unknown", checked_at=checked_at,
                        source="timeout")
    rec["hint"] = "usage request timed out"
    return rec


def _read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def read_claude_oauth_token(home, environ=None):
    """Return access token string or ''. Never raises. Does not log the token."""
    env = environ if environ is not None else os.environ
    direct = (env.get("ANTHROPIC_OAUTH_TOKEN") or "").strip()
    if direct:
        return direct
    home = os.path.expanduser(home or "~")
    candidates = (
        os.path.join(home, ".claude", ".credentials.json"),
        os.path.join(home, ".claude", "credentials.json"),
        os.path.join(home, ".config", "claude", ".credentials.json"),
    )
    for path in candidates:
        data = _read_json(path)
        if not isinstance(data, dict):
            continue
        oauth = data.get("claudeAiOauth") or data.get("oauth") or data
        if not isinstance(oauth, dict):
            continue
        token = (oauth.get("accessToken") or oauth.get("access_token") or "").strip()
        if token:
            return token
    return ""


def read_codex_auth_token(home, environ=None):
    env = environ if environ is not None else os.environ
    direct = (env.get("CODEX_AUTH_TOKEN") or env.get("CHATGPT_ACCESS_TOKEN") or "").strip()
    if direct:
        return direct
    home = os.path.expanduser(home or "~")
    candidates = (
        os.path.join(home, ".codex", "auth.json"),
        os.path.join(home, ".codex", "config.toml"),
    )
    for path in candidates:
        if path.endswith(".toml"):
            continue
        data = _read_json(path)
        if not isinstance(data, dict):
            continue
        tokens = data.get("tokens") if isinstance(data.get("tokens"), dict) else data
        token = (tokens.get("access_token") or tokens.get("accessToken")
                 or data.get("access_token") or "").strip()
        if token:
            return token
    return ""


def default_transport(url, headers, timeout):
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
            return resp.getcode(), body
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace") if exc.fp else ""
        return exc.code, body
    except TimeoutError:
        raise
    except Exception as exc:
        if "timed out" in str(exc).lower():
            raise TimeoutError(str(exc))
        raise


def fetch_provider_usage(provider, home="~", timeout=8, transport=None,
                         environ=None, now=None):
    """Credentialed read. Failures are unknown. Credentials never leave this fn."""
    hid = (provider or "").strip().lower()
    checked = iso_now(now)
    if hid in NO_DATA_PROVIDERS or hid == "antigravity":
        return empty_reading("agy" if hid == "antigravity" else hid,
                             status="no_data", checked_at=checked)
    if hid not in HTTP_PROVIDERS:
        return empty_reading(hid, status="unknown", checked_at=checked,
                             hint="no usage source")
    if hid == "claude":
        token = read_claude_oauth_token(home, environ)
        url = CLAUDE_USAGE_URL
        headers = {"Authorization": "Bearer %s" % token,
                   "Accept": "application/json"}
    else:
        token = read_codex_auth_token(home, environ)
        url = CODEX_USAGE_URL
        headers = {"Authorization": "Bearer %s" % token,
                   "Accept": "application/json"}
    if not token:
        return empty_credential_reading(hid, checked)
    send = transport or default_transport
    try:
        code, body = send(url, headers, timeout)
    except TimeoutError:
        return timeout_reading(hid, checked)
    except Exception:
        rec = empty_reading(hid, status="unknown", checked_at=checked,
                            source="network")
        rec["hint"] = "usage request failed"
        return rec
    account = "ready" if code == 200 else ("signed_out" if code == 401 else "unknown")
    return reading_from_http(hid, code, body, checked_at=checked,
                             account_state=account)


def refresh_http_providers(board, home="~", timeout=8, transport=None,
                           environ=None, now=None):
    """Refresh Claude and Codex. Never skips because a seat is busy."""
    out = {}
    for hid in ("claude", "codex"):
        reading = fetch_provider_usage(
            hid, home=home, timeout=timeout, transport=transport,
            environ=environ, now=now)
        out[hid] = put_reading(board, reading, now=now)
    for hid in ("cursor", "agy"):
        out[hid] = put_reading(board, empty_reading(hid, status="no_data",
                                                    checked_at=iso_now(now)),
                               now=now)
    return out
