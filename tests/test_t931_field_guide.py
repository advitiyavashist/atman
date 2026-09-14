"""T-931: Field Guide lessons on the knowledge graph, scoped prompt injection."""

import json
import os
from pathlib import Path

from test_byoa import board, run  # noqa: F401
from test_t620_knowledge_graph import graph_env, inherited_lines, write_graph

REPO = Path(__file__).resolve().parents[1]


def lesson(node_id, summary, applies_to, files=None, verification="verified"):
    return {
        "kind": "node", "id": node_id, "type": "lesson", "title": node_id,
        "summary": summary, "tags": ["lesson", "field-guide"],
        "applies_to": applies_to,
        "source": {"kind": "ticket", "ref": "T-931:" + node_id},
        "recorded_at": "2026-09-14T03:00:00Z", "owner": "test",
        "last_verified_at": "2026-09-14T03:00:00Z",
        "verification": verification, "confidence": 1.0,
        "stale_after_days": 90, "revision": 1,
        "data": {"files": files or [], "evidence": "T-931"},
    }


def test_bare_guide_still_prints_startup(board):
    r = run(board, "guide")
    assert r.returncode == 0, r.stderr
    for tool in ("claude", "codex", "cursor"):
        assert tool in r.stdout.lower()
    assert "guide add" not in r.stdout


def test_guide_add_and_retire_roundtrip(board, tmp_path):
    graph = write_graph(tmp_path / "graph", [])
    env = graph_env(graph)
    added = run(board, "guide", "add",
                "zsh does not word-split $VAR",
                "--evidence", "T-931",
                "--scope", "harness:zsh,role:ops",
                env=env)
    assert added.returncode == 0, added.stderr + added.stdout
    nid = [part for part in added.stdout.split() if part.startswith("lesson.")][0]
    assert nid.startswith("lesson.zsh-does-not-word-split")
    shown = run(board, "knowledge", "show", nid, env=env)
    assert shown.returncode == 0, shown.stderr
    rec = json.loads(shown.stdout)
    assert rec["type"] == "lesson"
    assert "zsh" in rec["applies_to"]
    retired = run(board, "guide", "retire", nid,
                  "--reason", "covered by a runbook", env=env)
    assert retired.returncode == 0, retired.stderr + retired.stdout
    shown = run(board, "knowledge", "show", nid, env=env)
    rec = json.loads(shown.stdout)
    assert rec["verification"] == "superseded"
    assert rec["data"]["retired_reason"] == "covered by a runbook"


def test_frozen_relevance_matching_ticket_gets_only_scoped_lessons(board, tmp_path):
    graph = write_graph(tmp_path / "graph", [
        lesson("lesson.codex-deaf", "Codex watchers go deaf after one run",
               ["codex", "backend"], ["tickets.py"]),
        lesson("lesson.full-suite", "Need a full clean-env suite vs main",
               ["verification"], ["tests/"]),
        lesson("lesson.zsh-split", "zsh does not word-split $VAR",
               ["zsh", "ops"]),
    ])
    env = graph_env(graph)
    assert run(board, "join", "backend-seat", "--roles", "backend",
               "--harness", "codex", env=env).returncode == 0
    prompt = run(board, "prompt", "--agent", "backend-seat",
                 "--extra", "repair tickets.py spawn resume", env=env)
    assert prompt.returncode == 0, prompt.stderr
    inherited = inherited_lines(prompt.stdout)
    ids = [line.split()[1] for line in inherited]
    assert "knowledge:lesson.codex-deaf" in ids
    assert "knowledge:lesson.full-suite" not in ids
    assert "knowledge:lesson.zsh-split" not in ids
    assert "(knowledge selected:" in prompt.stdout
    assert "chars)" in prompt.stdout


def test_frozen_relevance_unrelated_ui_ticket_gets_no_lessons(board, tmp_path):
    graph = write_graph(tmp_path / "graph", [
        lesson("lesson.codex-deaf", "Codex watchers go deaf after one run",
               ["codex", "backend"], ["tickets.py"]),
        lesson("lesson.full-suite", "Need a full clean-env suite vs main",
               ["verification"]),
        lesson("lesson.placeholder", "never write placeholder sounding evidence",
               ["planning"]),
    ])
    env = graph_env(graph)
    assert run(board, "join", "ui-seat", "--roles", "ui",
               "--harness", "cursor", env=env).returncode == 0
    prompt = run(board, "prompt", "--agent", "ui-seat",
                 "--extra", "redraw the work graph css", env=env)
    assert prompt.returncode == 0, prompt.stderr
    inherited = inherited_lines(prompt.stdout)
    assert inherited == []
    assert "lesson.codex-deaf" not in prompt.stdout
    assert "lesson.full-suite" not in prompt.stdout


def test_retired_lesson_is_not_auto_injected(board, tmp_path):
    graph = write_graph(tmp_path / "graph", [
        lesson("lesson.dead", "old advice", ["backend"], verification="superseded"),
    ])
    env = graph_env(graph)
    assert run(board, "join", "backend-seat", "--roles", "backend",
               "--harness", "claude", env=env).returncode == 0
    prompt = run(board, "prompt", "--agent", "backend-seat", env=env)
    assert "lesson.dead" not in prompt.stdout


def test_seeded_graph_stays_valid_and_verification_sees_full_suite_lesson(board):
    env = graph_env(REPO / "knowledge")
    valid = run(board, "knowledge", "validate", env=env)
    assert valid.returncode == 0, valid.stderr + valid.stdout
    assert run(board, "join", "verify-seat", "--roles", "verification",
               "--harness", "cursor", env=env).returncode == 0
    prompt = run(board, "prompt", "--agent", "verify-seat",
                 "--extra", "compare tests/ to sealed main", env=env)
    assert prompt.returncode == 0, prompt.stderr
    assert "knowledge:lesson.full-suite-vs-module-subset" in prompt.stdout
    assert "knowledge:lesson.zsh-no-word-split" not in prompt.stdout
    assert "(knowledge selected:" in prompt.stdout
