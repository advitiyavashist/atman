"""T-1040 / T-1056: per-provider usage ledger. Observed limits + credentialed reads.

Honesty: unknown remaining is unknown (never zero, never 'fine'/'available').
No reset unless the provider gave one. Token counts are the harness's own
report. Cursor and Antigravity stay 'no data'. Credentials are never stored
on the board, logged, printed, or returned in readings.

Claude Code on macOS keeps OAuth in the login keychain (generic password,
service Claude Code-credentials). ~/.claude/.credentials.json is the
fallback for platforms that still write that file; it is absent on this Mac.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

LEDGER_NAME = "provider_usage.json"
NO_DATA_PROVIDERS = frozenset({"cursor", "agy", "antigravity"})
HTTP_PROVIDERS = frozenset({"claude", "codex"})
CLAUDE_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
CODEX_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
# Paths found on this machine (Claude Code CLI + Codex CLI), not invented.
CLAUDE_CREDENTIALS_RELPATH = os.path.join(".claude", ".credentials.json")
# macOS login keychain service Claude Code itself uses (generic password).
CLAUDE_KEYCHAIN_SERVICE = "Claude Code-credentials"
CODEX_AUTH_RELPATH = os.path.join(".codex", "auth.json")
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
    # Live Claude /api/oauth/usage (2026-09-16) uses utilization in 0..1,
    # not used_percent. Treat it like `used`: a fraction, not a percent.
    for key in ("used_percent", "used_percentage", "usedPercent",
                "percent_used", "percentUsed", "used", "utilization"):
        pct = _as_float(item.get(key))
        if pct is None:
            continue
        if 0 <= pct <= 1 and key in ("used", "utilization"):
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


def _is_failed_reading(reading):
    """Unknown/failed credentialed read — never a successful 200 or observation."""
    return bool(reading) and reading.get("status") == "unknown"


def _is_observed_limit(reading):
    """A real observed limit (T-1022 watch rejection or a prior limited reading)."""
    if not reading or reading.get("status") != "limited":
        return False
    return True


def merge_reading(existing, incoming, now=None):
    """Keep an observed limit when a later read fails; a 200 may supersede."""
    incoming = expire_stale_resets(dict(incoming or {}), now)
    existing = expire_stale_resets(dict(existing), now) if existing else None
    if existing and _is_observed_limit(existing) and _is_failed_reading(incoming):
        out = dict(existing)
        fail_hint = (incoming.get("hint") or "").strip()
        if fail_hint:
            prev = (out.get("hint") or "").strip()
            if fail_hint not in prev:
                out["hint"] = ("%s; %s" % (prev, fail_hint)).strip("; ") if prev else fail_hint
        return expire_stale_resets(out, now)
    return incoming


def put_reading(board, reading, now=None):
    ledger = load_ledger(board)
    hid = reading["provider"]
    existing = (ledger.get("providers") or {}).get(hid)
    ledger["providers"][hid] = merge_reading(existing, reading, now)
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
    if not isinstance(rec, dict):
        # A hand-edited or truncated ledger entry is not a reading. Unknown is
        # the honest answer; a surface must not crash or vanish over it.
        return empty_reading(hid, status="unknown", hint="unreadable record")
    return expire_stale_resets(rec, now)


def public_reading(reading, now=None):
    """Ledger snapshot. Never includes credentials or raw HTTP bodies.

    ``limit_message`` stays here for ``format_usage_line`` (the ledger
    view). A user-facing surface -- compact header, Team tooltip -- must
    use ``ui_reading`` instead, which drops that field.
    """
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


def ui_reading(reading, now=None):
    """What board.json may show a user. No provider ``limit_message``.

    The compact header never prints the provider's own sentence, and a
    Team-card tooltip must not either -- it is unsanitized free text.
    ``reset_at`` is the same scrubbed label the compact line uses, not
    the raw ledger value (a path-shaped reset must not sit in board.json).
    """
    rec = public_reading(reading, now)
    rec.pop("limit_message", None)
    raw = rec.get("reset_at")
    if raw:
        rec["reset_at"] = reset_label(raw, now) or None
    return rec


def brief_usage_line(reading, now=None):
    """Seat-brief USAGE line, or '' when there is no real reading (T-1091).

    `format_usage_line` still says 'unknown · UNKNOWN · no data' for dash;
    the brief omits that placeholder entirely.
    """
    rec = public_reading(reading, now)
    status = rec.get("status") or "unknown"
    if status in ("unknown", "no_data") and rec.get("remaining") is None \
            and rec.get("tokens_reported") is None and not rec.get("limit_message"):
        return ""
    return format_usage_line(reading, now).strip()


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


# ---- T-1076: one short line per provider, for the surfaces users already read.
# `format_usage_line` above is the ledger view (`atm harness usage`): every
# field, one provider per line, no judgement. A header cannot carry that -- it
# has to fit next to the output the user actually came for, and it has to say
# *low* out loud, which the ledger view has no word for. So this is the same
# reading, same reader, rendered short: provider, the provider's own remaining
# %, its own reset, and the one word that changes what the user should do.
# Nothing here reads a credential, a path, or the network.
LOW_REMAINING_PCT = 20.0
# Older than this and the line says how old it is -- in EVERY state. A number,
# and just as much a LIMITED, is only as good as when it was read.
STALE_READING_SECS = 3600
# Budget for the text (a surface adds a 7-character prefix), so a realistic
# line lands around 60 columns and the worst case still clears 80. The parts
# are shed in priority order: the state and the age are never shed, the reset
# is (see _fit) -- staleness is the honesty-bearing part, a reset is detail.
HINT_MAX = 48
PROVIDER_MAX = 12
# The longest provider reset phrase carried at all. NOT a clip: a phrase over
# this is dropped whole (see reset_label), because a trimmed time is a wrong
# time, not a short one. What actually prints is decided by COMPACT_MAX/_fit.
RESET_MAX = 48
COMPACT_MAX = 56
# A raw epoch is a number, not a reset a user can read. 9+ digits covers
# seconds, milliseconds, microseconds and anything longer; 14+ used to
# slip past the old 9–13 cap and print as a reset.
_EPOCHISH = re.compile(r"^\d{9,}$")
# IANA area names only. A slash in a reset is a timezone, but only when
# the first segment is a real area -- otherwise Users/kavana/secrets and
# ghp/AAAAAAAAAAAA would skip _scrub's slash rule and 24-char cap.
# Later segments are letters/underscore only (no dots, no digits), so
# Etc/GMT is exempt and Etc/GMT+3 is not: a digit in a segment fails.
# Exempt tokens still respect the 24-character cap.
_TZ_AREAS = (
    "Africa", "America", "Antarctica", "Arctic", "Asia", "Atlantic",
    "Australia", "Europe", "Indian", "Pacific", "Etc", "UTC", "GMT",
)
_TZ_TOKEN = re.compile(
    r"^(?:%s)(?:/[A-Za-z_+-]{2,}){1,2}$" % "|".join(_TZ_AREAS)
)
_TZ_EXEMPT_MAX = 24
# A never-read provider is not a failed read: it must not say "re-login".
NEVER_READ_HINT = "not read yet (atm harness usage)"
# Defensive scrub for the hint, the only free-ish text a header repeats. Real
# hints are fixed internal strings ("re-login required", "HTTP 500", "reset
# elapsed"); a path, an assignment or a token is not one of them and never
# reaches a user's screen from here. The provider's own limit_message is not
# printed in a header at all.
_HINT_UNSAFE = re.compile(
    r"[/\\=]|^~"
    # Known credential shapes, including the short ones: OpenAI/Anthropic
    # sk-, Slack xox?-, GitHub gh?_, AWS AKIA/ASIA, Google AIza, JWTs.
    r"|^(?:sk|xox[abprs]?|ghp|gho|ghu|ghs|ghr|github_pat|glpat|shpat|pk|rk)[-_]"
    r"|^(?:AKIA|ASIA|AIza|ya29|eyJ)"
    r"|^Bearer$|^token$",
    re.I)


def remaining_percent(reading):
    """The provider's own worst-window remaining %, or None. Never a guess.

    None means "we do not know" and must print as unknown, not as 0.
    """
    rec = reading if isinstance(reading, dict) else {}
    vals = []
    for win in rec.get("windows") or []:
        if not isinstance(win, dict):
            continue
        pct = win.get("remaining_percent")
        if isinstance(pct, (int, float)) and not isinstance(pct, bool):
            vals.append(float(pct))
    if not vals:
        pct = _as_float(rec.get("remaining"))
        vals = [pct] if pct is not None else []
    if not vals:
        return None
    worst = min(vals)
    if worst != worst or worst < 0 or worst > 100:  # NaN or out of range
        return None
    return worst


def pct_label(pct):
    """Floor, so a header never overstates the headroom a provider reported."""
    if pct is None:
        return ""
    if pct <= 0:
        return "0%"
    if pct < 1:
        return "<1%"
    return "%d%%" % int(pct)


def reset_label(reset_at, now=None):
    """The provider's reset, short. Non-ISO text is the provider's own words.

    A reset is a TIME, so it is whole or it is nothing. `_reset_of` copies
    whatever the provider sent verbatim, and clipping that mid-string is how
    "2026-09-19 09:00:00 America/Los_Angeles" becomes "2026-09-19 09:00:00"
    (seven hours wrong, read as UTC) and "Fri, 19 Sep 2026 09:00:00 GMT"
    becomes "Fri, 19 Sep 2026 09:00:0" (a digit gone, still looking
    complete). Neither is a shorter truth. So an unparseable phrase is kept
    entire or dropped, never trimmed -- including at a word boundary, because
    the word at the end is exactly the timezone that makes it unambiguous.
    _fit then drops it whole again if the assembled line has no room.
    """
    text = " ".join((reset_at or "").split())
    if not text:
        return ""
    when = parse_iso(text)
    if when is None:
        # A raw epoch is a number, not something to show a user.
        if _EPOCHISH.match(text):
            return ""
        phrase = _scrub_reset(text)
        # Scrubbed something out, or too long to ever print: say nothing
        # rather than something that reads like a time and is not one.
        if "[redacted]" in phrase or len(phrase) > RESET_MAX:
            return ""
        return phrase
    now = now or utcnow()
    if when.date() == now.date():
        return when.strftime("%H:%M UTC")
    return when.strftime("%b %d %H:%M UTC")


def _scrub(text):
    """Drop anything path-, token- or assignment-shaped before it is printed."""
    out = []
    for word in (text or "").split():
        out.append("[redacted]" if len(word) >= 24 or _HINT_UNSAFE.search(word)
                   else word)
    return " ".join(out)


def _is_tz_token(word):
    """True only for a short, IANA-area-anchored zone token."""
    if not word or len(word) >= _TZ_EXEMPT_MAX or not _TZ_TOKEN.match(word):
        return False
    parts = word.split("/")
    # UTC and GMT have no real sub-zones. A slash after them is not a tz.
    if parts[0] in ("UTC", "GMT") and len(parts) > 1:
        return False
    # Etc only has a handful of real suffixes (UTC, GMT, …), not 19 letters.
    if parts[0] == "Etc":
        return len(parts) == 2 and parts[1] in (
            "UTC", "GMT", "UCT", "Zulu", "Greenwich", "Universal")
    for seg in parts[1:]:
        if _HINT_UNSAFE.search(seg):
            return False
    return True


def _scrub_reset(text):
    """_scrub for a reset phrase, where a "/" may be a timezone, not a path.

    Narrow on purpose: only an IANA-area-anchored zone token under the
    24-character cap is exempt, and only inside a reset -- a hint never
    gets this exemption. An all-digit epoch word is dropped the same way.
    """
    out = []
    for w in (text or "").split():
        if _is_tz_token(w):
            out.append(w)
        elif _EPOCHISH.match(w):
            out.append("[redacted]")
        else:
            out.append(_scrub(w))
    return " ".join(out)


def _short_hint(rec):
    hint = " ".join((rec.get("hint") or "").split())
    if not hint:
        return ""
    if hint == "no data" and not rec.get("checked_at"):
        return NEVER_READ_HINT
    hint = _scrub(hint)
    if len(hint) > HINT_MAX:
        # Cut on a word boundary: a header says less rather than ending in a
        # half-word that could be read as part of a value.
        hint = hint[:HINT_MAX].rsplit(" ", 1)[0]
    return hint.strip()


def age_amount(checked_at, now=None):
    """Just the magnitude of a reading's age: '6d', '14h', '3m', '45s'."""
    age = age_label(checked_at, now)
    if not age.startswith("last read ") or not age.endswith(" ago"):
        return ""
    return age[len("last read "):-len(" ago")]


