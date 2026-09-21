"""T-1118 / Phase 1S: `atm project split` -- one board per project (spec 4.11).

The fixture board is built to mirror the real shapes the migration has to
survive, because a split that only works on a tidy board proves nothing:
multi-repo tickets, unattributed tickets (live and done), cross-repo edges in
all three release states, seats homed in more than one project, a rotated
message archive, and a structured accept with its receipts.

Every test drives the engine through the same path the CLI uses.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from ticket_board import project_split as ps  # noqa: E402
from ticket_board import work_view as wv  # noqa: E402

TOOL = ROOT / "tickets.py"
ATMAN = "https://github.com/advitiyavashist/atman.git"
STEER = "https://github.com/advitiyavashist/steer.git"
HEAD_A = "a" * 40
HEAD_B = "b" * 40


# --------------------------------------------------------------------------
# fixture
# --------------------------------------------------------------------------

def _w(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _ticket(board, tid, **fields):
    rec = {"id": tid, "title": tid + " title", "body": "", "role": "backend",
           "status": "open", "deps": [], "priority": 2, "epic": "", "sprint": "",
           "needs": [], "owner": "", "created": "2026-09-01T00:00:00Z",
           "updated": "2026-09-10T00:00:00Z", "notes": []}
    rec.update(fields)
    # indent=2 with a trailing newline is exactly what `atm` writes, so the
    # byte-identity assertions below compare real board bytes.
    _w(board / (tid + ".json"), json.dumps(rec, indent=2) + "\n")
    return rec


def _jsonl(path, records):
    _w(path, "".join(json.dumps(r) + "\n" for r in records))


@pytest.fixture()
def shared(tmp_path):
    """A shared board holding two attributed projects plus unattributed work."""
    board = tmp_path / "shared" / ".tickets"
    board.mkdir(parents=True)

    # --- atman ---------------------------------------------------------
    _ticket(board, "T-100", repo=ATMAN, status="done", owner="ann",
            review_head=HEAD_A, epic="E-001",
            review_events=[{"kind": "accept", "by": "bo", "sha": HEAD_A,
                            "at": "2026-09-09T00:00:00Z", "notes": "ok"}])
    _ticket(board, "T-101", repo=ATMAN, status="done", owner="ann", epic="E-001")
    _ticket(board, "T-102", repo=ATMAN, status="open", epic="E-001")
    # a live atman ticket depending on a released atman parent: same project,
    # so it must keep a plain `deps` entry and gain no snapshot.
    _ticket(board, "T-103", repo=ATMAN, status="open", deps=["T-100"], epic="E-001")

    # --- steer, with every cross-project edge kind ----------------------
    # released parent T-100 (accepted), done-unaccepted parent T-101, open
    # parent T-102.
    _ticket(board, "T-200", repo=STEER, status="open", deps=["T-100"],
            owner="", epic="E-002")
    _ticket(board, "T-201", repo=STEER, status="open", deps=["T-101"], epic="E-002")
    _ticket(board, "T-202", repo=STEER, status="open", deps=["T-102"], epic="E-002")
    _ticket(board, "T-203", repo=STEER, status="claimed", owner="cy", epic="E-002")

    # --- unattributed ---------------------------------------------------
    _ticket(board, "T-300", status="done", owner="ann", epic="E-001")   # may stay archived
    _ticket(board, "T-301", status="open")                              # live: must be assigned

    # --- seats: `ann` works in both projects, `cy` only in steer --------
    _w(board / "agents" / "ann.json", json.dumps(
        {"owner": "ann", "inbox_seen": "2026-09-10T00:00:00Z",
         "inbox_seen_ids": ["msg_a"], "ticket": "", "wake_delivery": "direct"},
        indent=2))
    _w(board / "agents" / "cy.json", json.dumps(
        {"owner": "cy", "inbox_seen": "2026-09-08T00:00:00Z",
         "inbox_seen_ids": [], "ticket": "T-203"}, indent=2))
    _w(board / "agents" / "gone.json", json.dumps({"owner": "gone"}, indent=2))
    _w(board / "workforce.json", json.dumps(
        {"ann": {"tool": "claude"}, "cy": {"tool": "codex"},
         "gone": {"tool": "cursor"}}, indent=2))
    _w(board / "roles.json", json.dumps({"ann": ["backend"], "cy": ["backend"]}, indent=2))
    _w(board / "aliases.json", json.dumps({"lead": "ann", "ghost": "gone"}, indent=2))
    _w(board / "retired.json", json.dumps(
        {"old-seat": {"at": "2026-09-01T00:00:00Z", "alias": "ann",
                      "durable_roles": []}}, indent=2))
    _w(board / "coordination" / "state.json", json.dumps(
        {"schema": 1, "agents": {"ann": {"agent_id": "ann"},
                                 "cy": {"agent_id": "cy"},
                                 "gone": {"agent_id": "gone"}}}, indent=2))
    _w(board / "master.json", json.dumps({"owner": "ann", "cos": "cy"}))
    _w(board / "objective.json", json.dumps({"text": "ship it"}))
    _w(board / "MASTER.md", "# decisions\n\n- 2026-09-01 started\n")
    _w(board / "CONTEXT.md", "shared context\n")
    _w(board / "merge.json", json.dumps({"test": "pytest -q"}))
    _w(board / "provider_usage.json", json.dumps({"claude": {"checked_at": "x"}}))
    _w(board / "briefs" / "ann.md", "# Brief for ann\n")
    _w(board / "epics" / "E-001.json", json.dumps({"id": "E-001", "title": "atman epic"}, indent=2))
    _w(board / "epics" / "E-002.json", json.dumps({"id": "E-002", "title": "steer epic"}, indent=2))

    # --- messages: live + a rotated archive -----------------------------
    _jsonl(board / "messages.2026-09-08.jsonl", [
        {"id": "msg_a", "at": "2026-09-08T01:00:00Z", "from": "ann", "to": "cy",
         "re": "T-100", "text": "atman ticket mail"},
        {"id": "msg_b", "at": "2026-09-08T02:00:00Z", "from": "gone", "to": "",
         "re": "", "text": "broadcast from a seat homed nowhere"},
    ])
    _jsonl(board / "messages.jsonl", [
        {"id": "msg_c", "at": "2026-09-10T01:00:00Z", "from": "ann", "to": "cy",
         "re": "", "text": "directed, no ticket: lands in both seats' homes"},
        {"id": "msg_d", "at": "2026-09-10T02:00:00Z", "from": "cy", "to": "",
         "re": "T-203", "text": "steer ticket mail"},
        {"id": "msg_e", "at": "2026-09-10T03:00:00Z", "from": "ann", "to": "",
         "re": "T-301", "text": "mail about an unattributed ticket"},
    ])
    _jsonl(board / "trajectories.jsonl", [
        {"v": 1, "at": "2026-09-10T01:00:00Z", "kind": "claim",
         "ticket": "T-103", "agent": "ann"},
        {"v": 1, "at": "2026-09-10T02:00:00Z", "kind": "msg",
         "ticket": "", "agent": "cy"},
        {"v": 1, "at": "2026-09-10T03:00:00Z", "kind": "msg",
         "ticket": "", "agent": "gone"},
    ])
    return board


@pytest.fixture()
def homes(tmp_path):
    return {"atman": str(tmp_path / "atman" / ".tickets"),
            "steer": str(tmp_path / "steer-project" / ".tickets")}


def make_plan(shared, homes, assign=None, allow_pending=False):
    """Propose, then apply the edits the operator is required to make."""
    src = ps.SourceBoard(str(shared))
    plan = ps.propose(str(shared), generated="2026-09-21T12:00:00Z", registry={})
    for slug, path in homes.items():
        plan["projects"][slug]["board"] = path
    # Leadership is per project and the operator's call. Propose carries the
    # shared board's master and CoS into every project; here the operator
    # keeps the CoS on steer only, which is also what makes `cy` a
    # single-project seat next to the multi-project `ann`.
    plan["projects"]["atman"]["cos"] = ""
    for tid, slug in (assign or {}).items():
        plan["tickets"][tid] = slug
    plan["allow_pending_external"] = allow_pending
    # Seat homes are computed from the attribution, so recompute after edits.
    plan["seats"] = ps._seat_homes(src, plan["tickets"], plan["projects"],
                                   plan["generated"])
    plan["cross_project_edges"] = ps.cross_project_edges(src, plan["tickets"])
    plan["summary"] = ps.summarize(src, plan)
    return plan


def good_plan(shared, homes):
    """A plan with no refusals: the live unattributed ticket is attributed and
    the open cross-project parent is moved in with its child."""
    return make_plan(shared, homes,
                     assign={"T-301": "steer", "T-102": "steer", "T-103": "steer"})


def run_apply(shared, plan, registry=None):
    src = ps.SourceBoard(str(shared))
    return ps.apply_split(src, plan, registry_before=registry or {},
                          at="2026-09-21T12:30:00Z")


# --------------------------------------------------------------------------
# 1. the audit hook
# --------------------------------------------------------------------------

def _tree(path):
    out = {}
    for root, dirs, names in os.walk(path):
        dirs[:] = sorted(dirs)
        for n in sorted(names):
            p = Path(root) / n
            out[str(p.relative_to(path))] = p.read_bytes()
    return out


def test_split_dry_run_writes_nothing(shared, homes, tmp_path):
    plan = good_plan(shared, homes)
    before = _tree(shared)
    report = ps.dry_run(ps.SourceBoard(str(shared)), plan, registry_before={})
    assert report["refusals"] == [], report["refusals"]
    assert _tree(shared) == before, "the dry run wrote to the source board"
    for path in homes.values():
        assert not os.path.exists(path), "the dry run created %s" % path

    result = run_apply(shared, plan)
    assert result["ok"], result["refusals"]
    # The manifest the dry run printed is the manifest apply wrote -- equal as
    # whole objects, not merely in the fields this test happens to name.
    assert result["manifest"] == report["manifest"]
    written = json.loads((Path(homes["atman"]) / ps.MANIFEST_NAME).read_text())
    assert written == report["manifest"]


# --------------------------------------------------------------------------
# 2. nothing is lost
# --------------------------------------------------------------------------

def test_split_preserves_every_record(shared, homes):
    plan = good_plan(shared, homes)
    source_bytes = dict((tid, (shared / (tid + ".json")).read_bytes())
                        for tid in ps.SourceBoard(str(shared)).tickets)
    result = run_apply(shared, plan)
    assert result["ok"], result["refusals"]
    man = result["manifest"]

    # (a) every ticket file is byte-identical in its project, except the ones
    # §4.11 rewrites on purpose: a cross-project dependency becomes an
    # external_deps snapshot. Those keep every other field unchanged.
    rewritten = set(man["external_deps"])
    for tid, slug in plan["tickets"].items():
        if slug not in homes:
            assert not (Path(homes["atman"]) / (tid + ".json")).exists()
            assert not (Path(homes["steer"]) / (tid + ".json")).exists()
            assert (shared / (tid + ".json")).exists(), "archive lost %s" % tid
            continue
        landed = Path(homes[slug]) / (tid + ".json")
        assert landed.exists(), "%s never landed in %s" % (tid, slug)
        if tid in rewritten:
            old = json.loads(source_bytes[tid])
            new = json.loads(landed.read_bytes())
            old.pop("deps"), new.pop("deps"), new.pop("external_deps")
            assert old == new, "%s changed beyond its deps" % tid
        else:
            assert landed.read_bytes() == source_bytes[tid]

    # (b) every message and trajectory line appears in at least one target or
    # the archive, and the union of targets plus the archive equals the
    # source with nothing lost and nothing invented.
    for name in ["messages.2026-09-08.jsonl", "messages.jsonl", "trajectories.jsonl"]:
        src_lines = [ln for ln in (shared / name).read_text().splitlines() if ln.strip()]
        routes = man["routing"]["files"][name]
        assert len(routes) == len(src_lines)
        seen = {}
        for slug, home in homes.items():
            p = Path(home) / name
            seen[slug] = [ln for ln in (p.read_text().splitlines() if p.exists() else [])
                          if ln.strip()]
        union = []
        for i, line in enumerate(src_lines):
            targets = man["routing"]["legend"][routes[i]]
            for slug in targets:
                assert line in seen[slug], "%s line %d missing from %s" % (name, i, slug)
            if not targets:
                union.append(line)  # archive-only, still on the frozen board
            else:
                union.append(line)
        assert union == src_lines
        # no target invented a line
        for slug in homes:
            for line in seen[slug]:
                assert line in src_lines

    # (c) the shared board itself is untouched apart from the one marker.
    assert sorted(_tree(shared)) == sorted(
        list(source_only_tree(shared)) + [ps.SPLIT_MARKER])


def source_only_tree(shared):
    return [k for k in _tree(shared) if k != ps.SPLIT_MARKER]


# --------------------------------------------------------------------------
# 3. accepts and receipts
# --------------------------------------------------------------------------

def test_split_keeps_accepts_and_receipts(shared, homes):
    plan = good_plan(shared, homes)
    assert run_apply(shared, plan)["ok"]
    before = json.loads((shared / "T-100.json").read_text())
    after = json.loads((Path(homes["atman"]) / "T-100.json").read_text())
    assert after["review_events"] == before["review_events"]
    assert after["review_head"] == before["review_head"]
    assert wv.structured_accept(after) and wv.dep_released(after)

    # `msg_a` is an atman-ticket message, so it lands in atman only; its id is
    # unchanged, and ann's agent record carries `inbox_seen_ids` verbatim, so
    # the delivery label computed on the new board is the one computed on the
    # old board.
    labels = {}
    for board in (shared, Path(homes["atman"])):
        msg = [json.loads(ln) for ln in
               (board / "messages.2026-09-08.jsonl").read_text().splitlines() if ln.strip()]
        rec = [m for m in msg if m["id"] == "msg_a"][0]
        labels[str(board)] = _delivery_label(board, rec)
    assert len(set(labels.values())) == 1, labels
    # and the label is a real per-recipient receipt, not the broadcast
    # fallback -- otherwise the equality above would hold vacuously.
    one = json.loads(list(labels.values())[0])
    assert one["status"] == "direct" and one["receipts"][0]["agent"] == "cy"
    assert one["receipts"][0]["label"]
    assert json.loads((Path(homes["atman"]) / "agents" / "ann.json").read_text()) == \
        json.loads((shared / "agents" / "ann.json").read_text())


def _delivery_label(board, msg):
    """Run tickets.py's own delivery labeller against a board."""
    out = subprocess.run(
        [sys.executable, "-c",
         "import importlib.util,sys,json;"
         "spec=importlib.util.spec_from_file_location('t', sys.argv[1]);"
         "m=importlib.util.module_from_spec(spec);"
         "sys.modules['t']=m; spec.loader.exec_module(m);"
         "print(json.dumps(m._message_delivery(sys.argv[2], json.loads(sys.argv[3]))))",
         str(TOOL), str(board), json.dumps(msg)],
        capture_output=True, text=True,
        env=dict(os.environ, TICKETS_DIR=str(board), TICKET_AGENT="tester"))
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


