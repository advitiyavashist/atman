"""Persistent roles and recovery context for the existing tickets CLI."""

import fcntl
import json
import os
import re
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path


HANDOFF_FIELDS = (
    "objective_id",
    "completion_criteria",
    "work_completed",
    "decisions",
    "open_questions",
    "artifacts",
    "worktree",
    "checks",
    "next_action",
    "facts",
    "assumptions",
    "context_budget",
    "missing_artifacts",
)

HANDOFF_ALIASES = {
    "objective": "objective_id",
    "objective id": "objective_id",
    "objective_id": "objective_id",
    "completion criteria": "completion_criteria",
    "completion_criteria": "completion_criteria",
    "work completed": "work_completed",
    "work_completed": "work_completed",
    "decisions": "decisions",
    "open questions": "open_questions",
    "open_questions": "open_questions",
    "artifacts": "artifacts",
    "artifact": "artifacts",
    "worktree": "worktree",
    "worktrees": "worktree",
    "checks": "checks",
    "check results": "checks",
    "last verified": "checks",
    "next action": "next_action",
    "proposed next action": "next_action",
    "next_action": "next_action",
    "facts": "facts",
    "observed facts": "facts",
    "assumptions": "assumptions",
    "context budget": "context_budget",
    "context_budget": "context_budget",
    "missing artifacts": "missing_artifacts",
    "missing_artifacts": "missing_artifacts",
}

DEFAULT_CONTEXT_BUDGET = 4000


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
    if not reason.strip() or not context.strip():
        raise ValueError("Takeover requires a reason and context document path")
    record = {"role": role, "holder": actor, "since": stamp(), "context": context}
    state["roles"][role] = record
    state["history"].append(record | {"previous_holder": holder, "reason": reason})
    return record


def _normalize_heading(raw):
    key = re.sub(r"[^a-z0-9]+", " ", (raw or "").strip().lower()).strip()
    return HANDOFF_ALIASES.get(key) or HANDOFF_ALIASES.get(key.replace(" ", "_"))


def parse_handoff_document(text):
    """Turn a handover markdown file (or JSON object) into structured fields.

    Observed facts and the previous worker's assumptions are stored separately.
    Missing headings become empty strings and are listed in missing_fields.
    """
    text = (text or "").strip()
    fields = {name: "" for name in HANDOFF_FIELDS}
    if text.startswith("{"):
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("Handover JSON must be an object")
        for name in HANDOFF_FIELDS:
            value = data.get(name, "")
            fields[name] = value if isinstance(value, str) else json.dumps(value)
    else:
        current = None
        buf = []

        def flush():
            if current:
                fields[current] = "\n".join(buf).strip()

        for line in text.splitlines():
            match = re.match(r"^#{1,3}\s+(.+?)\s*$", line)
            if match:
                flush()
                current = _normalize_heading(match.group(1))
                buf = []
            elif current:
                buf.append(line)
        flush()
    missing_fields = [name for name in HANDOFF_FIELDS if not str(fields.get(name) or "").strip()]
    fields["missing_fields"] = missing_fields
    return fields


def _split_items(value):
    items = []
    for line in str(value or "").replace(",", "\n").splitlines():
        item = line.strip().lstrip("-").strip()
        if item and item.lower() not in ("none", "n/a", "-"):
            items.append(item)
    return items


def flag_missing_artifacts(fields, cwd=None):
    """Mark artifact paths that are absent on disk. Does not invent contents."""
    named = _split_items(fields.get("missing_artifacts"))
    flagged = list(named)
    root = Path(fields.get("worktree") or cwd or ".")
    for artifact in _split_items(fields.get("artifacts")):
        path = Path(artifact)
        if not path.is_absolute():
            path = root / path
        if not path.exists() and artifact not in flagged:
            flagged.append(artifact)
    return flagged


