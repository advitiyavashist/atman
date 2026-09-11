"""T-784: team-intro copy names probe, plan/--after, persist-to-review.

Does not rewrite landing/. Does not mention Minions/Inspect, Slack-as-bus,
or VM fleets on the intro surfaces.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tickets.py"
HOWTO = ROOT / "docs/onboarding/master-howto.md"
ONBOARD = ROOT / "docs/onboarding/README.md"
FIRST = ROOT / "docs/first-session.md"
README = ROOT / "README.md"
LANDING = ROOT / "landing" / "index.html"

BANNED_DOCS = (
    "Stripe Minions",
    "Ramp Inspect",
    "Slack-as-bus",
    "Slack as the control",
    "VM fleet",
    "devbox",
    "Total Football",
    "swarm",
    "1000 PRs",
    "~30%",
)

BANNED_LANDING = BANNED_DOCS + ("shared-memory brain",)

REQUIRED = (
    "tickets harness available",
    "tickets plan",
    "--after",
    "reviewable SHA",
    "human review",
)


def _ui_html():
    text = TOOL.read_text(encoding="utf-8")
    start = text.index('UI_HTML = r"""') + len('UI_HTML = r"""')
    end = text.index('"""', start)
    return text[start:end]


def test_docs_and_readme_name_the_contract():
    for path in (HOWTO, ONBOARD, FIRST, README):
        body = path.read_text(encoding="utf-8")
        lowered = body.lower()
        for phrase in REQUIRED:
            assert phrase.lower() in lowered, "%s missing %r" % (path.name, phrase)
        for word in BANNED_DOCS:
            assert word not in body, "%s leaked %r" % (path.name, word)
    assert "docs/onboarding/README.md" in README.read_text(encoding="utf-8")
    assert "docs/first-session.md" in README.read_text(encoding="utf-8")


def test_howto_still_starts_with_onboarding():
    lines = [ln.strip() for ln in HOWTO.read_text().splitlines() if ln.strip()]
    assert lines[0] == "# Master how-to"
    assert "You are onboarding" in lines[1]
    assert "one `tickets create` per title" in HOWTO.read_text()


def test_connect_names_plan_and_review_gate(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    (repo / "README").write_text("t784\n")
    subprocess.run(
        ["git", "-c", "user.email=t784@test", "-c", "user.name=t784",
         "add", "README"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t784@test", "-c", "user.name=t784",
         "commit", "-qm", "init"], cwd=repo, check=True)
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path / "home"),
        "PYTEST_CURRENT_TEST": "tests/test_t784_team_intro.py",
    }
    (tmp_path / "home").mkdir()
    r = subprocess.run(
        [sys.executable, str(TOOL), "init"],
        cwd=repo, env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    c = subprocess.run(
        [sys.executable, str(TOOL), "connect"],
        cwd=repo, env=env, capture_output=True, text=True)
    assert c.returncode == 0, c.stderr
    first = next(ln.strip() for ln in c.stdout.splitlines() if ln.strip())
    assert first.startswith("**You are onboarding.**")
    assert "harness available" in c.stdout
    assert "--after" in c.stdout
    assert "tickets plan" in c.stdout
    assert "reviewable SHA" in c.stdout
    assert "tickets review" in c.stdout
    for word in ("Minions", "Inspect", "Slack-as-bus"):
        assert word not in c.stdout


def test_ui_and_master_template_name_persist_to_review():
    src = TOOL.read_text(encoding="utf-8")
    ui = _ui_html()
    assert "Reviewable SHA" in ui
    assert "tickets review <id> --notes" in ui
    assert "tickets plan" in ui
    assert "reviewable SHA" in ui
    assert "one tickets create per task they named" not in src
    assert "tickets plan <<" in src
    assert "--after" in src[src.index("ONBOARDING_STARTUP"):src.index("INTEGRATION_CATALOG")]
    for word in BANNED_LANDING:
        assert word not in ui


def test_landing_pins_untouched():
    landing = LANDING.read_text(encoding="utf-8")
    assert "local control plane" in landing.lower()
    assert "Bring the agents you already use" in landing
    for word in BANNED_LANDING:
        assert word not in landing
