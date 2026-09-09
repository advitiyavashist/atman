"""T-620: separate repo-backed knowledge graph and bounded cross-harness inheritance."""

import json
import os
import subprocess
import sys
from pathlib import Path

from test_byoa import board, run  # noqa: F401


REPO = Path(__file__).resolve().parents[1]
TOOL = REPO / "tickets.py"


def graph_env(path=None):
    return {"ATMAN_KNOWLEDGE_DIR": str(path or (REPO / "knowledge"))}


def record(node_id, summary, *, verification="verified", confidence=1.0,
           verified_at="2026-09-09T03:00:00Z", stale_after=30,
           canonical_key=None, applies_to=None):
    value = {
        "kind": "node", "id": node_id, "type": "failure", "title": node_id,
        "summary": summary, "tags": ["gliner"],
        "applies_to": applies_to or ["detection"],
        "source": {"kind": "test", "ref": "fixture:" + node_id},
        "recorded_at": "2026-09-09T03:00:00Z", "owner": "test",
        "last_verified_at": verified_at, "verification": verification,
        "confidence": confidence, "stale_after_days": stale_after, "revision": 1,
    }
    if canonical_key:
        value["canonical_key"] = canonical_key
    return value


def write_graph(root, nodes, edges=(), budget=1200):
    root = Path(root)
    (root / "nodes").mkdir(parents=True)
    (root / "edges").mkdir(parents=True)
    (root / "manifest.json").write_text(json.dumps({
        "schema_version": 1, "name": "test", "context_budget_chars": budget,
    }))
    for node in nodes:
        (root / "nodes" / (node["id"] + ".json")).write_text(json.dumps(node))
    for edge in edges:
        (root / "edges" / (edge["id"] + ".json")).write_text(json.dumps(edge))
    return root


def inherited_lines(text):
    return [line for line in text.splitlines() if line.startswith("- knowledge:")]


def test_checked_in_graph_is_valid_and_exposes_verified_gliner_result(board):
    valid = run(board, "knowledge", "validate", env=graph_env())
    assert valid.returncode == 0, valid.stderr + valid.stdout
    assert "knowledge graph valid" in valid.stdout

    result = run(board, "knowledge", "query", "GLiNER T4 latency", "--json",
                 env=graph_env())
    assert result.returncode == 0, result.stderr + result.stdout
    nodes = {node["id"]: node for node in json.loads(result.stdout)["nodes"]}
    assert nodes["experiment.gliner-t613-onnx-t4"]["data"]["p95_ms"] == 8.351
    assert nodes["experiment.gliner-t613-native-t4"]["data"]["p95_ms"] == 13.771
    assert nodes["artifact.gliner-t613-result"]["data"]["job_id"] == \
        "fc-01M221DBPDJ6GN24EWCJJF0FQ5"


def test_two_harnesses_receive_same_referenced_skill_and_failure(board):
    assert run(board, "join", "claude-seat", "--roles", "detection",
               "--harness", "claude", "--knowledge-dir",
               str(REPO / "knowledge")).returncode == 0
    assert run(board, "join", "cursor-seat", "--roles", "detection",
               "--harness", "cursor", "--knowledge-dir",
               str(REPO / "knowledge")).returncode == 0
    reference = ("knowledge:skill.shared-knowledge "
                 "knowledge:failure.gliner-cpu-ort-shadow")
    claude = run(board, "prompt", "--agent", "claude-seat", "--extra", reference)
    cursor = run(board, "prompt", "--agent", "cursor-seat", "--extra", reference)
    assert claude.returncode == cursor.returncode == 0
    for marker in ("knowledge:skill.shared-knowledge",
                   "knowledge:failure.gliner-cpu-ort-shadow",
                   "onnxruntime-gpu[cuda,cudnn]==1.26.0"):
        assert marker in claude.stdout
        assert marker in cursor.stdout
    assert set(inherited_lines(claude.stdout)) == set(inherited_lines(cursor.stdout))


def test_new_agent_gets_failure_and_correct_runbook_without_ticket_copy(board):
    assert run(board, "join", "new-detector", "--roles", "detection",
               "--can", "gpu", "--harness", "custom:/bin/true").returncode == 0
    prompt = run(board, "prompt", "--agent", "new-detector", env=graph_env())
    assert prompt.returncode == 0, prompt.stderr + prompt.stdout
    assert "CPU ONNX Runtime shadowed" in prompt.stdout
    assert "uninstall both onnxruntime distributions" in prompt.stdout
    assert "onnxruntime-gpu[cuda,cudnn]==1.26.0" in prompt.stdout
    assert not (board / "knowledge").exists(), "the graph must not be stored in .tickets"


def test_graph_cannot_be_configured_inside_the_ticket_board(board):
    trapped = write_graph(board / "knowledge", [])
    joined = run(board, "join", "trapped", "--roles", "docs",
                 "--knowledge-dir", str(trapped))
    assert joined.returncode != 0
    assert "outside the ticket board" in joined.stderr + joined.stdout
    queried = run(board, "knowledge", "list", env=graph_env(trapped))
    assert queried.returncode != 0
    assert "outside the ticket board" in queried.stderr + queried.stdout