def apply_context_budget(document, fields):
    raw = str(fields.get("context_budget") or "").strip()
    try:
        budget = int(raw) if raw else DEFAULT_CONTEXT_BUDGET
    except ValueError:
        budget = DEFAULT_CONTEXT_BUDGET
    if budget < 256:
        budget = 256
    encoded = document.encode("utf-8", "replace")
    truncated = len(encoded) > budget
    stored = encoded[:budget].decode("utf-8", "replace") if truncated else document
    return {
        "context_budget": budget,
        "document_bytes": len(encoded),
        "truncated": truncated,
        "document": stored,
    }


def build_handoff_record(ticket_id, actor, document, parsed, *, source="", git=None,
                         objective_id="", harness="", cwd=None):
    fields = dict(parsed)
    if objective_id and not fields.get("objective_id"):
        fields["objective_id"] = objective_id
        if "objective_id" in fields.get("missing_fields", []):
            fields["missing_fields"] = [
                name for name in fields["missing_fields"] if name != "objective_id"
            ]
    budget = apply_context_budget(document, fields)
    missing_artifacts = flag_missing_artifacts(fields, cwd=cwd)
    return {
        "id": str(uuid.uuid4()),
        "ticket": ticket_id,
        "objective_id": fields.get("objective_id") or "",
        "agent": actor,
        "harness": harness or "",
        "at": stamp(),
        "source": source,
        "git": git,
        "completion_criteria": fields.get("completion_criteria") or "",
        "work_completed": fields.get("work_completed") or "",
        "decisions": fields.get("decisions") or "",
        "open_questions": fields.get("open_questions") or "",
        "artifacts": _split_items(fields.get("artifacts")),
        "worktree": fields.get("worktree") or "",
        "checks": fields.get("checks") or "",
        "next_action": fields.get("next_action") or "",
        "facts": fields.get("facts") or "",
        "assumptions": fields.get("assumptions") or "",
        "missing_fields": fields.get("missing_fields") or [],
        "missing_artifacts": missing_artifacts,
        "missing_artifacts_flagged": bool(missing_artifacts),
        **budget,
    }


def compact_handoff(record):
    """Ticket-visible slice: enough for INTERRUPTED recovery without the raw blob."""
    keys = (
        "id", "ticket", "objective_id", "agent", "harness", "at",
        "completion_criteria", "work_completed", "decisions", "open_questions",
        "artifacts", "worktree", "checks", "next_action", "facts", "assumptions",
        "missing_fields", "missing_artifacts", "missing_artifacts_flagged",
        "context_budget", "truncated",
    )
    return {key: record.get(key) for key in keys}


def write_ticket_handoff(board, ticket_id, record, actor=None):
    path = Path(board) / (ticket_id + ".json")
    ticket = json.loads(path.read_text())
    if actor is not None and ticket.get("owner") != actor:
        raise ValueError("ownership changed before handover could be saved")
    ticket["handoff"] = compact_handoff(record)
    ticket["updated"] = stamp()
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=ticket_id + "-")
    try:
        with os.fdopen(fd, "w") as output:
            json.dump(ticket, output, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return ticket


def owner_generation(ticket):
    try:
        return int((ticket or {}).get("owner_generation") or 0)
    except (TypeError, ValueError):
        return 0


def revoked_owners(ticket):
    """Owners fenced after one or more transfers. Survives A->B->C (not only last previous)."""
    names = []
    lease = (ticket or {}).get("owner_lease") if isinstance((ticket or {}).get("owner_lease"), dict) else {}
    for raw in (
        list((ticket or {}).get("revoked_owners") or []),
        list((lease or {}).get("revoked_owners") or []),
        [((lease or {}).get("previous_owner") or "")],
    ):
        for name in raw:
            if name and name not in names:
                names.append(name)
    return names


def _accumulate_revoked(ticket, owner, previous_owner=""):
    names = revoked_owners(ticket)
    prev = previous_owner or ""
    if prev and prev != (owner or "") and prev not in names:
        names.append(prev)
    return names


_TICKET_LOCKS = threading.local()


class TicketMutationLock:
    """Serialize compare-and-publish of one ticket JSON.

    Lock order with assign/claim: AgentLock (agents/<owner>.json.lock;
    two owners → sorted names) then this lock (<id>.json.lock). save()
    never takes AgentLock. Same-process reentry is allowed so a holder
    that already serialized the ticket can call save() without deadlocking.
    """

    def __init__(self, board, ticket_id):
        self.path = os.path.join(board, ticket_id + ".json.lock")
        self.key = (os.path.abspath(board), ticket_id)
        self.fd = None
        self.reentered = False

    def __enter__(self):
        held = getattr(_TICKET_LOCKS, "keys", None)
        if held is None:
            held = {}
            _TICKET_LOCKS.keys = held
        if self.key in held:
            held[self.key] += 1
            self.reentered = True
            return self
        try:
            import fcntl as flock_mod
        except ImportError:
            held[self.key] = 1
            return self
        self.fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o644)
        flock_mod.flock(self.fd, flock_mod.LOCK_EX)
        held[self.key] = 1
        return self

    def __exit__(self, *exc):
        held = getattr(_TICKET_LOCKS, "keys", None) or {}
        if self.reentered:
            if self.key in held:
                held[self.key] -= 1
                if held[self.key] <= 0:
                    held.pop(self.key, None)
            return
        if self.fd is not None:
            try:
                import fcntl as flock_mod
                flock_mod.flock(self.fd, flock_mod.LOCK_UN)
            finally:
                os.close(self.fd)
                self.fd = None
        held.pop(self.key, None)


