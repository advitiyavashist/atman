"""T-1040: credentialed provider usage ledger.

Primary source is the operator's own Claude / Codex credentials, sent only to
that provider. Observed T-1022 limit rejections stay the fallback. A failed
credentialed read never erases a real LIMITED state.

Credential paths were found on this machine, not invented:
  Claude: macOS Keychain service ``Claude Code-credentials`` (Claude Code CLI);
          file fallback ``~/.claude/.credentials.json`` (same path the CLI uses).
  Codex:  ``$CODEX_HOME/auth.json`` or ``~/.codex/auth.json``.

Cursor and Antigravity have no usage source: ``no data``.
"""
from __future__ import annotations

import json
import os
import pwd
import subprocess
import urllib.error
import urllib.request
from datetime import datetime, timezone

CLAUDE_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
CODEX_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
HTTP_TIMEOUT_S = 10
KEYCHAIN_SERVICE = "Claude Code-credentials"
NO_DATA_PROVIDERS = ("cursor", "agy")
LEDGER_PROVIDERS = ("claude", "codex", "cursor", "agy")
USAGE_STATES = ("ok", "limited", "unknown")
RELOGIN_HINT = "re-login"

# Distinctive markers tests plant; also used to scrub accidental leaks.
_SECRET_KEY_NAMES = (
    "access_token", "accesstoken", "refresh_token", "refreshtoken",
    "id_token", "idtoken", "authorization", "cookie", "openai_api_key",
    "password", "secret", "token",
)


def ledger_path(board):
    return os.path.join(board, "usage-ledger.json")


def provider_for_harness(name):
    n = (name or "").strip().lower()
    if n in ("agy", "antigravity"):
        return "agy"
    if n in ("claude", "claude-code", "anthropic"):
        return "claude"
    if n in ("codex", "chatgpt", "openai"):
        return "codex"
    if n == "cursor":
        return "cursor"
    return n or "claude"


def _utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _real_login_home():
    try:
        return pwd.getpwuid(os.getuid()).pw_dir
    except (KeyError, OSError, AttributeError):
        return os.path.expanduser("~")


def live_io_allowed():
    """No live keychain/HTTP under pytest. Tests stub the HTTP layer."""
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    if os.environ.get("ATMAN_USAGE_NO_NETWORK"):
        return False
    if os.environ.get("ATMAN_USAGE_REFRESH") == "0":
        return False
    return True


def _keychain_allowed():
    if not live_io_allowed():
        return False
    if os.uname().sysname != "Darwin":
        return False
    home = os.path.realpath(os.path.expanduser("~"))
    return home == os.path.realpath(_real_login_home())


def claude_credentials_file(home=None):
    home = home if home is not None else os.path.expanduser("~")
    return os.path.join(home, ".claude", ".credentials.json")


def claude_config_file(home=None):
    home = home if home is not None else os.path.expanduser("~")
    return os.path.join(home, ".claude.json")


def codex_auth_file(home=None, codex_home=None):
    if codex_home:
        return os.path.join(codex_home, "auth.json")
    env_home = os.environ.get("CODEX_HOME")
    if env_home:
        return os.path.join(env_home, "auth.json")
    home = home if home is not None else os.path.expanduser("~")
    return os.path.join(home, ".codex", "auth.json")


def empty_reading(provider, reason="", account_state="missing"):
    return {
        "provider": provider,
        "account": "",
        "account_state": account_state,
        "usage_state": "unknown",
        "read_at": "",
        "windows": {},
        "windows_read_at": "",
        "last_limit_rejection": "",
        "reason": reason,
    }


def no_data_reading(provider):
    rec = empty_reading(provider, reason="no data", account_state="unknown")
    rec["usage_state"] = "unknown"
    return rec


def _looks_secret_key(key):
    norm = str(key or "").replace("-", "").replace("_", "").lower()
    return any(norm == m or norm.endswith(m) for m in _SECRET_KEY_NAMES)


