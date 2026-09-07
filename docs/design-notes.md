# Design notes

Why the board is built the way it is. Moved out of `README.md` (T-322) so the
front page stays about getting a new user to a first claimed ticket.

## No daemon, no database, no network

The board is a directory of plain files. It works over any shared filesystem,
survives every process on the machine dying, and can be read, diffed and
committed with the tools you already have. There is nothing to start before an
agent can work, and nothing to migrate when the schema moves.

The cost is that there are no transactions across files. Everything below is
about getting the guarantees that matter without them.

## Atomicity comes from the filesystem

- **Claims and id allocation**: `O_EXCL`. Two agents calling `tickets next` at
  the same instant cannot receive the same ticket — the loser sees the lock and
  retries against the next candidate. This is the single most important
  property in the tool: duplicated work is the most expensive failure a swarm
  has.
- **Messages**: `O_APPEND` to `messages.jsonl`. Concurrent writers interleave
  whole lines, never partial ones.
- **Updates**: write a temp file, then `rename`. A reader sees the old file or
  the new one, never a half-written one.
- **Agent records**: a blocking `flock`, because heartbeats and `tickets limit`
  write the same record from different processes and a lost update there looks
  exactly like an agent that never reported.

## One file per ticket

The obvious design is one `tickets.json`. It is also the design where two agents
updating two different tickets lose one of the writes. One file per ticket makes
the common case — different agents touching different tickets — genuinely
independent, and makes a merge conflict on the board a real signal rather than
noise.

## Board resolution is a guard, not a convenience

Deciding which board a command talks to is the resolution path every single
command runs, so it is the highest-consequence code in the tool. `tickets init`
in particular must create **and bind** a board or refuse loudly: reporting
success while installing the protocol files into a different project is how a
test suite once minted a ticket on a live board. The rules, the refusal and the
escape hatch are in [board-resolution.md](board-resolution.md).

## The process rules are deliberately opinionated

One ticket at a time. Updates every 45 minutes. Review before done. Your own
worktree. `review` and `done` refuse from `main` or with uncommitted files.

These are not style preferences. Each one prevents a failure that costs hours:

| rule | failure it prevents |
| --- | --- |
| one ticket at a time | an agent holding work nobody else can see while it does something else |
| update every 45 min | silence being indistinguishable from a dead process |
| review before done | an author being the only witness to their own work |
| own worktree | two agents editing one checkout |
| refuse from `main` | work that cannot be reviewed or reverted as a unit |

The rules are cheap; the failures are not.

## Verification is a separate seat

An author's tests against their author's chosen cases are not evidence. The
board models verification as its own ticket with its own owner, because the
cases an author did not think of are exactly the ones that matter, and they are
not going to think of them on a second pass either.

## Python 3.9+, stdlib only

The tool has to run wherever an agent happens to be — a fresh container, a
colleague's laptop, a CI box — without an install step that can fail. A
dependency list is a thing that breaks between you and the board.
