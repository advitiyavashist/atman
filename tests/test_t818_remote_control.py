"""T-818: authenticated remote command with evidence-only delivery states.

Contract: Steer docs/product/atman-remote-control-contract.md (T-830).
Every case here runs on a throwaway board; nothing touches a live one.
"""
from __future__ import annotations

import http.client
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[1] / "tickets.py"
SEAT = "ceo-seat"
OTHER = "other-seat"


def _mod():
    spec = importlib.util.spec_from_file_location("tickets_t818", str(TOOL))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _rc_mod():
    here = str(TOOL.parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    import remote_control
    return remote_control


def run(board, *args, agent="lead", env=None):
    # A supervisor that launched this test run exports TICKET_SEAT (and a run
    # id) for its own seat, and TICKET_SEAT outranks TICKET_AGENT everywhere
    # -- so without pinning it here every subprocess would answer as the
    # launching seat on the throwaway board instead of the seat the case names.
    e = dict(os.environ, TICKETS_DIR=str(board), TICKET_AGENT=agent or "",
             TICKET_SEAT=agent or "", HOME=str(board.parent.parent / "home"))
    e.pop("TICKETS_STOP_HOOK", None)
    e.pop("TICKETS_REMOTE_TOKEN", None)
    e.pop("TICKETS_RUN_ID", None)
    e.pop("TICKETS_RUN_NO", None)
    e.pop("TICKETS_PY", None)
    if env:
        e.update(env)
    return subprocess.run([sys.executable, str(TOOL), *args], capture_output=True, text=True,
                          env=e, cwd=str(board.parent))


@pytest.fixture
def board(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (tmp_path / "home").mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "init"],
                   check=True,
                   env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                            GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"))
    b = repo / ".tickets"
    r = run(b, "create", "Remote work", "--role", "backend")
    assert r.returncode == 0, r.stderr
    r = run(b, "join", SEAT, "--roles", "backend", "--harness", "codex",
            "--wake-mode", "continuous", "--alias", "ceo")
    assert r.returncode == 0, r.stderr
    return b


class Harness:
    """In-process RemoteControl over the throwaway board, with a fake wake transport."""

    def __init__(self, board, monkeypatch, label="queued-offline"):
        self.board = board
        self.tk = _mod()
        self.mod = _rc_mod()
        self.wakes = []
        tk = self.tk

        def fake_wake(board_arg, seat, text, harness=None, message_id=""):
            self.wakes.append({"seat": seat, "text": text, "harness": harness, "mid": message_id})
            return label

        monkeypatch.setattr(tk, "_session_adapters", lambda: type("SA", (), {
            "wake_seat": staticmethod(fake_wake),
            "wake_payload": staticmethod(lambda fmt, m: fmt(m)),
            "provider_for_harness": staticmethod(lambda h: h or ""),
            "live_endpoint": staticmethod(lambda b, s: (None, False)),
            "is_reachable": staticmethod(lambda **k: bool(k.get("native_online") or k.get("watcher_online") or k.get("remote_online"))),
            "public_adapter_state": staticmethod(lambda b, s, h, online, pending: {
                "adapter_provider": h or "custom", "adapter_mode": "native",
                "adapter_native_online": False, "adapter_delivery": "offline"}),
            "remove_endpoint": staticmethod(lambda b, s: None),
            # board_snapshot asks the adapters directly; a stub missing this
            # would make the whole snapshot raise and silently blank the
            # runtime (provider, auth recovery) these cases assert on.
            "native_wake_online": staticmethod(lambda b, s: False),
        })())
        monkeypatch.setattr(tk, "_poke_persist_watch", lambda *a, **k: False)
        self.rc = self.mod.RemoteControl(str(board), tk)

    def token(self, grants=("dispatch", "retry"), step_up=(), operator="advitiya", **kw):
        raw, rec = self.rc.mint_token(operator, grants=grants, step_up=step_up, **kw)
        return raw, self.rc.authenticate(raw)

    def confirm(self, tok, role="ceo"):
        return self.rc.resolve(tok, role)["confirm"]

    def dispatch(self, tok, role="ceo", objective="do the thing", key=None, **kw):
        confirm = kw.pop("confirm", None) or self.confirm(tok, role)
        return self.rc.dispatch(tok, role, objective, confirm=confirm,
                                key=key or ("k-" + os.urandom(4).hex()), **kw)

    def messages(self):
        return self.tk.load_messages(str(self.board))

    def board_text(self):
        out = []
        for p in Path(self.board).rglob("*"):
            if p.is_file():
                try:
                    out.append(p.read_text(errors="ignore"))
                except OSError:
                    pass
        return "\n".join(out)


# --------------------------------------------------------------- credentials

def test_mint_stores_only_a_hash_and_refuses_denied_grants(board, monkeypatch):
    h = Harness(board, monkeypatch)
    raw, rec = h.rc.mint_token("advitiya", grants=["dispatch"], step_up=["cancel"])
    assert raw.startswith("rop_") and raw not in h.board_text()
    state = json.loads((board / "remote" / "state.json").read_text())
    assert list(state["tokens"].values())[0]["operator"] == "advitiya"
    assert oct(os.stat(board / "remote" / "state.json").st_mode & 0o777) == "0o600"
    for denied in ("merge", "shell", "destructive", "secrets"):
        with pytest.raises(h.mod.RemoteError) as e:
            h.rc.mint_token("advitiya", grants=[denied])
        assert e.value.code == "denied_in_v1" and e.value.status == 403
    with pytest.raises(h.mod.RemoteError) as e:
        h.rc.mint_token("advitiya", grants=["cancel"])
    assert e.value.code == "step_up_required"
    audit = h.rc.audit()
    assert any(r["action"] == "token.mint" and r["outcome"] == "denied" for r in audit)
    assert raw not in json.dumps(audit)


def test_auth_failed_paths(board, monkeypatch):
    h = Harness(board, monkeypatch)
    for raw, code in (("", "auth_missing"), ("rop_nope", "auth_invalid")):
        with pytest.raises(h.mod.RemoteError) as e:
            h.rc.authenticate(raw)
        assert e.value.code == code and e.value.status == 401
    raw, tok = h.token()
    h.rc.revoke_token(tok["id"])
    with pytest.raises(h.mod.RemoteError) as e:
        h.rc.authenticate(raw)
    assert e.value.code == "auth_revoked"
    raw2, _ = h.rc.mint_token("advitiya", expires_in="60s")
    late = h.mod.RemoteControl(str(board), h.tk, clock=lambda: h.mod.in_seconds(3600))
    with pytest.raises(h.mod.RemoteError) as e:
        late.authenticate(raw2)
    assert e.value.code == "auth_expired"
    assert [r for r in h.rc.audit() if r["action"] == "auth" and r["outcome"] == "denied"]


# ------------------------------------------------------------ role resolution

def test_role_resolves_through_alias_coordination_master_and_workforce(board, monkeypatch):
    h = Harness(board, monkeypatch)
    _, tok = h.token()
    assert h.rc.resolve_role("ceo") == {"role": "ceo", "seat": SEAT, "source": "alias"}
    assert h.rc.resolve_role(SEAT)["source"] == "seat"
    (board / "coordination").mkdir(exist_ok=True)
    (board / "coordination" / "state.json").write_text(json.dumps({
        "schema": 1, "agents": {}, "handovers": [], "history": [],
        "roles": {"steer.planner": {"role": "steer.planner", "holder": SEAT}}}))
    res = h.rc.resolve_role("planner")
    assert res["source"] == "coordination-role" and res["seat"] == SEAT
    (board / "master.json").write_text(json.dumps({"owner": SEAT, "since": h.tk.now()}))
    assert h.rc.resolve_role("master")["source"] == "master"
    assert run(board, "join", OTHER, "--roles", "backend", "--harness", "claude").returncode == 0
    with pytest.raises(h.mod.RemoteError) as e:
        h.rc.resolve_role("backend")
    assert e.value.code == "role_ambiguous" and len(e.value.extra["candidates"]) == 2
    with pytest.raises(h.mod.RemoteError) as e:
        h.rc.resolve_role("nobody")
    assert e.value.code == "role_unresolved"
    names = [r["role"] for r in h.rc.roles()]
    assert "ceo" in names and "steer.planner" in names and "master" in names
    # A retired seat behind an alias is never silently used: the alias is unbound.
    aliases = board / "aliases.json"
    aliases.write_text(json.dumps({"ceo": "retired-seat-0901"}))
    with pytest.raises(h.mod.RemoteError) as e:
        h.rc.resolve_role("ceo")
    assert e.value.code == "role_unbound" and "retired-seat-0901" in e.value.detail


def test_resolve_shows_runtime_and_permissions_before_any_action(board, monkeypatch):
    h = Harness(board, monkeypatch)
    _, tok = h.token(grants=["dispatch"], step_up=["cancel"])
    res = h.rc.resolve(tok, "ceo")
    rt = res["runtime"]
    assert rt["seat"] == SEAT and rt["provider"] == "codex" and rt["reachable"] is False
    assert rt["wake_mode"] == "continuous"
    assert res["permissions"]["granted"] == ["read", "message", "dispatch"]
    assert list(res["permissions"]["step_up"]) == ["cancel"]
    assert res["permissions"]["denied"] == ["merge", "destructive", "shell", "secrets"]
    assert len(res["confirm"]) == 20


# ------------------------------------------------------------- dispatch flow

def test_dispatch_requires_confirmation_then_queues_offline(board, monkeypatch):
    h = Harness(board, monkeypatch)
    _, tok = h.token()
    with pytest.raises(h.mod.RemoteError) as e:
        h.rc.dispatch(tok, "ceo", "do it", confirm="", key="k1")
    assert e.value.code == "confirmation_required" and e.value.extra["runtime"]["seat"] == SEAT
    status, body = h.dispatch(tok, key="k2", exit_criteria="board reply", ticket="T-001", expires_in="10m")
    assert status == 202
    assert body["state"] == "queued" and body["detail"].startswith("queued-offline")
    assert body["seat"] == SEAT and body["ticket"] == "T-001" and body["attempt"] == 1
    assert [e["state"] for e in body["evidence"]] == ["sent", "queued"]
    assert body["wake"]["label"] == "queued-offline"
    msgs = [m for m in h.messages() if m.get("dispatch") == body["id"]]
    assert len(msgs) == 1 and msgs[0]["kind"] == "task" and msgs[0]["to"] == SEAT
    assert msgs[0]["from"] == "remote:advitiya" and msgs[0]["re"] == "T-001"
    assert "tickets rc receipt %s --ack" % body["id"] in msgs[0]["text"]
    assert h.wakes and h.wakes[0]["seat"] == SEAT
    row = [r for r in h.rc.audit() if r["action"] == "dispatch"][0]
    assert row["actor"]["credential_class"] == "operator" and row["new_state"] == "queued"
    assert "do the thing" not in json.dumps(row) and row["content"]["length"] == len("do the thing")


def test_task_only_seat_message_waits_but_dispatch_triggers(board, monkeypatch):
    h = Harness(board, monkeypatch)
    assert run(board, "join", OTHER, "--roles", "docs", "--harness", "codex",
               "--wake-mode", "task-only").returncode == 0
    _, tok = h.token()
    confirm = h.confirm(tok, OTHER)
    status, body = h.rc.message(tok, OTHER, "just saying hi", confirm=confirm, key="m1")
    assert status == 202 and body["state"] == "queued"
    assert "task-only" in body["detail"] and body["wake"]["label"] == "not-attempted"
    assert h.wakes == []
    status, body = h.dispatch(tok, OTHER, key="d1")
    assert body["state"] == "queued" and h.wakes[-1]["seat"] == OTHER


def test_states_advance_only_on_evidence(board, monkeypatch):
    h = Harness(board, monkeypatch)
    _, tok = h.token()
    status, body = h.dispatch(tok, key="k1", ticket="T-001")
    rid = body["id"]
    # A wake label alone never advances past queued.
    assert h.rc.status(rid)["state"] == "queued"
    assert run(board, "inbox", agent=SEAT).returncode == 0
    st = h.rc.status(rid)
    assert st["state"] == "delivered"
    delivered_at = [e for e in st["evidence"] if e["state"] == "delivered"][0]["at"]
    assert h.rc.status(rid)["evidence"][2]["at"] == delivered_at  # stable, not re-stamped
    with pytest.raises(h.mod.RemoteError) as e:
        h.rc.receipt(OTHER if False else "stranger", rid, "ack")
    assert e.value.code == "not_addressed"
    st = h.rc.receipt(SEAT, rid, "ack", note="on it")
    assert st["state"] == "acknowledged" and st["receipts"][0]["kind"] == "ack"
    assert run(board, "claim", "T-001", agent=SEAT).returncode == 0
    st = h.rc.status(rid)
    assert st["state"] == "working" and "claim event on T-001" in st["evidence"][-1]["evidence"]
    t = json.loads((board / "T-001.json").read_text())
    t["status"] = "review"
    t["review_at"] = h.tk.now()
    t["commit"] = "abc1234"
    (board / "T-001.json").write_text(json.dumps(t))
    st = h.rc.status(rid)
    assert st["state"] == "review" and "abc1234" in st["evidence"][-1]["evidence"]


def test_plain_reply_naming_the_dispatch_counts_as_acknowledgement(board, monkeypatch):
    h = Harness(board, monkeypatch)
    _, tok = h.token()
    _, body = h.dispatch(tok, key="k1")
    assert run(board, "msg", "ack %s reading now" % body["id"], "--to", "remote:advitiya",
               agent=SEAT).returncode == 0
    st = h.rc.status(body["id"])
    assert st["state"] == "acknowledged" and st["receipts"][0].get("implicit") is True


def test_submitted_receipt_without_ticket_is_review_evidence(board, monkeypatch):
    h = Harness(board, monkeypatch)
    _, tok = h.token()
    _, body = h.dispatch(tok, key="k1")
    st = h.rc.receipt(SEAT, body["id"], "submitted", artifact="branch@deadbee")
    assert st["state"] == "review" and "no ticket-bound review gate" in st["evidence"][-1]["evidence"]


def test_busy_seat_is_reported_not_inferred(board, monkeypatch):
    h = Harness(board, monkeypatch)
    assert run(board, "create", "Other work", "--role", "backend").returncode == 0
    assert run(board, "claim", "T-002", agent=SEAT).returncode == 0
    _, tok = h.token()
    _, body = h.dispatch(tok, key="k1", ticket="T-001")
    assert body["busy"] is True and body["runtime"]["busy"] is True
    assert body["state"] == "queued"


# ------------------------------------------------------ failure and expiry

def test_auth_failed_seat_fails_fast_with_one_recovery_action(board, monkeypatch):
    h = Harness(board, monkeypatch)
    h.tk._agent_set(str(board), SEAT, auth_check={
        "state": "login_required", "detail": "codex login expired",
        "login_cmd": "codex login", "provider": "codex", "checked_at": h.tk.now()})
    _, tok = h.token()
    status, body = h.dispatch(tok, key="k1")
    assert status == 202 and body["state"] == "failed" and body["terminal"] is True
    assert body["evidence"][-1]["evidence"] == "auth_failed"
    assert body["recovery"] == "codex login"
    assert not [m for m in h.messages() if m.get("dispatch") == body["id"]]
    assert h.wakes == []


def test_usage_limit_seat_fails_with_recovery(board, monkeypatch):
    h = Harness(board, monkeypatch)
    h.tk._agent_set(str(board), SEAT, limit={"at": h.tk.now(), "until": "2026-09-14T00:00:00Z"})
    _, tok = h.token()
    _, body = h.dispatch(tok, key="k1")
    assert body["state"] == "failed" and body["evidence"][-1]["evidence"] == "usage_limit"
    assert "2026-09-14T00:00:00Z" in body["recovery"]


def test_wake_failure_after_queue_is_failed_with_reason(board, monkeypatch):
    h = Harness(board, monkeypatch)
    _, tok = h.token()
    _, body = h.dispatch(tok, key="k1")
    h.tk._agent_set(str(board), SEAT, adapter_failure={
        "state": "failed", "trigger": body["message_id"], "reason": "native wake refused",
        "at": h.tk.now(), "provider": "codex", "harness": "codex"})
    st = h.rc.status(body["id"])
    assert st["state"] == "failed" and st["evidence"][-1]["evidence"] == "wake_failed"
    assert st["recovery"]


def test_expired_then_retry_creates_linked_attempt(board, monkeypatch):
    h = Harness(board, monkeypatch)
    _, tok = h.token()
    _, body = h.dispatch(tok, key="k1", expires_in="60s")
    late = h.mod.RemoteControl(str(board), h.tk, clock=lambda: h.mod.in_seconds(120))
    st = late.status(body["id"])
    assert st["state"] == "expired" and "tickets rc retry" in st["recovery"]
    assert h.rc.status(body["id"])["state"] == "expired"  # persisted terminal, no auto-retry
    status, again = h.rc.retry(tok, body["id"], key="r1")
    assert status == 202 and again["attempt"] == 2 and again["prior_attempt"] == body["id"]
    assert again["logical_task"] == body["id"] and again["state"] == "queued"
    assert h.rc.status(body["id"])["state"] == "expired"
    assert len([m for m in h.messages() if m.get("dispatch") == again["id"]]) == 1
    row = [r for r in h.rc.audit() if r["action"] == "retry"][-1]
    assert row["linked"] == {"retry": again["id"]}


def test_retry_refuses_non_terminal_and_already_worked_attempts(board, monkeypatch):
    h = Harness(board, monkeypatch)
    _, tok = h.token()
    _, body = h.dispatch(tok, key="k1")
    with pytest.raises(h.mod.RemoteError) as e:
        h.rc.retry(tok, body["id"], key="r1")
    assert e.value.code == "not_retryable"
    _, nogrant = h.token(grants=[])
    with pytest.raises(h.mod.RemoteError) as e:
        h.rc.retry(nogrant, body["id"], key="r2")
    assert e.value.code == "forbidden_grant" and e.value.extra["grant"] == "retry"


# ---------------------------------------------------------- idempotency

def test_duplicate_key_replays_and_conflicting_key_is_refused(board, monkeypatch):
    h = Harness(board, monkeypatch)
    _, tok = h.token()
    confirm = h.confirm(tok)
    s1, b1 = h.rc.dispatch(tok, "ceo", "same task", confirm=confirm, key="dup")
    s2, b2 = h.rc.dispatch(tok, "ceo", "same task", confirm=confirm, key="dup")
    assert b1["id"] == b2["id"] and b2["replayed"] is True and b1["replayed"] is False
    assert len([m for m in h.messages() if m.get("dispatch") == b1["id"]]) == 1
    with pytest.raises(h.mod.RemoteError) as e:
        h.rc.dispatch(tok, "ceo", "different task", confirm=confirm, key="dup")
    assert e.value.code == "idempotency_conflict" and e.value.status == 409
    with pytest.raises(h.mod.RemoteError) as e:
        h.rc.dispatch(tok, "ceo", "x", confirm=confirm, key="")
    assert e.value.code == "idempotency_key_required"
    _, other = h.token(operator="someone-else")
    s3, b3 = h.rc.dispatch(other, "ceo", "same task", confirm=h.confirm(other), key="dup")
    assert b3["id"] != b1["id"]  # keys are per credential


# --------------------------------------------------------- permissions

def test_forbidden_without_grant_step_up_and_denied_v1(board, monkeypatch):
    h = Harness(board, monkeypatch)
    _, base = h.token(grants=[])
    with pytest.raises(h.mod.RemoteError) as e:
        h.dispatch(base, key="k1")
    assert e.value.code == "forbidden_grant" and e.value.status == 403
    _, tok = h.token()
    _, body = h.dispatch(tok, key="k2")
    for fn, code in ((lambda: h.rc.cancel(tok, body["id"], key="c1"), "step_up_required"),
                     (lambda: h.rc.reassign(tok, body["id"], SEAT, "why", key="r1"), "step_up_required"),
                     (lambda: h.rc.revoke_seat(tok, SEAT, key="v1"), "step_up_required")):
        with pytest.raises(h.mod.RemoteError) as e:
            fn()
        assert e.value.code == code
    denied = [r for r in h.rc.audit() if r["action"] == "permission" and r["outcome"] == "denied"]
    assert len(denied) >= 4
    _, scoped = h.token(targets=["ceo"])
    with pytest.raises(h.mod.RemoteError) as e:
        h.dispatch(scoped, role=SEAT, key="k3")
    assert e.value.code == "target_out_of_scope"


def test_step_up_grant_expires_on_its_own_clock(board, monkeypatch):
    h = Harness(board, monkeypatch)
    raw, tok = h.token(step_up=["cancel"])
    _, body = h.dispatch(tok, key="k1")
    late = h.mod.RemoteControl(str(board), h.tk, clock=lambda: h.mod.in_seconds(20 * 60))
    tok_late = late.authenticate(raw)
    assert late.permissions_for(tok_late)["step_up"] == {}
    with pytest.raises(h.mod.RemoteError) as e:
        late.cancel(tok_late, body["id"], key="c1")
    assert e.value.code == "step_up_required"


# ---------------------------------------------------------- revoke

def test_revoked_seat_fails_queued_work_and_blocks_new_delivery(board, monkeypatch):
    h = Harness(board, monkeypatch)
    _, tok = h.token(step_up=["revoke"])
    _, body = h.dispatch(tok, key="k1")
    status, out = h.rc.revoke_seat(tok, SEAT, reason="laptop lost", key="v1")
    assert out["revoked"] is True and out["failed_dispatches"] == [body["id"]]
    st = h.rc.status(body["id"])
    assert st["state"] == "failed" and st["evidence"][-1]["evidence"] == "revoked"
    assert h.rc.runtime_for(SEAT)["reachable"] is False and h.rc.runtime_for(SEAT)["adapter_state"] == "revoked"
    _, again = h.dispatch(tok, key="k2")
    assert again["state"] == "failed" and again["evidence"][-1]["evidence"] == "seat_revoked"
    with pytest.raises(h.mod.RemoteError) as e:
        h.rc.message(tok, "ceo", "hi", confirm=h.confirm(tok), key="m1")
    assert e.value.code == "seat_revoked"
    h.rc.revoke_seat(tok, SEAT, lift=True, key="v2")
    _, back = h.dispatch(tok, key="k3")
    assert back["state"] == "queued"


def test_token_self_revoke_stops_future_calls(board, monkeypatch):
    h = Harness(board, monkeypatch)
    raw, tok = h.token()
    status, out = h.rc.revoke_self(tok, key="s1")
    assert out["revoked"] is True
    with pytest.raises(h.mod.RemoteError) as e:
        h.rc.authenticate(raw)
    assert e.value.code == "auth_revoked"


# ---------------------------------------------------------- cancel

def test_cancel_before_delivery_is_immediate(board, monkeypatch):
    h = Harness(board, monkeypatch)
    _, tok = h.token(step_up=["cancel"])
    _, body = h.dispatch(tok, key="k1")
    status, st = h.rc.cancel(tok, body["id"], reason="changed plan", key="c1")
    assert st["state"] == "canceled" and st["cancel"]["stopped"] is True
    assert "before delivery" in st["detail"]
    assert h.rc.status(body["id"])["state"] == "canceled"
    with pytest.raises(h.mod.RemoteError) as e:
        h.rc.cancel(tok, body["id"], key="c2")
    assert e.value.code == "already_terminal"


def test_cancel_after_delivery_waits_for_child_exit_receipt(board, monkeypatch):
    h = Harness(board, monkeypatch)
    _, tok = h.token(step_up=["cancel"])
    _, body = h.dispatch(tok, key="k1", ticket="T-001")
    rid = body["id"]
    assert run(board, "inbox", agent=SEAT).returncode == 0
    assert run(board, "claim", "T-001", agent=SEAT).returncode == 0
    assert h.rc.status(rid)["state"] == "working"
    status, st = h.rc.cancel(tok, rid, reason="stop", key="c1")
    assert st["state"] == "working" and st["cancel"]["acknowledged"] is False and st["cancel"]["stopped"] is False
    cancel_msgs = [m for m in h.messages() if m.get("dispatch") == rid and m.get("cancel")]
    assert len(cancel_msgs) == 1 and cancel_msgs[0]["kind"] == "task"
    st = h.rc.receipt(SEAT, rid, "cancel-ack")
    assert st["cancel"]["acknowledged"] is True and st["cancel"]["stopped"] is False
    assert st["state"] == "working"
    h.tk.traj_event(str(board), "run_end", agent=SEAT, ticket="T-001", run_no=1, exit=143,
                    interrupted=True, outcome="interrupted")
    st = h.rc.status(rid)
    assert st["state"] == "canceled" and st["cancel"]["stopped"] is True
    assert "child-exit receipt" in st["detail"]


# ---------------------------------------------------------- reassign

def test_reassign_preserves_prior_owner_and_never_steals_active_work(board, monkeypatch):
    h = Harness(board, monkeypatch)
    assert run(board, "join", OTHER, "--roles", "docs", "--harness", "claude",
               "--wake-mode", "continuous").returncode == 0
    _, tok = h.token(step_up=["reassign"])
    _, body = h.dispatch(tok, key="k1")
    with pytest.raises(h.mod.RemoteError) as e:
        h.rc.reassign(tok, body["id"], OTHER, "", confirm=h.confirm(tok, OTHER), key="r0")
    assert e.value.code == "reason_required"
    status, moved = h.rc.reassign(tok, body["id"], OTHER, "ceo is out", confirm=h.confirm(tok, OTHER), key="r1")
    assert moved["seat"] == OTHER and moved["reassigned_from"] == SEAT and moved["attempt"] == 2
    old = h.rc.status(body["id"])
    assert old["state"] == "canceled" and old["evidence"][-1]["evidence"] == "reassigned"
    note = [m for m in h.messages() if m.get("dispatch") == body["id"] and m.get("reassigned")]
    assert note and note[0]["to"] == SEAT and "ceo is out" in note[0]["text"]
    row = [r for r in h.rc.audit() if r["action"] == "reassign"][-1]
    assert row["prior_owner"] == SEAT and row["new_owner"] == OTHER and "ceo is out" not in json.dumps(row)
    # Active work is never silently stolen.
    _, active = h.dispatch(tok, key="k2", ticket="T-001")
    assert run(board, "inbox", agent=SEAT).returncode == 0
    assert run(board, "claim", "T-001", agent=SEAT).returncode == 0
    with pytest.raises(h.mod.RemoteError) as e:
        h.rc.reassign(tok, active["id"], OTHER, "impatient", confirm=h.confirm(tok, OTHER), key="r2")
    assert e.value.code == "active_work"


# ------------------------------------------------------- secrets and bodies

def test_secret_shaped_payloads_are_refused_and_nothing_leaks(board, monkeypatch):
    h = Harness(board, monkeypatch)
    raw, tok = h.token()
    for text in ("use token sk-ant-api03-%s now" % ("a" * 40), "here %s" % raw):
        with pytest.raises(h.mod.RemoteError) as e:
            h.dispatch(tok, objective=text, key="s-" + str(len(text)))
        assert e.value.code == "secret_in_payload"
    _, body = h.dispatch(tok, key="ok", objective="an ordinary objective with words")
    blob = h.board_text() + json.dumps(h.rc.audit())
    assert raw not in blob
    assert "an ordinary objective" not in json.dumps(h.rc.audit())


# --------------------------------------------------------------- HTTP surface

def _http(port, method, path, token=None, body=None, key=None, extra=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    headers = {}
    if token:
        headers["Authorization"] = "Bearer " + token
    if body is not None:
        headers["Content-Type"] = "application/json"
        headers["Idempotency-Key"] = key or os.urandom(4).hex()
    headers.update(extra or {})
    conn.request(method, path, body=json.dumps(body).encode() if body is not None else None, headers=headers)
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    try:
        return resp.status, json.loads(data.decode()), data
    except ValueError:
        return resp.status, None, data


def test_http_roundtrip_from_a_separate_client(board, monkeypatch):
    h = Harness(board, monkeypatch)
    raw, tok = h.token(step_up=["cancel"])
    srv = h.mod.serve(h.rc, host="127.0.0.1", port=0, block=False)
    port = srv.server_address[1]
    try:
        status, page, data = _http(port, "GET", "/")
        assert status == 200 and b"Atman remote" in data and b"denied in V1" in data
        status, out, _ = _http(port, "GET", "/remote/v1/whoami")
        assert status == 401 and out["error"] == "auth_missing"
        status, out, _ = _http(port, "GET", "/remote/v1/whoami", token=raw)
        assert status == 200 and out["operator"] == "advitiya" and out["permissions"]["granted"] == ["read", "message", "dispatch", "retry"]
        status, out, _ = _http(port, "GET", "/remote/v1/roles", token=raw)
        assert any(r["role"] == "ceo" and r["seat"] == SEAT for r in out["roles"])
        status, res, _ = _http(port, "GET", "/remote/v1/resolve?role=ceo", token=raw)
        assert status == 200 and res["runtime"]["seat"] == SEAT and res["permissions"]["denied"]
        status, out, _ = _http(port, "POST", "/remote/v1/dispatch", token=raw,
                               body={"role": "ceo", "objective": "from the phone", "from": "evil"})
        assert status == 403 and out["error"] == "sender_identity_rejected"
        status, out, _ = _http(port, "POST", "/remote/v1/dispatch", token=raw,
                               body={"role": "ceo", "objective": "from the phone"})
        assert status == 409 and out["error"] == "confirmation_required"
        status, out, _ = _http(port, "POST", "/remote/v1/dispatch", token=raw, key="phone-1",
                               body={"role": "ceo", "objective": "from the phone", "confirm": res["confirm"],
                                     "expires_in": "5m"})
        assert status == 202 and out["state"] == "queued", out
        rid = out["id"]
        status, again, _ = _http(port, "POST", "/remote/v1/dispatch", token=raw, key="phone-1",
                                 body={"role": "ceo", "objective": "from the phone", "confirm": res["confirm"],
                                       "expires_in": "5m"})
        assert again["id"] == rid and again["replayed"] is True
        status, out, _ = _http(port, "GET", "/remote/v1/dispatches/" + rid, token=raw)
        assert status == 200 and out["state"] == "queued"
        status, out, _ = _http(port, "POST", "/remote/v1/merge", token=raw, body={})
        assert status == 403 and out["error"] == "denied_in_v1"
        status, out, _ = _http(port, "POST", "/remote/v1/dispatches/%s/cancel" % rid, token=raw,
                               body={"reason": "nope"})
        assert status == 202 and out["state"] == "canceled"
        status, out, _ = _http(port, "POST", "/remote/v1/dispatch", token=raw, key="o1",
                               body={"role": "ceo", "objective": "x", "confirm": res["confirm"]},
                               extra={"Origin": "http://attacker.example"})
        assert status == 403 and out["error"] == "origin_mismatch"
        status, out, _ = _http(port, "GET", "/remote/v1/audit", token=raw)
        assert status == 200 and raw not in json.dumps(out)
    finally:
        srv.shutdown()
        srv.server_close()
    assert raw not in h.board_text()


def test_serve_refuses_non_loopback_without_tls_acknowledgement(board, monkeypatch):
    h = Harness(board, monkeypatch)
    with pytest.raises(h.mod.RemoteError) as e:
        h.mod.serve(h.rc, host="0.0.0.0", port=0, block=False)
    assert e.value.code == "insecure_bind"


# ------------------------------------------------------------------ CLI

def test_cli_flow_mint_dispatch_receipt_status(board):
    r = run(board, "rc", "mint", "--operator", "advitiya", "--grant", "dispatch", "--json")
    assert r.returncode == 0, r.stderr
    minted = json.loads(r.stdout)
    raw = minted["token"]
    assert raw.startswith("rop_") and minted["record"]["grants"] == ["dispatch"]
    env = {"TICKETS_REMOTE_TOKEN": raw}
    r = run(board, "rc", "resolve", "ceo", env=env)
    assert r.returncode == 0 and "ceo -> ceo-seat (via alias)" in r.stdout, r.stdout + r.stderr
    r = run(board, "rc", "dispatch", "ceo", "--objective", "cli objective", "--re", "T-001",
            "--json", env=env)
    assert r.returncode == 0, r.stderr
    body = json.loads(r.stdout)
    assert body["state"] == "queued"
    rid = body["id"]
    r = run(board, "inbox", agent=SEAT)
    assert rid in r.stdout
    r = run(board, "rc", "receipt", rid, "--ack", "--note", "starting", agent=SEAT)
    assert r.returncode == 0 and "state=acknowledged" in r.stdout, r.stdout + r.stderr
    r = run(board, "rc", "receipt", rid, "--ack", agent=OTHER)
    assert r.returncode != 0 and "not_addressed" in r.stderr
    r = run(board, "rc", "status", rid)
    assert "acknowledged" in r.stdout and "delivered" in r.stdout
    r = run(board, "rc", "list")
    assert rid in r.stdout
    r = run(board, "rc", "tokens")
    assert minted["record"]["id"] in r.stdout and raw not in r.stdout
    r = run(board, "rc", "revoke-token", minted["record"]["id"])
    assert r.returncode == 0
    r = run(board, "rc", "resolve", "ceo", env=env)
    assert r.returncode != 0 and "auth_revoked" in r.stderr
    r = run(board, "rc", "resolve", "ceo")
    assert r.returncode != 0 and "auth_missing" in r.stderr
    r = run(board, "rc", "audit", "--limit", "3")
    assert r.returncode == 0 and raw not in r.stdout
    assert raw not in (board / "messages.jsonl").read_text()


def test_wake_recipients_matches_cmd_msg_for_directed_task(board, monkeypatch):
    """The refactored helper is what tickets msg prints; remote reuses it verbatim."""
    tk = _mod()
    labels = []
    monkeypatch.setattr(tk, "_session_adapters", lambda: type("SA", (), {
        "wake_seat": staticmethod(lambda b, s, t, harness=None, message_id="": "queued-offline"),
        "wake_payload": staticmethod(lambda fmt, m: "payload"),
        "provider_for_harness": staticmethod(lambda h: h or ""),
    })())
    monkeypatch.setattr(tk, "_poke_persist_watch", lambda b, s: labels.append(s) or True)
    m = tk.post_message(str(board), "lead", "please act", SEAT, "", kind="task", extra={"dispatch": "rc_0123456789ab"})
    out = tk.wake_recipients(str(board), m, echo=lambda *_: None)
    assert out == [{"seat": SEAT, "label": "watch-poked", "poked": True}]
    rec = json.loads((board / "agents" / (SEAT + ".json")).read_text())
    assert rec["wake_delivery"]["message_id"] == m["id"]
    stored = [x for x in tk.load_messages(str(board)) if x.get("id") == m["id"]][0]
    assert stored["dispatch"] == "rc_0123456789ab"
