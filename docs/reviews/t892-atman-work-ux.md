# Atman Work view: advisory product and UX review

Requested on T-889; tracked by Sol on T-892. Static review of Work PR #127
at `9f50606` and app shell PR #125 at `7c08839`, against the Product and UX
acceptance section of T-853. These are separate candidates, not a tested
integrated app. Later shell changes need their own check. No browser testing,
deployment verification, or independent acceptance verdict is claimed here.

The direction is right: real dependency order, selectable tickets, inherited
handoffs, a list alternative, and an Atman shell distinct from Steer. Preserve
that work. The requested candidates still miss the first-screen contract and
overstate several evidence states. Fix those within T-889/T-810 before launch;
this advisory review adds no approval hop.

## Edits ranked by launch impact

### 1. Make the first screen name the outcome, blockers, and next teammate

**Owner: T-810 integrator.** Shell `tickets.py:13190–13198` puts the summary
before the objective; its Work objective section has no terminal criterion.
`renderNow` at `13625–13631` selects the first array item, displays ticket IDs
without titles, and substitutes a generic next-step hint for who goes next.
That does not answer what we are finishing or who is next.

More seriously, Work `work_view.py:553` suppresses its own objective section
whenever `#workObjective` exists. The shell does not replace its **Done when**
field. Its Objective tab has the criterion, but Work loses it.

Put **Objective / Done when** directly above the summary and graph. Show the
next ticket's short title, recipient if known, and actual phase: “Ready ·
unassigned,” “Reserved for …,” or “Task posted to … · not claimed.” Separate
suggested from assigned teammates. Show actual blocking dependency IDs or the
recorded blocker reason; distinguish waiting, capture, and hold. Include a count
when displaying one of several blockers, and make that item selectable. Use
one payload for the shell, graph, list, and columns; `work.summary` is a useful
starting point, but needs the evidence corrections below. Do not infer the
critical path merely from ticket order or priority.

**Acceptance:** on a board with several claimed tickets, one held ticket, a
dependency wait, and an unassigned ready ticket, the first screen identifies
the objective, criterion, named blocker, and next ticket without opening tabs.
Missing objective/criterion gets a plain missing state and an available action.

### 2. Separate routing intent, posting, receipt, wake, and claim

**Owner: T-889 payload; T-810 consumes it.** `phase_of:117–118` calls a
reservation “dispatched.” `_dispatch_of:80–95` uses a task message's existence,
not transport/wake evidence. The detail carefully says “task posted,” but the
phase heading and `who:547` say “Dispatched” and “told.” `_starts_of:255–259`
chooses a reserved recipient without a message; `detailHtml:592` then says
that teammate was told. A reservation alone proves none of this.

Show **Reserved · no task posted** for a reservation, **Task posted · wake
unconfirmed** for a log entry, and confirmed delivery/wake only when there is
a matching transport receipt. Keep “inbox read” separate from explicit agent
acknowledgement. The supplied callback is `_agent_acked_message`, which checks
inbox seen state, not an agent response (`tickets.py@9f50606:13994–14000`).
Neither inbox read nor wake proves that work began. Keep claim evidence as
“Claimed by …”; do not imply a running process from a claim alone. Missing
evidence remains unknown, never successful.

**Acceptance:** reservation-only, queued message, inbox-read, confirmed wake,
and claimed cases have different evidence labels. A done parent's reserved
child never says “told.” A stale message from before reopen/reassignment does
not determine the current recipient or current dispatch state.

### 3. Correct the success-to-next story

**Owner: T-889.** `_started_by:209–236` chooses the latest completed parent
even if other dependencies remain unfinished. `detailHtml:585` always renders
“Freed when … finished.” Trigger matching uses message text, not a structured
parent/completion event or chronology. “Began” is based on any retained
`claimed_at`, so it does not prove this completion caused a new claim.

Say **Dependency completed; still waiting on T-…** until all dependencies
finish. Even after dependencies finish, capture or hold can prevent readiness.
Only say “became ready” when eligibility evidence supports it. Show trigger
posting/receipt and claim as separate facts; attribute causality to the parent
only when a matching completion event and subsequent claim support it. On
missing history, say “Claim recorded; trigger relationship unverified.”

**Acceptance:** with two parents and only one done, the child never says
“Freed.” Cover hold/capture, reopen with an old claim timestamp, and a historical
success-trigger message. Current claim state and historical work remain distinct.

### 4. Show the recorded review verdict for its exact artifact