# --------------------------------------------------------------------------
# 4 and 5. refusals
# --------------------------------------------------------------------------

def test_split_refuses_unassigned_live_ticket(shared, homes):
    # T-301 is open and has no repo. Nothing is auto-attributed, so the plan
    # must refuse until the operator gives it a project.
    plan = make_plan(shared, homes, assign={"T-102": "steer", "T-103": "steer"})
    refusals = ps.validate(ps.SourceBoard(str(shared)), plan)
    live = [r for r in refusals if r["kind"] == "unassigned-live"]
    assert [r["ticket"] for r in live] == ["T-301"]
    # the done unattributed ticket may stay in the archive without refusing
    assert not any(r.get("ticket") == "T-300" for r in refusals)
    assert not run_apply(shared, plan)["ok"]
    assert not os.path.exists(homes["atman"])
    assert not (shared / ps.SPLIT_MARKER).exists()

    plan["tickets"]["T-301"] = "steer"
    assert ps.validate(ps.SourceBoard(str(shared)), plan) == []


def test_split_reports_an_occupied_target_as_a_shadow(shared, homes, tmp_path):
    # §4.11 names this case: the local atman/.tickets already holds a few
    # tickets and messages. The dry run must report it as a shadow with what
    # it holds, not write a project board on top of it.
    existing = Path(homes["atman"])
    _ticket(existing, "T-900", repo=ATMAN)
    _jsonl(existing / "messages.jsonl", [
        {"id": "msg_old", "at": "2026-08-01T00:00:00Z", "from": "ann",
         "to": "", "re": "", "text": "on the shadow board"}])
    _w(existing / "agents" / "zed.json", json.dumps({"owner": "zed"}))

    plan = good_plan(shared, homes)
    refusals = ps.validate(ps.SourceBoard(str(shared)), plan)
    shadow = [r for r in refusals if r["kind"] == "project-board-shadow"]
    assert len(shadow) == 1 and shadow[0]["project"] == "atman"
    assert shadow[0]["shadow"] == {"path": str(existing), "tickets": 1,
                                   "messages": 1, "agents": 1}
    assert "atm board-archive-shadow" in shadow[0]["text"]
    assert not run_apply(shared, plan)["ok"]
    # nothing was written over it, and the shared board is not frozen
    assert (existing / "T-900.json").exists()
    assert not (existing / "T-100.json").exists()
    assert not (shared / ps.SPLIT_MARKER).exists()


