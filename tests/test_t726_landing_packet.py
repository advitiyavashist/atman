"""T-726 / T-1047: landing is the accept-gate surface, not a coordination slogan."""

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
    "task graph",
    "DAG",
    "Claude Code is an example, not a requirement",
    "autonomous",
    "fleet",
)

STEER_CHROME = (
    "#c8f04a",
    "grid wallpaper",
    "chevron",
)

PROMISE = "the next ticket opens only after someone else accepts that commit"


def _start_block():
    start = LANDING.index('id="start"')
    return LANDING[start:]


def test_hero_leads_with_the_accept_gate():
    hero = LANDING[LANDING.index("<h1>") : LANDING.index("</h1>")]
    lede = LANDING[LANDING.index('class="lede"') : LANDING.index('class="ctas"')]
    assert PROMISE in hero.lower()
    assert "atm accept" in lede.lower()
    assert "fewest turns" not in hero.lower()
    assert "coordinates the agents you already run" not in hero.lower()


def test_quickstart_has_one_project_setup_path():
    start = _start_block()
    assert "git clone https://github.com/advitiyavashist/atman.git" in start
    assert start.count("<pre") == 1
    assert "./install.sh --prefix" in start
    assert "unset TICKETS_DIR" in start
    assert start.index("atm where") < start.index("atm quickstart") < start.index("atm ui")
    assert "CoS is cursor" not in start
    assert "atm ui" in start
    assert "alice" not in start


def test_visual_tokens_are_bone_brass_not_steer_lime():
    assert "--bg: #0c0e12" in CSS
    assert "--accent: #c4b49a" in CSS
    assert "--fg: #ece8e1" in CSS
    assert "--warn: #e0a53d" in CSS
    assert "--live: #6f9e96" in CSS
    assert "#c8f04a" not in CSS
    assert "#0a0e14" not in CSS
    assert "#4d7cff" not in CSS
    assert "radial-gradient" not in CSS
    assert "4.5rem 4.5rem" not in CSS
    assert LANDING.count("<circle ") >= 10
    assert 'wordmark">atman' in LANDING
    assert "MIT licensed" in LANDING


def test_no_swarm_hype_or_localhost_product_link():
    lowered = LANDING.lower()
    for word in BANNED:
        assert word.lower() not in lowered, word
    for token in STEER_CHROME:
        assert token not in CSS
