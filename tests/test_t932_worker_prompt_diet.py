"""T-932: worker show/prompt is ticket-scoped; leadership keeps the wide view."""

import json

from test_byoa import board, run  # noqa: F401


EPIC_BODY = (
    "Product direction (user, 2026-09-07): Atman is the runtime for teams of "
    "arbitrary agents -- bring the agents you already use. Evolution: V0 rules; "
    "V1 capability; V2 learned router. Boundaries: not another multi-agent "
    "framework. Also lists many unrelated siblings that workers should not see."
)


def _seed_epic_and_tickets(board):
    created = run(board, "epic", "create", "Runtime epic", "--body", EPIC_BODY)
    assert created.returncode == 0, created.stderr
    eid = created.stdout.split()[1]
    specs = [
        ("Fix tickets.py spawn resume", "Change session_adapters.py and tests/test_t875_spawn_resume.py"),
        ("Rewrite docs/knowledge/howto.md", "Keep knowledge/nodes facts honest"),
        ("Repair tests/test_wakeup.py prompt text", "Do not drop tickets clear prohibition"),
        ("Trim src/ticket_board/cli.py show dump", "Keep compact rules"),
        ("Measure trajectories.jsonl prompt_chars", "Log sections per run"),
    ]
    ids = []
    for title, body in specs:
        r = run(board, "create", title, "--role", "backend", "--epic", eid,
                "--body", body)
        assert r.returncode == 0, r.stderr
        tid = [part for part in r.stdout.split() if part.startswith("T-")][0]
        ids.append(tid)
    return ids


def test_worker_show_drops_epic_prose_keeps_ticket_and_rules(board):
    ids = _seed_epic_and_tickets(board)
    assert run(board, "join", "worker", "--roles", "backend").returncode == 0
    assert run(board, "join", "boss", "--roles", "master").returncode == 0
    worker = run(board, "show", ids[0], env={"TICKET_AGENT": "worker"})
    boss = run(board, "show", ids[0], env={"TICKET_AGENT": "boss"})
    assert worker.returncode == boss.returncode == 0, worker.stderr + boss.stderr
    assert "session_adapters.py" in worker.stdout
    assert "Rules (compact)" in worker.stdout
    assert "never tickets clear" in worker.stdout
    assert "also in this epic" not in worker.stdout
    assert EPIC_BODY.split("Evolution")[0].strip() not in worker.stdout or "V0 rules" not in worker.stdout
    assert "also in this epic" in boss.stdout
    assert "V0 rules" in boss.stdout
    assert len(worker.stdout) < len(boss.stdout)


def test_five_ticket_before_after_context_size(board):
    ids = _seed_epic_and_tickets(board)
    assert run(board, "join", "worker", "--roles", "backend").returncode == 0
    assert run(board, "join", "boss", "--roles", "master").returncode == 0
    rows = []
    for tid in ids:
        worker = run(board, "show", tid, env={"TICKET_AGENT": "worker"})
        boss = run(board, "show", tid, env={"TICKET_AGENT": "boss"})
        assert worker.returncode == 0 and boss.returncode == 0
        assert "Rules (compact)" in worker.stdout
        rows.append((tid, len(worker.stdout), len(boss.stdout)))
        assert len(worker.stdout) < len(boss.stdout)
    # All five worker views are smaller than the leadership dump.
    assert all(w < b for _, w, b in rows)
    # Trajectory recorded prompt_chars + sections.
    log = (board / "trajectories.jsonl").read_text()
    events = [json.loads(line) for line in log.splitlines() if line.strip()]
    prompts = [e for e in events if e.get("kind") == "prompt"]
    assert prompts
    assert any(e.get("prompt_view") == "compact" and e.get("prompt_chars", 0) > 0
               for e in prompts)
    assert any(e.get("prompt_view") == "wide" for e in prompts)


def _prompt_manifests(board):
    events = [json.loads(line) for line in
              (board / "trajectories.jsonl").read_text().splitlines() if line.strip()]
    out = []
    for e in events:
        if e.get("kind") != "prompt" or not e.get("prompt_manifest"):
            continue
        out.append(json.loads(e["prompt_manifest"]))
    return out


def test_prompt_manifest_has_revisions_sections_and_null_tokens(board, tmp_path):
    from test_t620_knowledge_graph import graph_env, write_graph
    from test_t931_field_guide import lesson

    stale = lesson("lesson.old-advice", "stale scoped advice for tickets.py",
                   ["backend"], ["tickets.py"])
    stale["last_verified_at"] = "2020-01-01T00:00:00Z"
    stale["stale_after_days"] = 1
    stale["revision"] = 4
    fresh = lesson("lesson.codex-deaf", "Codex watchers go deaf after one run",
                   ["backend"], ["tickets.py"])
    fresh["revision"] = 2
    graph = write_graph(tmp_path / "graph", [fresh, stale], budget=4000)
    env = graph_env(graph)
    assert run(board, "join", "backend-seat", "--roles", "backend",
               "--harness", "codex", env=env).returncode == 0
    r = run(board, "prompt", "--agent", "backend-seat",
            "--extra", "repair tickets.py knowledge:does.not.exist", env=env)
    assert r.returncode == 0, r.stderr
    assert "never run `tickets clear`" in r.stdout
    manifests = _prompt_manifests(board)
    assert manifests
    man = manifests[-1]
    ids = {row["id"]: row["revision"] for row in man["knowledge"]}
    assert ids.get("lesson.codex-deaf") == 2
    assert ids.get("lesson.old-advice") == 4
    assert "policy" in man["section_chars"]
    assert man["section_chars"]["policy"] > 0
    assert man["budget_chars"] == 4000
    assert "does.not.exist" in man["missing"]
    assert "lesson.old-advice" in man["stale"]
    assert man["tokens"] is None or isinstance(man["tokens"], int)
    assert man["view"] in ("compact", "wide")
    assert man["chars"] == len(r.stdout.rstrip("\n")) or man["chars"] == len(r.stdout)


def test_prompt_manifest_marks_truncated_knowledge(board, tmp_path):
    from test_t620_knowledge_graph import graph_env, write_graph
    from test_t931_field_guide import lesson

    nodes = [
        lesson("lesson.long-a", "A" * 300, ["backend"], ["tickets.py"]),
        lesson("lesson.long-b", "B" * 300, ["backend"], ["tickets.py"]),
        lesson("lesson.long-c", "C" * 300, ["backend"], ["tickets.py"]),
    ]
    graph = write_graph(tmp_path / "graph", nodes, budget=500)
    env = graph_env(graph)
    assert run(board, "join", "backend-seat", "--roles", "backend",
               "--harness", "codex", env=env).returncode == 0
    r = run(board, "prompt", "--agent", "backend-seat",
            "--extra", "repair tickets.py", env=env)
    assert r.returncode == 0, r.stderr
    man = _prompt_manifests(board)[-1]
    assert man["truncated"] is True
    assert man["budget_chars"] == 500


def test_worker_prompt_still_has_required_instructions(board):
    assert run(board, "join", "doc", "--roles", "docs").returncode == 0
    r = run(board, "prompt", "--agent", "doc", "--extra", "EXTRA LINE")
    assert r.returncode == 0, r.stderr
    assert "You are doc" in r.stdout
    assert "tickets review" in r.stdout
    assert "tickets clear" in r.stdout
    assert "EXTRA LINE" in r.stdout
    log = (board / "trajectories.jsonl").read_text()
    assert '"kind": "prompt"' in log
