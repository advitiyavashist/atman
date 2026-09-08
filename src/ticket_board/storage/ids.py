"""Identifier minting and clock.

Id shapes are pinned by the frozen contract (`ProjectId`, `AgentId`,
`SessionId`, `AuditEventId`, `EventId` patterns in openapi.yaml). They are
generated here so a typo shows up in one place rather than at every call site,
and so tests can assert the published patterns.
"""

import datetime as _dt
import re
import secrets

ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"

TICKET_ID_RE = re.compile(r"^[A-Z][A-Z0-9]{1,15}-[0-9]{1,6}$")
REQUEST_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


def _suffix(n=8):
    return "".join(secrets.choice(ALPHABET) for _ in range(n))


def project_id():
    return "prj_" + _suffix(8)


def agent_id():
    return "agt_" + _suffix(8)


def session_id():
    return "ses_" + _suffix(8)


def audit_id():
    return "aud_" + _suffix(8)


def update_id():
    return "upd_" + _suffix(8)


def review_id():
    return "rev_" + _suffix(8)


def assignment_id():
    return "asg_" + _suffix(8)


def member_id():
    return "mem_" + _suffix(8)


def invitation_id():
    return "inv_" + _suffix(8)


def channel_id():
    return "chn_" + _suffix(8)


def message_id():
    return "msg_" + _suffix(8)


def thread_id():
    return "thr_" + _suffix(8)


def delivery_id():
    return "dlv_" + _suffix(8)


def wake_job_id():
    return "wjb_" + _suffix(8)


def run_id():
    return "run_" + _suffix(8)


def event_id(seq):
    """Monotonic, lexicographically sortable SSE stream id: evt_<12 digits>_<6>.

    The sequence number is zero-padded so string ordering matches numeric
    ordering -- the contract uses this value as `Last-Event-ID`, and a reconnect
    that sorts lexicographically must not skip or replay the wrong events.
    """
    return "evt_{:012d}_{}".format(int(seq), _suffix(6))


def now():
    """RFC 3339 UTC, second precision. Timestamps are always server-assigned."""
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse(stamp):
    """The inverse of `now()`. Every stored timestamp is in exactly this shape."""
    return _dt.datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=_dt.timezone.utc)


def in_seconds(seconds, *, at=None):
    """An RFC 3339 UTC timestamp `seconds` from `at` (default now)."""
    base = _dt.datetime.now(_dt.timezone.utc) if at is None else parse(at)
    return (base + _dt.timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")
