"""KB v0: index tracked docs/knowledge/. Inject is E-013 briefs only.

CEO/PM lock: board docs + tracked docs + .tickets/briefs/ (shared/roles/agent).
Not a shared-memory brain, vector DB, or auto-sync role KB.
Local pytest only.
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
               body="Prefer short sentences. No new APIs."):
    return (
        "---\n"
        "id: %s\n"
        "title: %s\n"
        "tags: [%s]\n"
        "---\n\n"
        "# %s\n\n"
        "%s\n" % (doc_id, title, tags, title, body)
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
    """Catalog is not inject. No auto-sync into briefs/roles."""
    kb = tmp_path / "knowledge"
    write_kb(kb, "house-style.md", sample_doc(body="KB-AUTO-LEAK"))
    assert run(board, "join", "alice", "--roles", "backend").returncode == 0
    r = run(board, "prompt", "--agent", "alice", env=_kb_env(kb))
    assert r.returncode == 0, r.stderr
    assert "KB-AUTO-LEAK" not in r.stdout
    assert "Knowledge [house-style]" not in r.stdout
    assert not (board / "knowledge").exists()
    assert not (board / "briefs" / "roles" / "backend.md").exists()


def test_e013_brief_is_the_inject_path(board, tmp_path):
    """Standing knowledge reaches a prompt only through tickets brief (E-013)."""
    kb = tmp_path / "knowledge"
    write_kb(kb, "house-style.md", sample_doc(body="KB-CATALOG-ONLY"))
    assert run(board, "join", "alice", "--roles", "backend").returncode == 0
    r = run(board, "brief", "--role", "backend",
            "House rule: one file per change.", agent="master")
    assert r.returncode == 0, r.stderr + r.stdout
    path = board / "briefs" / "roles" / "backend.md"
    assert "one file per change" in path.read_text()
    prompt = run(board, "prompt", "--agent", "alice", env=_kb_env(kb))
    assert prompt.returncode == 0, prompt.stderr
    assert "one file per change" in prompt.stdout
    assert str(path) in prompt.stdout
    assert "KB-CATALOG-ONLY" not in prompt.stdout
    assert not (board / "knowledge").exists()


def test_knowledge_pin_does_not_exist(board, tmp_path):
    kb = tmp_path / "knowledge"
    write_kb(kb, "house-style.md", sample_doc())
    r = run(board, "knowledge", "pin", "house-style", "--role", "backend",
            env=_kb_env(kb))
    assert r.returncode != 0
    assert "NO CHANGE WAS MADE" in (r.stderr + r.stdout)
    assert not (board / "briefs" / "roles").exists()


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
