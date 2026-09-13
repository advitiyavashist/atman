"""Cron recurring wake for a named seat.

Board file: .tickets/schedule.json. Crontab calls `tickets schedule --due`.
Firing persist-pokes the seat (and posts a task). It does not spawn a product
job. Gemini and every other harness share this path.
"""
from __future__ import print_function

import json
import os
import re
from datetime import datetime, timedelta, timezone


SCHEDULE_NAME = "schedule.json"
CRON_MARK = "tickets-schedule"


def schedule_path(board):
    return os.path.join(board, SCHEDULE_NAME)


def load_schedule(board):
    path = schedule_path(board)
    try:
        with open(path) as f:
            data = json.load(f)
    except (IOError, ValueError):
        return {"seats": {}}
    if not isinstance(data, dict):
        return {"seats": {}}
    seats = data.get("seats")
    if not isinstance(seats, dict):
        data["seats"] = {}
    return data


def save_schedule(board, data):
    path = schedule_path(board)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)
    return data


def parse_iso(stamp):
    s = (stamp or "").strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def format_iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_every(spec):
    raw = (spec or "").strip().lower()
    if not raw:
        return 0
    m = re.match(r"^(\d+)(s|m|h|d)?$", raw)
    if not m:
        raise ValueError("schedule --every needs 30s, 15m, 1h, or seconds")
    n = int(m.group(1))
    unit = m.group(2) or "s"
    return n * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]


def _cron_field(field, value, lo, hi):
    field = (field or "").strip()
    if field == "*":
        return True
    for part in field.split(","):
        part = part.strip()
        if not part:
            continue
        if part.startswith("*/"):
            step = int(part[2:])
            if step <= 0:
                return False
            if (value - lo) % step == 0 and lo <= value <= hi:
                return True
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            if int(a) <= value <= int(b):
                return True
            continue
        if int(part) == value:
            return True
    return False


def cron_match(expr, dt):
    fields = (expr or "").strip().split()
    if len(fields) != 5:
        raise ValueError("cron needs 5 fields (min hour dom month dow)")
    return (
        _cron_field(fields[0], dt.minute, 0, 59)
        and _cron_field(fields[1], dt.hour, 0, 23)
        and _cron_field(fields[2], dt.day, 1, 31)
        and _cron_field(fields[3], dt.month, 1, 12)
        and _cron_field(fields[4], dt.weekday() % 7, 0, 6)
    )


def cron_next(expr, dt):
    """Next UTC minute (exclusive of dt) that matches expr, within 8 days."""
    cur = (dt.replace(second=0, microsecond=0) + timedelta(minutes=1))
    end = dt + timedelta(days=8)
    while cur <= end:
        if cron_match(expr, cur):
            return cur
        cur += timedelta(minutes=1)
    raise ValueError("cron %r never matches in 8 days" % expr)


def bump_next(entry, now_dt):
    every = int(entry.get("every_sec") or 0)
    cron = (entry.get("cron") or "").strip()
    if every:
        return format_iso(now_dt + timedelta(seconds=every))
    if cron:
        return format_iso(cron_next(expr=cron, dt=now_dt))
    return format_iso(now_dt + timedelta(minutes=1))


def due_seats(data, now_iso):
    now_dt = parse_iso(now_iso) or datetime.now(timezone.utc)
    out = []
    for seat, entry in (data.get("seats") or {}).items():
        if not (entry or {}).get("enabled", True):
            continue
        nxt = parse_iso(entry.get("next") or "")
        if nxt is None or nxt <= now_dt:
            out.append(seat)
    return out


def crontab_path():
    return (os.environ.get("TICKETS_CRONTAB") or "").strip()


def board_mark(board):
    return "%s-%s" % (CRON_MARK, hashlib_sha(board))


def hashlib_sha(board):
    import hashlib
    return hashlib.sha256(os.path.abspath(board).encode()).hexdigest()[:12]


def due_crontab_line(board, tickets_py, python_exe):
    mark = board_mark(board)
    cmd = "%s %s %s schedule --due" % (
        _env_prefix(board),
        _shell_quote(python_exe),
        _shell_quote(os.path.abspath(tickets_py)),
    )
    return "* * * * * %s" % cmd, mark


def _env_prefix(board):
    return "TICKETS_DIR=%s TICKET_AGENT=schedule" % _shell_quote(os.path.abspath(board))


def _shell_quote(s):
    return "'" + (s or "").replace("'", "'\"'\"'") + "'"


def read_crontab_text():
    path = crontab_path()
    if path:
        try:
            with open(path) as f:
                return f.read()
        except IOError:
            return ""
    import subprocess
    r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    if r.returncode != 0:
        return ""
    return r.stdout or ""


def write_crontab_text(text):
    path = crontab_path()
    if path:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            f.write(text)
            if text and not text.endswith("\n"):
                f.write("\n")
        os.replace(tmp, path)
        return path
    import subprocess
    r = subprocess.run(["crontab", "-"], input=text, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "crontab failed").strip())
    return "crontab"


def upsert_crontab(board, tickets_py, python_exe):
    line, mark = due_crontab_line(board, tickets_py, python_exe)
    body = read_crontab_text()
    kept = []
    skip_next = False
    for ln in (body or "").splitlines():
        if skip_next:
            skip_next = False
            continue
        if mark in ln:
            skip_next = True
            continue
        kept.append(ln)
    kept.append("# %s" % mark)
    kept.append(line)
    text = "\n".join(kept).rstrip() + "\n"
    return write_crontab_text(text), line


def remove_crontab(board):
    mark = board_mark(board)
    body = read_crontab_text()
    kept = []
    skip_next = False
    found = False
    for ln in (body or "").splitlines():
        if skip_next:
            skip_next = False
            continue
        if mark in ln:
            found = True
            skip_next = True
            continue
        kept.append(ln)
    if found:
        write_crontab_text("\n".join(kept).rstrip() + ("\n" if kept else ""))
    return found
