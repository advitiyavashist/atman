"""Persistent roles and recovery context for the existing tickets CLI."""

import fcntl
import json
import os
import re
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path


def stamp():
    return datetime.now(timezone.utc).isoformat()


def safe(value):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,100}", value):
        raise ValueError("IDs must be 1–101 letters, digits, dots, underscores or hyphens")
    return value


def transact(board, operation):
    directory = Path(board) / "coordination"
    directory.mkdir(exist_ok=True)
    with (directory / "state.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = directory / "state.json"
        state = (
            json.loads(path.read_text())
            if path.exists()
            else {"schema": 1, "agents": {}, "roles": {}, "handovers": [], "history": []}
        )
        result = operation(state)
        fd, temporary = tempfile.mkstemp(dir=directory, prefix="state-")
        try:
            with os.fdopen(fd, "w") as output:
                json.dump(state, output, indent=2)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return result


def take(state, role, actor, expected, reason, context):
    safe(role)
    safe(actor)
    old = state["roles"].get(role, {})
    holder = old.get("holder")
    if holder == actor:
        return old
    if holder != (None if expected == "none" else expected):
        raise ValueError(
            f"Role holder changed: expected {expected}, found {holder}; reread role first"
        )
    inherited = old.get("context", "")
    if not context.strip():
        # A role that already carries context keeps it. Requiring a fresh
        # context on every takeover meant the first agent to claim a seeded
        # role DESTROYED the description of the role it was claiming -- the
        # opposite of what the requirement is for. A reason is still mandatory,
        # so a takeover is still justified; only the re-typing is optional.
        context = inherited
    if not reason.strip() or not context.strip():
        raise ValueError("Takeover requires a reason, and a context for a role that has none")
    record = {"role": role, "holder": actor, "since": stamp(), "context": context}
    state["roles"][role] = record
    state["history"].append(record | {"previous_holder": holder, "reason": reason})
    return record


def register(sub, api):
    identity = sub.add_parser("identity", help="persist and show this agent's unique identity")
    identity.set_defaults(fn=lambda args, board: run("identity", args, board, api))
    role = sub.add_parser("role", help="durable role: list, show, take (explicit expected holder)")
    role.add_argument("operation", choices=["list", "show", "take"])
    role.add_argument("role_id", nargs="?")
    role.add_argument("--expected-holder", default="none")
    role.add_argument("--reason", default="")
    role.add_argument("--context", default="")
    role.set_defaults(fn=lambda args, board: run("role", args, board, api))
    handover = sub.add_parser("handover", help="save recovery context and publish a ticket update")
    handover.add_argument("ticket")
    handover.add_argument(
        "--file", required=True, help="Markdown with status, checks, blockers and next step"
    )
    handover.set_defaults(fn=lambda args, board: run("handover", args, board, api))
    pulse = sub.add_parser("pulse", help="show heartbeat and progress-update deadlines")
    pulse.set_defaults(fn=lambda args, board: run("pulse", args, board, api))


def run(command, args, board, api):
    try:
        actor = safe(os.environ.get("TICKET_AGENT", ""))
        if command == "identity":

            def identify(state):
                agent = state["agents"].setdefault(
                    actor,
                    {"agent_id": actor, "instance_id": str(uuid.uuid4()), "created_at": stamp()},
                )
                agent.update(api["git_state"]() or {})
                agent["seen"] = stamp()
                return agent

            result = transact(board, identify)
        elif command == "role":

            def roles(state):
                if args.operation == "list":
                    return state["roles"]
                if not args.role_id:
                    raise ValueError("role ID required")
                safe(args.role_id)
                if args.operation == "show":
                    return state["roles"].get(args.role_id, {})
                return take(
                    state, args.role_id, actor, args.expected_holder, args.reason, args.context
                )

            result = transact(board, roles)
        elif command == "handover":
            if not re.fullmatch(r"T-\d+", args.ticket):
                raise ValueError("Expected ticket ID T-123")
            ticket = json.loads((Path(board) / (args.ticket + ".json")).read_text())
            if ticket.get("owner") != actor:
                raise ValueError("Only the current ticket owner may publish its handover")
            document = Path(args.file).read_text()
            if not document.strip() or len(document.encode()) > 65536:
                raise ValueError("Handover must contain 1–65536 bytes")
            record = {
                "id": str(uuid.uuid4()),
                "ticket": args.ticket,
                "agent": actor,
                "at": stamp(),
                "document": document,
                "source": str(Path(args.file).resolve()),
                "git": api["git_state"](),
            }
            transact(board, lambda state: state["handovers"].append(record))
            api["post_message"](
                board,
                actor,
                f"Handover saved for {args.ticket}: {record['id']}. Read with tickets pulse; context source {record['source']}",
                re=args.ticket,
            )
            result = record
        else:
            agents = {
                p.stem: json.loads(p.read_text()) for p in (Path(board) / "agents").glob("*.json")
            }
            active = []
            now = datetime.now(timezone.utc)

            def minutes(value):
                try:
                    return round(
                        (now - datetime.fromisoformat(value.replace("Z", "+00:00"))).total_seconds()
                        / 60,
                        1,
                    )
                except (ValueError, TypeError, AttributeError):
                    return None

            for path in sorted(Path(board).glob("T-*.json")):
                ticket = json.loads(path.read_text())
                if ticket.get("status") != "claimed":
                    continue
                owner = ticket.get("owner")
                notes = [n.get("at") for n in ticket.get("notes", []) if n.get("by") == owner]
                progress = minutes(max(notes) if notes else ticket.get("claimed_at"))
                heartbeat = minutes(agents.get(owner, {}).get("seen"))
                active.append(
                    {
                        "ticket": ticket["id"],
                        "owner": owner,
                        "heartbeat_minutes": heartbeat,
                        "heartbeat_due": heartbeat is None or heartbeat >= 15,
                        "progress_minutes": progress,
                        "update_due": progress is None or progress >= 45,
                    }
                )
            result = transact(
                board,
                lambda state: {
                    "roles": state["roles"],
                    "active": active,
                    "handovers": state["handovers"][-20:],
                    "cadence": "tickets here every 15m; tickets update every 45m and milestones",
                },
            )
        print(json.dumps(result, indent=2))
    except (ValueError, OSError, KeyError) as exc:
        raise SystemExit(str(exc)) from exc
