# T-1019 comparison: T3 prototype vs current `tickets ui`

**Recommendation: do not adopt a T3 rewrite for the product UI.**

The preview stays on the current local app (already reviewed on T-986 / T-1011).
A typed contract is the right *shape* for the honesty rules. Putting that
contract in Next.js + tRPC does not make those rules harder to break while
Python still ships the operator surface — it adds a second home for them.

SHA of this prototype: see the PR. Board files read: the same
`T-*.json` / `agents/*.json` / `messages.jsonl` / `objective.json` layout
`tickets.py` `load_all` / `load_agents` / `load_messages` / `load_objective` use.

## What the prototype adds that the current app cannot do

1. **A TypeScript discriminated union for agent reach.**
   `{ phase: "found", connected: true }` is a type error. The current Python
   snapshot can still *compute* the same fields (`auth=login_required`,
   `reachable=false`) and a later HTML/JS branch can ignore them. Types do not
   stop a Python template from printing "connected" next to a found binary;
   they only stop a TypeScript screen that is forced to take `AgentReach`.

2. **Unverified done is visible by construction.**
   `keepOnWorkGraph` returns true for `status=done` without a structured
   ACCEPT. T-986's named fail — isolated done counted as progress and omitted
   from Work — cannot happen in this router unless someone edits
   `honesty.ts`. The current app already *has* the copy
   ("Marked done; verification not recorded" in `work_view.py:260`) and then
   hides the node via `workflow_graph(..., include_done=False)`.

That is the whole addition. tRPC did not give the product a capability the
board files lacked. It re-expressed rules that already live in Python.

## What it costs

| Cost | Evidence |
|---|---|
| Node + Next as a hard dependency | Current local app: Python 3.9+ and git. This prototype: Node 24 + `npm install` before any screen. |
| Install size | **400 MB** `node_modules` after a clean `npm install` (Next itself 153 MB). The launch UI adds none of this. |
| Extra build step | `next build` / `next dev`. `tickets ui` is one Python process serving HTML. |
| Second home for honesty rules | `src/ticket_board/work_view.py` + `tickets.py` `board_snapshot` remain the operator path. `prototypes/t3-ui/src/server/honesty.ts` is a parallel judge. Two homes *are* the T-986/T-1011 failure mode. |
| Prisma / NextAuth | Not used. They would pull a database and a hosted identity onto a file board. That is a product change, not a UI refresh. |
| Offline | Next still needs a Node server. Disconnecting the board dir keeps the last judged snapshot (`workLost: false`) — same idea as the current UI keeping `/board.json`. The recovery command is still `tickets ui`, because this prototype is not the runtime. |

## Do the four honesty rules survive?

Driven against `fixtures/throwaway-board` (same shapes as the T-986 throwaway).

| Rule | T3 prototype | Current local app (T-986) |
|---|---|---|
| Binary found ≠ connected | PASS. Alice `login_required` → `connected: false`, label Login required, cmd `agent login`. | PASS (Team reach). |
| Submitted ≠ accepted | PASS. T-005 `status=review`, empty `review_events` → `accepted: false`, "Awaiting review · no verdict recorded". | PASS. |
| Done-without-ACCEPT visible | PASS. T-006 on the graph with the designed label; not added to `doneVerified` (T-007 is). | **FAIL.** Isolated done counted 1/6 and omitted; designed label never rendered. |
| Disconnected ≠ work lost | PASS. After deleting the board dir, last snapshot kept, `workLost: false`, recovery `tickets ui`. | PASS (OFFLINE + last snapshot). |

Targeted tests: `cd prototypes/t3-ui && npm test`.

The one rule the prototype "wins" is the filter bug T-986 already named. The
smallest fix is in Python (`keep` unverified-done in `workflow_graph`, do not
increment the verified-done chip). That does not require Node.

## Offline behaviour

- Board files missing on first read: `phase=offline`, empty designed state, no invented tickets.
- Board files removed after a live read: last judged snapshot stays; `workLost` is a type-level `false`.
- There is no "Recover" button that claims to rewrite the board. The command is `tickets ui`.
- A Next process that is itself down is a darker offline than `tickets ui`: the operator has two runtimes to restart.

## Would a rewrite reduce the work to keep the product honest?

No. Evidence:

1. The T-986 fail is a **keep/count filter**, not an untyped view. The Python
   module already distinguishes ACCEPT from a chat note. Types would not have
   written `include_done=False`.
2. Adopting T3 **while shipping `tickets ui`** doubles the honesty surface.
   Every later rule (receipt ≠ ACK, reserved ≠ claimed) must be ported or it
   will drift — the exact defect two reviewers caught.
3. Replacing Python UI entirely would *then* make the typed contract the
   single home. That is a product rewrite (Node on every first-run, a build
   step, a second language in a Python CLI) for a gain that a one-function
   filter change already buys.

Adopt the *idea*: one judged view-model, screens cannot see raw status.
Implement it in the Python snapshot the operator already runs. Keep this
directory as a throwaway experiment.

## What would have falsified this recommendation

A measurement that the typed contract prevented a class of honesty bugs that
Python could not prevent without adding Node — for example, a compile-time
proof that no screen can print "connected" without `phase: "responding"`,
*and* a decision to delete `tickets ui`. Neither is true today.
