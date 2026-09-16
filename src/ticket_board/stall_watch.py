"""T-1042: measure a live run that has gone silent.

STALLED is distinct from working, idle, LIMITED, and dead. The recorded
duration is the measured silence, never the configured threshold.
"""
from __future__ import annotations

import os
import signal
from datetime import datetime, timezone

DEFAULT_THRESHOLD_S = 600.0
DEFAULT_CHECK_S = 30.0


def utcnow():
    return datetime.now(timezone.utc)


def iso_now(when=None):
    return (when or utcnow()).strftime("%Y-%m-%dT%H:%M:%SZ")


def threshold_s(environ=None):
    env = environ if environ is not None else os.environ
    raw = (env.get("TICKETS_STALL_SECS") or "").strip()
    try:
        val = float(raw) if raw else DEFAULT_THRESHOLD_S
    except ValueError:
        val = DEFAULT_THRESHOLD_S
    return max(0.05, val)


def check_s(environ=None):
    env = environ if environ is not None else os.environ
    raw = (env.get("TICKETS_STALL_CHECK_SECS") or "").strip()
    try:
        val = float(raw) if raw else DEFAULT_CHECK_S
    except ValueError:
        val = DEFAULT_CHECK_S
    return max(0.02, val)


def measured_stall_s(last_output_mono, now_mono):
    if last_output_mono is None or now_mono is None:
        return 0.0
    return max(0.0, float(now_mono) - float(last_output_mono))


def should_mark_stalled(last_output_mono, now_mono, threshold, limited=False):
    if limited or last_output_mono is None:
        return False
    return measured_stall_s(last_output_mono, now_mono) >= float(threshold)


def stall_record(last_output_at, now, measured, pid=None):
    return {
        "at": iso_now(now),
        "last_output_at": last_output_at or "",
        "measured_s": float(measured),
        "pid": pid,
        "source": "watch",
    }


def resolved_record(last_output_at, now):
    return {
        "at": iso_now(now),
        "last_output_at": last_output_at or "",
        "source": "watch",
    }


def stall_note(owner, measured, last_output_at):
    return ("STALLED: %s silent %ss (measured; last output %s). "
            "Automatic retrigger paused until output resumes or the stall is cleared."
            % (owner, _fmt_secs(measured), last_output_at or "unknown"))


def resolved_note(owner, last_output_at):
    return "STALL resolved: %s produced output again at %s." % (
        owner, last_output_at or iso_now())


def _fmt_secs(secs):
    secs = float(secs)
    if secs < 60:
        return "%g" % round(secs, 1)
    return "%g" % round(secs)


def kill_process_group(pid):
    """Kill the session/group. Returns True if a signal was sent."""
    if not pid:
        return False
    sent = False
    try:
        os.killpg(int(pid), signal.SIGKILL)
        sent = True
    except OSError:
        try:
            os.kill(int(pid), signal.SIGKILL)
            sent = True
        except OSError:
            return False
    return sent


def pid_alive(pid):
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except OSError:
        return False
    return True
