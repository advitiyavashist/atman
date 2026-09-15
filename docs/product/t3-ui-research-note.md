# T3 stack research note (T-1019)

CEO note, committed with the prototype.

Core of T3 is Next.js + TypeScript + Tailwind. tRPC / Prisma / NextAuth are
optional. Attractive here for one reason that matters: a typed board contract
could put the state-honesty rules in a single definition instead of per-view.
State-honesty drift is what T-986 and T-1011 caught.

Costs, stated plainly:

- a hard Node/Next dependency on a tool that today needs only Python 3.9 + git
- a second home for the honesty rules if `tickets ui` still ships (two homes =
  drift = the product lying to a user)
- Prisma/NextAuth pulling toward a database and a server when the board is
  plain files on purpose
- a build step against local-first / offline

The T-1019 prototype (`prototypes/t3-ui`) answers whether the four honesty
rules become harder to break under a typed contract, by enough to justify Node
in a Python local-first tool — not whether the stack is nicer.

Verdict from the working prototype: **no**. See `prototypes/t3-ui/COMPARISON.md`.
The judged-view-model idea should be applied in the existing Python snapshot.
A rewrite is not adopted.
