"""T-1027: native-shell notifications read the same judged snapshot as `atm ui`.

This is not a second honesty home. Callers pass `work.nodes[]` / `agents[]`
from `board_snapshot`. Copy is derived from those fields; it must never say
`accepted` for work that is only marked done.

  python3 -m ticket_board.native_notify --snapshot board.json
"""
from __future__ import annotations

import json
import re
import sys

ACCEPTED = re.compile(r"\baccepted\b", re.I)


def _review_verified(node):
    review = node.get("review") or {}
    if isinstance(review, dict) and "verified" in review:
        return bool(review.get("verified"))
    return False


def ticket_alert(node):
    """One notification for one work-graph node. Never invents Accepted."""
    node = node or {}
    tid = (node.get("id") or "").strip()
    title = (node.get("title") or "").strip()
    status = (node.get("status") or "").strip()
    unverified = bool(node.get("unverified"))
    verified = _review_verified(node)
    who = (node.get("who") or node.get("owner") or "").strip()

    if unverified or (status == "done" and not verified):
        alert = {
            "kind": "unverified-done",
            "title": "Marked done; verification not recorded",
            "body": "%s %s" % (tid, title),
            "ticket": tid,
        }
    elif status == "review" and not verified:
        alert = {
            "kind": "submitted",
            "title": "Submitted for review",
            "body": "%s %s — awaiting review" % (tid, title),
            "ticket": tid,
        }
    elif verified:
        alert = {
            "kind": "accepted",
            "title": "Accepted",
            "body": "%s %s" % (tid, title),
            "ticket": tid,
        }
    elif status == "claimed":
        alert = {
            "kind": "working",
            "title": "In progress",
            "body": "%s %s (%s)" % (tid, title, who or "unassigned"),
            "ticket": tid,
        }
    else:
        alert = {
            "kind": "other",
            "title": (node.get("verdict") or status or "Ticket").strip() or "Ticket",
            "body": "%s %s" % (tid, title),
            "ticket": tid,
        }
    _assert_honest(alert, verified)
    return alert


def _assert_honest(alert, verified):
    text = "%s %s" % (alert.get("title") or "", alert.get("body") or "")
    if alert.get("kind") != "accepted" and ACCEPTED.search(text):
        raise ValueError("notification said accepted without a structured ACCEPT: %r" % alert)
    if alert.get("kind") == "accepted" and not verified:
        raise ValueError("accepted notification without review.verified: %r" % alert)


def fleet_row(agent):
    """Menu-bar row. Binary found / login required is not connected."""
    agent = agent or {}
    name = (agent.get("name") or "").strip()
    auth = (agent.get("auth") or "").strip()
    reachable = bool(agent.get("reachable") or agent.get("adapter_online"))
    limit = agent.get("limit")
    if limit:
        phase, connected, label = "limited", False, "limited"
    elif auth in ("login_required", "expired"):
        phase, connected, label = "found", False, (
            "login required" if auth == "login_required" else "expired"
        )
    elif not reachable:
        phase, connected, label = "offline", False, "offline"
    else:
        phase, connected, label = "connected", True, "connected"
    return {
        "name": name,
        "phase": phase,
        "connected": connected,
        "label": label,
        "auth": auth,
        "ticket": agent.get("ticket") or "",
    }


def alerts_from_snapshot(snap):
    work = (snap or {}).get("work") or {}
    nodes = work.get("nodes") or []
    agents = (snap or {}).get("agents") or []
    return {
        "objective": ((snap or {}).get("objective") or {}).get("text") or "",
        "tickets": [ticket_alert(n) for n in nodes],
        "fleet": [fleet_row(a) for a in agents],
    }


def main(argv=None):
    argv = list(argv if argv is not None else sys.argv[1:])
    if "--help" in argv or "-h" in argv:
        print("usage: python3 -m ticket_board.native_notify --snapshot board.json")
        return 0
    path = ""
    if "--snapshot" in argv:
        path = argv[argv.index("--snapshot") + 1]
    if not path:
        sys.exit("native_notify: pass --snapshot <board.json from atm ui --json>")
    snap = json.loads(open(path, encoding="utf-8").read())
    print(json.dumps(alerts_from_snapshot(snap), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
