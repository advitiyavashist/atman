"""Independent supply/provenance cases; no live provider or tokenizer calls."""

import ast
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from test_byoa import board, run  # noqa: F401
from test_t620_knowledge_graph import graph_env, inherited_lines, record, write_graph
from test_t931_field_guide import lesson


def manifests(board):
    events = [json.loads(line) for line in (board / "trajectories.jsonl").read_text().splitlines()]
    return [json.loads(event["prompt_manifest"]) for event in events
            if event.get("kind") == "prompt" and event.get("prompt_manifest")]


@pytest.mark.parametrize("kind", ["out_of_scope", "retired"])
def test_graph_edges_cannot_bypass_lesson_eligibility(board, tmp_path, kind):
    ui = record("decision.ui-layout", "UI layout uses formation dots", applies_to=["ui"])
    ui.update(type="decision", tags=["ui"])
    excluded = lesson("lesson.excluded", "EXCLUDED LESSON CANARY",
        ["backend"] if kind == "out_of_scope" else ["ui"],
        ["backend-only.py"], verification="verified" if kind == "out_of_scope" else "superseded")
    edge = {"kind": "edge", "id": "edge.ui-lesson", "type": "requires",
            "from": ui["id"], "to": excluded["id"], "owner": "test",
            "source": {"kind": "test", "ref": "fixture:edge.ui-lesson"},
            "recorded_at": "2026-09-09T03:00:00Z", "last_verified_at": "2026-09-09T03:00:00Z",
            "verification": "verified", "confidence": 1.0, "stale_after_days": 90}
    graph = write_graph(tmp_path / "graph", [ui, excluded], [edge], budget=2000)
    env = graph_env(graph)
    assert run(board, "knowledge", "validate", env=env).returncode == 0
    assert run(board, "join", "ui-worker", "--roles", "ui", "--harness", "cursor", env=env).returncode == 0
    output = run(board, "prompt", "--agent", "ui-worker", "--extra", "Fix UI layout", env=env)
    assert output.returncode == 0, output.stderr
    assert "EXCLUDED LESSON CANARY" not in output.stdout


def truncated_prompt(board, tmp_path):
    nodes = [lesson("lesson.long-" + key, key * 300, ["backend"], ["tickets.py"])
             for key in ["a", "b", "c"]]
    env = graph_env(write_graph(tmp_path / "graph", nodes, budget=500))
    assert run(board, "join", "backend-worker", "--roles", "backend", env=env).returncode == 0
    output = run(board, "prompt", "--agent", "backend-worker", "--extra", "repair tickets.py", env=env)
    assert output.returncode == 0, output.stderr
    return output.stdout, manifests(board)[-1]


def test_receipt_distinguishes_retrieved_from_rendered_knowledge(board, tmp_path):
    output, manifest = truncated_prompt(board, tmp_path)
    rendered_ids = {line.split()[1].removeprefix("knowledge:") for line in inherited_lines(output)}
    delivered = manifest.get("rendered_knowledge", manifest["knowledge"])
    assert {item["id"] for item in delivered} == rendered_ids


def test_knowledge_budget_includes_its_emitted_selection_footer(board, tmp_path):
    output, manifest = truncated_prompt(board, tmp_path)
    assert manifest["section_chars"]["lessons"] <= manifest["budget_chars"]


def test_non_model_token_count_has_explicit_tokenizer_provenance(monkeypatch):
    source = ast.parse((Path(__file__).resolve().parents[1] / "tickets.py").read_text())
    names = {"_prompt_token_count", "_infer_prompt_sections", "_log_prompt_diet"}
    functions = [node for node in source.body if isinstance(node, ast.FunctionDef) and node.name in names]
    events = []
    scope = {"json": json, "traj_event": lambda *args, **kwargs: events.append(kwargs)}
    exec(compile(ast.Module(body=functions, type_ignores=[]), "candidate-prompt-functions", "exec"), scope)
    monkeypatch.setitem(sys.modules, "tiktoken", SimpleNamespace(
        get_encoding=lambda name: SimpleNamespace(encode=lambda text: [1, 2, 3])))
    scope["_log_prompt_diet"]("disposable-board", "claude-worker", "synthetic context", "compact")
    manifest = json.loads(events[-1]["prompt_manifest"])
    assert manifest["tokens"] is None or manifest.get("tokenizer") or manifest.get("token_encoding")
