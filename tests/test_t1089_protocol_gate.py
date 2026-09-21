"""Protocol readers must learn the gate, including generated startup files."""
import importlib.util
import re
from pathlib import Path

import pytest

from test_t263_init_isolation import TOOLS, clean_env, make_repo, run

ROOT = Path(__file__).resolve().parents[1]


def module(path):
    spec = importlib.util.spec_from_file_location("t1089_" + path.stem, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def assert_gate(text):
    text = " ".join(text.split())
    assert re.search(r"atm review.*pins.*review head", text)
    assert "DIFFERENT seat" in text
    assert "atm accept <id> --sha <full 40-char review head>" in text
    assert "Never self-accept" in text
    assert "Dependents stay shut until that accept" in text
    assert "A moved head voids the accept" in text


# These patterns intentionally reject historical close instructions even if
# somebody appends a correct gate paragraph elsewhere in the same document.
PRE_GATE = [
    r"\btickets done\b",
    r"master reviews,? merges",
    r"master.{0,60}merges? to main and closes",
    r"until (?:those )?dependencies are marked done",
    r"`done --notes` must include",
    r"main, closes the ticket",
    r"master `atm merge` then `atm done`",
]


def assert_no_pre_gate(text):
    flat = " ".join(text.split())
    for pattern in PRE_GATE:
        assert not re.search(pattern, flat, re.I), pattern


def protocol_files():
    yield ROOT / "AGENTS.md"
    yield ROOT / "docs/handoffs/SEATS_AND_HOOKS.md"
    for directory in (".cursor/rules", "roles", "briefs/roles"):
        yield from (p for p in (ROOT / directory).rglob("*") if p.is_file())


def test_every_committed_protocol_surface_teaches_gate():
    paths = list(protocol_files())
    assert any(".cursor/rules" in str(p) for p in paths)
    for path in paths:
        text = path.read_text()
        assert_no_pre_gate(text)
        assert_gate(text)


@pytest.mark.parametrize("bad", [
    "Finish with tickets done T-002.",
    "The master reviews, merges to main and closes it.",
    "Wait until those dependencies are marked done.",
    "main, closes the ticket",
])
def test_sweep_detects_stale_instruction_even_beside_valid_gate(bad):
    with pytest.raises(AssertionError):
        assert_no_pre_gate((ROOT / "AGENTS.md").read_text() + "\n" + bad)


@pytest.mark.parametrize("tool", TOOLS, ids=lambda p: p.name)
def test_templates_and_generated_init_files(tmp_path, tool):
    mod = module(tool)
    for name in ("PROTOCOL", "CURSOR_RULE", "MASTER_TEMPLATE"):
        text = getattr(mod, name)
        assert_gate(text)
        assert_no_pre_gate(text)
    repo = make_repo(tmp_path / "repo")
    result = run(tool, repo, "init", env=clean_env(tmp_path))
    assert result.returncode == 0, result.stdout + result.stderr
    # Read each artifact alone: no prompt or external brief can fill its gaps.
    for relative in ("AGENTS.md", ".cursor/rules/tickets.mdc", ".tickets/MASTER.md"):
        text = (repo / relative).read_text()
        assert_gate(text)
        assert_no_pre_gate(text)


def test_quickstart_generated_agents_is_sufficient(tmp_path):
    repo = make_repo(tmp_path / "repo")
    result = run(TOOLS[0], repo, "quickstart", env=clean_env(tmp_path))
    assert result.returncode == 0, result.stdout + result.stderr
    for relative in ("AGENTS.md", ".cursor/rules/tickets.mdc"):
        text = (repo / relative).read_text()
        assert_gate(text)
        assert_no_pre_gate(text)


def test_all_seat_prompts_teach_gate():
    mod = module(TOOLS[0])
    for name in ("MASTER_PROMPT", "COS_PROMPT"):
        text = getattr(mod, name)
        assert_gate(text)
        assert_no_pre_gate(text)
    from ticket_board.seat_brief import GATE_ONE_LINER
    text = mod.WORKER_PROMPT.format(agent="worker", board="board", root="repo",
                                  master="master", extra="", gate=GATE_ONE_LINER, run="")
    assert_no_pre_gate(text)
    assert "atm review` pins the review head" in text
    assert "DIFFERENT seat" in text and "cannot accept your own" in text
    assert "moves the head and voids the accept" in text
    assert "depends on yours opens until the accept lands" in text


def test_checked_in_startup_files_match_generator():
    mod = module(TOOLS[0])
    assert (ROOT / "AGENTS.md").read_text() == mod.PROTOCOL
    assert (ROOT / ".cursor/rules/tickets.mdc").read_text() == mod.CURSOR_RULE
