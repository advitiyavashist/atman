"""T-1440: the always-loaded seat prompt has a stated word budget.

Every seat pays for the first-wake prompt before it has done any work, and it
has grown with every fix. The caps that already existed bound each PART and
never the total, so nothing failed when the total moved -- and one part,
`ticket_context`, had no cap at all.

Two budgets, because two different things grow:

  * the SCAFFOLD -- the text the tool itself ships, rendered on a board with
    no briefs, no role context, no knowledge graph and no ticket context.
    This moves when someone edits a prompt string, which is the growth the
    ticket is about. Pinned deterministically: the knowledge root is pointed
    at an empty directory so the number does not depend on which repo pytest
    happens to run in.
  * the TOTAL -- the scaffold plus every board-content part filled to its own
    cap. This is the worst case a seat can actually be handed, and it fails if
    any cap is raised or a new uncapped part is added to the prompt.

Also pinned here: the accept gate is said once per prompt and never twice (the
first turn carries the full ACCEPT GATE block, later turns carry the
one-liner), and both budget checks have teeth -- padding a real render past
its budget makes the same check fail.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

from test_byoa import board, run  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tickets.py"
sys.path.insert(0, str(ROOT / "src"))
from ticket_board import seat_brief  # noqa: E402

TITLE = "Cap the always-loaded seat prompt: word budget plus a test"
BODY = ("The first-wake prompt (gate rule, commands, refusals, usage, brief) grows with "
        "every fix. Outcome: a stated word budget for what every seat loads on first wake, "
        "and a test that fails when the rendered prompt exceeds it. Anything over the "
        "budget moves behind a trigger.")


def _load_tk():
    spec = importlib.util.spec_from_file_location("tickets_t1440", str(TOOL))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Args:
    """The shape `prompt_text` reads: a seat, a turn number, no extra."""

    def __init__(self, agent="alice", run_no=1, master=False, cos=False):
        self.agent = agent
        self.run_no = run_no
        self.master = master
        self.cos = cos
        self.extra = ""


def _held_id(board):
    """The id `atm next` actually handed alice.

    The shared board fixture already seeds a docs ticket, so the backend one
    created here is not T-001 -- read the claim rather than assuming an id.
    """
    tk = _load_tk()
    held = [t["id"] for t in tk.load_all(str(board))
            if t.get("owner") == "alice" and t.get("status") == "claimed"]
    assert len(held) == 1, held
    return held[0]


def _filler(n):
    """n characters of ordinary words, so a cap is hit by content not padding."""
    return (" ".join("word%04d" % i for i in range(n // 8 + 2)))[:n]


# A seat running this suite has TICKET_AGENT, TICKET_SEAT, TICKETS_DIR and
# TICKETS_RUN_NO set in its own environment. `run()` overrides the first and
# third per call but not the rest, and an ambient TICKET_SEAT renames the seat
# so `atm next` claims for somebody else -- which shows up as an unrelated
# flake several tests later. Clear them for every test in this module.
_LEAKY = ("TICKET_SEAT", "TICKETS_RUN_NO", "TICKETS_RUN_ID", "TICKET_SESSION_ID",
          "TICKETS_WATCH_PINNED", "TICKETS_STOP_HOOK", "TICKETS_PY")


@pytest.fixture(autouse=True)
def _no_ambient_seat(monkeypatch):
    for name in _LEAKY:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def seated(board, tmp_path, monkeypatch):
    """A worker holding a ticket, on a board with no content of its own.

    `prompt_text` is called in-process, so the knowledge root would otherwise
    resolve against the repo pytest was launched from. Pointing it at an empty
    directory is what makes the scaffold number a property of this tree.
    """
    empty = tmp_path / "no-knowledge"
    empty.mkdir()
    monkeypatch.setenv("ATMAN_KNOWLEDGE_DIR", str(empty))
    monkeypatch.setenv("HOME", str(board.parent.parent / "home"))
    assert run(board, "join", "alice", "--roles", "backend", "--harness", "claude",
               agent="alice").returncode == 0
    assert run(board, "create", TITLE, "--body", BODY, "--role", "backend",
               agent="alice").returncode == 0
    r = run(board, "next", agent="alice")
    assert r.returncode == 0, r.stderr
    _held_id(board)  # fail here, not three tests later, if nothing was claimed
    return board


def _fill_every_cap(board):
    """Fill each board-content part of the prompt to the cap the CODE declares.

    Reading the caps off the module rather than copying the numbers is what
    makes raising one show up here as a bigger render.
    """
    tk = _load_tk()
    briefs = Path(board) / "briefs"
    (briefs / "roles").mkdir(parents=True, exist_ok=True)
    (briefs / "alice.md").write_text(_filler(tk.AGENT_BRIEF_LIMIT))
    (briefs / "_shared.md").write_text(_filler(tk.ROLE_CONTEXT_LIMIT))
    (briefs / "roles" / "backend.md").write_text(_filler(tk.ROLE_CONTEXT_LIMIT))
    r = run(board, "brief", "--ticket", _held_id(board),
            _filler(tk.TICKET_CONTEXT_LIMIT + 1000), agent="alice")
    assert r.returncode == 0, r.stderr
    return tk


# ---- the budgets are stated, and stated in words -------------------------

def test_budgets_are_stated_and_ordered():
    assert seat_brief.word_count("a b  c\n d") == 4
    assert seat_brief.word_count("") == 0
    assert seat_brief.over_budget("one two three", 3) == 0
    assert seat_brief.over_budget("one two three", 2) == 1
    assert seat_brief.STEADY_BUDGET_WORDS < seat_brief.FIRST_WAKE_BUDGET_WORDS
    assert seat_brief.FIRST_WAKE_BUDGET_WORDS < seat_brief.TOTAL_FIRST_WAKE_BUDGET_WORDS


# ---- the scaffold: what the tool ships -----------------------------------

def test_first_wake_scaffold_fits_the_budget(seated):
    tk = _load_tk()
    text = tk.prompt_text(_Args(run_no=1), str(seated))
    assert text.startswith(seat_brief.BRIEF_HEAD), "the brief must head the first turn"
    over = seat_brief.over_budget(text, seat_brief.FIRST_WAKE_BUDGET_WORDS)
    assert not over, (
        "first-wake scaffold is %d words, %d over the %d-word budget. Move the new text "
        "behind a trigger that already exists (is_first_turn, holding a ticket, a pinned "
        "review head) rather than raising the budget."
        % (seat_brief.word_count(text), over, seat_brief.FIRST_WAKE_BUDGET_WORDS))


def test_steady_state_scaffold_fits_the_budget(seated):
    tk = _load_tk()
    text = tk.prompt_text(_Args(run_no=7), str(seated))
    assert seat_brief.BRIEF_HEAD not in text, "a later turn must not be re-briefed"
    over = seat_brief.over_budget(text, seat_brief.STEADY_BUDGET_WORDS)
    assert not over, "steady-state scaffold is %d words, %d over the %d-word budget" % (
        seat_brief.word_count(text), over, seat_brief.STEADY_BUDGET_WORDS)


def test_master_cos_and_planner_bodies_fit_the_budget():
    tk = _load_tk()
    budget = seat_brief.ROLE_PROMPT_BUDGET_WORDS
    bodies = {
        "MASTER_PROMPT": tk.MASTER_PROMPT.format(agent="a", board="/b", root="/r", extra=""),
        "COS_PROMPT": tk.COS_PROMPT.format(agent="a", board="/b", root="/r", extra=""),
        "PLANNER_PROMPT": tk.PLANNER_PROMPT.format(agent="a", board="/b", root="/r",
                                                   cos="c", extra=""),
    }
    too_big = {n: seat_brief.word_count(t) for n, t in bodies.items()
               if seat_brief.over_budget(t, budget)}
    assert not too_big, "over the %d-word budget: %s" % (budget, too_big)


# ---- the total: the worst case a seat can be handed ----------------------

def test_total_first_wake_fits_when_every_cap_is_filled(seated):
    """Every board-content part at its cap. Fails if a cap rises, or a new
    uncapped part joins the prompt."""
    tk = _fill_every_cap(seated)
    text = tk.prompt_text(_Args(run_no=1), str(seated))
    over = seat_brief.over_budget(text, seat_brief.TOTAL_FIRST_WAKE_BUDGET_WORDS)
    assert not over, (
        "worst-case first-wake prompt is %d words, %d over the %d-word total budget. "
        "A per-part cap was raised, or a new part joined the prompt without one."
        % (seat_brief.word_count(text), over, seat_brief.TOTAL_FIRST_WAKE_BUDGET_WORDS))


def test_board_context_caps_sum_to_the_declared_budget():
    """Every per-part cap, added up, is the stated board-content budget.

    The render test above cannot fill the knowledge context without building a
    graph, so this is what stops any of the four caps being raised without the
    word budget being re-measured.
    """
    tk = _load_tk()
    caps = {
        "agent_brief": tk.AGENT_BRIEF_LIMIT,
        "role_context": tk.ROLE_CONTEXT_LIMIT,
        "knowledge": tk._KNOWLEDGE_MAX_BUDGET,
        "ticket_context": tk.TICKET_CONTEXT_LIMIT,
    }
    assert sum(caps.values()) == seat_brief.BOARD_CONTEXT_BUDGET_CHARS, (
        "a per-part cap moved (%s). Re-measure the first-wake prompt and update "
        "BOARD_CONTEXT_BUDGET_CHARS and TOTAL_FIRST_WAKE_BUDGET_WORDS together." % caps)


def test_ticket_context_is_capped(seated):
    """It was the one always-loaded part with no bound at all."""
    tk = _load_tk()
    r = run(seated, "brief", "--ticket", _held_id(seated), _filler(9000), agent="alice")
    assert r.returncode == 0, r.stderr
    ctx = tk.ticket_context(str(seated), "alice")
    assert len(ctx) <= tk.TICKET_CONTEXT_LIMIT + 60, len(ctx)
    assert "context truncated" in ctx
    # and an uncapped render would have blown the total
    assert seat_brief.word_count(_filler(9000)) > seat_brief.TOTAL_FIRST_WAKE_BUDGET_WORDS / 4


# ---- the gate is said once per prompt, never twice -----------------------

def test_first_turn_states_the_gate_once(seated):
    tk = _load_tk()
    text = tk.prompt_text(_Args(run_no=1), str(seated))
    assert seat_brief.GATE_HEAD in text, "the first turn must carry the full gate block"
    assert seat_brief.GATE_ONE_LINER not in text, "the gate is stated twice in one prompt"
    low = text.lower()
    for claim in ("different seat", "atm accept", "is void", "depends on"):
        assert claim in low, claim
    assert "board.\n\n" not in text, "dropping the gate line left a blank line behind"


def test_later_turns_keep_the_one_line_gate(seated):
    tk = _load_tk()
    text = tk.prompt_text(_Args(run_no=7), str(seated))
    assert seat_brief.GATE_ONE_LINER in text
    assert seat_brief.RUN_ONE_LINER in text


def test_body_gate_line_switches_on_the_head():
    assert seat_brief.body_gate_line(False) == seat_brief.GATE_ONE_LINER
    assert seat_brief.body_gate_line(True) == ""


# ---- the checks have teeth -----------------------------------------------

@pytest.mark.parametrize("run_no,budget_name", [(1, "FIRST_WAKE_BUDGET_WORDS"),
                                                (7, "STEADY_BUDGET_WORDS")])
def test_budget_check_fails_on_a_padded_render(seated, run_no, budget_name):
    """Pad a real render one word past its budget: the check must fail.

    Without this, a budget far above any reachable size would pass forever and
    pin nothing.
    """
    tk = _load_tk()
    budget = getattr(seat_brief, budget_name)
    text = tk.prompt_text(_Args(run_no=run_no), str(seated))
    assert not seat_brief.over_budget(text, budget)
    padded = text + "\n" + ("filler " * (budget - seat_brief.word_count(text) + 1))
    assert seat_brief.over_budget(padded, budget) == 1


def test_budgets_have_headroom_but_are_not_vacuous(seated):
    """Set from a measurement, so each must sit near the render it bounds.

    A budget an order of magnitude above the real size would never fire.
    """
    tk = _load_tk()
    first = seat_brief.word_count(tk.prompt_text(_Args(run_no=1), str(seated)))
    later = seat_brief.word_count(tk.prompt_text(_Args(run_no=7), str(seated)))
    _fill_every_cap(seated)
    total = seat_brief.word_count(tk.prompt_text(_Args(run_no=1), str(seated)))
    for size, budget, name in ((first, seat_brief.FIRST_WAKE_BUDGET_WORDS, "first wake"),
                               (later, seat_brief.STEADY_BUDGET_WORDS, "steady state"),
                               (total, seat_brief.TOTAL_FIRST_WAKE_BUDGET_WORDS, "total")):
        assert size <= budget, (name, size, budget)
        assert budget <= size * 2, (
            "%s budget %d is more than twice the measured render %d -- it pins nothing"
            % (name, budget, size))
