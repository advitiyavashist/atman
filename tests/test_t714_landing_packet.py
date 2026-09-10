"""T-714: Atman public landing serves the Claude-first BYOA spike only."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LANDING = (ROOT / "landing" / "index.html").read_text(encoding="utf-8")
CSS = (ROOT / "landing" / "styles.css").read_text(encoding="utf-8")

BANNED = (
    "Total Football",
    "football",
    "swarm",
    "shared-memory brain",
    "memory brain",
    "localhost:8765",
    "127.0.0.1",
)


def _start_block():
    start = LANDING.index('id="start"')
    return LANDING[start:]


def _start_terminal():
    start = _start_block()
    pre = start.index("<pre>")
    return start[pre : start.index("</pre>")]


def test_hero_promises_fewest_turns_and_least_measured_cost():
    hero = LANDING[LANDING.index("<h1>") : LANDING.index("</h1>")]
    assert "fewest turns" in hero.lower()
    assert "least measured cost" in hero.lower()


def test_hero_uses_em_dash_honesty_not_fake_metrics():
    promise = LANDING[LANDING.index('class="promise"') : LANDING.index('class="trust"')]
    assert promise.count("—") >= 2
    assert "$0" not in promise
    assert "0.0" not in promise
    assert "Unknown until a done ticket reports" in promise


def test_start_is_clone_claude_code_then_tickets_ui():
    start = _start_block()
    term = _start_terminal()
    assert "git clone https://github.com/advitiyavashist/atman.git" in start
    assert "tickets quickstart --agent alice --roles backend" in term
    assert "tickets hooks claude --agent alice" in term
    assert "tickets ui" in term
    assert term.index("tickets quickstart") < term.index("tickets hooks claude")
    assert term.index("tickets hooks claude") < term.index("tickets ui")
    assert "tickets next" not in term


def test_usage_limit_recovery_appears_before_start_section():
    before_start = LANDING[: LANDING.index('id="start"')]
    assert "usage limit" in before_start.lower() or "usage-limit" in before_start.lower()
    assert "recover" in before_start.lower()


def test_example_roster_labels_claude_code_first_seat():
    assert "<strong>Claude Code</strong>" in LANDING
    assert "First seat" in LANDING


def test_brahman_is_research_not_required():
    assert "Brahman · research" in LANDING
    assert "not required" in LANDING.lower()


def test_keeps_local_mit_formation_dots_and_dark_brand():
    assert LANDING.count("<circle ") >= 10
    assert "MIT licensed" in LANDING
    assert "wordmark\">atman" in LANDING
    assert "--bg: #0b1416" in CSS
    assert "--accent: #c8f04a" in CSS


def test_no_swarm_hype_or_localhost_product_link():
    lowered = LANDING.lower()
    for word in BANNED:
        assert word.lower() not in lowered, word