def scrub_secrets(obj):
    """Drop credential-shaped keys. Never persist or print them."""
    if isinstance(obj, dict):
        return {k: scrub_secrets(v) for k, v in obj.items() if not _looks_secret_key(k)}
    if isinstance(obj, list):
        return [scrub_secrets(v) for v in obj]
    return obj


def contains_secret(text, secrets):
    blob = text if isinstance(text, str) else ""
    for secret in secrets:
        if secret and secret in blob:
            return True
    return False


def _safe_json_load(raw):
    if raw is None:
        return None
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", "replace")
    try:
        return json.loads(raw)
    except (ValueError, TypeError, UnicodeDecodeError):
        return None


def _read_text(path):
    try:
        with open(path) as f:
            return f.read()
    except (OSError, UnicodeError):
        return None


def _parse_claude_oauth_blob(raw):
    """Return (token, account) from a Claude credentials JSON blob. Token stays here."""
    data = _safe_json_load(raw)
    if not isinstance(data, dict):
        return "", ""
    oauth = data.get("claudeAiOauth")
    if not isinstance(oauth, dict):
        oauth = data
    token = oauth.get("accessToken") or oauth.get("access_token") or ""
    if not isinstance(token, str):
        token = ""
    token = token.strip()
    account = ""
    for key in ("accountUuid", "account_uuid", "accountId", "account_id"):
        val = oauth.get(key) or data.get(key)
        if isinstance(val, str) and val.strip():
            account = val.strip()
            break
    return token, account


def read_claude_keychain_blob():
    """Read the Keychain item. Caller must not log or print the return value."""
    if not _keychain_allowed():
        return None
    try:
        proc = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def read_claude_account_uuid(home=None):
    raw = _read_text(claude_config_file(home))
    data = _safe_json_load(raw)
    if not isinstance(data, dict):
        return ""
    acct = data.get("oauthAccount")
    if isinstance(acct, dict):
        uuid = acct.get("accountUuid") or ""
        if isinstance(uuid, str):
            return uuid.strip()
    return ""


def load_claude_credentials(home=None, keychain_reader=None):
    """Load Claude OAuth material. Never log the token.

    Returns {present, token, account} — token is only for the Anthropic request.
    """
    token, account = "", ""
    reader = keychain_reader if keychain_reader is not None else read_claude_keychain_blob
    blob = reader() if callable(reader) else None
    if blob:
        token, account = _parse_claude_oauth_blob(blob)
    if not token:
        token, file_account = _parse_claude_oauth_blob(_read_text(claude_credentials_file(home)))
        account = account or file_account
    account = account or read_claude_account_uuid(home)
    return {"present": bool(token), "token": token, "account": account}


def load_codex_credentials(home=None, codex_home=None):
    """Load Codex backend auth. Never log the token.

    Returns {present, token, account_id} — token is only for the ChatGPT request.
    """
    raw = _read_text(codex_auth_file(home, codex_home))
    data = _safe_json_load(raw)
    if not isinstance(data, dict):
        return {"present": False, "token": "", "account_id": ""}
    tokens = data.get("tokens")
    if not isinstance(tokens, dict):
        tokens = {}
    token = tokens.get("access_token") or ""
    if not isinstance(token, str):
        token = ""
    token = token.strip()
    account_id = tokens.get("account_id") or ""
    if not isinstance(account_id, str):
        account_id = ""
    return {"present": bool(token), "token": token, "account_id": account_id.strip()}


def claude_auth_headers(token):
    return {
        "Authorization": "Bearer %s" % token,
        "anthropic-beta": "oauth-2025-04-20",
        "User-Agent": "claude-code/2.1.0",
        "Accept": "application/json",
    }


def codex_auth_headers(token, account_id=""):
    headers = {
        "Authorization": "Bearer %s" % token,
        "User-Agent": "codex-cli",
        "OpenAI-Beta": "codex-1",
        "originator": "Codex Desktop",
        "Accept": "application/json",
    }
    if account_id:
        headers["ChatGPT-Account-Id"] = account_id
    return headers


