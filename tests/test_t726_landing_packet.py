"""T-726: Atman landing is a distinct team-runtime surface, any first seat."""

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
    "Steer",
)

STEER_CHROME = (
    "#c8f04a",
    "grid wallpaper",
    "chevron",
)


def _start_block():
    start = LANDING.index('id="start"')
    return LANDING[start:]


def test_hero_leads_with_team_runtime_line():
    hero = LANDING[LANDING.index("<h1>") : LANDING.index("</h1>")]
    assert "Bring the agents you already use" in hero
    assert "We make them one team" in hero


def test_fewest_turns_and_cost_are_secondary_dashes():
    promise = LANDING[LANDING.index('class="promise"') : LANDING.index('class="trust"')]
    assert promise.count("—") >= 2
    assert "$0" not in promise
    assert "0.0" not in promise
    assert "Unknown until a done ticket reports" in promise
    assert "fewest turns" in LANDING.lower()
    assert "measured cost" in LANDING.lower()


def test_claude_code_is_example_not_requirement():
    assert "Claude Code is an example, not a requirement" in LANDING
    assert "Sit Claude Code first" not in LANDING
    assert "Sit Claude Code." not in LANDING


def test_provider_order_is_claude_codex_cursor_grok_custom():
    roster = LANDING[LANDING.index('class="roster"') : LANDING.index('class="lanes"')]
    order = [
        roster.index("<strong>Claude Code</strong>"),
        roster.index("<strong>Codex</strong>"),
        roster.index("<strong>Cursor</strong>"),
        roster.index("<strong>Grok Bot</strong>"),
        roster.index("<strong>Custom</strong>"),
    ]
    assert order == sorted(order)
    tabs = [
        LANDING.index('for="seat-claude"'),
        LANDING.index('for="seat-codex"'),
        LANDING.index('for="seat-cursor"'),
        LANDING.index('for="seat-grok"'),
        LANDING.index('for="seat-custom"'),
    ]
    assert tabs == sorted(tabs)


def test_quickstart_tabs_cover_ordered_providers():
    start = _start_block()
    assert "git clone https://github.com/advitiyavashist/atman.git" in start
    assert "tickets quickstart --agent alice --roles backend" in start
    assert "tickets hooks claude --agent alice" in start
    assert "tickets hooks codex --agent alice" in start
    assert "tickets hooks cursor --agent alice" in start
    assert "tickets hooks remote --agent grok-worker --prompt-kind cos" in start
    assert "tickets join qwen" in start
    assert "--harness custom" in start
    assert "tickets ui" in start
    assert 'id="seat-claude" checked' in start


def test_surfaces_explain_team_ia():
    lowered = LANDING.lower()
    for phrase in (
        "objective",
        "team",
        "work",
        "intervene",
        "messages",
        "liveness",
        "durable handoff",
    ):
        assert phrase in lowered
    assert "Blocked" in LANDING
    assert "In flight" in LANDING
    assert "No silent auto-promote" in LANDING or "does not silently auto-promote" in LANDING


def test_visual_tokens_are_navy_action_blue_not_steer_lime():
    assert "--bg: #0a0e14" in CSS
    assert "--acc: #4d7cff" in CSS
    assert "--warn: #e0a53d" in CSS
    assert "--live: #5ec7b0" in CSS
    assert "#c8f04a" not in CSS
    assert "radial-gradient" not in CSS
    assert "4.5rem 4.5rem" not in CSS
    assert LANDING.count("<circle ") >= 10
    assert 'wordmark">atman' in LANDING
    assert "MIT licensed" in LANDING


def test_no_fake_progress_or_cloud_claim():
    assert "3 / 5" not in LANDING
    assert "progress-track" not in LANDING
    assert "hosted demo" in LANDING.lower() or "no cloud signup" in LANDING.lower()
    assert "fabricated" in LANDING.lower()


def test_no_swarm_hype_or_localhost_product_link():
    lowered = LANDING.lower()
    for word in BANNED:
        assert word.lower() not in lowered, word
    for token in STEER_CHROME:
        assert token not in CSS