def test_split_refuses_with_active_run(shared, homes):
    plan = good_plan(shared, homes)
    (shared / "agents" / "ann.run").write_text(json.dumps({"active": True, "pid": 1}))
    result = run_apply(shared, plan)
    assert not result["ok"]
    assert [r["kind"] for r in result["refusals"]] == ["active-run"]
    assert "atm spawn --stop" in result["refusals"][0]["text"]
    assert not os.path.exists(homes["atman"])

    (shared / "agents" / "ann.run").write_text(json.dumps({"active": False}))
    assert run_apply(shared, plan)["ok"]


def test_split_refuses_second_run_on_a_split_board(shared, homes):
    plan = good_plan(shared, homes)
    assert run_apply(shared, plan)["ok"]
    again = ps.apply_split(ps.SourceBoard(str(shared)), plan, registry_before={})
    assert not again["ok"]
    assert any(r["kind"] == "already-split" for r in again["refusals"])


# --------------------------------------------------------------------------
# 6. cross-project dependencies
# --------------------------------------------------------------------------

def test_cross_project_dep_snapshot(shared, homes):
    # T-200 -> T-100 (accepted)      : released snapshot, child unblocked
    # T-201 -> T-101 (done, no accept): released:false, dep_unaccepted
    # T-202 -> T-102 (open)          : refused without the flag
    plan = make_plan(shared, homes, assign={"T-301": "steer"})
    refusals = ps.validate(ps.SourceBoard(str(shared)), plan)
    pending = [r for r in refusals if r["kind"] == "pending-external-dep"]
    assert [r["ticket"] for r in pending] == ["T-202"]
    # A child that stays on the archive is not copied and keeps the dep it
    # always had, so it is not a decision the operator has to make.
    t300 = json.loads((shared / "T-300.json").read_text())
    t300["deps"] = ["T-102"]
    (shared / "T-300.json").write_text(json.dumps(t300, indent=2) + "\n")
    again = ps.validate(ps.SourceBoard(str(shared)), make_plan(
        shared, homes, assign={"T-301": "steer"}))
    assert [r["ticket"] for r in again
            if r["kind"] == "pending-external-dep"] == ["T-202"]

    plan = make_plan(shared, homes, assign={"T-301": "steer"}, allow_pending=True)
    assert [r for r in ps.validate(ps.SourceBoard(str(shared)), plan)
            if r["kind"] == "pending-external-dep"] == []
    assert run_apply(shared, plan)["ok"]

    steer = Path(homes["steer"])
    t200 = json.loads((steer / "T-200.json").read_text())
    assert "T-100" not in t200["deps"]
    dep = t200["external_deps"][0]
    assert dep["project"] == "atman" and dep["id"] == "T-100"
    assert dep["released"]["kind"] == "accept" and dep["released"]["sha"] == HEAD_A
    assert wv.unreleased_dep_id(t200, [t200]) == "", "released parent must unblock"

    t201 = json.loads((steer / "T-201.json").read_text())
    assert t201["external_deps"][0]["released"] is False
    assert t201["external_deps"][0]["reason"] == "done-unaccepted"
    assert wv.unreleased_dep_id(t201, [t201]) == "atman:T-101"
    chips = wv.blockers_of(dict(t201, phase="ready"), {"T-201": t201})
    assert [c["kind"] for c in chips] == ["dep_unaccepted"]
    assert chips[0]["on"] == "atman:T-101"

    t202 = json.loads((steer / "T-202.json").read_text())
    assert t202["external_deps"][0]["reason"] == "pending"
    assert wv.unreleased_dep_id(t202, [t202]) == "atman:T-102"

    # refresh-external flips a snapshot only after a real accept on the other
    # board -- never because the parent merely looks finished.
    boards = {"atman": homes["atman"], "steer": homes["steer"]}
    res = ps.refresh_external(str(steer), "T-201", boards)
    assert res["changed"] == []
    assert json.loads((steer / "T-201.json").read_text())["external_deps"][0]["released"] is False

    parent = Path(homes["atman"]) / "T-101.json"
    rec = json.loads(parent.read_text())
    rec["review_head"] = HEAD_B
    rec["review_events"] = [{"kind": "accept", "by": "bo", "sha": HEAD_B,
                             "at": "2026-09-22T00:00:00Z"}]
    parent.write_text(json.dumps(rec, indent=2))
    res = ps.refresh_external(str(steer), "T-201", boards)
    assert [d["id"] for d in res["changed"]] == ["T-101"]
    t201 = json.loads((steer / "T-201.json").read_text())
    assert t201["external_deps"][0]["released"]["sha"] == HEAD_B
    assert wv.unreleased_dep_id(t201, [t201]) == ""