**Owner: T-889 and shell helper.** `verdict_of:54–65` returns “awaiting review”
for every review-status ticket before reading verdict notes; shell
`_ticket_verdict:1531–1541` has the same problem. A review containing a FIX
verdict displays that generic label. Done status alone returns “accepted.”

Separate **ticket status** from **review evidence**. Show the latest applicable
verdict with reviewer and artifact SHA; if notes cannot reliably identify one,
show the note as recorded evidence with unknown applicability. A verdict on an
older SHA is historical. A done flag without independent acceptance/main
evidence should remain **Marked done; verification not recorded** rather than
inventing acceptance. Reuse the launch metric contracts, not another definition
of success.

**Acceptance:** FIX, ACCEPT, no verdict, superseded verdict, and marked-done
without evidence remain distinguishable beside the submitted artifact.

### 5. Finish accessibility and shell/module integration

**Owner: T-810 integrator, with T-889 module support.** Preserve the native
ticket buttons, text labels, arrow navigation, list fallback, Escape behavior,
and reduced-motion rules already present. The light shell retains dark-theme
brass `--acc:#c4b49a` (`tickets.py:12850–12851`); Work uses it as dispatched
text at `work_view.py:489,499` and as focus outline at `472`. Calculated solid
color contrast is **1.95:1 against the light card**, **1.69:1 against the light
surface**. Use a darker light-theme action/focus token. Brass also currently
means dispatched status, contrary to `docs/brand/atman-tokens.md`; use a
semantic status token and reserve brass for actions and focus.

Polling replaces the Work subtree whenever its time-sensitive payload changes
(`render:666–680`). It preserves ticket/close focus, but not Graph/List control
focus; preserve the active layout control too, and avoid repeated whole-detail
live announcements on timestamp-only changes. Mobile detail stacks below the
graph: provide a clear way to reach the selected detail and return, while
preserving scrolling position. Verify this in the integrated app, not just CSS.

The shell's Graph/Columns controls use `role=tablist` without complete tab
semantics (`tickets.py:13208–13210`).
Use ordinary pressed toggle buttons, or finish tab roles, relationships, and
keyboard behavior. Choose one layout control boundary so Graph/List/Columns
do not compete. Work emits `atman:work-select` (`653`); the requested shell
candidate has no listener. Integrate it so compose refers to the selected ticket,
with no parallel second detail panel and no unrelated ticket auto-filled as
message context. This is an integration check, not a claim that the two
standalone branches were already combined.

**Acceptance:** keyboard-only select/detail/compose/close/list flow survives
refresh; layout controls retain focus; light/dark focus and status text are
readable; 390px follow-up can reach the selected detail and composer.

## Smaller edits; keep the date

- Replace visible `tickets` commands with `atm` once the shared CLI candidate
  lands, keeping the compatibility alias. Remove the shell's explanatory
  command wall (`tickets.py:13212`). Suggested visible copy: **“Follow the work.
  Select a ticket for its blockers, handoff, and review.”** Put recovery commands
  in selected detail/help. “Children start when a parent is accepted” also needs
  the evidence qualification from items 2–3.
- A board of independent tasks is valid. `empty_state:287–293` should not urge
  users to invent dependencies. With a single `T-001`, the generated example
  can even become a self-dependency. Say **“No dependencies yet”** with an
  optional planning action; only offer a concrete dependency edit when two
  distinct tickets exist and the user actually intends that order.
- Keep formation dots, lowercase wordmark, flat charcoal/brass palette, and
  compact team panels. They already establish Atman identity. Collapse redundant
  metrics/portfolio chrome above the first work decision. The disconnected
  Brahman/ATI menu entries are not launch functionality and should not compete
  with managing this team. No redesign, ornamental canvas, or new research
  surface is needed for this release.

## Verification performed

Read both exact candidate sources, the T-853 acceptance section, Atman brand
tokens/direction, and the author's screenshot evidence description. Did not
inspect screenshots or launch a browser. Executed the candidate's pure payload
functions with synthetic data and stubbed sounding helpers: reservation-only
returned `dispatched`; a review with `verdict: FIX` returned `awaiting review`;
a two-parent child with one unfinished parent was `waiting` but had a
`started_by` record that the UI renders as “Freed.” Calculated contrast from
the declared solid CSS colors. These focused checks are not the author's test
suite or end-to-end accessibility certification.

Routing: post this advisory on T-889/T-810; CEO determines disposition, CoS
coordinates the existing owners. Keep items 1–5 within the integrated Work
scope rather than creating a parallel redesign graph.
