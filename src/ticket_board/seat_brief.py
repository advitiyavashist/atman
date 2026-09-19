"""T-1078: the seat brief -- the board's rules in the seat's first token.

A spawned coding agent gets one prompt and no second round trip. Whatever is
not in that prompt, the seat does not know: it will not read this source, and
it may never open AGENTS.md. So the prompt itself has to say who the seat is,
what it owns, the gate its commit must pass, the exact commands that move the
board, and the three things it must refuse.

This module is pure text composition over facts the caller already has. It
reads no file, no environment and no credential -- `tickets.py` gathers the
facts (seat, ticket, reviewer, one usage line from the provider usage reader)
and renders the brief in the one place a seat prompt is built (`prompt_text`).
There is no second delivery mechanism: the brief is the head of the ordinary
worker prompt, not a separate message.

The brief is a FIRST-TURN document. A watcher re-renders the prompt on every
wake, so `is_first_turn` gates it on the per-run counter the watcher already
stamps (`TICKETS_RUN_NO`): turn 1 gets the brief, turn 7 gets the steady-state
worker prompt it has had all along. A steer never renders a prompt at all, so
a mid-run course correction cannot re-brief a seat either.
"""
from __future__ import annotations

BRIEF_HEAD = "SEAT BRIEF"
GATE_HEAD = "ACCEPT GATE"
REFUSE_HEAD = "REFUSE -- not yours to do, ever"
COMMANDS_HEAD = "COMMANDS -- nothing else moves the board"
RUNS_HEAD = "RUNS -- this run ends when you stop"
SCOPE_LIMIT = 360

# One line, carried by EVERY worker turn (not just the first), so the rule that
# decides whether work counts is never only in a prompt the seat has forgotten.
GATE_ONE_LINER = (
    "ACCEPT GATE: your commit lands only when a DIFFERENT seat runs "
    "`atm accept <id> --sha <full 40-char review head> --notes \"...\"`; you cannot accept your own "
    "ticket, a commit after `atm review` moves the head and voids the accept, and no ticket that "
    "depends on yours opens until the accept lands."
)
# T-1097: persist seats lose a suite that was backgrounded when the run exits.
RUN_ONE_LINER = (
    "RUN: long commands stay in the FOREGROUND; this run ends when you stop -- "
    "backgrounding a test suite loses the work."
)


def _scope_lines(body, limit=SCOPE_LIMIT):
    """The ticket body, trimmed to something a reader holds in their head."""
    text = (body or "").strip()
    if not text:
        return []
    if len(text) > limit:
        text = text[:limit].rstrip() + " ...(`atm show <id>` for the rest)"
    return [ln.strip() for ln in text.splitlines() if ln.strip()][:4]


def compose(seat, roles="", harness="", worktree="", ticket_id="", ticket_title="",
            ticket_status="", review_head="", scope="", reviewer="", usage_line=""):
    """The seat brief: identity, ticket, gate, commands, refusals, usage.

    Every argument is a fact, already resolved by the caller. Missing facts
    degrade to an honest line ("no ticket yet") rather than a silent gap --
    a seat that is told nothing about its ticket must be told that, too.
    """
    seat = (seat or "?").strip() or "?"
    tid = (ticket_id or "").strip()
    slot = tid or "<id>"
    reviewer = (reviewer or "").strip()
    who = [seat]
    if roles:
        who.append("roles " + roles)
    who.append("harness " + ((harness or "").strip() or "claude"))
    if worktree:
        who.append("worktree " + worktree)

    out = [
        "%s -- read once, then act from it. You get no second briefing." % BRIEF_HEAD,
        "",
        "SEAT      " + " · ".join(who),
    ]
    if tid:
        out.append("TICKET    %s  %s%s" % (
            tid, (ticket_title or "").strip(),
            ("  [%s]" % ticket_status) if ticket_status else ""))
        for ln in _scope_lines(scope):
            out.append("  scope   " + ln)
        out.append("  exit    tests pass, then `atm review %s` -- and an accept from another seat (below)." % tid)
        if review_head:
            out.append("  in review at %s: it needs another seat's accept on exactly that SHA. "
                       "Commit on top of it and the accept is void." % review_head)
    else:
        out.append("TICKET    none held yet -- `atm next` hands you one and prints its scope and briefing "
                   "files. Work only what it hands you.")
    out += [
        "",
        "%s -- the rule that decides whether your work counts" % GATE_HEAD,
        "  1. You finish at a SHA: commit, `atm sync`, then",
        "     `atm review %s --notes \"paths, tests, decisions\"`. That pins the review head." % slot,
        "  2. A DIFFERENT seat must then run",
        "     `atm accept %s --sha <full 40-char review head> --notes \"why this is accepted\"`." % slot,
        "     `atm accept` refuses the ticket's own author, and refuses any SHA that is not the pinned head.",
        "  3. Until that accept lands, every ticket that depends on %s stays shut: `atm next` hands the" % slot,
        "     follow-on work to nobody, you included. Done without an accept blocks the chain.",
        "  4. Commit again after `atm review` and the head has moved: the accept is void and the",
        "     work must be reviewed again at the new head. Do not push past a pending accept.",
    ]
    if reviewer:
        out.append("  Ask %s for the accept (`atm msg --to %s --re %s`). Never your own name."
                   % (reviewer, reviewer, slot))
    out += [
        "",
        COMMANDS_HEAD,
        "  claim      atm next                 (`atm mine` shows what you already hold)",
        "  hand off   atm reopen %s --notes \"where I left it, what is next\"" % slot,
        "  progress   atm update %s \"...\"        at least every 45 minutes" % slot,
        "  review     atm sync && atm review %s --notes \"paths, tests, decisions\"" % slot,
        "  blocked    atm block %s --reason \"...\"  and  atm msg \"stuck: what, tried, need\"%s --re %s"
        % (slot, (" --to " + reviewer) if reviewer else "", slot),
        "  note       atm note %s \"...\"   |   atm msg \"...\" --to <seat>" % slot,
        "",
        RUNS_HEAD,
        "  Long commands (pytest, builds) stay in the FOREGROUND of this turn.",
        "  Backgrounding a test suite loses it: the watcher treats the run as finished",
        "  and the work never lands.",
        "",
        REFUSE_HEAD,
        "  1. Another seat's ticket, branch or worktree. You touch %s and your own worktree, nothing else."
        % (tid or "the one ticket you hold"),
        "  2. Accepting your own work -- including asking for an accept under another name, or",
        "     recording one yourself. The accept must come from a seat that did not write the commit.",
        "  3. Editing .tickets/ JSON (or any board file) by hand. Every board change goes through `atm`.",
    ]
    if usage_line:
        out += ["", "USAGE     " + usage_line.strip()]
    return "\n".join(out)


def is_first_turn(run_no):
    """True when this prompt render is a seat's first turn of its life.

    The watcher stamps the run counter for each launch, so run 1 is the spawn
    turn and every later wake is a re-render of the same seat's prompt. An
    unstamped render (an operator typing `atm prompt`, a fresh interactive
    session pasting it in) is a first turn by definition: nobody has briefed
    that reader yet.
    """
    raw = ("" if run_no is None else str(run_no)).strip()
    if not raw:
        return True
    try:
        return int(raw) <= 1
    except ValueError:
        return True