def test_external_dep_on_an_archived_parent_names_the_archive(shared, homes):
    # T-300 is done and unattributed, so it stays on the frozen archive. A
    # live child of it gets a snapshot naming `shared-archive`, not the
    # pseudo-value `unassigned` -- the archive is a registered, readable
    # project, so the reference resolves.
    t = json.loads((shared / "T-203.json").read_text())
    t["deps"] = ["T-300"]
    (shared / "T-203.json").write_text(json.dumps(t, indent=2) + "\n")
    plan = good_plan(shared, homes)
    assert run_apply(shared, plan)["ok"]
    dep = json.loads((Path(homes["steer"]) / "T-203.json").read_text())["external_deps"][0]
    assert dep == {"project": ps.ARCHIVE_SLUG, "id": "T-300",
                   "released": False, "reason": "done-unaccepted"}
    # and it is honestly unresolvable until the operator attributes T-300:
    # the archive takes no accepts, so refresh-external cannot flip it.
    res = ps.refresh_external(homes["steer"], "T-203",
                              {"atman": homes["atman"], "steer": homes["steer"]})
    assert res["changed"] == []
    assert "no board registered" in res["unchanged"][0]["why"]


def test_migration_never_turns_unaccepted_into_accepted(shared, homes):
    plan = make_plan(shared, homes, assign={"T-301": "steer"}, allow_pending=True)
    assert run_apply(shared, plan)["ok"]
    for tid in ("T-201", "T-202"):
        rec = json.loads((Path(homes["steer"]) / (tid + ".json")).read_text())
        assert rec["external_deps"][0]["released"] is False
        assert wv.unreleased_dep_id(rec, [rec]) != ""


# --------------------------------------------------------------------------
# 7. seats
# --------------------------------------------------------------------------

def test_multi_project_seat_gets_one_record_per_home(shared, homes):
    plan = good_plan(shared, homes)
    assert plan["seats"]["ann"] == ["atman", "steer"]
    assert plan["seats"]["cy"] == ["steer"]
    assert "gone" not in plan["seats"], "a seat with no activity stays archived"
    assert run_apply(shared, plan)["ok"]

    source = json.loads((shared / "agents" / "ann.json").read_text())
    for slug in ("atman", "steer"):
        rec = json.loads((Path(homes[slug]) / "agents" / "ann.json").read_text())
        assert rec == source, "%s: inbox_seen must carry so nothing is redelivered" % slug
        assert rec["inbox_seen_ids"] == ["msg_a"]
    assert not (Path(homes["atman"]) / "agents" / "cy.json").exists()
    for slug in homes:
        assert not (Path(homes[slug]) / "agents" / "gone.json").exists()
        assert not (Path(homes[slug]) / "agents" / "ann.run").exists()
    # workforce/roles/aliases are filtered to the seats actually homed here
    assert sorted(json.loads((Path(homes["atman"]) / "workforce.json").read_text())) == ["ann"]
    assert json.loads((Path(homes["atman"]) / "aliases.json").read_text()) == {"lead": "ann"}
    assert "ghost" not in json.loads((Path(homes["steer"]) / "aliases.json").read_text())
    coord = json.loads((Path(homes["steer"]) / "coordination" / "state.json").read_text())
    assert sorted(coord["agents"]) == ["ann", "cy"]
    # decision 2: the lead is the user's choice, so the split never picks one
    for slug in homes:
        assert "lead" not in json.loads((Path(homes[slug]) / "master.json").read_text())
        assert (Path(homes[slug]) / "MASTER.md").read_text().startswith(
            "forked from the shared board ")
        assert "- 2026-09-01 started" in (Path(homes[slug]) / "MASTER.md").read_text()

    lines = ps.restart_lines(plan, {"atman": homes["atman"], "steer": homes["steer"]})
    assert "TICKETS_DIR=%s atm spawn ann --persist" % homes["atman"] in lines
    assert "TICKETS_DIR=%s atm spawn ann --persist" % homes["steer"] in lines


# --------------------------------------------------------------------------
# 8. the frozen shared board
# --------------------------------------------------------------------------

def _atm(board, *args, agent="ann", home=None, config=None, timeout=None):
    env = dict(os.environ, TICKETS_DIR=str(board), TICKET_AGENT=agent,
               TICKETS_GC_OPEN_PRS="none")
    env.pop("TICKET_SEAT", None)
    env.pop("TICKETS_STOP_HOOK", None)
    for var in ("CLAUDE_CODE_SESSION_ID", "CODEX_SESSION_ID", "CURSOR_SESSION_ID",
                "TERM_SESSION_ID", "TICKET_SESSION_ID"):
        env.pop(var, None)
    if home:
        env["HOME"] = str(home)
    env["ATMAN_BOARD_CONFIG"] = str(config or (Path(board).parent / "board.json"))
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    try:
        return subprocess.run([sys.executable, str(TOOL), *args],
                              capture_output=True, text=True, env=env,
                              cwd=str(Path(board).parent), timeout=timeout)
    except subprocess.TimeoutExpired:
        # `dash` refreshes in place forever by design. Being killed mid-refresh
        # is the harshest moment to check for a stray write, so the caller
        # still gets to compare the tree.
        return None


def test_frozen_shared_board_refuses_writes_with_new_board_named(shared, homes, tmp_path):
    plan = good_plan(shared, homes)
    assert run_apply(shared, plan)["ok"]

    out = _atm(shared, "note", "T-100", "after the split", agent="ann")
    assert out.returncode != 0
    assert "REFUSING WRITE" in out.stderr
    # the refusal names where this seat now works, not just that it refused
    assert homes["atman"] in out.stderr and homes["steer"] in out.stderr
    assert "ann now works on" in out.stderr
    assert "atm project split --undo" in out.stderr

    # a seat with no home is told so plainly
    out = _atm(shared, "note", "T-100", "x", agent="gone")
    assert out.returncode != 0
    assert "has no home project" in out.stderr

    # every write refuses, not just `note`. `status` sets a status, `here`
    # records a check-in and `inbox` stamps inbox_seen -- all writes, so none
    # of them may be on the read-only allow-list.
    for args in (["msg", "hello", "--to", "cy"], ["claim", "T-102"],
                 ["status", "T-102", "done"], ["here"], ["inbox"]):
        out = _atm(shared, *args, agent="ann")
        assert out.returncode != 0, "%s was not refused" % args[0]
        assert "REFUSING WRITE" in out.stderr, args[0]

    # reads still work: the archive stays readable forever
    for args in (["show", "T-100"], ["list"], ["who"], ["map"]):
        out = _atm(shared, *args)
        assert out.returncode == 0, "%s: %s" % (args[0], out.stderr)
    assert "T-100" in _atm(shared, "show", "T-100").stdout

    # and the new board takes writes normally
    out = _atm(Path(homes["atman"]), "note", "T-100", "on the new board", agent="ann")
    assert out.returncode == 0, out.stderr


