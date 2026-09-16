"""T-713 / T-1047: public landing is the accept gate; no fake metrics."""

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
    "autonomous",
    "fleet",
)

PROMISE = "the next ticket opens only after someone else accepts that commit"


def _start_block():
    start = LANDING.index('id="start"')
    return LANDING[start:]


def _start_terminal():
    start = _start_block()
    pre = start.index("<pre")
    return start[pre : start.index("</pre>")]


def test_hero_is_the_accept_gate_not_turns_or_cost():
    lowered = HERO.lower()
    assert PROMISE in lowered
    assert "fewest turns" not in lowered
    assert "measured cost" not in lowered
    assert "coordinates the agents you already run" not in lowered


def test_start_is_source_install_then_sample_project_then_atm_ui():
    start = _start_block()
    term = _start_terminal()
    assert "git clone https://github.com/advitiyavashist/atman.git" in start
    assert "./install.sh --prefix" in term
    assert "unset TICKETS_DIR" in term
    assert "atm ui" in term
    assert term.index("atm where") < term.index("atm quickstart") < term.index("atm ui")
    assert "atm next" not in term
    assert "alice" not in term


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
