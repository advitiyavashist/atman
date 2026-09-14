"""T-948: discover harnesses, suggest roles, never hardcode Cursor as CoS."""

ROLE_MASTER = "master"
ROLE_COS = "cos"

# harness ids we will suggest for leadership, in preference order when tied
_LEADER_FIT = ("claude", "codex", "cursor", "agy", "gemini", "devin", "grok")


def master_holder(state):
    name = ((state or {}).get("owner") or "").strip()
    return name or ""


def cos_holder(state):
    name = ((state or {}).get("cos") or "").strip()
    return name or ""


def format_holder(name, role):
    if name:
        return name
    if role == ROLE_COS:
        return "no CoS yet"
    return "no master yet"


def mail_to_cos(state):
    """Who to message for CoS work. Never a hardcoded seat."""
    who = cos_holder(state)
    if who:
        return "tickets msg --to %s" % who
    return "tickets msg --to everyone  # no CoS yet; tickets master cos <seat>"


def usage_cell(row):
    """Honest remaining/reset. Missing data is unknown, never 0 or FAIL."""
    rem = row.get("remaining")
    reset = row.get("reset")
    if rem in (None, "", "(missing)"):
        rem_s = "unknown"
    else:
        rem_s = str(rem)
        if rem_s in ("0", "0.0"):
            rem_s = "unknown"
    if reset in (None, "", "(missing)"):
        reset_s = "unknown"
    else:
        reset_s = str(reset)
    return rem_s, reset_s


def logged_in_cell(row):
    if not row.get("on_disk"):
        return "no"
    usage = (row.get("usage") or "").strip().lower()
    if usage == "ok" or row.get("remaining") not in (None, "", "(missing)"):
        return "yes"
    if usage in ("fail", "error"):
        return "unknown"
    return "unknown"


def discovery_rows(catalog_rows):
    out = []
    for r in catalog_rows or []:
        rem, reset = usage_cell(r)
        out.append({
            "harness": r.get("id") or "",
            "installed": "yes" if r.get("on_disk") else "no",
            "logged_in": logged_in_cell(r),
            "remaining": rem,
            "reset": reset,
        })
    return out


def format_discovery_table(rows):
    lines = [
        "DISCOVER (probe only — unknown means we do not have the figure)",
        "%-10s %-10s %-10s %-16s %s" % (
            "harness", "installed", "logged_in", "remaining", "reset"),
    ]
    for r in rows:
        lines.append("%-10s %-10s %-10s %-16s %s" % (
            r["harness"][:10], r["installed"], r["logged_in"],
            r["remaining"][:16], r["reset"]))
    return "\n".join(lines)


def _rank(row):
    installed = 0 if row.get("on_disk") else 1
    logged = 0 if logged_in_cell(row) == "yes" else 1
    rem, _ = usage_cell(row)
    known = 0 if rem != "unknown" else 1
    try:
        fit = _LEADER_FIT.index(row.get("id") or "")
    except ValueError:
        fit = 99
    return (installed, logged, known, fit, row.get("id") or "")


def suggest_assignment(catalog_rows):
    """Ranked suggestion. Never applied. Empty names mean 'choose explicitly'."""
    ranked = sorted(catalog_rows or [], key=_rank)
    usable = [r for r in ranked if r.get("on_disk")]
    master = usable[0]["id"] if usable else ""
    cos = usable[1]["id"] if len(usable) > 1 else ""
    workers = [r["id"] for r in usable[2:]]
    return {
        "master": master,
        "cos": cos,
        "workers": workers,
        "suggestion": True,
    }


def format_suggestion(suggestion):
    master = suggestion.get("master") or "(none installed — choose --master)"
    cos = suggestion.get("cos") or "(none — choose with tickets master cos <seat>)"
    workers = ", ".join(suggestion.get("workers") or []) or "(none)"
    return "\n".join([
        "SUGGESTED ASSIGNMENT (suggestion only — not applied)",
        "  master:  %s" % master,
        "  CoS:     %s" % cos,
        "  workers: %s" % workers,
        "Never auto-assign master or CoS. Apply explicitly:",
        "  tickets master take --owner <seat>",
        "  tickets master cos <seat>",
        "Non-interactive connect: pass --master <seat> --cos <seat> to apply.",
    ])