# Every command-line surface the allow-list lets onto a frozen board, as it
# stood when each one was checked by hand. The test below compares this with
# `--help`, so a flag or sub-command added later to an allow-listed command
# fails until somebody decides whether it writes. `plan-status --write-master`
# is exactly the case that makes this worth pinning: a single flag on an
# otherwise read-only command, writing MASTER.md.
ALLOW_LISTED_SURFACE = {
    "board": {"flags": ["--all", "--quiet"]},
    "show": {"flags": ["--json"]},
    "list": {"flags": ["--json", "--owner", "--role", "--status"],
             "subs": ["blocked", "claimed", "done", "open", "review"]},
    "where": {"flags": []},
    "dash": {"flags": ["--every", "--messages", "--once"]},
    "map": {"flags": ["--all"]},
    "graph": {"flags": []},
    "guide": {"flags": []},
    "limits": {"flags": ["--hours", "--raw-scan", "--verbose"]},
    "context": {"flags": []},
    "who": {"flags": ["--no-liveness"]},
    "mine": {"flags": ["--owner"]},
    "turns": {"flags": ["--agent", "--epic", "--json", "--model", "--since",
                        "--ticket", "--until"]},
    "plan-status": {"flags": ["--write-master"]},
    "util": {"flags": ["--hours", "--json"]},
    "project": {"flags": [], "subs": ["add", "list", "refresh-external", "split"]},
    "self": {"flags": []},
    "doctor": {"flags": []},
    "trajectories": {"flags": ["--agent", "--json", "--kind", "--limit",
                               "--since", "--summary", "--ticket", "--until"],
                     "subs": ["backfill", "export"]},
    "traj": {"flags": ["--agent", "--json", "--kind", "--limit", "--since",
                       "--summary", "--ticket", "--until"],
             "subs": ["backfill", "export"]},
}


def _help_surface(cmd, *sub):
    out = _atm(Path("/tmp/nope"), cmd, *sub, "--help", timeout=20)
    text = out.stdout + out.stderr
    flags = sorted({w.split("=")[0].rstrip(",:)") for w in text.split()
                    if w.startswith("--")} - {"--help"})
    import re
    m = re.search(r"\{([a-z0-9,\-]+)\}", text)
    return flags, sorted(m.group(1).split(",")) if m else []


def test_every_allow_listed_command_writes_nothing_to_the_frozen_board(shared, homes):
    """Check the allow-list instead of trusting an audit of it.

    `status`, `here`, `identity` and `board-backup` all sat on this list until
    I re-read each one and found they write -- but "I read the code" is exactly
    the evidence that let them on in the first place. So run every name on the
    list against a frozen board and compare the tree byte for byte. Any write
    at all fails here: a status change, a check-in, an `inbox_seen` stamp, a
    cache file dropped inside the board.

    The unit of "reads" is an invocation, not a command name, and this test
    found two real writes that a name-only check waved through:
    `trajectories backfill`, and `plan-status --write-master`.
    """
    plan = good_plan(shared, homes)
    assert run_apply(shared, plan)["ok"]

    # Read the live allow-list out of a subprocess rather than importing
    # `tickets` into this pytest session: the import has board-guard side
    # effects, and a test about writes should not add any of its own.
    probe = subprocess.run(
        [sys.executable, "-c",
         "import json,sys; sys.path.insert(0, %r); import tickets; "
         "print(json.dumps(sorted(tickets.SPLIT_READ_ONLY_CMDS)))" % str(ROOT)],
        capture_output=True, text=True, cwd=str(ROOT))
    assert probe.returncode == 0, probe.stderr
    allow_listed = set(json.loads(probe.stdout))

    invocations = {
        "board": ["board"],
        "show": ["show", "T-100"],
        "list": ["list"],
        "where": ["where"],
        "dash": ["dash"],
        "map": ["map"],
        "graph": ["graph"],
        "guide": ["guide"],
        "limits": ["limits"],
        "context": ["context"],
        "who": ["who"],
        "mine": ["mine"],
        "turns": ["turns"],
        "plan-status": ["plan-status"],
        "util": ["util"],
        "project": ["project", "list"],
        "self": ["self"],
        "doctor": ["doctor"],
        "trajectories": ["trajectories"],
        "traj": ["traj"],
    }
    assert set(invocations) == allow_listed, (
        "the allow-list and this test disagree; a name was allowed without "
        "being checked: %s" % sorted(set(invocations) ^ allow_listed))
    assert set(ALLOW_LISTED_SURFACE) == allow_listed

    # A command's surface is what decides whether it writes, so pin the
    # surface: a flag or sub-command added later to an allow-listed command
    # fails here until somebody checks it.
    for cmd, want in sorted(ALLOW_LISTED_SURFACE.items()):
        flags, subs = _help_surface(cmd)
        assert flags == want["flags"], (
            "%s grew or lost a flag (%s); decide whether it writes on a "
            "frozen board before changing this list"
            % (cmd, sorted(set(flags) ^ set(want["flags"]))))
        assert subs == want.get("subs", []), (
            "%s grew or lost a sub-command (%s); decide whether it writes"
            % (cmd, sorted(set(subs) ^ set(want.get("subs", [])))))

    # every allow-listed sub-command of a reading command runs too
    # a real ticket, not just `--help`: on the archive every ticket keeps its
    # plain `deps` (external_deps are written onto the copies), so this is a
    # no-op by construction -- but "by construction" is the kind of claim that
    # stops being true, so it is checked.
    invocations["project refresh-external"] = ["project", "refresh-external",
                                               "T-200"]
    for name in ("trajectories", "traj"):
        invocations["%s export" % name] = [
            name, "export", "--out",
            str(Path(homes["atman"]).parent / ("%s-export.jsonl" % name))]

    # Give one archived ticket the timestamps a real board carries. Without
    # them `backfill` has nothing to synthesise, writes 0 events, and the
    # check below would pass for the wrong reason -- it would prove only that
    # backfill is refused, never that being allowed would have cost anything.
    archived = json.loads((shared / "T-101.json").read_text())
    archived.update(claimed_at="2026-09-02T00:00:00Z",
                    review_at="2026-09-03T00:00:00Z",
                    done_at="2026-09-04T00:00:00Z")
    (shared / "T-101.json").write_text(json.dumps(archived, indent=2) + "\n")

    before = _tree(shared)
    for cmd, args in sorted(invocations.items()):
        _atm(shared, *args, timeout=8)
        after = _tree(shared)
        assert after == before, "%s wrote to the frozen board: %s" % (
            cmd, sorted(set(after) ^ set(before))
            or [k for k in before if after.get(k) != before[k]])

    # `board-mark-primary` never reaches the refusal above: it is dispatched
    # before board_dir() so it can repair resolution on a board resolution
    # itself refuses. It is also the most dangerous thing to allow here --
    # marking the archive primary would win resolution for the repo and route
    # every seat back onto the board that takes no new records, silently. It
    # wrote `.primary` into the frozen board until this case was added.
    out = _atm(shared, "board-mark-primary", agent="ann", timeout=20)
    assert out.returncode != 0, "the archive was marked primary"
    assert "REFUSING WRITE" in out.stderr
    assert _tree(shared) == before

    # The three writing forms are refused, each with the archive's wording.
    # None of them is a command name: one is a sub-command, one is a
    # sub-command plus a path, one is a flag.
    writing = {
        "backfill": ["trajectories", "backfill"],
        "export into the board": ["trajectories", "export", "--out",
                                  str(shared / "agents" / "ann.json")],
        "export onto the log": ["trajectories", "export", "--out",
                                str(shared / "trajectories.jsonl")],
        "plan-status --write-master": ["plan-status", "--write-master"],
    }
    for name, args in sorted(writing.items()):
        out = _atm(shared, *args, agent="ann", timeout=20)
        assert out.returncode != 0, "%s was allowed on a frozen board" % name
        assert "REFUSING WRITE" in out.stderr, name
        assert _tree(shared) == before, "%s wrote anyway" % name

    # ...and those refusals are load-bearing: the same board, unfrozen, is
    # what each of them would have written into.
    (shared / ps.SPLIT_MARKER).rename(shared.parent / "split-aside")
    thawed = {k: v for k, v in before.items() if k != ps.SPLIT_MARKER}
    try:
        for name in ("backfill", "export into the board",
                     "plan-status --write-master"):
            out = _atm(shared, *writing[name], agent="ann", timeout=20)
            assert out.returncode == 0, (name, out.stderr)
            assert _tree(shared) != thawed, (
                "%s wrote nothing even unfrozen, so refusing it proves "
                "nothing: %s" % (name, out.stdout))
            # put the board back for the next one -- the marker stays off
            for rel, blob in thawed.items():
                (shared / rel).write_bytes(blob)
            for rel in set(_tree(shared)) - set(thawed):
                (shared / rel).unlink()
            assert _tree(shared) == thawed
    finally:
        (shared.parent / "split-aside").rename(shared / ps.SPLIT_MARKER)
    assert _tree(shared) == before


