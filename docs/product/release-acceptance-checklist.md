# Release acceptance

Atman coordinates the agents a team already uses. A first successful run takes
a user from installation to a teammate completing a defined piece of work,
with the handoff, review and result visible in the app.

## First successful run

1. Install a versioned runtime using a published, tested installation path.
   `atm` is the primary command; `tickets` remains a compatibility alias.
2. Create a team or join an existing team. Show the selected repository and
   board before changing them. Give each runtime a unique identity and show
   its stable role, provider, permissions and lifecycle.
3. Set an objective and an observable completion criterion. Show the next
   available work, its dependencies and its owner.
4. Send a directed task. Prove the intended teammate starts work through its
   supported adapter without an operator keystroke. Ordinary messages must not
   start an unattended task-only worker.
5. Submit an exact artifact for independent review. Mark completion only after
   the accepted change reaches the target branch and required checks pass.

## Evidence in the app

The first Work screen answers what the team is finishing, what is blocked and
who is next. Reservation, message posting, inbox read, confirmed wake, work
start, review and acceptance are separate facts. An absent receipt remains
unknown. A reopened task must not inherit dispatch evidence from its prior life.

Stored and linked selections open the same detail and composer context as a
click, including on mobile. Initial selection must not steal focus. Check
keyboard use, contrast, reduced motion and narrow screens against real behavior.

## Installation and deployment

Published install instructions must work verbatim for a fresh user. A formula
in the repository is not proof that its tap is published. Bind releases,
package hashes, installation receipts and deployment URLs to the accepted
commit, and retain a rollback target.

Document the full runtime and any smaller packaged CLI separately. Unsupported
commands or provider capabilities must be stated beside the installation path.
A hosted product page or static example must not be presented as a live local
team. Authenticate remote tasks and test expiry, revocation and duplicate
suppression through a separate client.

## Review and measurement

Review the exact submitted commit. For consequential changes, preserve a clean
suite receipt and compare failures with the matching accepted baseline. Report
exclusions and existing failures explicitly; focused passes are not evidence
that the complete suite is green.

Metrics need defined cohorts, event linkage, denominators and coverage.
Unknown turns, cost, acknowledgement or work-start evidence remains null.
Keep provider-reported cost separate from estimates, and include repair,
review and coordination when comparing the cost of accepted work.
