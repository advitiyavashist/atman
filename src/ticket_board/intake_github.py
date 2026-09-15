"""T-1023: GitHub Issues intake -- import issues as tickets, push status back.

Scope is GitHub Issues only (Linear/Jira are separate follow-ups). Uses `gh`
via subprocess, same as sounding.py's gh_pr_state -- no new dependency.

Imported tickets land at lane=capture (the same not-yet-claimable lane
`tickets capture` uses, T-798): an issue is a thought to triage, not
automatically claimable work.

"Never mutate an issue we did not create without explicit config" (the T-1023
brief): push (comment/label) is only allowed when the board itself posted the
original linking comment (github_issue.origin == "import"), or a ticket was
attached to a pre-existing issue via `tickets github-link --allow-push`,
an explicit opt-in recorded on the ticket.
"""
from __future__ import annotations

import json
import os
import subprocess


STATUS_LABELS = {
    "open": "atman:status-open",
    "claimed": "atman:status-claimed",
    "review": "atman:status-review",
    "blocked": "atman:status-blocked",
    "done": "atman:status-done",
}


def _run_gh(args, timeout=20):
    try:
        r = subprocess.run(["gh"] + list(args), capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, str(e)
    if r.returncode != 0:
        return None, (r.stderr or r.stdout or "gh exited %d" % r.returncode).strip()
    return r.stdout, ""


def _injected_issues(repo):
    """TICKETS_GH_ISSUES=<json {repo: [issue, ...]}> fakes `gh issue` for tests."""
    raw = os.environ.get("TICKETS_GH_ISSUES") or ""
    if not raw:
        return None
    try:
        table = json.loads(raw)
    except ValueError:
        return None
    return table.get(repo)


def fetch_issue(repo, number):
    """One issue dict, or (None, reason)."""
    injected = _injected_issues(repo)
    if injected is not None:
        for row in injected:
            if str(row.get("number")) == str(number):
                return row, ""
        return None, "no injected issue #%s for %s" % (number, repo)
    out, err = _run_gh(["issue", "view", str(number), "--repo", repo,
                         "--json", "number,title,body,url,state,labels"])
    if out is None:
        return None, err
    try:
        return json.loads(out), ""
    except ValueError as e:
        return None, "bad gh json: %s" % e


def fetch_issues(repo, state="open", limit=50):
    """List of issue dicts, or ([], reason) on failure."""
    injected = _injected_issues(repo)
    if injected is not None:
        rows = injected
        if state and state != "all":
            rows = [r for r in rows if (r.get("state") or "").upper() == state.upper()]
        return rows[:limit], ""
    out, err = _run_gh(["issue", "list", "--repo", repo, "--state", state,
                         "--limit", str(limit),
                         "--json", "number,title,body,url,state,labels"])
    if out is None:
        return [], err
    try:
        return json.loads(out), ""
    except ValueError as e:
        return [], "bad gh json: %s" % e


def _calls_log_path():
    return os.environ.get("TICKETS_GH_CALLS_LOG") or ""


def _record_or_run(args, summary):
    """Mutating gh calls: append-only log under test injection, else shell out."""
    log = _calls_log_path()
    if log:
        with open(log, "a") as f:
            f.write(json.dumps(summary) + "\n")
        return True, ""
    _, err = _run_gh(args)
    return (err == ""), err


def post_link_comment(repo, number, ticket_id, board_name=""):
    body = "Linked to ticket %s on the %s board." % (ticket_id, board_name or "atman")
    return _record_or_run(
        ["issue", "comment", str(number), "--repo", repo, "--body", body],
        {"action": "comment", "kind": "link", "repo": repo, "number": number,
         "ticket": ticket_id, "body": body})


def post_status_comment(repo, number, ticket_id, status, note=""):
    body = "%s is now %s." % (ticket_id, status)
    if note:
        body += " " + note
    return _record_or_run(
        ["issue", "comment", str(number), "--repo", repo, "--body", body],
        {"action": "comment", "kind": "status", "repo": repo, "number": number,
         "ticket": ticket_id, "status": status, "body": body})


def apply_status_label(repo, number, status):
    label = STATUS_LABELS.get(status)
    if not label:
        return True, ""
    return _record_or_run(
        ["issue", "edit", str(number), "--repo", repo, "--add-label", label],
        {"action": "label", "repo": repo, "number": number, "label": label})


def issue_body_with_footer(issue, repo):
    body = (issue.get("body") or "").strip()
    footer = "Imported from %s#%s (%s)" % (repo, issue.get("number"), issue.get("url") or "")
    return (body + "\n\n---\n" + footer) if body else footer


def already_imported(tickets, repo, number):
    for t in tickets:
        gi = t.get("github_issue") or {}
        if gi.get("repo") == repo and str(gi.get("number")) == str(number):
            return t
    return None


def push_allowed(t):
    gi = t.get("github_issue") or {}
    if not gi:
        return False, "no linked github issue"
    if gi.get("origin") == "import":
        return True, ""
    if gi.get("allow_push"):
        return True, ""
    return False, ("issue not created by this board's import; re-link with "
                    "`tickets github-link --allow-push` to permit pushing status back")
