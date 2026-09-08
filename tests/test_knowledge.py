"""Team knowledge: docs/knowledge/ index + pin into the existing brief inject.

Not a shared-memory product. Pin writes a pointer into .tickets/briefs/;
watch/spawn already inject that store (T-529 / T-530). Local pytest only.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

from test_byoa import board, run  # noqa: F401

TOOL = Path(__file__).resolve().parents[1] / "tickets.py"


def _kb_env(kb):
    return {"TICKETS_KNOWLEDGE_DIR": str(kb)}


def write_kb(root, name, text):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def sample_doc(doc_id="house-style", title="House style", tags="docs, backend",
               body="Prefer short sentences. No new APIs.", pin=""):
    pin_line = ("pin: %s\n" % pin) if pin else ""
    return (
        "---\n"
        "id: %s\n"
        "title: %s\n"
        "tags: [%s]\n"
        "seats: [docs]\n"
        "%s"
        "---\n\n"
        "# %s\n\n"
        "%s\n" % (doc_id, title, tags, pin_line, title, body)
    )


def test_knowledge_list_and_tag_filter(board, tmp_path):
    kb = tmp_path / "knowledge"
    write_kb(kb, "house-style.md", sample_doc())
    write_kb(kb, "memory.md", sample_doc("memory", "Memory lock", "memory, product",
                                         "Board docs and briefs only."))
    r = run(board, "knowledge", "--json", env=_kb_env(kb))
    assert r.returncode == 0, r.stderr + r.stdout
    rows = json.loads(r.stdout)
    ids = {row["id"] for row in rows}
    assert ids == {"house-style", "memory"}
    tagged = run(board, "knowledge", "--tag", "memory", "--json", env=_kb_env(kb))
    assert tagged.returncode == 0, tagged.stderr
    only = json.loads(tagged.stdout)
    assert [row["id"] for row in only] == ["memory"]


def test_knowledge_list_subcommand_tag(board, tmp_path):
    kb = tmp_path / "knowledge"
    write_kb(kb, "house-style.md", sample_doc())
    r = run(board, "knowledge", "list", "--tag", "docs", "--json", env=_kb_env(kb))
    assert r.returncode == 0, r.stderr + r.stdout
    rows = json.loads(r.stdout)
    assert [row["id"] for row in rows] == ["house-style"]


def test_knowledge_show_prints_body(board, tmp_path):
    kb = tmp_path / "knowledge"
    write_kb(kb, "house-style.md", sample_doc(body="KB-SHOW-MARKER"))
    r = run(board, "knowledge", "show", "house-style", env=_kb_env(kb))
    assert r.returncode == 0, r.stderr + r.stdout
    assert "KB-SHOW-MARKER" in r.stdout
    assert "house-style.md" in r.stdout
    assert "---" not in r.stdout.split("house-style.md", 1)[-1]


def test_knowledge_show_missing_and_unsafe(board, tmp_path):
    kb = tmp_path / "knowledge"
    kb.mkdir()
    missing = run(board, "knowledge", "show", "nosuch", env=_kb_env(kb))
    assert missing.returncode != 0
    assert "no knowledge doc" in (missing.stderr + missing.stdout)
    for bad in ("../secret", "foo/bar", ".", ".."):
        r = run(board, "knowledge", "show", bad, env=_kb_env(kb))
        assert r.returncode != 0, bad
        assert "NO CHANGE WAS MADE" in (r.stderr + r.stdout) or "not a safe name" in (r.stderr + r.stdout)


def test_knowledge_missing_tree_is_honest(board, tmp_path):
    kb = tmp_path / "missing-knowledge"
    r = run(board, "knowledge", env=_kb_env(kb))
    assert r.returncode == 0, r.stderr + r.stdout
    assert "no team knowledge tree" in r.stdout
    assert str(kb) in r.stdout


def test_knowledge_is_not_auto_injected(board, tmp_path):
    kb = tmp_path / "knowledge"
    write_kb(kb, "house-style.md", sample_doc(body="KB-AUTO-LEAK"))
    assert run(board, "join", "alice", "--roles", "backend").returncode == 0
    r = run(board, "prompt", "--agent", "alice", env=_kb_env(kb))
    assert r.returncode == 0, r.stderr
    assert "KB-AUTO-LEAK" not in r.stdout
    assert "Knowledge [house-style]" not in r.stdout
    assert not (board / "knowledge").exists()


def test_knowledge_pin_role_reaches_prompt(board, tmp_path):
    kb = tmp_path / "knowledge"
    write_kb(kb, "house-style.md", sample_doc(
        pin="Prefer short sentences.", body="KB-PIN-BODY"))
    assert run(board, "join", "alice", "--roles", "backend").returncode == 0
    r = run(board, "knowledge", "pin", "house-style", "--role", "backend",
            env=_kb_env(kb), agent="master")
    assert r.returncode == 0, r.stderr + r.stdout
    path = board / "briefs" / "roles" / "backend.md"
    assert path.is_file()
    text = path.read_text()
    assert "Knowledge [house-style]:" in text
    assert "docs/knowledge/house-style.md" in text
    assert "Prefer short sentences." in text
    assert "KB-PIN-BODY" not in text
    assert not (board / "knowledge").exists()

    shown = run(board, "brief", "--role", "backend", "--show")
    assert "Knowledge [house-style]:" in shown.stdout

    prompt = run(board, "prompt", "--agent", "alice", env=_kb_env(kb))
    assert prompt.returncode == 0, prompt.stderr
    assert "Knowledge [house-style]:" in prompt.stdout
    assert "Prefer short sentences." in prompt.stdout
    assert str(path) in prompt.stdout
    assert "KB-PIN-BODY" not in prompt.stdout


def test_knowledge_pin_shared_and_agent(board, tmp_path):
    kb = tmp_path / "knowledge"
    write_kb(kb, "memory.md", sample_doc("memory", "Memory lock", "memory",
                                         "Board docs only.", pin="Memory is briefs."))
    run(board, "join", "scribe", "--roles", "docs")
    shared = run(board, "knowledge", "pin", "memory", "--shared", env=_kb_env(kb))
    assert shared.returncode == 0, shared.stderr + shared.stdout
    assert "Knowledge [memory]:" in (board / "briefs" / "_shared.md").read_text()

    agent = run(board, "knowledge", "pin", "memory", "--agent", "scribe",
                env=_kb_env(kb))
    assert agent.returncode == 0, agent.stderr + agent.stdout
    assert "Knowledge [memory]:" in (board / "briefs" / "scribe.md").read_text()

    prompt = run(board, "prompt", "--agent", "scribe")
    assert "Knowledge [memory]:" in prompt.stdout
    assert "Memory is briefs." in prompt.stdout


def test_knowledge_pin_is_idempotent(board, tmp_path):
    kb = tmp_path / "knowledge"
    write_kb(kb, "house-style.md", sample_doc())
    first = run(board, "knowledge", "pin", "house-style", "--role", "docs",
                env=_kb_env(kb))
    assert first.returncode == 0, first.stderr
    second = run(board, "knowledge", "pin", "house-style", "--role", "docs",
                 env=_kb_env(kb))
    assert second.returncode == 0, second.stderr
    assert "already pinned" in second.stdout
    text = (board / "briefs" / "roles" / "docs.md").read_text()
    assert text.count("Knowledge [house-style]:") == 1


def test_knowledge_pin_needs_exactly_one_target(board, tmp_path):
    kb = tmp_path / "knowledge"
    write_kb(kb, "house-style.md", sample_doc())
    none = run(board, "knowledge", "pin", "house-style", env=_kb_env(kb))
    assert none.returncode != 0
    both = run(board, "knowledge", "pin", "house-style", "--role", "docs",
               "--shared", env=_kb_env(kb))
    assert both.returncode != 0
    combo = run(board, "knowledge", "pin", "house-style", "--role", "docs",
                "--agent", "alice", env=_kb_env(kb))
    assert combo.returncode != 0


def test_knowledge_pin_rejects_shared_as_role(board, tmp_path):
    kb = tmp_path / "knowledge"
    write_kb(kb, "house-style.md", sample_doc())
    r = run(board, "knowledge", "pin", "house-style", "--role", "_shared",
            env=_kb_env(kb))
    assert r.returncode != 0
    assert not (board / "briefs" / "roles" / "_shared.md").exists()


def test_knowledge_pin_missing_doc(board, tmp_path):
    kb = tmp_path / "knowledge"
    kb.mkdir()
    r = run(board, "knowledge", "pin", "ghost", "--role", "docs", env=_kb_env(kb))
    assert r.returncode != 0
    assert "no knowledge doc" in (r.stderr + r.stdout)


def test_kb_alias_lists(board, tmp_path):
    kb = tmp_path / "knowledge"
    write_kb(kb, "house-style.md", sample_doc())
    r = run(board, "kb", "--json", env=_kb_env(kb))
    assert r.returncode == 0, r.stderr + r.stdout
    assert [row["id"] for row in json.loads(r.stdout)] == ["house-style"]


def test_knowledge_nested_file_and_first_id_wins(board, tmp_path):
    kb = tmp_path / "knowledge"
    write_kb(kb, "ops/oncall.md", sample_doc("oncall", "Who to wake", "ops",
                                            "Wake the master."))
    write_kb(kb, "oncall-dup.md", sample_doc("oncall", "Duplicate", "ops",
                                            "Should be skipped."))
    r = run(board, "knowledge", "--json", env=_kb_env(kb))
    assert r.returncode == 0, r.stderr
    rows = json.loads(r.stdout)
    assert [row["id"] for row in rows] == ["oncall"]
    shown = run(board, "knowledge", "show", "oncall", env=_kb_env(kb))
    # Deterministic: lexicographic rel wins (oncall-dup.md before ops/oncall.md).
    assert "Should be skipped." in shown.stdout
    assert "Wake the master." not in shown.stdout


def test_knowledge_root_helper_honours_env(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("tickets_kb", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    kb = tmp_path / "override-kb"
    kb.mkdir()
    old = os.environ.get("TICKETS_KNOWLEDGE_DIR")
    os.environ["TICKETS_KNOWLEDGE_DIR"] = str(kb)
    try:
        assert Path(mod.knowledge_root("/tmp/board")) == kb.resolve()
    finally:
        if old is None:
            os.environ.pop("TICKETS_KNOWLEDGE_DIR", None)
        else:
            os.environ["TICKETS_KNOWLEDGE_DIR"] = old


def test_knowledge_list_works_without_live_board(tmp_path):
    """Read path does not require T-*.json — catalog is repo docs."""
    repo = tmp_path / "empty"
    repo.mkdir()
    kb = tmp_path / "knowledge"
    write_kb(kb, "house-style.md", sample_doc())
    e = dict(os.environ, TICKETS_DIR=str(repo / ".tickets"), TICKET_AGENT="",
             HOME=str(tmp_path / "home"), TICKETS_KNOWLEDGE_DIR=str(kb))
    e.pop("TICKETS_STOP_HOOK", None)
    (tmp_path / "home").mkdir()
    r = subprocess.run([sys.executable, str(TOOL), "knowledge", "--json"],
                       capture_output=True, text=True, env=e, cwd=str(repo))
    assert r.returncode == 0, r.stderr + r.stdout
    assert [row["id"] for row in json.loads(r.stdout)] == ["house-style"]