def _stale_age(rec, now=None, force=False):
    """Both age suffixes for a reading too old to present as current -- in
    every state, LIMITED included.

    A LIMITED with no provider reset can never expire on its own
    (``expire_stale_resets`` has nothing to compare it against), and LIMITED
    is the word that stops a dispatch, so it is exactly the state that must
    not look freshly observed. Two forms because the age is never shed: the
    terse one buys room for a reset that would otherwise not fit.
    """
    when = parse_iso(rec.get("checked_at"))
    if when is None:
        return "", ""
    old = ((now or utcnow()) - when).total_seconds() >= STALE_READING_SECS
    amount = age_amount(rec.get("checked_at"), now)
    if not (old or force) or not amount:
        return "", ""
    return " (read %s ago)" % amount, " (%s old)" % amount


MID_MIN = 12


def _fit(head, mid, tail, age, terse_age="", truncatable=True):
    """Assemble within COMPACT_MAX, shedding the reset/hint before the age.

    ``head`` (who and what state), ``tail`` (the "-- low" call-out) and the
    age are never shed: they are what the user has to act on, and a number
    without its staleness is the lie this formatter exists to avoid. ``mid``
    is the reset or the hint -- detail. So the order of sacrifice is: the
    full age suffix shortens to its terse form, then ``mid`` is truncated on
    a word boundary, then ``mid`` goes.

    ``truncatable=False`` for a reset: half of "Sep 19 09:00 UTC" is not a
    shorter truth but an ambiguous one, and half of a provider's own phrase
    ("resets next Tuesday at") is not a time at all -- so a reset is kept or
    dropped whole, and only a hint is ever shortened.
    """
    forms = [age] + ([terse_age] if terse_age and terse_age != age else [])
    for shown in forms:
        if not mid or len(mid) <= COMPACT_MAX - len(head + tail + shown):
            return head + mid + tail + shown
    room = COMPACT_MAX - len(head + tail + age)
    cut = mid[:max(0, room)].rstrip() if truncatable else ""
    cut = cut.rsplit(" ", 1)[0] if " " in cut else ""
    return head + (cut if len(cut) >= MID_MIN else "") + tail + age


