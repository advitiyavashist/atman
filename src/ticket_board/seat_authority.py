"""T-022: authority comes from the live seat tree, not from what a process says.

`whoami()` is the environment: TICKET_SEAT, then TICKET_AGENT. Any process
can set either, so a worker that runs `TICKET_AGENT=atman-ceo atm accept`
used to record an accept as the CEO -- the "seats posting as CEO" bug.
Message provenance already marks such posts `[unverified:TICKET_AGENT]`,
but a mark changes no gate.

The one thing a process cannot rewrite is its own ancestry. A supervised
seat is a harness launched by `tickets.py watch --agent <seat>` (from
`atm spawn` or the per-user seat service), so every command that harness
runs has that watcher above it in the process tree. That gives the rule:

  * inside a seat tree (a watcher for THIS board is an ancestor), the seat
    named by the NEAREST such watcher is who you are. Acting as any other
    name is refused;
  * a watcher whose own ancestry holds another seat's watcher was started
    from inside that seat. A seat cannot mint a seat, so that is refused too;
  * outside every seat tree (the operator's own terminal, an attached
    harness, CI) nothing here changes: that caller is not a supervised seat
    and this module has no tree to judge it by.

Roster files, history records, message text, tool output and repo files are
never consulted -- only the live process table and the board's own watch pid
claims, which `tickets.py` resolves before calling `decide`.

Limit, stated plainly: every seat runs as the same OS user. A seat that
double-forks a detached `watch --agent atman-ceo` (parent pid 1) produces a
tree this module cannot tell from the service's. Closing that needs a
launcher-held secret the seat cannot read, which is out of scope here.

`seat_chain`/`decide`/`mismatch` are pure. `process_ancestors` and
`board_chain` read the process table; `tickets.py` passes its stricter
watch-loop matcher and pid-reuse fence, `cli.py` uses the defaults here.
"""
from __future__ import annotations

import os
import shlex
import subprocess

AUTHORITY_VERBS = ("accept", "reject", "assign")


def seat_chain(ancestors, agent_of, on_board):
    """Seat names of THIS board's watchers above the caller, nearest first.

    `ancestors` is [(pid, command), ...] from the caller's parent upward.
    `agent_of(command)` returns the `--agent` of a watch loop or "".
    `on_board(pid, command, agent)` says whether that loop serves this board;
    a watcher for another board (a scratch test board, another repo) is not
    this board's seat and is skipped.
    """
    out = []
    for pid, cmd in ancestors:
        agent = agent_of(cmd) or ""
        if agent and on_board(pid, cmd, agent):
            out.append(agent)
    return out


_SCRIPT_NAMES = ("tickets.py", "tickets", "atm", "cli.py")


def _argv(cmd):
    try:
        return shlex.split(cmd)
    except ValueError:
        return cmd.split()


def _flag(argv, name):
    for i, tok in enumerate(argv):
        if tok == name and i + 1 < len(argv):
            return argv[i + 1]
        if tok.startswith(name + "="):
            return tok[len(name) + 1:]
    return ""


def watch_agent(cmd):
    """--agent of a `<tickets script> watch` command line, else ''."""
    argv = _argv(cmd)
    for i, tok in enumerate(argv[:-1]):
        if os.path.basename(tok) in _SCRIPT_NAMES and argv[i + 1] == "watch":
            return _flag(argv[i + 2:], "--agent")
    return ""


def process_ancestors(start=None, limit=64):
    """[(pid, command), ...] from `start` (default: our parent) up to init.

    One `ps` per hop; only the authority verbs and message provenance ask. An
    unreadable hop ends the walk: missing evidence leaves the caller outside
    every seat tree, which changes nothing.
    """
    pid = int(start or os.getppid())
    out, seen = [], set()
    while pid > 1 and pid not in seen and len(out) < limit:
        seen.add(pid)
        try:
            r = subprocess.run(["ps", "-ww", "-o", "ppid=", "-o", "command=", "-p", str(pid)],
                               capture_output=True, text=True)
        except OSError:
            break
        line = (r.stdout or "").strip()
        if r.returncode != 0 or not line:
            break
        head, _, cmd = line.partition(" ")
        out.append((pid, cmd.strip()))
        try:
            pid = int(head)
        except ValueError:
            break
    return out


def _under(path, repo):
    if not path:
        return False
    real = os.path.realpath(path)
    return real == repo or real.startswith(repo + os.sep)


def _pid_file_names(board, agent, pid):
    try:
        with open(os.path.join(board, "agents", agent + ".watch.pid")) as f:
            return int((f.read() or "0").strip() or 0) == int(pid)
    except (OSError, ValueError):
        return False


def board_chain(board, agent_of=watch_agent, claimed=_pid_file_names, ancestors=None):
    """This board's seat watchers above the caller, nearest first.

    A watcher serves this board when its --cwd lies under the board's repo or
    the board's agents/<seat>.watch.pid names it (Steer board + Atman
    worktree), the same evidence `atm who` uses for a live loop.
    """
    repo = os.path.realpath(os.path.dirname(os.path.abspath(board)))

    def on_board(pid, cmd, agent):
        return _under(_flag(_argv(cmd), "--cwd"), repo) or claimed(board, agent, pid)

    rows = process_ancestors() if ancestors is None else ancestors
    return seat_chain(rows, agent_of, on_board)


def mismatch(sender, chain):
    """The seat a supervised process really is, when it posts as someone else."""
    return chain[0] if chain and sender and sender != chain[0] else ""


def decide(actor, chain, verb="accept"):
    """(ok, how, reason) for `actor` acting with `verb` given `chain`.

    `how` is the provenance recorded with the act: "seat-tree:<seat>" or
    "outside-seat-tree". `reason` is the refusal text when not ok.
    """
    if not chain:
        return True, "outside-seat-tree", ""
    seat = chain[0]
    minted_by = next((s for s in chain[1:] if s != seat), "")
    if minted_by:
        return False, "seat-tree:%s" % seat, (
            "refused: %s -- the watcher for %s runs inside seat %s's process tree. "
            "A seat cannot start another seat; authority comes from the live seat "
            "tree (atm spawn / the seat service), not from a watcher a seat launched."
            % (verb, seat, minted_by))
    if actor != seat:
        return False, "seat-tree:%s" % seat, (
            "refused: %s as %s -- this process runs inside seat %s's watcher, so it "
            "acts as %s. TICKET_AGENT/TICKET_SEAT do not change who a supervised seat "
            "is. Ask %s (atm msg --to %s) to do it, or have the operator run it from "
            "their own terminal."
            % (verb, actor, seat, seat, actor, actor))
    return True, "seat-tree:%s" % seat, ""
