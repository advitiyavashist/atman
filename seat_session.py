"""Headless seat session resume (T-1501).

Store each seat's provider session id after a run and resume it on the next
wake (Claude ``--resume``, Codex thread id). A failed resume starts fresh
*visibly*. Auth errors never silently discard the stored session.
"""

from __future__ import annotations

import re
import uuid

CLAUDE_RESUME_FAIL = re.compile(
    r"(no such session|session not found|unknown session|cannot resume|failed to resume)",
    re.I,
)
CODEX_RESUME_FAIL = re.compile(
    r"(thread not found|unknown thread|cannot resume|no such thread)",
    re.I,
)


def new_claude_session_id():
    return str(uuid.uuid4())


def extract_session_id(harness, output, *, env_thread=""):
    """Best-effort parse of a provider session/thread id from run output."""
    text = output or ""
    h = (harness or "").split("+", 1)[0]
    if h == "claude":
        for pat in (
            r'"session_id"\s*:\s*"([0-9a-fA-F-]{36})"',
            r"session[_ ]id[=: ]+([0-9a-fA-F-]{36})",
        ):
            m = re.search(pat, text, re.I)
            if m:
                return m.group(1)
        return ""
    if h == "codex":
        if env_thread:
            return env_thread.strip()
        for pat in (
            r'"thread_id"\s*:\s*"([^"]+)"',
            r"thread[_ ]id[=: ]+(\S+)",
        ):
            m = re.search(pat, text, re.I)
            if m:
                return m.group(1).strip()
        return ""
    return ""


def resume_failed(harness, output, rc):
    """True when the provider rejected a resume and a fresh start is warranted."""
    if rc in (0, None):
        return False
    text = output or ""
    h = (harness or "").split("+", 1)[0]
    if h == "claude":
        return bool(CLAUDE_RESUME_FAIL.search(text))
    if h == "codex":
        return bool(CODEX_RESUME_FAIL.search(text))
    return False


def inject_claude_session(cmd, session_id, *, resume):
    """Insert ``--resume`` or ``--session-id`` after the claude token."""
    sid = (session_id or "").strip()
    if not sid or not cmd:
        return cmd
    flag = "--resume" if resume else "--session-id"
    if flag in cmd and sid in cmd:
        return cmd
    # Drop a prior session flag so we do not double-bind.
    cleaned = re.sub(r"\s--(?:resume|session-id)\s+\S+", "", cmd)
    return re.sub(r"\bclaude\b", "claude %s %s" % (flag, sid), cleaned, count=1)


def plan_run(harness, stored, *, force_fresh=False, fresh_reason=""):
    """Decide resume vs fresh for one headless run.

    Returns dict: mode ('resume'|'fresh'), session_id, reason, env_updates.
    """
    h = (harness or "").split("+", 1)[0]
    stored = stored or {}
    stored_id = (stored.get("id") or "").strip()
    same = (stored.get("harness") or "").split("+", 1)[0] == h
    if h == "claude":
        if force_fresh or not stored_id or not same:
            sid = new_claude_session_id()
            reason = fresh_reason or (
                "forced fresh" if force_fresh else
                ("no stored session" if not stored_id else "harness changed"))
            return {
                "mode": "fresh",
                "session_id": sid,
                "reason": reason,
                "env_updates": {},
                "notice": "fresh session (%s); id=%s" % (reason, sid),
            }
        return {
            "mode": "resume",
            "session_id": stored_id,
            "reason": "",
            "env_updates": {},
            "notice": "resuming session %s" % stored_id,
        }
    if h == "codex":
        if force_fresh or not stored_id or not same:
            reason = fresh_reason or (
                "forced fresh" if force_fresh else
                ("no stored thread" if not stored_id else "harness changed"))
            return {
                "mode": "fresh",
                "session_id": "",
                "reason": reason,
                "env_updates": {},
                "notice": "fresh Codex thread (%s)" % reason,
            }
        return {
            "mode": "resume",
            "session_id": stored_id,
            "reason": "",
            "env_updates": {"CODEX_THREAD_ID": stored_id, "CODEX_SESSION_ID": stored_id},
            "notice": "resuming Codex thread %s" % stored_id,
        }
    return {
        "mode": "fresh",
        "session_id": "",
        "reason": "harness has no resume path",
        "env_updates": {},
        "notice": "",
    }


def apply_plan_to_cmd(cmd, harness, plan):
    h = (harness or "").split("+", 1)[0]
    if h == "claude" and plan.get("session_id"):
        return inject_claude_session(
            cmd, plan["session_id"], resume=(plan.get("mode") == "resume"))
    return cmd


def session_record(harness, session_id, *, mode, reason=""):
    return {
        "harness": harness or "",
        "id": session_id or "",
        "mode": mode or "fresh",
        "reason": reason or "",
        "at": "",  # filled by caller with board now()
    }
