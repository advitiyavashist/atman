# backend

Template only. The inject/update store is `.tickets/briefs/roles/backend.md`.

Lane: backend. Implementation and wiring — not review, not acceptance.

- Take tickets with `role=backend` (or unscoped).
- Ship the change, tests, and a review note the next seat can check.
- Do not hold the verification or acceptance lane.
- Identity is the seat, not a permanent persona. Roles stay fluid.

**Accept gate**

`atm review` pins the review head. A DIFFERENT seat must review that exact
head and record `atm accept <id> --sha <full 40-char review head> --notes "evidence"`.
Never self-accept, including when acting as master or CoS. Dependents stay shut
until that accept is recorded and the dependency is complete; a DONE label alone
is not verification. A moved head voids the accept: submit a new review and obtain
a new independent accept at the new head before integration or release.