def ticket_mutation_lock(board, ticket_id):
    return TicketMutationLock(board, ticket_id)


def generation_publish_error(current, incoming, expected_generation=None):
    """Refuse publishing a write that lost a concurrent generation bump."""
    err = stale_write_error(current, incoming)
    if err:
        return err
    if expected_generation is not None and owner_generation(current) != int(expected_generation):
        return (
            "%s stale ownership generation %s (board is %s); reread before writing"
            % (incoming.get("id") or current.get("id"), expected_generation, owner_generation(current))
        )
    return None


def issue_owner_lease(ticket, owner, *, harness="", reason="claim", previous_owner=""):
    """Advance the ticket's ownership generation. Reuses the ticket JSON; no database."""
    previous_generation = owner_generation(ticket)
    generation = previous_generation + 1
    revoked = _accumulate_revoked(ticket, owner, previous_owner=previous_owner)
    ticket["owner_generation"] = generation
    ticket["revoked_owners"] = revoked
    ticket["owner_lease"] = {
        "generation": generation,
        "owner": owner or "",
        "harness": harness or "",
        "issued_at": stamp(),
        "reason": reason,
        "previous_owner": previous_owner or "",
        "previous_generation": previous_generation,
        "revoked_owners": revoked,
    }
    return generation


def rewrite_claim_lock(board, ticket_id, owner):
    """Point the existing claim lock at the new owner so a stale process is not 'labelled' owner."""
    lock = os.path.join(board, ticket_id + ".lock")
    if not os.path.exists(lock):
        return False
    fd, temporary = tempfile.mkstemp(dir=board, prefix=ticket_id + "-lock-")
    try:
        with os.fdopen(fd, "w") as output:
            output.write(owner or "")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, lock)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return True


