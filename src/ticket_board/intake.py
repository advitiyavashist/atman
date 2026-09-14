"""T-949: signals become capture-lane tickets. Never dispatch."""

SOURCES = ("github-issue", "ci-failure", "cron")
ALIASES = {
    "github-issue": "github-issue",
    "issue": "github-issue",
    "github": "github-issue",
    "ci-failure": "ci-failure",
    "ci": "ci-failure",
    "cron": "cron",
}


def normalize_kind(kind):
    return ALIASES.get((kind or "").strip().lower(), (kind or "").strip())


def source_id(kind, payload):
    if kind == "github-issue":
        repo = (payload.get("repo") or "").strip()
        number = str(payload.get("number") or "").strip()
        if repo and number:
            return "gh:%s#%s" % (repo, number)
    if kind == "ci-failure":
        repo = (payload.get("repo") or "").strip()
        run_id = str(payload.get("run_id") or payload.get("runId") or "").strip()
        if repo and run_id:
            return "ci:%s/run/%s" % (repo, run_id)
    if kind == "cron":
        name = (payload.get("name") or "").strip()
        when = (payload.get("schedule") or payload.get("when") or "").strip()
        if name:
            return "cron:%s@%s" % (name, when or "manual")
    return ""


def find_by_source(tickets, sid):
    if not sid:
        return None
    for t in tickets or []:
        intake = t.get("intake") or {}
        if intake.get("source_id") == sid:
            return t
    return None


def title_for(kind, payload):
    raw = (payload.get("title") or payload.get("name") or payload.get("prompt") or "").strip()
    if kind == "github-issue":
        return raw or ("GitHub issue %s#%s" % (payload.get("repo"), payload.get("number")))
    if kind == "ci-failure":
        job = payload.get("name") or payload.get("workflow") or "CI"
        return "CI failed: %s" % job
    if kind == "cron":
        return raw[:80] or ("cron: %s" % (payload.get("name") or "scheduled"))
    return raw[:80] or "intake signal"


def body_for(kind, payload, sid):
    url = (payload.get("url") or "").strip()
    evidence = payload.get("evidence") or payload
    lines = [
        "INTAKE (capture lane — not claimable until tickets sound)",
        "source: %s" % kind,
        "source_id: %s" % sid,
    ]
    if url:
        lines.append("url: %s" % url)
    lines.append("evidence:")
    for key in ("repo", "number", "run_id", "sha", "name", "schedule", "prompt", "conclusion"):
        if payload.get(key) not in (None, ""):
            lines.append("  %s: %s" % (key, payload[key]))
    if isinstance(evidence, dict) and evidence.get("snippet"):
        lines.append("  snippet: %s" % evidence["snippet"])
    lines.append("")
    lines.append("Do not dispatch. Sound with cause/change/proof first.")
    return "\n".join(lines)


def ingest(board, kind, payload, hooks):
    """Create a capture-lane ticket or return the existing one. Never sounds."""
    kind = normalize_kind(kind)
    if kind not in SOURCES:
        raise ValueError("unknown intake source %r" % kind)
    sid = source_id(kind, payload)
    if not sid:
        raise ValueError("intake %s needs a stable source id" % kind)
    existing = find_by_source(hooks.load_all(board), sid)
    if existing:
        return existing, "deduped"
    t = hooks.create(
        board,
        title_for(kind, payload),
        body=body_for(kind, payload, sid),
        role=payload.get("role") or "",
        deps=[],
        priority=int(payload.get("priority") or 2),
    )
    t["lane"] = "capture"
    t["open_questions"] = ["not yet sounded"]
    t["intake"] = {
        "source": kind,
        "source_id": sid,
        "url": (payload.get("url") or "").strip(),
        "evidence": {
            "repo": payload.get("repo") or "",
            "number": payload.get("number") or "",
            "run_id": payload.get("run_id") or payload.get("runId") or "",
            "sha": payload.get("sha") or "",
            "name": payload.get("name") or "",
        },
    }
    t["notes"] = list(t.get("notes") or [])
    t["notes"].append({
        "by": hooks.who(),
        "at": hooks.now(),
        "text": "intake %s %s (capture; not dispatched)" % (kind, sid),
    })
    hooks.save(board, t)
    return t, "created"
