"""T-712: public landing spike — clone → Claude Code seat → tickets ui."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LANDING = (ROOT / "landing" / "index.html").read_text(encoding="utf-8")

BANNED = (
    "Total Football", "total football", "football", "goalpost",
    "keeper", "shirts", "midfield", "on the pitch",
    "Steer ^", "mandala", "awakening", "Om ",
    "shared-memory brain", "memory-brain", "vector DB",
)


def _section(html, start_marker, end_marker):
    start = html.index(start_marker)
    end = html.index(end_marker, start)
    return html[start:end]


def test_hero_shows_fewest_turns_least_cost_above_the_fold():
    hero = _section(LANDING, 'class="hero"', 'id="runtime"')
    assert "at least cost" in hero
    assert "fewest turns" in hero
    h1 = _section(hero, "<h1>", "</h1>")
    assert "at least cost" in h1
    assert "fewest turns" in h1


def test_hero_surfaces_usage_limit_recovery_before_deep_features():
    hero = _section(LANDING, 'class="hero"', 'id="runtime"')
    assert "usage limit" in hero
    assert "Usage-limit recovery" in hero
    runtime = LANDING.index('id="runtime"')
    product = LANDING.index('id="product"')
    assert LANDING.index("usage limit") < runtime
    assert LANDING.index("Usage-limit recovery") < product


def test_start_is_claude_code_first_seat_then_tickets_ui():
    start = _section(LANDING, 'id="start"', "</section>")
    assert "Claude Code" in start
    assert "tickets hooks claude --agent alice" in start
    assert "BYOA" in start
    assert "tickets join" in start
    terminal = _section(start, "<pre>", "</pre>")
    assert terminal.index("tickets quickstart") < terminal.index("tickets hooks claude")
    assert terminal.index("tickets hooks claude") < terminal.index("tickets ui")
    assert "tickets next" not in terminal


def test_start_tickets_ui_honesty_uses_emdash_not_zero():
    start = _section(LANDING, 'id="start"', "</section>")
    assert "tickets ui" in start
    assert "stay — until" in start
    assert "unknown is not zero" in start
    assert "yield@cost" in start


def test_example_roster_labels_claude_code_seat():
    hero = _section(LANDING, 'class="hero"', 'id="runtime"')
    assert "<strong>Claude Code</strong>" in hero
    assert "<strong>Claude</strong>" not in hero


def test_brahman_stays_research_not_a_ship_dependency():
    assert "Brahman · research" in LANDING
    assert "Research results are not shipped Atman features." in LANDING


def test_keeps_local_mit_boundaries_and_formation_dots():
    assert "Plain local state" in LANDING
    assert "MIT licensed" in LANDING
    header = _section(LANDING, "<header", "</header>")
    assert header.count("<circle ") == 5
    assert 'class="wordmark">atman</span>' in header
    for word in BANNED:
        assert word not in LANDING, "banned chrome on landing: %s" % word