def compact_reading(reading, now=None):
    """Header-sized view of one provider reading.

    ``level`` is the state the copy is chosen from: ok / low / limited /
    unknown / no_data. An expired limit arrives here already downgraded to
    unknown by ``expire_stale_resets``, so it never reads as limited; a limit
    with no reset to expire against carries its age instead.
    """
    rec = public_reading(reading, now)
    # A provider id is a short label, never a path: keep label characters only.
    hid = re.sub(r"[^A-Za-z0-9_.+-]", "", rec["provider"] or "")[:PROVIDER_MAX] or "?"
    status = rec["status"]
    reset = reset_label(rec["reset_at"], now)
    when_reset = parse_iso(rec["reset_at"])
    pct = remaining_percent(rec)
    out = {"provider": hid, "level": "unknown", "remaining_pct": None,
           "remaining": "", "reset": reset, "text": ""}
    if status == "no_data":
        # Nothing was ever read and nothing ever will be: an age would be noise.
        out.update(level="no_data", reset="",
                   text="%s no usage data (no source to read)" % hid)
        return out
    age, terse = _stale_age(rec, now)
    if status == "limited":
        mid = (", resets %s" % reset) if reset else ", reset unknown"
        out.update(level="limited", remaining_pct=pct,
                   remaining=pct_label(pct) if pct is not None else "",
                   text=_fit("%s LIMITED" % hid, mid, "", age, terse,
                             truncatable=not reset))
        return out
    if status == "ok" and pct is not None:
        # A reset that has already passed describes a window that has since
        # rolled over: do not print it as though it were still ahead, and say
        # how old the number is even if the ledger was read minutes ago.
        if when_reset is not None and when_reset <= (now or utcnow()):
            reset = ""
            out["reset"] = ""
            age, terse = _stale_age(rec, now, force=True)
        low = pct < LOW_REMAINING_PCT
        out.update(level="low" if low else "ok", remaining_pct=pct,
                   remaining=pct_label(pct),
                   text=_fit("%s %s left" % (hid, pct_label(pct)),
                             (", resets %s" % reset) if reset else "",
                             " -- low" if low else "", age, terse,
                             truncatable=False))
        return out
    # Unknown, and every shape that cannot produce an honest number: an ok
    # status with no percent in it is still unknown, never 0 and never "fine".
    hint = _short_hint(rec)
    out.update(level="unknown",
               text=_fit("%s unknown" % hid, (" -- %s" % hint) if hint else "",
                         "", age, terse))
    return out