def _cli(board, *args, agent="ann", home=None, config=None, timeout=None):
    """Run the PACKAGED entry point, not tickets.py.

    pyproject maps both console scripts -- `atm` and `tickets` -- to
    `ticket_board.cli:main`, so this is the CLI a pip install actually gives
    an operator, and the one CI installs. It is a separate implementation with
    its own 50-command parser, which is why the frozen-board refusal has to
    exist in it too and why these tests drive it directly.
    """
    env = dict(os.environ, TICKETS_DIR=str(board), TICKET_AGENT=agent,
               TICKETS_GC_OPEN_PRS="none")
    env.pop("TICKET_SEAT", None)
    env.pop("TICKETS_STOP_HOOK", None)
    for var in ("CLAUDE_CODE_SESSION_ID", "CODEX_SESSION_ID", "CURSOR_SESSION_ID",
                "TERM_SESSION_ID", "TICKET_SESSION_ID"):
        env.pop(var, None)
    if home:
        env["HOME"] = str(home)
    env["ATMAN_BOARD_CONFIG"] = str(config or (Path(board).parent / "board.json"))
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    try:
        return subprocess.run(
            [sys.executable, "-c", "from ticket_board.cli import main; main()",
             *args],
            capture_output=True, text=True, env=env,
            cwd=str(Path(board).parent), timeout=timeout)
    except subprocess.TimeoutExpired:
        return None


# Invocations the two entry points must agree about, one per shape that
# decides the verdict. `refused` is the expected verdict; the tree is compared
# byte for byte after every one of them either way, because a command that
# writes and *then* refuses would pass a returncode check.
PARITY_INVOCATIONS = {
    # writes, by command name
    "note": (["note", "T-100", "after the split"], True),
    "claim": (["claim", "T-102"], True),
    "status": (["status", "T-102", "done"], True),
    "update": (["update", "T-102", "still going"], True),
    "assign": (["assign", "T-102", "--role", "ui"], True),
    "dep": (["dep", "T-102", "--after", "T-100"], True),
    "block": (["block", "T-102", "--reason", "x"], True),
    "reopen": (["reopen", "T-101"], True),
    "review": (["review", "T-102", "--notes", "x"], True),
    "accept": (["accept", "T-101", "--sha", HEAD_A, "--notes", "x"], True),
    "reject": (["reject", "T-101", "--sha", HEAD_A, "--reason", "x"], True),
    "msg": (["msg", "hello", "--to", "cy"], True),
    "inbox": (["inbox"], True),
    "here": (["here"], True),
    "next": (["next"], True),
    "sync": (["sync"], True),
    "identity": (["identity"], True),
    "role": (["role", "take", "planner"], True),
    "pulse": (["pulse"], True),
    "clear": (["clear"], True),
    "route": (["route"], True),
    "retire": (["retire", "ann"], True),
    "reserve": (["reserve", "T-102", "--for", "cy"], True),
    "hold": (["hold", "T-102"], True),
    "limit": (["limit", "ann", "--note", "x"], True),
    # reads: the archive stays readable forever
    "show": (["show", "T-100"], False),
    "list": (["list"], False),
    "board": (["board"], False),
    "who": (["who"], False),
    "mine": (["mine"], False),
    "map": (["map"], False),
    "graph": (["graph"], False),
    "where": (["where"], False),
    "context": (["context"], False),
    "limits": (["limits"], False),
    "turns": (["turns"], False),
    "trajectories": (["trajectories"], False),
    "traj": (["traj"], False),
    # the three shapes that are not a command name
    "traj backfill": (["trajectories", "backfill"], True),
    "traj export inside": (["trajectories", "export", "--out", "AGENTS/ann.json"],
                           True),
    "traj export outside": (["trajectories", "export", "--out", "OUT/ex.jsonl"],
                            False),
}