def test_ticket_brief_stores_only_a_knowledge_reference(board):
    attached = run(
        board, "brief", "--ticket", "T-001", "--knowledge",
        "failure.gliner-cpu-ort-shadow", env=graph_env(), agent="master",
    )
    assert attached.returncode == 0, attached.stderr + attached.stdout
    ticket = json.loads((board / "T-001.json").read_text())
    notes = [n["text"] for n in ticket["notes"] if n.get("kind") == "context"]
    assert notes == ["knowledge:failure.gliner-cpu-ort-shadow"]
    assert "CPU ONNX Runtime shadowed" not in json.dumps(ticket)
    assert run(board, "join", "worker", "--roles", "docs", "--knowledge-dir",
               str(REPO / "knowledge")).returncode == 0
    claimed = run(board, "next", "--owner", "worker")
    assert claimed.returncode == 0, claimed.stderr + claimed.stdout
    assert "knowledge:failure.gliner-cpu-ort-shadow" in claimed.stdout
    assert "onnxruntime-gpu[cuda,cudnn]==1.26.0" in claimed.stdout


def test_native_session_hooks_use_the_same_bounded_selector(board):
    graph = str(REPO / "knowledge")
    assert run(board, "join", "hook-seat", "--roles", "detection",
               "--knowledge-dir", graph).returncode == 0

    claude = run(board, "board", agent="hook-seat")
    assert "Inherited knowledge" in claude.stdout
    assert "failure.gliner-cpu-ort-shadow" in claude.stdout

    env = dict(os.environ, TICKETS_DIR=str(board), TICKET_AGENT="hook-seat",
               HOME=str(board.parent.parent / "home"))
    event = json.dumps({"hook_event_name": "SessionStart", "cwd": str(board.parent)})
    codex = subprocess.run(
        [sys.executable, str(TOOL), "codex-hook", "--agent", "hook-seat"],
        input=event, capture_output=True, text=True, env=env, cwd=board.parent,
    )
    assert codex.returncode == 0, codex.stderr + codex.stdout
    codex_context = json.loads(codex.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "failure.gliner-cpu-ort-shadow" in codex_context

    installed = run(board, "hooks", "cursor", "--agent", "hook-seat", "--force")
    assert installed.returncode == 0, installed.stderr + installed.stdout
    hook = board.parent / ".cursor" / "hooks" / "tickets-board.py"
    cursor = subprocess.run(
        [sys.executable, str(hook)], input=event, capture_output=True, text=True,
        env=env, cwd=board.parent,
    )
    assert cursor.returncode == 0, cursor.stderr + cursor.stdout
    assert "failure.gliner-cpu-ort-shadow" in json.loads(cursor.stdout)["additional_context"]


def test_stale_label_dedup_and_budget_are_deterministic(board, tmp_path):
    weak = record("failure.weak", "old duplicate", verification="inferred",
                  confidence=0.4, canonical_key="failure.same")
    strong = record("failure.strong", "current duplicate " + ("x" * 900),
                    canonical_key="failure.same")
    stale = record("failure.stale", "known stale failure", verified_at="2020-01-01T00:00:00Z")
    graph = write_graph(tmp_path / "knowledge", [weak, strong, stale], budget=500)
    result = run(board, "knowledge", "query", "gliner", "--max-chars", "500",
                 env=graph_env(graph))
    assert result.returncode == 0, result.stderr + result.stdout
    assert len(result.stdout.rstrip("\n")) <= 500
    assert "failure.weak" not in result.stdout
    assert "failure.strong" in result.stdout
    assert "knowledge budget reached" in result.stdout
    stale_result = run(board, "knowledge", "list", "--stale", "--json",
                       env=graph_env(graph))
    assert [row["id"] for row in json.loads(stale_result.stdout)] == ["failure.stale"]


def test_authoring_validates_graph_and_update_increments_revision(board, tmp_path):
    graph = write_graph(tmp_path / "knowledge", [])
    source = tmp_path / "fact.json"
    fact = record("failure.authored", "first observation")
    source.write_text(json.dumps(fact))
    added = run(board, "knowledge", "add", str(source), env=graph_env(graph))
    assert added.returncode == 0, added.stderr + added.stdout
    target = graph / "nodes" / "failure.authored.json"
    assert json.loads(target.read_text())["revision"] == 1

    fact["summary"] = "corrected observation"
    fact["last_verified_at"] = "2026-09-09T04:00:00Z"
    source.write_text(json.dumps(fact))
    updated = run(board, "knowledge", "update", str(source), env=graph_env(graph))
    assert updated.returncode == 0, updated.stderr + updated.stdout
    stored = json.loads(target.read_text())
    assert stored["revision"] == 2
    assert stored["summary"] == "corrected observation"

    bad = record("failure.bad", "bad")
    bad["source"] = {}
    source.write_text(json.dumps(bad))
    refused = run(board, "knowledge", "add", str(source), env=graph_env(graph))
    assert refused.returncode != 0
    assert "NO CHANGE WAS MADE" in refused.stderr + refused.stdout
    assert not (graph / "nodes" / "failure.bad.json").exists()

    (graph / "nodes" / "corrupt.json").write_text("{}")
    listed = run(board, "knowledge", "list", env=graph_env(graph))
    assert listed.returncode != 0
    assert "knowledge graph invalid" in listed.stderr + listed.stdout


def test_legacy_markdown_catalog_remains_read_only_and_not_injected(board, tmp_path):
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / "old.md").write_text(
        "---\nid: old\ntitle: Old\ntags: [docs]\n---\n\nLEGACY-ONLY\n")
    assert run(board, "join", "reader", "--roles", "docs").returncode == 0
    listed = run(board, "knowledge", "--json", env=graph_env(legacy))
    assert listed.returncode == 0
    assert json.loads(listed.stdout)[0]["id"] == "old"
    prompt = run(board, "prompt", "--agent", "reader", env=graph_env(legacy))
    assert "LEGACY-ONLY" not in prompt.stdout
