"""T-1024: named automations over the existing schedule.json file."""

import json
from pathlib import Path

from test_schedule_wake import boot, run
from ticket_board.seat_schedule import public_rows

TOOL = Path(__file__).resolve().parents[1] / "tickets.py"


def test_automation_help_is_on_the_cli():
    src = TOOL.read_text()
    assert 'sub.add_parser("automation"' in src
    assert src.count('sub.add_parser("schedule"') == 1


def test_automation_add_list_inspect_disable(tmp_path):
    repo, env = boot(tmp_path)
    r = run(repo, "automation", "add", "nightly", "--every", "15m",
            "--to", "worker-a", env=env, tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr + r.stdout
    assert "nightly" in r.stdout and "worker-a" in r.stdout
    lst = run(repo, "automation", "list", env=env, tmp_path=tmp_path)
    assert lst.returncode == 0, lst.stderr
    assert "nightly" in lst.stdout
    ins = run(repo, "automation", "inspect", "nightly", env=env, tmp_path=tmp_path)
    assert ins.returncode == 0, ins.stderr
    assert "action    msg -> worker-a" in ins.stdout
    assert "enabled   yes" in ins.stdout
    assert "last      never" in ins.stdout
    dis = run(repo, "automation", "disable", "nightly", env=env, tmp_path=tmp_path)
    assert dis.returncode == 0, dis.stderr
    ins = run(repo, "automation", "inspect", "nightly", env=env, tmp_path=tmp_path)
    assert "enabled   no" in ins.stdout
    due = run(repo, "schedule", "--due", env=env, tmp_path=tmp_path)
    assert "no due schedules" in due.stdout


def test_due_records_ok_and_skip_reason(tmp_path):
    repo, env = boot(tmp_path)
    assert run(repo, "automation", "add", "poke-a", "--every", "15m",
               "--to", "worker-a", env=env, tmp_path=tmp_path).returncode == 0
    r = run(repo, "schedule", "--due", env=env, tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr + r.stdout
    ins = run(repo, "automation", "inspect", "poke-a", env=env, tmp_path=tmp_path)
    assert "ok" in ins.stdout
    assert "skipped   (none)" in ins.stdout
    data = json.loads((repo / ".tickets" / "schedule.json").read_text())
    rows = public_rows(data)
    poke = [x for x in rows if x["name"] == "poke-a"][0]
    assert poke["last_result"] == "ok"
    assert poke["last"]

    assert run(repo, "automation", "add", "ghost", "--every", "15m",
               "--to", "worker-a", env=env, tmp_path=tmp_path).returncode == 0
    sched = json.loads((repo / ".tickets" / "schedule.json").read_text())
    sched["seats"]["ghost"]["target"] = "missing-seat"
    (repo / ".tickets" / "schedule.json").write_text(json.dumps(sched, indent=2))
    r = run(repo, "schedule", "--due", env=env, tmp_path=tmp_path)
    assert "skip ghost" in r.stdout
    ins = run(repo, "automation", "inspect", "ghost", env=env, tmp_path=tmp_path)
    assert "skipped" in ins.stdout
    assert "not joined" in ins.stdout


def test_snapshot_includes_automations(tmp_path):
    repo, env = boot(tmp_path)
    run(repo, "automation", "add", "nightly", "--every", "1h",
        "--to", "worker-a", env=env, tmp_path=tmp_path)
    r = run(repo, "ui", "--json", env=env, tmp_path=tmp_path)
    assert r.returncode == 0, r.stderr + r.stdout
    data = json.loads(r.stdout)
    names = [a["name"] for a in data.get("automations") or []]
    assert "nightly" in names
    row = [a for a in data["automations"] if a["name"] == "nightly"][0]
    assert row["action"] == "msg" and row["target"] == "worker-a"
    assert row["last_result"] == ""
    assert "never" not in json.dumps(row) or row["last"] == ""
