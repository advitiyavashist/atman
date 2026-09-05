"""tickets CLI — multi-agent file-backed board.

``tickets clear`` only releases this agent's claim. It never deletes ticket
files. Nuclear wipe is ``tickets board-reset --i-understand-destroy-all-tickets``
plus a mandatory confirmation string.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from tickets import DESTROY_CONFIRM, __version__
from tickets.backup import BackupError, backup_board, restore_board
from tickets.board import Board, BoardError
from tickets.coordination import BoardLock, read_presence, resolve_agent, write_here, write_presence
from tickets.models import PRIORITIES, STATUSES, utcnow
from tickets.paths import normalize_ticket_id, resolve_board

# Subcommands that may delete or replace ticket files. Each takes an automatic
# secret-excluding snapshot first.
DESTRUCTIVE = frozenset({"board-reset", "board-restore"})


class CliError(Exception):
    def __init__(self, message: str, code: int = 2) -> None:
        super().__init__(message)
        self.code = code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tickets",
        description=(
            "Multi-agent ticket board. "
            "clear = unclaim (never wipes). "
            "board-reset is the only full wipe and requires two confirmations."
        ),
    )
    parser.add_argument("--board", help="Board directory (default: TICKETS_BOARD or nearest .tickets)")
    parser.add_argument("--as", dest="as_agent", help="Act as this agent (else TICKETS_AGENT / here / $USER)")
    parser.add_argument("--json", dest="as_json", action="store_true", help="Machine-readable output")
    parser.add_argument("--version", action="version", version=f"tickets {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("create", help="Create a ticket")
    p.add_argument("title", nargs="+", help="Ticket title")
    p.add_argument("-b", "--body", default="", help="Description")
    p.add_argument("--priority", default="p2", choices=PRIORITIES)
    p.add_argument("--epic")
    p.add_argument("--sprint")
    p.add_argument("--dep", action="append", default=[], help="Dependency ticket id (repeatable)")

    p = sub.add_parser("assign", help="Assign a ticket to an agent (does not claim)")
    p.add_argument("ticket")
    p.add_argument("agent")

    p = sub.add_parser("claim", help="Claim a ticket or the next unblocked open ticket")
    p.add_argument("ticket", nargs="?")

    p = sub.add_parser("done", help="Mark a ticket done")
    p.add_argument("ticket", nargs="?")
    p.add_argument("note", nargs="*")

    p = sub.add_parser("review", help="Move a ticket to in_review, or record a review verdict")
    p.add_argument("ticket", nargs="?")
    p.add_argument("note", nargs="*")
    p.add_argument("--pass", dest="verdict_pass", action="store_true", help="Accept and mark done")
    p.add_argument("--fail", dest="verdict_fail", action="store_true", help="Return to claimed")

    p = sub.add_parser("note", help="Append a note (history is never dropped by clear)")
    p.add_argument("ticket")
    p.add_argument("text", nargs="+")

    p = sub.add_parser("msg", help="Message an agent or leave a note on a ticket")
    p.add_argument("target", help="Agent name or ticket id")
    p.add_argument("text", nargs="+")

    sub.add_parser("who", help="Who is holding which tickets")
    sub.add_parser("pulse", help="Board summary")

    p = sub.add_parser("here", help="Register this agent on the board")
    p.add_argument("name", nargs="?")

    p = sub.add_parser("update", help="Update ticket fields")
    p.add_argument("ticket")
    p.add_argument("--title")
    p.add_argument("--body")
    p.add_argument("--status", choices=STATUSES)
    p.add_argument("--priority", choices=PRIORITIES)
    p.add_argument("--epic")
    p.add_argument("--sprint")
    p.add_argument("--clear-epic", action="store_true")
    p.add_argument("--clear-sprint", action="store_true")

    p = sub.add_parser("list", help="List tickets")
    p.add_argument("--status", choices=STATUSES)
    p.add_argument("--assignee")
    p.add_argument("--epic")
    p.add_argument("--sprint")
    p.add_argument("--all", action="store_true", help="Include done tickets (default: hide done)")

    p = sub.add_parser("show", help="Show one ticket")
    p.add_argument("ticket")

    sub.add_parser("next", help="Show the next unblocked open ticket")

    p = sub.add_parser("dep", help="Manage dependencies")
    dep_sub = p.add_subparsers(dest="dep_cmd", required=True)
    addp = dep_sub.add_parser("add", help="T-a depends on T-b")
    addp.add_argument("ticket")
    addp.add_argument("dependency")
    rmp = dep_sub.add_parser("remove", help="Remove a dependency")
    rmp.add_argument("ticket")
    rmp.add_argument("dependency")
    lsp = dep_sub.add_parser("list", help="List dependencies for a ticket")
    lsp.add_argument("ticket")

    p = sub.add_parser("epic", help="List or assign epics")
    epic_sub = p.add_subparsers(dest="epic_cmd", required=True)
    epic_sub.add_parser("list")
    ec = epic_sub.add_parser("create")
    ec.add_argument("name")
    ea = epic_sub.add_parser("assign")
    ea.add_argument("ticket")
    ea.add_argument("name")

    p = sub.add_parser("sprint", help="List or assign sprints")
    sprint_sub = p.add_subparsers(dest="sprint_cmd", required=True)
    sprint_sub.add_parser("list")
    sc = sprint_sub.add_parser("create")
    sc.add_argument("name")
    sa = sprint_sub.add_parser("assign")
    sa.add_argument("ticket")
    sa.add_argument("name")

    p = sub.add_parser("join", help="Join a ticket as collaborator")
    p.add_argument("ticket")

    p = sub.add_parser(
        "clear",
        help="Unclaim this agent's ticket (NEVER deletes ticket files)",
    )
    p.add_argument(
        "--ticket",
        dest="ticket",
        help="Unclaim this ticket (must be held by you unless --force)",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Allow unclaiming a ticket held by someone else",
    )
    # Trap the old wipe-shaped invocations so they fail closed.
    p.add_argument("--all", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--wipe", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--board-reset", action="store_true", help=argparse.SUPPRESS)

    p = sub.add_parser(
        "board-reset",
        help="DANGER: delete all T-*.json after backup (requires confirmations)",
    )
    p.add_argument(
        "--i-understand-destroy-all-tickets",
        dest="i_understand_destroy_all_tickets",
        action="store_true",
        help="Required acknowledgement that this destroys every ticket",
    )
    p.add_argument(
        "--confirm",
        metavar="PHRASE",
        help=f"Must be exactly {DESTROY_CONFIRM}",
    )
    p.add_argument("-y", "--yes", action="store_true", help="Skip TTY prompt (still need --confirm)")

    p = sub.add_parser("board-backup", help="Snapshot .tickets (secrets excluded)")
    p.add_argument("dest", nargs="?", help="Archive path (default: sibling .tickets-backups/)")
    p.add_argument("--reason", default="manual")

    p = sub.add_parser("board-restore", help="Restore a snapshot (backs up the live board first)")
    p.add_argument("archive", help="Path to a board-backup archive")
    p.add_argument(
        "--keep-extra",
        action="store_true",
        help="Do not delete live tickets that are absent from the archive",
    )

    return parser


def emit(args: argparse.Namespace, text: str, payload: object | None = None) -> None:
    if args.as_json:
        print(json.dumps(payload if payload is not None else {"message": text}, indent=2, default=str))
    else:
        print(text)


def ticket_brief(ticket) -> dict:
    return {
        "id": ticket.id,
        "title": ticket.title,
        "status": ticket.status,
        "assignee": ticket.holder(),
        "priority": ticket.priority,
        "epic": ticket.epic,
        "sprint": ticket.sprint,
        "deps": ticket.deps,
        "notes": len(ticket.notes),
    }


def require_ticket(board: Board, raw: str | None, agent: str, *, held: bool = False):
    if raw:
        return board.load(raw)
    held_tickets = board.held_by(agent)
    if len(held_tickets) == 1:
        return held_tickets[0]
    if not held_tickets:
        raise CliError("no ticket specified and you hold none (pass T-xxx)")
    ids = ", ".join(t.id for t in held_tickets)
    raise CliError(f"you hold multiple tickets ({ids}); pass --ticket / T-xxx")


def cmd_create(board: Board, agent: str, args: argparse.Namespace) -> int:
    title = " ".join(args.title).strip()
    if not title:
        raise CliError("title is required")
    ticket = board.create(
        title,
        body=args.body,
        agent=agent,
        priority=args.priority,
        epic=args.epic,
        sprint=args.sprint,
        deps=args.dep,
    )
    emit(args, f"created {ticket.id}: {ticket.title}", ticket.to_dict())
    return 0


def cmd_assign(board: Board, agent: str, args: argparse.Namespace) -> int:
    ticket = board.load(args.ticket)
    ticket.assignee = args.agent
    ticket.updated_at = utcnow()
    ticket.add_note(agent, f"assigned to {args.agent} by {agent}", kind="assign")
    board.save(ticket)
    emit(args, f"{ticket.id} assigned to {args.agent}", ticket.to_dict())
    return 0


def cmd_claim(board: Board, agent: str, args: argparse.Namespace) -> int:
    if args.ticket:
        ticket = board.load(args.ticket)
        if not board.deps_satisfied(ticket):
            raise CliError(f"{ticket.id} has unfinished dependencies: {', '.join(ticket.deps) or 'none'}")
    else:
        ticket = board.next_open(agent=agent)
        if ticket is None:
            raise CliError("no unblocked open ticket to claim", code=1)
    board.claim(ticket, agent)
    write_presence(board.root, agent, ticket_id=ticket.id)
    emit(args, f"claimed {ticket.id}: {ticket.title}", ticket.to_dict())
    return 0


def cmd_done(board: Board, agent: str, args: argparse.Namespace) -> int:
    ticket = require_ticket(board, args.ticket, agent)
    note = " ".join(args.note).strip() or None
    board.mark_done(ticket, agent, note)
    write_presence(board.root, agent, ticket_id=None)
    emit(args, f"{ticket.id} done", ticket.to_dict())
    return 0


def cmd_review(board: Board, agent: str, args: argparse.Namespace) -> int:
    ticket = require_ticket(board, args.ticket, agent)
    note = " ".join(args.note).strip()
    if args.verdict_pass and args.verdict_fail:
        raise CliError("use only one of --pass / --fail")
    if args.verdict_pass:
        ticket.status = "done"
        ticket.add_note(agent, note or f"review passed by {agent}", kind="review")
    elif args.verdict_fail:
        ticket.status = "claimed"
        ticket.add_note(agent, note or f"review failed by {agent}", kind="review")
    else:
        ticket.status = "in_review"
        ticket.add_note(agent, note or f"submitted for review by {agent}", kind="review")
    ticket.updated_at = utcnow()
    board.save(ticket)
    emit(args, f"{ticket.id} {ticket.status}", ticket.to_dict())
    return 0


def cmd_note(board: Board, agent: str, args: argparse.Namespace) -> int:
    ticket = board.load(args.ticket)
    text = " ".join(args.text).strip()
    if not text:
        raise CliError("note text is required")
    ticket.add_note(agent, text, kind="note")
    board.save(ticket)
    emit(args, f"noted {ticket.id}", ticket.to_dict())
    return 0


def cmd_msg(board: Board, agent: str, args: argparse.Namespace) -> int:
    text = " ".join(args.text).strip()
    if not text:
        raise CliError("message text is required")
    target = args.target
    try:
        tid = normalize_ticket_id(target)
    except ValueError:
        tid = None
    if tid:
        ticket = board.load(tid)
        ticket.add_note(agent, f"msg to {tid}: {text}", kind="msg")
        board.save(ticket)
        board.append_inbox(agent, tid, text)
        emit(args, f"messaged {tid}", {"to": tid, "text": text})
        return 0
    board.append_inbox(agent, target, text)
    emit(args, f"messaged {target}", {"to": target, "text": text})
    return 0


def cmd_who(board: Board, agent: str, args: argparse.Namespace) -> int:
    rows = []
    for ticket in board.all_tickets():
        holder = ticket.holder()
        if holder and ticket.status != "done":
            rows.append({"ticket": ticket.id, "title": ticket.title, "agent": holder, "status": ticket.status})
    presence = read_presence(board.root)
    if args.as_json:
        emit(args, "", {"assignments": rows, "presence": presence})
        return 0
    if not rows:
        print("no active assignments")
    else:
        for row in rows:
            print(f"{row['ticket']}  {row['agent']:12}  {row['status']:10}  {row['title']}")
    if presence:
        print("-- presence --")
        for p in presence:
            print(f"{p.get('agent')}  ticket={p.get('ticket') or '-'}  at={p.get('at')}  host={p.get('host')}")
    return 0


def cmd_pulse(board: Board, agent: str, args: argparse.Namespace) -> int:
    tickets = board.all_tickets()
    counts = {s: 0 for s in STATUSES}
    for t in tickets:
        counts[t.status] = counts.get(t.status, 0) + 1
    held = board.held_by(agent)
    payload = {
        "board": str(board.root),
        "agent": agent,
        "counts": counts,
        "total": len(tickets),
        "you_hold": [t.id for t in held],
        "next": (board.next_open(agent=agent) or TicketStub()).id if tickets else None,
    }
    nxt = board.next_open(agent=agent)
    payload["next"] = nxt.id if nxt else None
    if args.as_json:
        emit(args, "", payload)
        return 0
    bits = "  ".join(f"{s}={counts.get(s, 0)}" for s in STATUSES)
    print(f"board {board.root}")
    print(f"agent {agent}  holding {', '.join(payload['you_hold']) or '-'}")
    print(f"tickets {len(tickets)}  {bits}")
    print(f"next {payload['next'] or '-'}")
    return 0


class TicketStub:
    id = None


def cmd_here(board: Board, agent: str, args: argparse.Namespace) -> int:
    name = (args.name or agent).strip()
    write_here(board.root, name)
    held = board.held_by(name)
    write_presence(board.root, name, ticket_id=held[0].id if held else None)
    emit(args, f"here {name}", {"agent": name, "board": str(board.root)})
    return 0


def cmd_update(board: Board, agent: str, args: argparse.Namespace) -> int:
    ticket = board.load(args.ticket)
    if args.title is not None:
        ticket.title = args.title
    if args.body is not None:
        ticket.body = args.body
    if args.status is not None:
        ticket.status = args.status
    if args.priority is not None:
        ticket.priority = args.priority
    if args.clear_epic:
        ticket.epic = None
    elif args.epic is not None:
        ticket.epic = args.epic
    if args.clear_sprint:
        ticket.sprint = None
    elif args.sprint is not None:
        ticket.sprint = args.sprint
    ticket.updated_at = utcnow()
    ticket.add_note(agent, f"updated by {agent}", kind="update")
    board.save(ticket)
    emit(args, f"updated {ticket.id}", ticket.to_dict())
    return 0


def cmd_list(board: Board, agent: str, args: argparse.Namespace) -> int:
    tickets = board.all_tickets()
    if not args.all:
        tickets = [t for t in tickets if t.status != "done"]
    if args.status:
        tickets = [t for t in tickets if t.status == args.status]
    if args.assignee:
        tickets = [t for t in tickets if t.holder() == args.assignee]
    if args.epic:
        tickets = [t for t in tickets if t.epic == args.epic]
    if args.sprint:
        tickets = [t for t in tickets if t.sprint == args.sprint]
    payload = [ticket_brief(t) for t in tickets]
    if args.as_json:
        emit(args, "", payload)
        return 0
    if not tickets:
        print("no tickets")
        return 0
    for t in tickets:
        holder = t.holder() or "-"
        print(f"{t.id:8} {t.status:10} {t.priority:4} {holder:12} {t.title}")
    return 0


def cmd_show(board: Board, agent: str, args: argparse.Namespace) -> int:
    ticket = board.load(args.ticket)
    if args.as_json:
        emit(args, "", ticket.to_dict())
        return 0
    print(f"{ticket.id}  {ticket.status}  {ticket.priority}  {ticket.title}")
    print(f"assignee: {ticket.holder() or '-'}")
    if ticket.epic:
        print(f"epic: {ticket.epic}")
    if ticket.sprint:
        print(f"sprint: {ticket.sprint}")
    if ticket.deps:
        print(f"deps: {', '.join(ticket.deps)}")
    if ticket.body:
        print()
        print(ticket.body)
    if ticket.notes:
        print()
        print("notes:")
        for note in ticket.notes:
            print(f"  [{note.get('at')}] {note.get('by')} ({note.get('kind')}): {note.get('text')}")
    return 0


def cmd_next(board: Board, agent: str, args: argparse.Namespace) -> int:
    ticket = board.next_open(agent=agent)
    if ticket is None:
        emit(args, "no unblocked open ticket", {"ticket": None})
        return 1
    emit(args, f"{ticket.id}  {ticket.priority}  {ticket.title}", ticket.to_dict())
    return 0


def cmd_dep(board: Board, agent: str, args: argparse.Namespace) -> int:
    ticket = board.load(args.ticket)
    if args.dep_cmd == "list":
        emit(args, ", ".join(ticket.deps) or "(none)", {"ticket": ticket.id, "deps": ticket.deps})
        return 0
    dep = normalize_ticket_id(args.dependency)
    board.load(dep)  # must exist
    if args.dep_cmd == "add":
        if dep == ticket.id:
            raise CliError("a ticket cannot depend on itself")
        if dep not in ticket.deps:
            ticket.deps.append(dep)
            ticket.add_note(agent, f"depends on {dep}", kind="dep")
            board.save(ticket)
        emit(args, f"{ticket.id} depends on {dep}", ticket.to_dict())
        return 0
    if dep in ticket.deps:
        ticket.deps.remove(dep)
        ticket.add_note(agent, f"removed dependency {dep}", kind="dep")
        board.save(ticket)
    emit(args, f"{ticket.id} no longer depends on {dep}", ticket.to_dict())
    return 0


def _meta_list(board: Board, key: str) -> list:
    meta = board.meta()
    names = list(meta.get(key) or [])
    for t in board.all_tickets():
        value = getattr(t, key[:-1] if key.endswith("s") else key, None)
        # epic / sprint fields
    for t in board.all_tickets():
        value = t.epic if key == "epics" else t.sprint
        if value and value not in names:
            names.append(value)
    return names


def cmd_epic(board: Board, agent: str, args: argparse.Namespace) -> int:
    return _group_cmd(board, agent, args, group="epic", cmd_attr="epic_cmd")


def cmd_sprint(board: Board, agent: str, args: argparse.Namespace) -> int:
    return _group_cmd(board, agent, args, group="sprint", cmd_attr="sprint_cmd")


def _group_cmd(board: Board, agent: str, args: argparse.Namespace, *, group: str, cmd_attr: str) -> int:
    action = getattr(args, cmd_attr)
    meta_key = "epics" if group == "epic" else "sprints"
    if action == "list":
        names = _meta_list(board, meta_key)
        emit(args, "\n".join(names) if names else f"no {group}s", {meta_key: names})
        return 0
    if action == "create":
        meta = board.meta()
        if args.name not in meta[meta_key]:
            meta[meta_key].append(args.name)
            board.save_meta(meta)
        emit(args, f"{group} {args.name}", {group: args.name})
        return 0
    ticket = board.load(args.ticket)
    setattr(ticket, group, args.name)
    ticket.add_note(agent, f"{group}={args.name}", kind=group)
    board.save(ticket)
    meta = board.meta()
    if args.name not in meta[meta_key]:
        meta[meta_key].append(args.name)
        board.save_meta(meta)
    emit(args, f"{ticket.id} {group}={args.name}", ticket.to_dict())
    return 0


def cmd_join(board: Board, agent: str, args: argparse.Namespace) -> int:
    ticket = board.load(args.ticket)
    if agent not in ticket.collaborators:
        ticket.collaborators.append(agent)
        ticket.add_note(agent, f"{agent} joined", kind="join")
        board.save(ticket)
    emit(args, f"{agent} joined {ticket.id}", ticket.to_dict())
    return 0


def cmd_clear(board: Board, agent: str, args: argparse.Namespace) -> int:
    """Unclaim only. Refuses every wipe-shaped invocation."""
    if args.all or args.wipe or args.board_reset:
        raise CliError(
            "tickets clear does not delete tickets. "
            "To unclaim, omit --all/--wipe. "
            f"Nuclear wipe is: tickets board-reset --i-understand-destroy-all-tickets --confirm {DESTROY_CONFIRM}",
            code=2,
        )
    if args.ticket:
        ticket = board.load(args.ticket)
        holder = ticket.holder()
        if holder and holder != agent and not args.force:
            raise CliError(f"{ticket.id} is held by {holder} (pass --force to unclaim anyway)")
        before_notes = len(ticket.notes)
        ticket.unclaim(agent)
        board.save(ticket)
        write_presence(board.root, agent, ticket_id=None)
        emit(
            args,
            f"unclaimed {ticket.id} (notes preserved: {before_notes + 1})",
            ticket.to_dict(),
        )
        return 0

    held = board.held_by(agent)
    if not held:
        emit(args, f"{agent} holds no ticket; nothing cleared", {"unclaimed": [], "agent": agent})
        return 0
    ids = []
    payloads = []
    for ticket in held:
        ticket.unclaim(agent)
        board.save(ticket)
        ids.append(ticket.id)
        payloads.append(ticket.to_dict())
    write_presence(board.root, agent, ticket_id=None)
    emit(args, f"unclaimed {', '.join(ids)}", {"unclaimed": ids, "tickets": payloads})
    return 0


def cmd_board_reset(board: Board, agent: str, args: argparse.Namespace) -> int:
    if not args.i_understand_destroy_all_tickets:
        raise CliError(
            "refusing to wipe the board. "
            f"Required: tickets board-reset --i-understand-destroy-all-tickets --confirm {DESTROY_CONFIRM}"
        )
    if args.confirm != DESTROY_CONFIRM:
        raise CliError(f"--confirm must be exactly {DESTROY_CONFIRM}")
    files = board.ticket_files()
    if not files:
        emit(args, "board already empty", {"deleted": []})
        return 0
    archive = backup_board(board.root, reason="board-reset")
    deleted = []
    for path in files:
        path.unlink()
        deleted.append(path.name)
    emit(
        args,
        f"destroyed {len(deleted)} tickets; backup {archive}",
        {"deleted": deleted, "backup": str(archive)},
    )
    return 0


def cmd_board_backup(board: Board, agent: str, args: argparse.Namespace) -> int:
    dest = Path(args.dest).expanduser() if args.dest else None
    archive = backup_board(board.root, dest, reason=args.reason)
    emit(args, f"backup {archive}", {"archive": str(archive)})
    return 0


def cmd_board_restore(board: Board, agent: str, args: argparse.Namespace) -> int:
    archive = Path(args.archive)
    pre = backup_board(board.root, reason="pre-restore")
    manifest = restore_board(board.root, archive, replace=not args.keep_extra)
    emit(
        args,
        f"restored {archive} (pre-restore backup {pre})",
        {"archive": str(archive.resolve()), "pre_restore": str(pre), "manifest": manifest},
    )
    return 0


COMMANDS = {
    "create": cmd_create,
    "assign": cmd_assign,
    "claim": cmd_claim,
    "done": cmd_done,
    "review": cmd_review,
    "note": cmd_note,
    "msg": cmd_msg,
    "who": cmd_who,
    "pulse": cmd_pulse,
    "here": cmd_here,
    "update": cmd_update,
    "list": cmd_list,
    "show": cmd_show,
    "next": cmd_next,
    "dep": cmd_dep,
    "epic": cmd_epic,
    "sprint": cmd_sprint,
    "join": cmd_join,
    "clear": cmd_clear,
    "board-reset": cmd_board_reset,
    "board-backup": cmd_board_backup,
    "board-restore": cmd_board_restore,
}


def dispatch(args: argparse.Namespace) -> int:
    create = args.cmd in {"create", "here", "board-backup", "board-restore", "board-reset"}
    root = resolve_board(args.board, create=create)
    if args.cmd not in {"board-backup"} and not root.exists() and args.cmd not in {"create", "here", "board-restore"}:
        # Read-only commands on a missing board: create the path only for writers.
        if args.cmd in {"list", "pulse", "who", "next", "show"}:
            raise CliError(f"no board at {root} (set --board or TICKETS_BOARD, or run tickets create / tickets here)")
    board = Board(root)
    agent = resolve_agent(args.as_agent, board=root)
    handler = COMMANDS[args.cmd]
    with BoardLock(root):
        return handler(board, agent, args)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(list(argv) if argv is not None else None)
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else 2
    try:
        return dispatch(args)
    except (CliError, BoardError, BackupError, ValueError) as exc:
        message = str(exc)
        code = getattr(exc, "code", 2)
        # Never leave a partial wipe without a message.
        print(f"error: {message}", file=sys.stderr)
        return code


if __name__ == "__main__":
    raise SystemExit(main())
