"""Fatih-style sounding loop mapped onto the living ticket board.

No plans/drafts/next/open/done folders. The board is the index.
lane=capture | ready | discarded. Capture is not HOLD.
"""
from __future__ import print_function

import json
import os
import re
import subprocess
import sys


LANES = ("capture", "ready", "discarded")
NO_CHANGE = (
    "", "none", "n/a", "no", "no change", "no code change",
    "investigation", "investigation only", "research only",
)
EMPTY_QUESTIONS = ("", "none", "(none)", "[]", "-", "n/a", "no", "empty", "nil")
_HEADING_MAP = (
    ("cause", ("cause or spec", "cause", "spec")),
    ("change", ("change",)),
    ("proof", ("proof", "proof commands", "prove")),
    ("deps", ("deps", "dependencies", "after")),
    ("open_questions", ("open questions", "open question", "questions")),
)


def ticket_lane(t):
    lane = (t.get("lane") or "").strip()
    if lane in LANES:
        return lane
    return "ready"


def ticket_on_hold(t):
    if t.get("hold"):
        return True
    body = (t.get("body") or "").lstrip()
    return body.upper().startswith("HOLD")


def claimable_lane(t):
    return ticket_lane(t) == "ready" and not ticket_on_hold(t)


def _questions_list(text):
    raw = (text or "").strip()
    low = raw.lower()
    if low in EMPTY_QUESTIONS:
        return []
    if raw.startswith("["):
        try:
            val = json.loads(raw)
            if isinstance(val, list):
                return [str(x).strip() for x in val if str(x).strip()]
        except ValueError:
            pass
    out = []
    for ln in raw.splitlines():
        item = ln.strip().lstrip("-*").strip()
        if item and item.lower() not in EMPTY_QUESTIONS:
            out.append(item)
    return out


def is_no_change(change):
    return (change or "").strip().lower() in NO_CHANGE


def parse_sound_body(body):
    sections = {"cause": "", "change": "", "proof": "", "deps": "", "open_questions": ""}
    current = None
    buf = []

    def flush():
        if current:
            sections[current] = "\n".join(buf).strip()

    for line in (body or "").splitlines():
        m = re.match(r"^#{1,3}\s+(.*)$", line.strip())
        if m:
            heading = m.group(1).strip().lower().rstrip(":")
            found = None
            for key, aliases in _HEADING_MAP:
                if heading in aliases:
                    found = key
                    break
            if found:
                flush()
                current = found
                buf = []
                continue
        if current is not None:
            buf.append(line)
    flush()
    return sections


def parse_sound_notes(notes):
    out = {}
    if not (notes or "").strip():
        return out
    aliases = {
        "cause": "cause", "spec": "cause", "change": "change", "proof": "proof",
        "deps": "deps", "dependencies": "deps", "questions": "open_questions",
        "open_questions": "open_questions", "open-questions": "open_questions",
    }
    for part in re.split(r";\s*", notes.strip()):
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        key = aliases.get(k.strip().lower())
        if key:
            out[key] = v.strip()
    return out


def merge_sound_fields(body, notes):
    fields = parse_sound_body(body)
    notes_fields = parse_sound_notes(notes)
    for k, v in notes_fields.items():
        fields[k] = v
    return fields


def sound_fields_complete(fields):
    for key in ("cause", "change", "proof", "deps"):
        if not (fields.get(key) or "").strip():
            return False
    return True


def format_sound_body(thought, fields, extra=""):
    qs = fields.get("open_questions") or "(none)"
    if isinstance(qs, (list, tuple)):
        qs = "\n".join("- %s" % q for q in qs) if qs else "(none)"
    parts = []
    if thought:
        parts.append(thought.strip())
    if extra:
        parts.append(extra.strip())
    parts.append("## Cause or spec\n%s" % (fields.get("cause") or "").strip())
    parts.append("## Change\n%s" % (fields.get("change") or "").strip())
    parts.append("## Proof\n%s" % (fields.get("proof") or "").strip())
    parts.append("## Deps\n%s" % (fields.get("deps") or "").strip())
    parts.append("## Open questions\n%s" % qs)
    return "\n\n".join(p for p in parts if p).strip() + "\n"


def capture_title(thought, area=""):
    text = (thought or "").strip()
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    slug = slug[:48] or "capture"
    if area:
        return ("%s-%s" % (re.sub(r"[^a-z0-9]+", "-", area.lower()).strip("-"), slug))[:72]
    return (text[:72] if text else slug)


def related_done_notes(tickets, thought, limit=3):
    tokens = set(w.lower() for w in re.findall(r"[A-Za-z]{4,}", thought or ""))
    if not tokens:
        return []
    hits = []
    for t in tickets:
        if t.get("status") != "done":
            continue
        blob = " ".join(
            [t.get("title") or "", t.get("body") or ""]
            + [n.get("text", "") for n in (t.get("notes") or [])]
        ).lower()
        score = sum(1 for w in tokens if w in blob)
        if not score:
            continue
        last = ""
        notes = t.get("notes") or []
        if notes:
            last = (notes[-1].get("text") or "")[:200]
        hits.append((score, t["id"], t.get("title") or "", last))
    hits.sort(key=lambda x: (-x[0], x[1]))
    return hits[:limit]


