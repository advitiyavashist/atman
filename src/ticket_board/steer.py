"""T-1047: live steer a running seat without killing it.

Claude Code's messaging socket already accepts a priority-next user frame
(T-857). This module frames a course correction or a question, classifies
the inject receipt honestly, and records the steer on the ticket.

A steer never rewrites ticket scope. Delivery is evidence-only: a write
without a receipt is not delivered.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

STEERABLE_PROVIDERS = ("claude",)

# Receipts that prove the frame was accepted by the peer inbox.
# Write-without-ack (delivered-unconfirmed) is NOT delivered.
DELIVERED_RECEIPTS = ("delivered-confirmed", "woken")

UNSTEERABLE = {
    "codex": "harness codex has no mid-run steer transport (Claude UDS priority=next only)",
    "cursor": "harness cursor has no mid-run steer transport (Claude UDS priority=next only)",
    "agy": "harness agy is supervised; no mid-run inject path",
    "antigravity": "harness antigravity is supervised; no mid-run inject path",
    "gemini": "harness gemini is supervised; no mid-run inject path",
    "devin": "harness devin is supervised; no mid-run inject path",
    "grok": "harness grok has no mid-run steer transport",
    "grokbots": "harness grokbots has no mid-run steer transport",
    "remote": "harness remote has no mid-run steer transport",
}


def new_steer_id():
    return "ste-" + uuid.uuid4().hex[:12]


def utcnow():
    return datetime.now(timezone.utc)


def iso_now(when=None):
    return (when or utcnow()).strftime("%Y-%m-%dT%H:%M:%SZ")


def is_delivered(label):
    return str(label or "") in DELIVERED_RECEIPTS


def report_receipt(label):
    """Human receipt. The word 'delivered' appears only with evidence."""
    s = str(label or "").strip() or "no-receipt"
    if is_delivered(s):
        return "delivered (%s)" % s
    if s == "delivered-unconfirmed":
        return "unconfirmed (no receipt)"
    return s


def harness_refuse_reason(harness, provider=""):
    """Why this seat cannot be steered, or '' if Claude can try."""
    name = (harness or provider or "").strip().lower()
    if name in ("claude", "cursor+claude"):
        return ""
    if name in UNSTEERABLE:
        return UNSTEERABLE[name]
    if name:
        return "harness %s has no mid-run steer transport (Claude UDS priority=next only)" % name
    return "unknown harness; no mid-run steer transport"


def frame_payload(kind, sender, text, ticket, steer_id, at=""):
    """Instruction the running seat sees. Does not change ticket fields."""
    at = at or iso_now()
    kind = "ask" if kind == "ask" else "redirect"
    text = (text or "").strip()
    if kind == "ask":
        return (
            "STEER-ASK id=%s from=%s at=%s ticket=%s\n"
            "Question: %s\n"
            "Reply on this ticket with: STEER-REPLY %s: <answer>\n"
            "Do not end the current run. Do not change ticket scope."
            % (steer_id, sender, at, ticket, text, steer_id)
        )
    return (
        "STEER id=%s from=%s at=%s ticket=%s\n"
        "Course correction: %s\n"
        "Post on this ticket that you received this steer (quote the id). "
        "Do not silently rewrite ticket scope. Continue the current run with "
        "this correction; it applies at the next tool boundary."
        % (steer_id, sender, at, ticket, text)
    )


def ticket_note(kind, sender, seat, text, receipt, steer_id, at=""):
    return (
        "STEER %s id=%s to=%s receipt=%s by=%s at=%s: %s"
        % (kind, steer_id, seat, report_receipt(receipt), sender, at or iso_now(),
           " ".join((text or "").split())[:240])
    )


def steer_record(kind, sender, seat, text, receipt, steer_id, ticket, at=""):
    return {
        "id": steer_id,
        "kind": "ask" if kind == "ask" else "redirect",
        "from": sender,
        "to": seat,
        "ticket": ticket,
        "at": at or iso_now(),
        "text": text,
        "receipt": str(receipt or ""),
        "delivered": is_delivered(receipt),
    }


def last_output(rec, transcript_ts="", watch_log_ts=""):
    """Best known last-output timestamp. Stall (T-1042) wins when present.

    Heartbeat / loop-seen is not output and is never returned as such.
    """
    stall = (rec or {}).get("stall") or {}
    if stall.get("last_output_at"):
        return stall["last_output_at"], "stall"
    if (rec or {}).get("last_output_at"):
        return rec["last_output_at"], "agent"
    if transcript_ts:
        return transcript_ts, "transcript"
    if watch_log_ts:
        return watch_log_ts, "watch-log"
    return "", "none"


def iso_from_age(age_secs, now=None):
    if age_secs is None:
        return ""
    try:
        age = float(age_secs)
    except (TypeError, ValueError):
        return ""
    when = (now or utcnow()) - timedelta(seconds=max(0.0, age))
    return iso_now(when)


def parse_steer_reply(note_text, steer_id):
    """Return the answer text if this note is STEER-REPLY <id>:, else ''."""
    prefix = "STEER-REPLY %s:" % steer_id
    text = (note_text or "").strip()
    if text.startswith(prefix):
        return text[len(prefix):].strip()
    return ""