def test_the_packaged_entry_point_refuses_the_same_writes(shared, homes, tmp_path):
    """The refusal has to live in every CLI that can write, not just one.

    This is the fifth write onto a frozen archive this ticket has produced,
    and the only one that is not a command: it is a whole ENTRY POINT the
    guard never reached. pyproject installs `atm` and `tickets` as
    `ticket_board.cli:main`, a separate 6,000-line implementation, and the
    split refusal lived only in tickets.py. Measured before the fix, on a
    board with a `.split` marker: `python3 tickets.py note T-1 x` refused,
    and the packaged `atm note T-1 x` printed "noted on T-001" and changed
    the ticket file. CI installs that entry point, so it was the one most
    likely to be pointed at the archive.

    Sharing the block through an import was the obvious fix and the wrong one:
    a gate every write passes through must not be switchable off by an
    ImportError. So the block is duplicated, exactly as `_shadow_board_refusal`
    already is, and this test is what keeps the copies honest -- it drives one
    invocation matrix through BOTH entry points and fails if either decides
    differently.
    """
    plan = good_plan(shared, homes)
    assert run_apply(shared, plan)["ok"]
    outside = tmp_path / "exports"
    outside.mkdir()

    # the exact measured regression first, spelled out
    before = _tree(shared)
    out = _cli(shared, "note", "T-100", "after the split", agent="ann")
    assert out.returncode != 0, "the packaged entry point wrote a note"
    assert "REFUSING WRITE" in out.stderr
    assert _tree(shared) == before
    assert "atm project split --undo" not in out.stderr, (
        "this entry point does not carry `project`, so it must not name an "
        "invocation the operator cannot run")
    assert "python3 tickets.py project split --undo" in out.stderr

    # one allow-list, not two: the names have to match exactly
    lists = {}
    for name, code in (("tickets", "import tickets as m"),
                       ("cli", "from ticket_board import cli as m")):
        probe = subprocess.run(
            [sys.executable, "-c",
             "import json,sys; sys.path.insert(0, %r); sys.path.insert(0, %r); "
             "%s; print(json.dumps(sorted(m.SPLIT_READ_ONLY_CMDS)))"
             % (str(ROOT), str(ROOT / "src"), code)],
            capture_output=True, text=True, cwd=str(ROOT))
        assert probe.returncode == 0, probe.stderr
        lists[name] = json.loads(probe.stdout)
    assert lists["tickets"] == lists["cli"], (
        "the two entry points disagree about what only reads: %s"
        % sorted(set(lists["tickets"]) ^ set(lists["cli"])))

    # One allow-list covers two parsers only because this entry point's
    # command set is a subset of tickets.py's -- 55 shared, 0 unique when this
    # was written. A command added to only this CLI is refused by default
    # (it is not on the list), which is the safe direction; this assertion is
    # what makes "one list" a checked fact rather than an assumption.
    import re as _re

    def _commands(out):
        text = out.stdout + out.stderr
        m = _re.search(r"\{([a-z0-9,\-]+)\}", text)
        return set(m.group(1).split(",")) if m else set()

    only_packaged = _commands(_cli(shared, "--help")) - _commands(
        _atm(shared, "--help"))
    assert not only_packaged, (
        "the packaged CLI carries commands tickets.py does not, so the shared "
        "allow-list has not been checked against them: %s" % sorted(only_packaged))

    for label, (args, refused) in sorted(PARITY_INVOCATIONS.items()):
        args = [str(shared / "agents" / "ann.json") if a == "AGENTS/ann.json"
                else str(outside / "ex.jsonl") if a == "OUT/ex.jsonl" else a
                for a in args]
        runs = {"tickets.py": _atm(shared, *args, agent="ann", timeout=20),
                "packaged atm": _cli(shared, *args, agent="ann", timeout=20)}
        for who, out in runs.items():
            assert out is not None, "%s: %s timed out" % (who, label)
            # A command this entry point does not carry is argparse's business,
            # not the guard's: parity is about the verdict where both can run.
            if out.returncode == 2 and "invalid choice" in out.stderr:
                continue
            if refused:
                assert out.returncode != 0, "%s: %s was allowed" % (who, label)
                assert "REFUSING WRITE" in out.stderr, "%s: %s" % (who, label)
            else:
                assert out.returncode == 0, "%s: %s refused: %s" % (
                    who, label, out.stderr)
            assert _tree(shared) == before, "%s: %s wrote to the archive" % (
                who, label)


def test_the_frozen_board_probe_needs_no_engine_import(shared, homes, tmp_path):
    """`split_marker` must not depend on importing the split engine.

    It is the gate every write passes through. When it built the marker
    filename as `_project_split().SPLIT_MARKER` it also swallowed ImportError
    and returned {} -- "no marker" -- so any install where the engine is not
    importable turned the whole refusal off silently, which is the T-959
    failure shape one level up. `_ensure_src_path()` is dirname(__file__)/src,
    so a bare COPY of tickets.py on PATH has no engine beside it. The
    documented install is a symlink, where realpath finds `src/`, so this is a
    defensive case rather than a live hole -- but the guard is the wrong place
    to find out. It also charged an engine import (measured 18.8ms) to every
    invocation that is not on the allow-list.

    The filename is now a literal in each entry point, pinned here against the
    engine's own constant so the three cannot drift.
    """
    for label, code in (("tickets", "import tickets as m"),
                        ("cli", "from ticket_board import cli as m")):
        probe = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, %r); sys.path.insert(0, %r); "
             "%s; print(m.SPLIT_MARKER_NAME)" % (str(ROOT), str(ROOT / "src"), code)],
            capture_output=True, text=True, cwd=str(ROOT))
        assert probe.returncode == 0, probe.stderr
        assert probe.stdout.strip() == ps.SPLIT_MARKER, label

    plan = good_plan(shared, homes)
    assert run_apply(shared, plan)["ok"]
    before = _tree(shared)

    # a bare copy: tickets.py alone, no src/ beside it, nothing on PYTHONPATH
    bare = tmp_path / "bin"
    bare.mkdir()
    copy = bare / "atm"
    copy.write_bytes((ROOT / "tickets.py").read_bytes())
    env = dict(os.environ, TICKETS_DIR=str(shared), TICKET_AGENT="ann",
               TICKETS_GC_OPEN_PRS="none",
               ATMAN_BOARD_CONFIG=str(Path(shared).parent / "board.json"))
    env.pop("PYTHONPATH", None)
    env.pop("TICKET_SEAT", None)
    for var in ("CLAUDE_CODE_SESSION_ID", "CODEX_SESSION_ID", "CURSOR_SESSION_ID",
                "TERM_SESSION_ID", "TICKET_SESSION_ID"):
        env.pop(var, None)
    out = subprocess.run([sys.executable, str(copy), "note", "T-100", "bare"],
                         capture_output=True, text=True, env=env,
                         cwd=str(Path(shared).parent), timeout=60)
    assert out.returncode != 0, "a bare copy wrote to the frozen board"
    assert "REFUSING WRITE" in out.stderr
    assert _tree(shared) == before


# --------------------------------------------------------------------------
# 9 and 10. undo
# --------------------------------------------------------------------------

def test_undo_restores_registry_and_sets_new_boards_aside(shared, homes, tmp_path):
    registry = {"boards": {str(tmp_path / "steer"): str(shared)}, "projects": {}}
    plan = good_plan(shared, homes)
    result = run_apply(shared, plan, registry=registry)
    assert result["ok"]
    man = result["manifest"]
    assert man["registry_after"]["projects"]["atman"]["board"] == \
        os.path.realpath(homes["atman"])
    assert man["registry_after"]["projects"][ps.ARCHIVE_SLUG]["read_only"] is True

    written = {}
    report = ps.undo(man, do_apply=True, at="20260921T130000Z",
                     write_registry=lambda d: written.update(d))
    assert report["ok"], report["refusals"]
    assert written == registry, "undo restores the registry it found"
    assert not (shared / ps.SPLIT_MARKER).exists()
    for slug, dest in report["moved_aside"].items():
        assert not os.path.exists(homes[slug]), "%s should be gone" % slug
        assert os.path.isdir(dest), "the board must be moved aside, never deleted"
        assert (Path(dest) / ps.MANIFEST_NAME).exists()
    # the shared board writes again
    out = _atm(shared, "note", "T-100", "back on the shared board")
    assert out.returncode == 0, out.stderr