def http_get(url, headers, timeout=HTTP_TIMEOUT_S):
    """GET url. Returns (status, body_bytes). status is int, 'timeout', or 'error'.

    Headers never appear in the return value or raised messages.
    """
    req = urllib.request.Request(url, headers=headers or {}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.getcode(), resp.read()
    except TimeoutError:
        return "timeout", b""
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read() if exc.fp is not None else b""
        except OSError:
            body = b""
        return exc.code, body
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, TimeoutError):
            return "timeout", b""
        name = type(reason).__name__ if reason is not None else ""
        if "timeout" in name.lower() or "timed out" in str(reason).lower():
            return "timeout", b""
        return "error", b""
    except OSError as exc:
        if "timed out" in str(exc).lower():
            return "timeout", b""
        return "error", b""


def _as_percent(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        pct = float(value)
    else:
        text = str(value).strip().rstrip("%")
        if not text:
            return None
        try:
            pct = float(text)
        except ValueError:
            return None
    if pct != pct or pct < 0 or pct > 100:
        return None
    return pct


def _as_reset_iso(value):
    if value is None or value is False:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        ts = float(value)
        if ts > 10_000_000_000:
            ts = ts / 1000.0
        try:
            return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    try:
        numeric = float(text)
    except ValueError:
        numeric = None
    if numeric is not None:
        return _as_reset_iso(numeric)
    try:
        stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _window_from_mapping(raw):
    if not isinstance(raw, dict):
        return None
    percent = None
    for key in ("percent", "used_percentage", "used_percent", "utilization", "usedPercent"):
        percent = _as_percent(raw.get(key))
        if percent is not None:
            break
    reset = None
    for key in ("resets_at", "reset_at", "resetsAt", "resetAt"):
        reset = _as_reset_iso(raw.get(key))
        if reset:
            break
    if percent is None or not reset:
        return None
    out = {"percent_used": percent, "resets_at": reset}
    model = raw.get("model")
    if isinstance(model, str) and model.strip():
        out["model"] = model.strip()
    return out


def map_claude_windows(payload):
    """Map five_hour, seven_day, and model-scoped weekly entries.

    Missing required window/field -> None (caller records unknown).
    """
    if not isinstance(payload, dict):
        return None
    windows = {}
    for key in ("five_hour", "seven_day"):
        mapped = _window_from_mapping(payload.get(key))
        if mapped is None:
            return None
        windows[key] = mapped
    extras = []
    for key, raw in payload.items():
        if key in ("five_hour", "seven_day", "limits"):
            continue
        if not isinstance(raw, dict):
            continue
        if key.startswith("seven_day") or key.endswith("_weekly") or "fable" in key:
            mapped = _window_from_mapping(raw)
            if mapped:
                extras.append((key, mapped))
    limits = payload.get("limits")
    if isinstance(limits, list):
        for item in limits:
            if not isinstance(item, dict):
                continue
            kind = str(item.get("kind") or "")
            scope = item.get("scope") if isinstance(item.get("scope"), dict) else {}
            model = ""
            model_obj = scope.get("model") if isinstance(scope.get("model"), dict) else {}
            if isinstance(model_obj.get("display_name"), str):
                model = model_obj["display_name"].strip()
            if kind == "weekly_scoped" or model:
                mapped = _window_from_mapping(item)
                if mapped:
                    if model:
                        mapped["model"] = model
                    label = "weekly_%s" % (model or kind or "scoped")
                    extras.append((label, mapped))
    for key, mapped in extras:
        windows[key] = mapped
    return windows


def _codex_window_label(raw, fallback):
    seconds = raw.get("limit_window_seconds") if isinstance(raw, dict) else None
    try:
        seconds = float(seconds)
    except (TypeError, ValueError):
        seconds = None
    if seconds is None:
        return fallback
    if 3 * 3600 <= seconds <= 6 * 3600:
        return "five_hour"
    if 6 * 86400 <= seconds <= 8 * 86400:
        return "seven_day"
    return fallback


def map_codex_windows(payload):
    """Map Codex primary/secondary windows to percent_used + resets_at."""
    if not isinstance(payload, dict):
        return None
    rate = payload.get("rate_limit") or payload.get("rateLimit")
    if not isinstance(rate, dict):
        return None
    windows = {}
    pairs = (
        ("primary_window", "primary"),
        ("secondary_window", "secondary"),
    )
    found = 0
    incomplete = False
    for src, fallback in pairs:
        raw = rate.get(src) or rate.get(src.replace("_window", "Window"))
        if raw is None:
            continue
        if not isinstance(raw, dict):
            incomplete = True
            continue
        mapped = _window_from_mapping(raw)
        if mapped is None:
            incomplete = True
            continue
        windows[_codex_window_label(raw, fallback)] = mapped
        found += 1
    if incomplete or found == 0:
        return None
    return windows


def classify_windows(windows):
    if not windows:
        return "unknown"
    for item in windows.values():
        if _as_percent(item.get("percent_used")) == 100:
            return "limited"
    return "ok"


def _unknown_reason(status, body=None, missing=""):
    if status == "timeout":
        return "timeout"
    if status == 401:
        return "401; %s" % RELOGIN_HINT
    if status == "error":
        return "transport error"
    if isinstance(status, int) and status != 200:
        return "http %s" % status
    if missing:
        return missing
    return "unparseable body"


def fetch_claude_usage(creds, http_get_fn=None, timeout=HTTP_TIMEOUT_S, now_stamp=None):
    stamp = now_stamp or _utc_now()
    rec = empty_reading("claude", account_state="present" if creds.get("present") else "missing")
    rec["read_at"] = stamp
    rec["account"] = creds.get("account") or ""
    if not creds.get("present") or not creds.get("token"):
        rec["reason"] = "no credentials; %s" % RELOGIN_HINT
        return rec
    getter = http_get_fn or http_get
    if getter is http_get and not live_io_allowed():
        rec["reason"] = "http disabled"
        return rec
    try:
        status, body = getter(CLAUDE_USAGE_URL, claude_auth_headers(creds["token"]), timeout)
    except Exception:
        rec["reason"] = "transport error"
        return rec
    if status != 200:
        rec["reason"] = _unknown_reason(status)
        return rec
    payload = _safe_json_load(body)
    windows = map_claude_windows(payload)
    if windows is None:
        rec["reason"] = "unparseable body" if payload is None else "missing field"
        return rec
    rec["windows"] = windows
    rec["windows_read_at"] = stamp
    rec["usage_state"] = classify_windows(windows)
    rec["reason"] = ""
    return rec


def fetch_codex_usage(creds, http_get_fn=None, timeout=HTTP_TIMEOUT_S, now_stamp=None):
    stamp = now_stamp or _utc_now()
    rec = empty_reading("codex", account_state="present" if creds.get("present") else "missing")
    rec["read_at"] = stamp
    rec["account"] = creds.get("account_id") or creds.get("account") or ""
    if not creds.get("present") or not creds.get("token"):
        rec["reason"] = "no credentials; %s" % RELOGIN_HINT
        return rec
    getter = http_get_fn or http_get
    if getter is http_get and not live_io_allowed():
        rec["reason"] = "http disabled"
        return rec
    try:
        status, body = getter(
            CODEX_USAGE_URL,
            codex_auth_headers(creds["token"], creds.get("account_id") or ""),
            timeout,
        )
    except Exception:
        rec["reason"] = "transport error"
        return rec
    if status != 200:
        rec["reason"] = _unknown_reason(status)
        return rec
    payload = _safe_json_load(body)
    windows = map_codex_windows(payload)
    if windows is None:
        rec["reason"] = "unparseable body" if payload is None else "missing field"
        return rec
    rec["windows"] = windows
    rec["windows_read_at"] = stamp
    rec["usage_state"] = classify_windows(windows)
    rec["reason"] = ""
    return rec


def merge_reading(prev, new, observed=None):
    """Keep last good windows; never let a failed read erase LIMITED."""
    prev = dict(prev or {})
    out = dict(new or empty_reading((prev.get("provider") or "unknown")))
    observed = observed or {}
    if prev.get("windows") and not out.get("windows"):
        out["windows"] = dict(prev["windows"])
        out["windows_read_at"] = prev.get("windows_read_at") or prev.get("read_at") or ""
    if not out.get("last_limit_rejection") and prev.get("last_limit_rejection"):
        out["last_limit_rejection"] = prev["last_limit_rejection"]
    if not out.get("account") and prev.get("account"):
        out["account"] = prev["account"]
    if observed.get("note"):
        out["last_limit_rejection"] = observed["note"]
    failed = out.get("usage_state") == "unknown"
    if failed and (prev.get("usage_state") == "limited" or observed.get("note")):
        out["usage_state"] = "limited"
    return out


def collect_observed_limits(board, load_agents_fn=None, load_workforce_fn=None):
    """T-1022 fallback: verbatim rejection per provider from seat records."""
    observed = {}
    if load_agents_fn is None:
        return observed
    try:
        agents = load_agents_fn(board) or []
    except Exception:
        return observed
    workforce = {}
    if load_workforce_fn is not None:
        try:
            workforce = load_workforce_fn(board) or {}
        except Exception:
            workforce = {}
    for rec in agents:
        lim = rec.get("limit") if isinstance(rec, dict) else None
        if not isinstance(lim, dict) or not lim.get("note"):
            continue
        owner = rec.get("owner") or ""
        entry = workforce.get(owner) or {}
        harness = lim.get("harness") or entry.get("harness") or entry.get("tool") or ""
        provider = provider_for_harness(harness)
        if provider not in ("claude", "codex"):
            provider = provider_for_harness(entry.get("harness") or entry.get("tool") or "claude")
        current = observed.get(provider)
        if current and current.get("at", "") > lim.get("at", ""):
            continue
        observed[provider] = {
            "note": lim.get("note") or "",
            "at": lim.get("at") or "",
            "until": lim.get("until") or "",
            "reset_at": lim.get("reset_at") or "",
        }
    return observed


def load_usage_ledger(board):
    raw = _read_text(ledger_path(board))
    data = _safe_json_load(raw)
    if not isinstance(data, dict):
        return {"updated_at": "", "providers": {}}
    providers = data.get("providers")
    if not isinstance(providers, dict):
        providers = {}
    return {"updated_at": data.get("updated_at") or "", "providers": providers}


def save_usage_ledger(board, ledger):
    os.makedirs(board, exist_ok=True)
    clean = scrub_secrets(ledger)
    path = ledger_path(board)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(clean, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)
    return clean


def refresh_usage_ledger(board, http_get_fn=None, claude_creds=None, codex_creds=None,
                         now_stamp=None, observed=None, home=None, keychain_reader=None):
    """Read Claude + Codex usage and persist. Busy seats do not skip this."""
    stamp = now_stamp or _utc_now()
    prev = load_usage_ledger(board)
    prev_providers = prev.get("providers") or {}
    if claude_creds is None:
        claude_creds = load_claude_credentials(home=home, keychain_reader=keychain_reader)
    if codex_creds is None:
        codex_creds = load_codex_credentials(home=home)
    claude = fetch_claude_usage(claude_creds, http_get_fn=http_get_fn, now_stamp=stamp)
    codex = fetch_codex_usage(codex_creds, http_get_fn=http_get_fn, now_stamp=stamp)
    providers = {
        "claude": merge_reading(prev_providers.get("claude"), claude, (observed or {}).get("claude")),
        "codex": merge_reading(prev_providers.get("codex"), codex, (observed or {}).get("codex")),
        "cursor": no_data_reading("cursor"),
        "agy": no_data_reading("agy"),
    }
    ledger = {"updated_at": stamp, "providers": providers}
    return save_usage_ledger(board, ledger)


def _fmt_percent(value):
    pct = _as_percent(value)
    if pct is None:
        return "?"
    if float(pct) == int(pct):
        return "%d%%" % int(pct)
    return "%g%%" % pct


def _fmt_reset_clock(resets_at):
    text = str(resets_at or "").strip()
    if "T" in text:
        clock = text.split("T", 1)[1]
        return clock[:5]
    return text[:16]


def _age_label(stamp, now_stamp=None):
    if not stamp:
        return ""
    try:
        then = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return ""
    now = datetime.now(timezone.utc)
    if now_stamp:
        try:
            now = datetime.strptime(now_stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            pass
    seconds = max(0, (now - then).total_seconds())
    if seconds < 90:
        return "%dm ago" % max(1, int(round(seconds / 60.0))) if seconds >= 30 else "just now"
    minutes = seconds / 60.0
    if minutes < 60:
        return "%dm ago" % int(round(minutes))
    hours = minutes / 60.0
    if hours < 48:
        return "%.1fh ago" % hours
    return "%.1fd ago" % (hours / 24.0)


def format_provider_line(rec, now_stamp=None):
    """Human line, e.g. 'claude 5h 62% used, resets 03:10; 7d 41% (read 2m ago)'."""
    provider = rec.get("provider") or "unknown"
    if provider in NO_DATA_PROVIDERS or rec.get("reason") == "no data":
        return "%s no data" % ("agy" if provider == "antigravity" else provider)
    windows = rec.get("windows") or {}
    age = _age_label(rec.get("windows_read_at") or rec.get("read_at"), now_stamp)
    age_bit = (" (read %s)" % age) if age else ""
    state = rec.get("usage_state") or "unknown"
    reason = rec.get("reason") or ""
    if windows:
        ordered = []
        for key in ("five_hour", "seven_day"):
            if key in windows:
                ordered.append((key, windows[key]))
        for key, item in windows.items():
            if key in ("five_hour", "seven_day"):
                continue
            ordered.append((key, item))
        parts = []
        for i, (key, item) in enumerate(ordered):
            label = {"five_hour": "5h", "seven_day": "7d", "primary": "5h", "secondary": "7d"}.get(key)
            if not label:
                model = item.get("model") or key.replace("weekly_", "").replace("seven_day_", "")
                label = "7d %s" % model if model else key
            bit = "%s %s used" % (label, _fmt_percent(item.get("percent_used")))
            if i == 0:
                clock = _fmt_reset_clock(item.get("resets_at"))
                if clock:
                    bit += ", resets %s" % clock
            parts.append(bit)
        body = "; ".join(parts)
        if state == "unknown" and reason:
            return "%s unknown (%s) %s%s" % (provider, reason, body, age_bit)
        if state == "limited":
            return "%s limited %s%s" % (provider, body, age_bit)
        return "%s %s%s" % (provider, body, age_bit)
    if state == "limited" and rec.get("last_limit_rejection"):
        return "%s limited (%s)%s" % (provider, rec["last_limit_rejection"], age_bit)
    if reason:
        return "%s unknown (%s)%s" % (provider, reason, age_bit)
    return "%s unknown%s" % (provider, age_bit)


def format_usage_section(ledger, now_stamp=None):
    lines = []
    providers = (ledger or {}).get("providers") or {}
    for name in LEDGER_PROVIDERS:
        rec = providers.get(name) or no_data_reading(name)
        rec = dict(rec)
        rec.setdefault("provider", name)
        lines.append("  " + format_provider_line(rec, now_stamp=now_stamp))
    return "\n".join(lines)


def format_seat_usage_line(harness, ledger, now_stamp=None):
    provider = provider_for_harness(harness)
    rec = ((ledger or {}).get("providers") or {}).get(provider) or empty_reading(provider)
    rec = dict(rec)
    rec.setdefault("provider", provider)
    if provider in NO_DATA_PROVIDERS:
        rec["reason"] = "no data"
    return format_provider_line(rec, now_stamp=now_stamp)