def format_compact_line(reading, now=None):
    """One short line for one provider: 'claude 12% left, resets 21:00 UTC -- low'."""
    return compact_reading(reading, now)["text"]


def canonical_provider(name):
    hid = (name or "").strip().lower()
    return "agy" if hid == "antigravity" else hid


def header_providers(board, harnesses=(), now=None):
    """Providers a header should name: every one read, plus HTTP seats not read yet.

    Read-only: the ledger file and the names the caller passed in. Providers
    with no usage source at all (cursor, agy) stay out of a header -- they can
    never say anything but "no data", and a header has to stay short.
    """
    known = load_ledger(board).get("providers") or {}
    seats = {canonical_provider(h) for h in harnesses or ()}
    out = []
    for hid in ("claude", "codex"):
        if hid in known or hid in seats:
            out.append(hid)
    for hid in sorted(known):
        if hid in out or hid in NO_DATA_PROVIDERS:
            continue
        rec = known.get(hid)
        if isinstance(rec, dict) and rec.get("status") == "no_data":
            continue
        out.append(hid)
    return out


def header_lines(board, now=None, providers=None, harnesses=(), prefix="usage  "):
    """The usage header: one short line per provider, or one honest line if none.

    Reads the ledger only -- no network, no subprocess, no write. A board where
    nothing has ever been read says so, and names the command that would read.
    """
    ids = list(providers) if providers is not None else header_providers(
        board, harnesses, now)
    if not ids:
        return [prefix + "no provider read yet (atm harness usage)"]
    return [prefix + format_compact_line(get_reading(board, hid, now), now)
            for hid in ids]


