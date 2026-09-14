"""T-948: discover harnesses, suggest roles, never hardcode Cursor as CoS."""
import subprocess

ROLE_MASTER = "master"
ROLE_COS = "cos"

CLI_NOTE = "CLI: atm (tickets is an alias)."

# Leadership: reliable interactive harnesses first. Codex watchers drop wake
# (T-875 #9) — do not suggest Codex as master or CoS.
_LEADER_FIT = ("claude", "cursor", "agy", "gemini", "grok", "devin")
_NOT_LEADER = {
    "codex": "Codex watchers drop wake (T-875); worker only, not CoS/master",
}

# Cheap login argv after the catalog binary. Unknown only when the probe fails.
LOGIN_TIMEOUT = 5
LOGIN_ARGS = {
    "cursor": ("status",),
    "grok": ("status",),
    "claude": ("auth", "status"),
    "codex": ("login", "status"),
    "devin": ("auth", "status"),
    "agy": ("auth", "status"),
    "gemini": ("auth", "status"),
}
_LOGIN_NO = (
    "login required", "not logged", "unauthenticated", "please log in",
    "please login", "authentication required", "auth failed",
)


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
        return "atm msg --to %s" % who
    return "atm msg --to everyone  # no CoS yet; atm master cos <seat>"


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


def classify_login_output(rc, output):
    low = (output or "").lower()
    if any(m in low for m in _LOGIN_NO):
        return "no"
    if rc == 0:
        return "yes"
    return "unknown"


def probe_login(row, runner=None, timeout=LOGIN_TIMEOUT):
    """yes / no / unknown. unknown only when the probe fails or is undefined."""
    if not row.get("on_disk") or not row.get("path"):
        return "no"
    hid = row.get("id") or ""
    args = LOGIN_ARGS.get(hid)
    if not args:
        return "unknown"
    argv = [row["path"]] + list(args)
    if runner is not None:
        rc, output = runner(argv, timeout)
        return classify_login_output(rc, output)
    try:
        r = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout,
            stdin=subprocess.DEVNULL)
        output = (r.stdout or "") + (r.stderr or "")
        return classify_login_output(r.returncode, output)
    except (subprocess.TimeoutExpired, OSError):
        return "unknown"


def attach_login_probes(rows, runner=None, timeout=LOGIN_TIMEOUT):
    for row in rows or []:
        row["logged_in"] = probe_login(row, runner=runner, timeout=timeout)
    return rows


def logged_in_cell(row):
    preset = row.get("logged_in")
    if preset in ("yes", "no", "unknown"):
        return preset
    if not row.get("on_disk"):
        return "no"
    usage = (row.get("usage") or "").strip().lower()
    if usage == "ok" or row.get("remaining") not in (None, "", "(missing)"):
        return "yes"
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
    hid = row.get("id") or ""
    avoid = 1 if hid in _NOT_LEADER else 0
    logged = 0 if logged_in_cell(row) == "yes" else 1
    rem, _ = usage_cell(row)
    known = 0 if rem != "unknown" else 1
    try:
        fit = _LEADER_FIT.index(hid)
    except ValueError:
        fit = 99
    return (installed, avoid, logged, known, fit, hid)


def _reason(row, role):
    hid = row.get("id") or ""
    bits = ["installed" if row.get("on_disk") else "not installed"]
    bits.append("logged_in=%s" % logged_in_cell(row))
    rem, _ = usage_cell(row)
    bits.append("quota=%s" % rem)
    if hid in _NOT_LEADER:
        bits.append(_NOT_LEADER[hid])
    elif role in ("master", "cos") and hid in _LEADER_FIT:
        bits.append("reliable interactive harness")
    return "; ".join(bits)


def suggest_assignment(catalog_rows):
    """Ranked suggestion. Never applied. Empty names mean 'choose explicitly'."""
    ranked = sorted(catalog_rows or [], key=_rank)
    usable = [r for r in ranked if r.get("on_disk")]
    leaders = [r for r in usable if r.get("id") not in _NOT_LEADER]
    workers = [r for r in usable if r not in leaders[:2]]
    master = leaders[0] if leaders else None
    cos = leaders[1] if len(leaders) > 1 else None
    return {
        "master": (master or {}).get("id", ""),
        "cos": (cos or {}).get("id", ""),
        "workers": [r["id"] for r in workers],
        "reasons": {
            "master": _reason(master, "master") if master else "none installed — choose --master",
            "cos": _reason(cos, "cos") if cos else "none — choose with atm master cos <seat>",
            "workers": "; ".join(
                "%s (%s)" % (r["id"], _reason(r, "worker")) for r in workers) or "(none)",
        },
        "suggestion": True,
    }


def format_suggestion(suggestion):
    master = suggestion.get("master") or "(none installed — choose --master)"
    cos = suggestion.get("cos") or "(none — choose with atm master cos <seat>)"
    workers = ", ".join(suggestion.get("workers") or []) or "(none)"
    reasons = suggestion.get("reasons") or {}
    return "\n".join([
        CLI_NOTE,
        "SUGGESTED ASSIGNMENT (suggestion only — not applied)",
        "  master:  %s  — %s" % (master, reasons.get("master", "")),
        "  CoS:     %s  — %s" % (cos, reasons.get("cos", "")),
        "  workers: %s  — %s" % (workers, reasons.get("workers", "")),
        "Never auto-assign master or CoS. Apply explicitly:",
        "  atm master take --owner <seat>",
        "  atm master cos <seat>",
        "Non-interactive connect: pass --master <seat> --cos <seat> to apply.",
    ])