def stale_accept_error(ticket, actor, *, kind):
    """Refuse a fenced previous worker accepting results after a lease bump.

    `done` of an IN REVIEW ticket stays legal for the closer (master merge).
    `done` of claimed work requires the current owner.
    `review` still allows a helper submitter (T-238) unless that helper is the
    previous owner of the current lease — that is the stale-worker case.
    A presented TICKET_OWNER_GENERATION that does not match the board is stale.
    """
    owner = (ticket or {}).get("owner") or ""
    generation = owner_generation(ticket)
    lease = (ticket or {}).get("owner_lease") or {}
    previous = lease.get("previous_owner") or ""
    revoked = revoked_owners(ticket)
    presented = os.environ.get("TICKET_OWNER_GENERATION", "").strip()
    if presented:
        try:
            presented_gen = int(presented)
        except ValueError:
            presented_gen = None
        if presented_gen is not None and presented_gen != generation:
            return (
                "%s stale ownership generation %s (board is %s); "
                "reread before accepting results"
                % (ticket.get("id"), presented_gen, generation)
            )
    if kind == "done" and (ticket or {}).get("status") == "review":
        return None
    if kind == "done" and owner and actor and owner != actor:
        return (
            "%s is owned by %s under generation %s; %s cannot %s "
            "(stale ownership after restart or reassignment)"
            % (ticket.get("id"), owner, generation, actor, kind)
        )
    if kind == "review" and actor and owner != actor and (
        actor == previous or actor in revoked
    ):
        return (
            "%s is owned by %s under generation %s; %s cannot %s "
            "(stale ownership after restart or reassignment)"
            % (ticket.get("id"), owner, generation, actor, kind)
        )
    return None


def stale_write_error(current, incoming):
    """Compare-and-swap: a write loaded before a lease bump must not land."""
    current_gen = owner_generation(current)
    incoming_gen = owner_generation(incoming)
    if current_gen and incoming_gen < current_gen:
        return (
            "%s stale ownership generation %s (board is %s); reread before writing"
            % (incoming.get("id") or current.get("id"), incoming_gen, current_gen)
        )
    return None


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
            if args.operation == "take":
                warn = api.get("warn_leadership_offline") if isinstance(api, dict) else None
                if callable(warn):
                    warn(board, actor, args.role_id)
        elif command == "handover":
            if not re.fullmatch(r"T-\d+", args.ticket):
                raise ValueError("Expected ticket ID T-123")
            ticket = json.loads((Path(board) / (args.ticket + ".json")).read_text())
            if ticket.get("owner") != actor:
                raise ValueError("Only the current ticket owner may publish its handover")
            document = Path(args.file).read_text()
            if not document.strip() or len(document.encode()) > 65536:
                raise ValueError("Handover must contain 1–65536 bytes")
            parsed = parse_handoff_document(document)
            objective_id = ""
            oid_fn = api.get("_objective_id") if isinstance(api, dict) else None
            if callable(oid_fn):
                objective_id = oid_fn(board) or ""
            if not parsed.get("completion_criteria"):
                load_obj = api.get("load_objective") if isinstance(api, dict) else None
                if callable(load_obj):
                    obj = load_obj(board) or {}
                    parsed["completion_criteria"] = (obj.get("exit_criterion") or "") if isinstance(obj, dict) else ""
                    if parsed["completion_criteria"] and "completion_criteria" in parsed.get("missing_fields", []):
                        parsed["missing_fields"] = [
                            name for name in parsed["missing_fields"] if name != "completion_criteria"
                        ]
            harness = ""
            harness_fn = api.get("_agent_harness") if isinstance(api, dict) else None
            if callable(harness_fn):
                try:
                    harness = harness_fn(board, actor)[0]
                except (TypeError, IndexError, KeyError):
                    harness = ""
            record = build_handoff_record(
                args.ticket,
                actor,
                document,
                parsed,
                source=str(Path(args.file).resolve()),
                git=api["git_state"](),
                objective_id=objective_id,
                harness=harness,
                cwd=os.getcwd(),
            )
            transact(board, lambda state: state["handovers"].append(record))
            write_ticket_handoff(board, args.ticket, record, actor=actor)
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
                lease = ticket.get("owner_lease") or {}
                row = {
                    "ticket": ticket["id"],
                    "owner": owner,
                    "owner_generation": owner_generation(ticket),
                    "owner_harness": lease.get("harness") or "",
                    "heartbeat_minutes": heartbeat,
                    "heartbeat_due": heartbeat is None or heartbeat >= 15,
                    "progress_minutes": progress,
                    "update_due": progress is None or progress >= 45,
                }
                if ticket.get("handoff"):
                    row["handoff"] = ticket["handoff"]
                active.append(row)
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
