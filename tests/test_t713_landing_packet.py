"""T-713: Atman public landing is team runtime / any first seat; turns/cost secondary."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LANDING = (ROOT / "landing" / "index.html").read_text(encoding="utf-8")
CSS = (ROOT / "landing" / "styles.css").read_text(encoding="utf-8")
HERO = LANDING[LANDING.index("<h1>") : LANDING.index("</h1>")]

BANNED = (
    "Total Football",
    "football",
    "swarm",
    "shared-memory brain",
    "memory brain",
    "localhost:8765",
    "127.0.0.1",
    "Try the API",
    "Presidio",
    "/v1/evaluate",
)

SEATS = (
    "Claude Code",
    "Codex",
    "Cursor",
    "Grok Bot",
    "custom",
)


def _start_block():
    start = LANDING.index('id="start"')
    return LANDING[start:]


def _start_terminal():
    start = _start_block()
    pre = start.index("<pre")
    return start[pre : start.index("</pre>")]


def test_hero_is_control_plane_not_turns_or_cost():
    lowered = HERO.lower()
    assert "coordinates the agents you already run" in lowered
    assert "fewest turns" not in lowered
    assert "measured cost" not in lowered


def test_turns_and_cost_are_secondary_honesty_not_fake_metrics():
    efficiency = LANDING[LANDING.index('id="efficiency"') : LANDING.index('id="graph"')]
    assert "fewest turns" in efficiency.lower()
    assert "measured cost" in efficiency.lower()
    assert efficiency.count("—") >= 2
    assert "$0" not in efficiency
    assert "0.0" not in efficiency
    assert "Unknown until a done ticket reports" in efficiency
    assert "fewest turns" not in HERO.lower()


def test_first_seat_examples_appear_in_order():
    lowered = LANDING
    positions = [lowered.index(name) for name in SEATS]
    assert positions == sorted(positions)


def test_start_is_clone_any_seat_then_tickets_ui():
    start = _start_block()
    term = _start_terminal()
    assert "git clone https://github.com/advitiyavashist/atman.git" in start
    assert "any first seat" in start.lower() or "any first seat" in LANDING.lower()
    assert "tickets connect" in term
    assert "atman-ceo" in term
    assert "tickets ui" in term
    assert term.index("tickets connect") < term.index("tickets ui")
    assert "tickets next" not in term
    assert "alice" not in term
    for name in SEATS:
        assert name in start or name in LANDING


def test_usage_limit_recovery_appears_before_start_section():
    before_start = LANDING[: LANDING.index('id="start"')]
    assert "usage limit" in before_start.lower() or "usage-limit" in before_start.lower()
    assert "recover" in before_start.lower()


def test_example_roster_does_not_lock_claude_as_the_only_first_seat():
    assert "Example first seat" in LANDING
    assert "Sit Claude Code first" not in LANDING
    assert "Claude Code as the first seat" not in LANDING


def test_brahman_is_research_not_required():
    assert "Brahman · research" not in LANDING
    assert "multi-agent framework" in LANDING.lower()
    assert "not required for the first win" not in LANDING.lower()


def test_keeps_local_mit_formation_dots_and_distinct_dark_brand():
    assert LANDING.count("<circle ") >= 10
    assert "MIT licensed" in LANDING
    assert "wordmark\">atman" in LANDING
    assert "--bg: #0c0e12" in CSS
    assert "--accent: #c4b49a" in CSS
    assert "#c8f04a" not in CSS
    assert "#0b1416" not in CSS
    assert "4.5rem 4.5rem" not in CSS


def test_no_swarm_hype_steer_cta_or_localhost_product_link():
    lowered = LANDING.lower()
    for word in BANNED:
        assert word.lower() not in lowered, word