def usage_provider_for_harness(harness):
    """The provider whose quota a seat on this harness spends, or "" if none.

    A cursor+claude seat spends Claude's quota. remote/custom/a bare
    executable is not a metered provider this program can read, and must not
    be dressed up as one that simply has not been read yet.
    """
    hid = canonical_provider(harness)
    if hid == "cursor+claude":
        return "claude"
    if hid in HTTP_PROVIDERS or hid in NO_DATA_PROVIDERS:
        return hid
    return ""


def seat_usage_line(board, harness, now=None, prefix="usage  "):
    """The one line a spawned/dispatched seat's provider gets. Read-only."""
    hid = usage_provider_for_harness(harness)
    if not hid:
        label = canonical_provider(harness) or "harness"
        return prefix + "%s no usage data (no source to read)" % label
    return prefix + format_compact_line(get_reading(board, hid, now), now)


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
    if status_code != 200:
        rec = empty_reading(hid, status="unknown", checked_at=checked_at,
                            source="http_%s" % status_code,
                            account_state=account_state)
        rec["hint"] = "HTTP %s" % status_code
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


def claude_credentials_file(home=None):
    """Claude Code CLI credentials file — fallback when the keychain is empty."""
    home = os.path.expanduser(home or "~")
    return os.path.join(home, CLAUDE_CREDENTIALS_RELPATH)


