"""Optional model suggestions, validated before they can reach a write.

`routing_mode: deterministic_plus_suggestions` is the only place a model may
touch routing, and the contract bounds it precisely: suggestions "may only
reorder among already-eligible candidates". So this module can never *add* a
candidate, never *create* one, and never change why a ticket was skipped. The
worst a broken or hostile recommender can do is reorder a list the deterministic
pass already approved -- or be ignored.

That bound is enforced here rather than trusted, because a recommender is the
one input to this loop that nobody on the board reviewed: it may be a model, a
stub, or a file someone edited. `validate` treats its output as a claim to be
checked against the eligible set, and every rejection is returned rather than
raised, so a bad suggestion degrades to deterministic order and says so in the
decision log instead of stopping the sweep.
"""

from __future__ import annotations

from collections import namedtuple

Suggestion = namedtuple("Suggestion", "order rejected")


def apply(candidates, suggested, *, ticket_id):
    """Reorder `candidates` by `suggested`, dropping anything not eligible.

    Returns (ordered_candidates, notes). `notes` is empty when the suggestion
    was fully usable; otherwise it says exactly what was ignored, so the reason
    written to `master_decisions` reflects what actually happened rather than
    what was proposed.
    """
    notes = []
    if suggested is None:
        return list(candidates), notes

    if not isinstance(suggested, (list, tuple)):
        return list(candidates), [
            "suggestion for %s ignored: expected a list of agent ids, got %s"
            % (ticket_id, type(suggested).__name__)]

    by_id = {c.agent_id: c for c in candidates}
    ordered, seen = [], set()
    for entry in suggested:
        if not isinstance(entry, str):
            notes.append("dropped non-string suggestion %r" % (entry,))
            continue
        if entry in seen:
            notes.append("dropped duplicate suggestion %s" % entry)
            continue
        seen.add(entry)
        candidate = by_id.get(entry)
        if candidate is None:
            # The important case: a model naming an agent the deterministic
            # pass rejected, or one that does not exist at all.
            notes.append("dropped ineligible suggestion %s" % entry)
            continue
        ordered.append(candidate)

    # Anything the suggestion did not mention keeps its deterministic position
    # behind what it did, so a partial suggestion is still a total order.
    tail = [c for c in candidates if c.agent_id not in seen]
    if not ordered:
        notes.append("suggestion named no eligible agent; using deterministic order")
        return list(candidates), notes
    return ordered + tail, notes


def call(recommender, ticket, candidates):
    """Invoke a recommender without letting it break the sweep.

    A recommender that raises is a recommender that is ignored: routing is the
    board's critical path, and the deterministic answer is always available.
    """
    if recommender is None:
        return None, []
    try:
        return recommender(dict(ticket), [c.agent_id for c in candidates]), []
    except Exception as error:  # noqa: BLE001 -- deliberately total
        return None, ["recommender raised %s: %s"
                      % (type(error).__name__, error)]