def graph_snapshot(tickets, ready_ids, capture_ids):
    counts = {}
    for t in tickets:
        counts[t.get("status") or "?"] = counts.get(t.get("status"), 0) + 1
    status = ", ".join("%d %s" % (counts[s], s) for s in (
        "open", "claimed", "review", "blocked", "done") if s in counts)
    ready = ", ".join(ready_ids[:12]) or "(none)"
    capture = ", ".join(capture_ids[:12]) or "(none)"
    return "status %s; ready %s; capture %s" % (status or "empty", ready, capture)


def fail_ids_from_env():
    raw = os.environ.get("TICKETS_HARNESS_FAIL") or ""
    return set(x.strip().lower() for x in raw.split(",") if x.strip())


def catalog_dispatch_fail(row):
    """Return a reason string if this catalog row must not be dispatched."""
    hid = (row.get("id") or "").strip().lower()
    if hid in fail_ids_from_env():
        return "FAIL usage (%s in TICKETS_HARNESS_FAIL)" % hid
    if (row.get("usage_status") or "").upper() == "FAIL":
        return "FAIL usage row"
    pol = (row.get("policy") or "").lower()
    if hid == "gemini" or "list only" in pol:
        return "FAIL list-only harness"
    return ""


def pr_num(pr):
    s = str(pr or "").strip()
    if not s:
        return ""
    if s.startswith("http"):
        s = s.rstrip("/").split("/")[-1]
    return s


def injected_pr_state(pr):
    raw = os.environ.get("TICKETS_PR_STATE") or ""
    if not raw:
        return ""
    try:
        table = json.loads(raw)
    except ValueError:
        return ""
    key = pr_num(pr)
    return str(table.get(str(pr)) or table.get(key) or table.get(int(key) if key.isdigit() else "") or "")


def gh_pr_state(pr):
    """Best-effort GitHub PR state: merged | waiting | ask."""
    injected = injected_pr_state(pr)
    if injected:
        return injected.lower()
    num = pr_num(pr)
    if not num:
        return "ask"
    try:
        r = subprocess.run(
            ["gh", "pr", "view", num, "--json", "state,mergedAt,reviewDecision,statusCheckRollup"],
            capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return "ask"
    if r.returncode != 0:
        return "ask"
    try:
        data = json.loads(r.stdout or "{}")
    except ValueError:
        return "ask"
    if (data.get("state") or "").upper() == "MERGED" or data.get("mergedAt"):
        return "merged"
    return "waiting"


def pin_is_trunk_ancestor(git_fn, trunk_fn, t):
    sha = (t.get("commit") or "").strip()
    if not sha:
        return False
    if os.environ.get("TICKETS_PR_ANCESTOR") == "1":
        return True
    art = t.get("artifact_dir") or None
    trunk = trunk_fn(cwd=art)
    return git_fn("merge-base", "--is-ancestor", sha, trunk, cwd=art) is not None


def review_requires_pr(t):
    if not t.get("sounded_at"):
        return False
    role = (t.get("role") or "").strip()
    body = t.get("body") or ""
    if role in ("docs", "pm") and re.search(r"\bno PR\b", body, re.I):
        return False
    return True


def worker_busy(tickets, seat, except_id=""):
    """Claimed or reserved open/review tickets counting as one-per-worker."""
    held = []
    for t in tickets:
        if t.get("id") == except_id:
            continue
        if t.get("status") == "claimed" and (t.get("owner") or "") == seat:
            held.append(t["id"])
        elif (t.get("reserved_for") or "").strip() == seat and t.get("status") in (
                "open", "claimed", "review"):
            held.append(t["id"])
        elif t.get("status") == "review" and (t.get("owner") or "") == seat:
            held.append(t["id"])
    return held


def retro_repeats(tickets, since_iso=""):
    """Shared 4-grams across done + discarded notes. Empty if nothing repeats."""
    blobs = []
    for t in tickets:
        if t.get("status") != "done" and ticket_lane(t) != "discarded":
            continue
        if since_iso and (t.get("done_at") or t.get("updated") or t.get("created") or "") < since_iso:
            continue
        text = " ".join(
            [t.get("title") or ""]
            + [n.get("text", "") for n in (t.get("notes") or [])]
        )
        words = [w.lower() for w in re.findall(r"[A-Za-z]{3,}", text)
                 if w.lower() not in _STOP]
        grams = [" ".join(words[i:i + 4]) for i in range(0, max(0, len(words) - 3))]
        blobs.append((t["id"], t.get("title") or "", grams, text))
    counts = {}
    owners = {}
    for tid, title, grams, _text in blobs:
        for g in set(grams):
            counts[g] = counts.get(g, 0) + 1
            owners.setdefault(g, []).append(tid)
    repeats = [(n, g, sorted(set(owners[g]))) for g, n in counts.items() if n >= 2]
    repeats.sort(reverse=True)
    return repeats[:8]


_STOP = set("""
the a an and or to of for with from that this was were been have has had not
you your they them their this those into onto over under after before ticket
tickets tickets.py review notes sha branch main origin worktree
""".split())


def since_cutoff(spec):
    """Parse 7d / 24h / ISO. Empty spec = no cutoff."""
    spec = (spec or "").strip()
    if not spec:
        return ""
    m = re.match(r"^(\d+)d$", spec)
    if m:
        from datetime import datetime, timedelta, timezone
        return (datetime.now(timezone.utc) - timedelta(days=int(m.group(1)))).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
    m = re.match(r"^(\d+)h$", spec)
    if m:
        from datetime import datetime, timedelta, timezone
        return (datetime.now(timezone.utc) - timedelta(hours=int(m.group(1)))).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
    return spec