def codex_auth_file(home=None, environ=None):
    """Codex CLI auth.json — $CODEX_HOME/auth.json or ~/.codex/auth.json."""
    env = environ if environ is not None else os.environ
    env_home = (env.get("CODEX_HOME") or "").strip()
    if env_home:
        return os.path.join(env_home, "auth.json")
    home = os.path.expanduser(home or "~")
    return os.path.join(home, CODEX_AUTH_RELPATH)


def _oauth_token_from_blob(data):
    """Extract an access token from a Claude Code credentials blob. Never logs."""
    if isinstance(data, (bytes, bytearray)):
        try:
            data = data.decode("utf-8", "replace")
        except Exception:
            return ""
    if isinstance(data, str):
        text = data.strip()
        if not text:
            return ""
        try:
            data = json.loads(text)
        except (TypeError, ValueError):
            return ""
    if not isinstance(data, dict):
        return ""
    oauth = data.get("claudeAiOauth") or data.get("oauth") or data
    if not isinstance(oauth, dict):
        return ""
    return (oauth.get("accessToken") or oauth.get("access_token") or "").strip()


def macos_keychain_generic_password(service, account="", runner=None):
    """Read a macOS generic password. Returns '' on miss/fail. Never logs the value."""
    if not service:
        return ""
    cmd = ["security", "find-generic-password", "-s", service, "-w"]
    if account:
        cmd = ["security", "find-generic-password", "-a", account,
               "-s", service, "-w"]
    run = runner or subprocess.run
    try:
        proc = run(cmd, capture_output=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if getattr(proc, "returncode", 1) != 0:
        return ""
    out = getattr(proc, "stdout", None) or b""
    if isinstance(out, bytes):
        return out.decode("utf-8", "replace").strip()
    return str(out).strip()


def default_claude_keychain_reader():
    """Claude Code's login-keychain item. Never logs or writes the value."""
    return macos_keychain_generic_password(CLAUDE_KEYCHAIN_SERVICE)


def read_claude_oauth_token(home, environ=None, keychain_reader=None,
                            platform=None):
    """Return access token string or ''. Never raises. Does not log the token.

    Order: ANTHROPIC_OAUTH_TOKEN, then macOS login keychain (Claude Code's
    generic-password item), then ~/.claude/.credentials.json and siblings.
    """
    env = environ if environ is not None else os.environ
    direct = (env.get("ANTHROPIC_OAUTH_TOKEN") or "").strip()
    if direct:
        return direct
    plat = sys.platform if platform is None else platform
    reader = keychain_reader
    if reader is None and str(plat).startswith("darwin"):
        reader = default_claude_keychain_reader
    if reader is not None:
        try:
            blob = reader()
        except Exception:
            blob = ""
        token = _oauth_token_from_blob(blob)
        if token:
            return token
    home = os.path.expanduser(home or "~")
    candidates = (
        claude_credentials_file(home),
        os.path.join(home, ".claude", "credentials.json"),
        os.path.join(home, ".config", "claude", ".credentials.json"),
    )
    for path in candidates:
        token = _oauth_token_from_blob(_read_json(path))
        if token:
            return token
    return ""


def read_codex_auth_token(home, environ=None):
    env = environ if environ is not None else os.environ
    direct = (env.get("CODEX_AUTH_TOKEN") or env.get("CHATGPT_ACCESS_TOKEN") or "").strip()
    if direct:
        return direct
    candidates = (codex_auth_file(home, environ=env),)
    for path in candidates:
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
                         environ=None, now=None, keychain_reader=None,
                         platform=None):
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
        token = read_claude_oauth_token(
            home, environ, keychain_reader=keychain_reader, platform=platform)
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
                           environ=None, now=None, keychain_reader=None,
                           platform=None):
    """Refresh Claude and Codex. Never skips because a seat is busy."""
    out = {}
    for hid in ("claude", "codex"):
        reading = fetch_provider_usage(
            hid, home=home, timeout=timeout, transport=transport,
            environ=environ, now=now, keychain_reader=keychain_reader,
            platform=platform)
        out[hid] = put_reading(board, reading, now=now)
    for hid in ("cursor", "agy"):
        out[hid] = put_reading(board, empty_reading(hid, status="no_data",
                                                    checked_at=iso_now(now)),
                               now=now)
    return out