def test_undo_refuses_post_split_writes_without_merge_back(shared, homes):
    plan = good_plan(shared, homes)
    man = run_apply(shared, plan)["manifest"]
    with open(Path(homes["atman"]) / "messages.jsonl", "a") as f:
        f.write(json.dumps({"id": "msg_new", "at": "2026-09-21T14:00:00Z",
                            "from": "ann", "to": "", "re": "", "text": "after"}) + "\n")
    report = ps.undo(man, do_apply=False)
    assert not report["ok"]
    assert any(r["kind"] == "post-split-writes" for r in report["refusals"])
    assert os.path.isdir(homes["atman"]), "a refused undo changes nothing"
    assert (shared / ps.SPLIT_MARKER).exists()


def test_undo_merge_back_dedups_by_id(shared, homes):
    plan = good_plan(shared, homes)
    man = run_apply(shared, plan)["manifest"]
    new = {"id": "msg_new", "at": "2026-09-21T14:00:00Z", "from": "ann",
           "to": "", "re": "", "text": "written after the split"}
    # The same line appended on BOTH new boards -- one copied message, two
    # appends. Merge-back must fold it into the shared board exactly once.
    for slug in homes:
        with open(Path(homes[slug]) / "messages.jsonl", "a") as f:
            f.write(json.dumps(new) + "\n")
    # and a ticket edited after the split comes back by `updated`
    t = json.loads((Path(homes["atman"]) / "T-101.json").read_text())
    t["updated"] = "2026-09-30T00:00:00Z"
    t["notes"] = [{"by": "ann", "at": "2026-09-30T00:00:00Z", "text": "post-split"}]
    (Path(homes["atman"]) / "T-101.json").write_text(json.dumps(t, indent=2))

    before = len((shared / "messages.jsonl").read_text().splitlines())
    dry = ps.undo(man, merge_back=True, do_apply=False)
    assert dry["ok"], dry["refusals"]
    assert dry["conflicts"] == []
    assert dry["would_merge"]["lines"]["messages.jsonl"] == 1
    assert dry["would_merge"]["tickets"] == ["T-101"]
    assert len((shared / "messages.jsonl").read_text().splitlines()) == before

    report = ps.undo(man, merge_back=True, do_apply=True, at="20260921T140000Z")
    assert report["ok"], report["refusals"]
    lines = [json.loads(x) for x in
             (shared / "messages.jsonl").read_text().splitlines() if x.strip()]
    assert [m["id"] for m in lines].count("msg_new") == 1
    assert json.loads((shared / "T-101.json").read_text())["notes"][0]["text"] == "post-split"
    assert not (shared / ps.SPLIT_MARKER).exists()


def test_merge_back_restores_a_rewritten_ticket_s_original_deps(shared, homes):
    # T-200 -> T-100 crosses a project, so the split moved that edge into an
    # external_deps snapshot. Merging the ticket back must put `deps` as the
    # shared board had it, not leave a truncated list and a snapshot that
    # means nothing on a board with one project.
    plan = good_plan(shared, homes)
    man = run_apply(shared, plan)["manifest"]
    assert "T-200" in man["external_deps"]
    p = Path(homes["steer"]) / "T-200.json"
    t = json.loads(p.read_text())
    assert t["deps"] == [] and t["external_deps"][0]["id"] == "T-100"
    t["updated"] = "2026-09-30T00:00:00Z"
    p.write_text(json.dumps(t, indent=2))

    report = ps.undo(man, merge_back=True, do_apply=True, at="20260921T150000Z")
    assert report["ok"], report["refusals"]
    back = json.loads((shared / "T-200.json").read_text())
    assert back["deps"] == ["T-100"]
    assert "external_deps" not in back


def test_merge_back_refuses_a_rewritten_log(shared, homes):
    # Append-only is checked, not assumed: a log that was rewritten rather
    # than appended to would make "the bytes after offset N" arbitrary.
    plan = good_plan(shared, homes)
    man = run_apply(shared, plan)["manifest"]
    p = Path(homes["atman"]) / "messages.2026-09-08.jsonl"
    p.write_text(json.dumps({"id": "msg_z", "at": "2026-09-30T00:00:00Z",
                             "from": "ann", "to": "", "re": "", "text": "z"}) + "\n")
    report = ps.undo(man, merge_back=True, do_apply=False)
    assert not report["ok"]
    assert any("rewritten, not appended" in c.get("problem", "")
               for c in report["conflicts"]), report["conflicts"]


def test_undo_merge_back_refuses_on_a_changed_shared_board(shared, homes):
    plan = good_plan(shared, homes)
    man = run_apply(shared, plan)["manifest"]
    # the shared board is frozen, so this cannot happen -- it is checked anyway
    (shared / "T-100.json").write_text(json.dumps({"id": "T-100", "status": "open"}))
    report = ps.undo(man, merge_back=True, do_apply=False)
    assert not report["ok"]
    assert any(c["file"] == "T-100.json" for c in report["conflicts"])
    assert any(r["kind"] == "merge-back-conflict" for r in report["refusals"])


# --------------------------------------------------------------------------
# 11. ids
# --------------------------------------------------------------------------

def test_board_own_git_dir_is_never_auto_attributed():
    # The real shared board has one ticket whose `repo` is the board's own
    # `.tickets/.git`. Stripping `.git` leaves a dot-directory, which must not
    # become a `tickets` project: it is not a repo, so it is unassigned.
    assert ps.repo_slug("/Users/operator/work/steer/.tickets/.git") == ""
    assert ps.repo_slug("https://github.com/advitiyavashist/tickets.git") == "tickets"
    assert ps.repo_slug("git@github.com:advitiyavashist/steer.git") == "steer"
    assert ps.repo_slug("") == "" and ps.repo_slug(None) == ""


def test_new_ids_do_not_collide_across_projects(shared, homes, tmp_path):
    plan = good_plan(shared, homes)
    assert run_apply(shared, plan)["ok"]
    highest = max(int(t.split("-")[1]) for t in ps.SourceBoard(str(shared)).tickets)
    minted = {}
    for slug, path in homes.items():
        floor = json.loads((Path(path) / "_alloc.json").read_text())["T"]
        assert floor > highest, "%s: floor %d is not above %d" % (slug, floor, highest)
        out = _atm(Path(path), "create", "new work on " + slug, "--role", "backend")
        assert out.returncode == 0, out.stderr
        ids = sorted(p.name[:-len(".json")] for p in Path(path).glob("T-*.json"))
        fresh = [i for i in ids if int(i.split("-")[1]) >= floor]
        assert len(fresh) == 1, fresh
        minted[slug] = fresh[0]
    assert len(set(minted.values())) == len(minted), \
        "two project boards minted the same id: %r" % minted
    for slug, tid in minted.items():
        assert int(tid.split("-")[1]) > highest
