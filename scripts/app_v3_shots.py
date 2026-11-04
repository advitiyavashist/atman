#!/usr/bin/env python3
"""Screenshots of the built /app/ for every state in docs/design/app-v3.md §7 (T-1486).

Seeds one throwaway board per state (project ``demo``, operator ``boss``,
seat ``worker``, reviewer ``astra``; tickets T-001..T-003 as ``atm quickstart``
makes them), serves it with the real ``atm ui`` on a free port with
``--app-dir ui/dist``, and captures the page at 1440x900 and 390x844 with
Playwright (light scheme). Panels and dialogs are fixed, so those are viewport
shots; everything else is full page. At 390 it also measures horizontal
overflow and refuses to call a state done when the page scrolls sideways.

Usage (from the repo root, after ``npm run build -w ui``):

    python3 scripts/app_v3_shots.py [--out docs/design/app-v3/build] [--only fresh,needs-you]

Never touches a live board: TICKETS_DIR, HOME, ATMAN_BOARD_CONFIG and the
cache all point under a temporary directory, and every session variable is
unset for the server.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tickets.py"
SHA = "3f2a1c0d9e8b7a6f5e4d3c2b1a0f9e8d7c6b5a49"
SHA2 = "9c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d"
SESSION_PREFIXES = ("CLAUDE", "CODEX_", "CURSOR_", "TICKET_", "TERM_SESSION", "ATMAN_")
OBJ = {"text": "Ship a small stats API with a screen on top",
       "exit_criterion": "the screen shows live stats from the API, accepted by a second seat", "state": "active"}
TITLES = {
    "T-001": ("Sample: design the data model", "backend", [],
              "A sample ticket created by `atm quickstart`.\n\nIt has no dependencies, so it is the one `atm next` hands out first.\n"
              "Work it like a real ticket: claim it, post an update, then send it to review."),
    "T-002": ("Sample: build the API on top of the model", "backend", ["T-001"],
              "A sample ticket that DEPENDS on the first one.\n\n`atm next` will not offer it until the schema ticket is accepted."),
    "T-003": ("Sample: put a screen on the API", "console", ["T-002"],
              "The third sample, two hops down the chain.\n\nRun `atm graph` to see all three and what is blocking what."),
}
REVIEW_NOTES = "schema.sql, docs/data-model.md; pytest tests/test_schema.py (6 passed); ids are ULIDs"
REVIEW_NOTES2 = "schema.sql, docs/data-model.md; pytest tests/test_schema.py (8 passed); ids are ULIDs; added the rollups table the question was about"


def stamp(mins_ago=0):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - mins_ago * 60))


def write(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1))


def seat(board: Path, name, seen_mins=1, **extra):
    rec = {"owner": name}
    if seen_mins is not None:
        rec["seen"] = stamp(seen_mins)
    rec.update(extra)
    write(board / "agents" / (name + ".json"), rec)


def ticket(board: Path, tid, **kw):
    title, role, deps, body = TITLES[tid]
    t = dict(id=tid, title=title, status="open", role=role, deps=list(deps), notes=[], priority=2,
             created=stamp(600), body=body)
    t.update(kw)
    write(board / (tid + ".json"), t)


def msg(board: Path, mins_ago, frm, text, to="", re=""):
    with open(board / "messages.jsonl", "a") as f:
        f.write(json.dumps({"id": "msg_%d" % int((time.time() - mins_ago * 60) * 1000), "at": stamp(mins_ago),
                            "from": frm, "to": to, "re": re, "text": text}) + "\n")


def run_start(board: Path, agent, tid, mins_ago, harness="cursor"):
    with open(board / "trajectories.jsonl", "a") as f:
        f.write(json.dumps({"v": 1, "at": stamp(mins_ago), "kind": "run_start", "agent": agent, "ticket": tid,
                            "run_no": 1, "run_id": "r-%s-1" % agent, "harness": harness, "trigger": ["holding"]}) + "\n")


def accepted(board: Path, tid, owner="worker", by="boss", mins_ago=120):
    ticket(board, tid, status="done", owner=owner, claimed_at=stamp(mins_ago + 90), review_at=stamp(mins_ago + 10),
           done_at=stamp(mins_ago), review_head=SHA, commit="%s/t-001@%s" % (owner, SHA), branch="%s/t-001" % owner,
           review_events=[{"kind": "accept", "by": by, "at": stamp(mins_ago), "sha": SHA,
                           "notes": "schema matches the brief; 6 tests pass"}],
           notes=[{"by": owner, "at": stamp(mins_ago + 10), "text": "REVIEW: " + REVIEW_NOTES},
                  {"by": by, "at": stamp(mins_ago), "text": "accept: schema matches the brief; 6 tests pass"}])


def in_review(board: Path, owner="worker", mins_ago=5, head=SHA, notes=REVIEW_NOTES):
    ticket(board, "T-001", status="review", owner=owner, claimed_at=stamp(mins_ago + 40), review_at=stamp(mins_ago),
           review_head=head, commit="%s/t-001@%s" % (owner, head), branch="%s/t-001" % owner,
           notes=[{"by": owner, "at": stamp(mins_ago + 30), "text": "update: drafting the schema: three tables, sites, samples, rollups"},
                  {"by": owner, "at": stamp(mins_ago), "text": "REVIEW: " + notes}])


def base(board: Path, operator="boss", with_astra=False, objective=True):
    seat(board, operator, 0)
    wf = {"worker": {"harness": "cursor"}}
    if with_astra:
        wf["astra"] = {"harness": "codex"}
    write(board / "workforce.json", wf)
    write(board / "master.json", {"owner": "", "cos": "", "since": stamp(600)})
    if objective:
        write(board / "objective.json", OBJ)


def seed(state: str, board: Path):
    board.mkdir(parents=True)
    if state == "empty":
        base(board, objective=False)
        seat(board, "worker", 3 * 24 * 60)
        return
    if state == "fresh":
        base(board)
        seat(board, "worker", 1)
        for tid in TITLES:
            ticket(board, tid)
        msg(board, 30, "worker", "joined the board; roles=['backend']")
        msg(board, 29, "boss", "hi")
        return
    if state == "working":
        base(board)
        seat(board, "worker", 1, ticket="T-001")
        ticket(board, "T-001", status="claimed", owner="worker", claimed_at=stamp(12),
               notes=[{"by": "worker", "at": stamp(2), "text": "update: drafting the schema: three tables, sites, samples, rollups"}])
        ticket(board, "T-002")
        ticket(board, "T-003")
        run_start(board, "worker", "T-001", 12)
        msg(board, 30, "worker", "joined the board; roles=['backend']")
        msg(board, 29, "boss", "hi", to="worker")
        msg(board, 10, "worker", "claimed T-001; drafting the schema. Three tables: sites, samples, rollups.", to="boss", re="T-001")
        return
    if state in ("needs-you", "stale", "read-only"):
        base(board, with_astra=True)
        seat(board, "worker", 5)
        seat(board, "astra", 120)
        in_review(board)
        ticket(board, "T-002")
        ticket(board, "T-003")
        msg(board, 60, "boss", "hi", to="worker")
        msg(board, 45, "worker", "claimed T-001; drafting the schema. Three tables: sites, samples, rollups.", to="boss", re="T-001")
        msg(board, 44, "boss", "good, go ahead", to="worker", re="T-001")
        msg(board, 20, "worker", "@boss should ids be ULIDs or auto-increment ints? I used ULIDs; say if you want ints before you accept.", to="boss", re="T-001")
        msg(board, 5, "worker", "review: #T-001 at 3f2a1c0 — " + REVIEW_NOTES + ". #T-002 can start once this is accepted.", re="T-001")
        return
    if state == "self-review":
        base(board)
        seat(board, "worker", 60)
        in_review(board, owner="boss")
        ticket(board, "T-002")
        ticket(board, "T-003")
        msg(board, 5, "boss", "review: #T-001 at 3f2a1c0 — " + REVIEW_NOTES, re="T-001")
        return
    if state == "blocked":
        base(board, with_astra=True)
        seat(board, "worker", 70, ticket="T-002")
        seat(board, "astra", 180)
        accepted(board, "T-001")
        ticket(board, "T-002", status="blocked", owner="worker", claimed_at=stamp(130),
               notes=[{"by": "worker", "at": stamp(70),
                       "text": "blocked: needs staging DB credentials: no STAGING_DB_URL on this machine and I cannot mint one"}])
        ticket(board, "T-003")
        msg(board, 130, "worker", "review: #T-001 at 3f2a1c0 — " + REVIEW_NOTES, to="boss", re="T-001")
        msg(board, 120, "boss", "accept: schema matches the brief; 6 tests pass", to="worker", re="T-001")
        msg(board, 70, "worker", "stuck: needs staging DB credentials: no STAGING_DB_URL on this machine and I cannot mint one. #T-003 waits on this.", to="boss", re="T-002")
        return
    if state == "accepted":
        base(board, with_astra=True)
        seat(board, "worker", 1, ticket="T-002")
        seat(board, "astra", 120)
        accepted(board, "T-001")
        ticket(board, "T-002", status="claimed", owner="worker", claimed_at=stamp(25),
               notes=[{"by": "worker", "at": stamp(2), "text": "update: writing tests for the /stats endpoint; rollup job next"}])
        ticket(board, "T-003")
        run_start(board, "worker", "T-002", 25)
        msg(board, 120, "boss", "accept: schema matches the brief; 6 tests pass", to="worker", re="T-001")
        msg(board, 110, "worker", "claimed #T-002; /stats endpoint first, then the rollup job. Builds on #T-001.", to="boss", re="T-002")
        return
    if state == "offline":
        base(board, with_astra=True)
        seat(board, "worker", 9, ticket="T-002", limit={"at": stamp(9), "until": "17:40", "note": "weekly cap"})
        seat(board, "astra", None)
        accepted(board, "T-001")
        ticket(board, "T-002", status="claimed", owner="worker", claimed_at=stamp(50),
               notes=[{"by": "worker", "at": stamp(30), "text": "update: /stats endpoint drafted; rollup job next"}])
        ticket(board, "T-003")
        msg(board, 110, "worker", "claimed #T-002; /stats endpoint first, then the rollup job.", to="boss", re="T-002")
        msg(board, 40, "boss", "keep the rollup job in the same PR", to="worker", re="T-002")
        return
    raise SystemExit("unknown state %s" % state)


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    def __init__(self, board: Path, tmp: Path, operator="boss"):
        self.port = free_port()
        env = {k: v for k, v in os.environ.items() if not k.startswith(SESSION_PREFIXES)}
        home = tmp / "home"
        home.mkdir(exist_ok=True)
        cfg = tmp / "board-config.json"
        write(cfg, {"projects": {"demo": {"board": str(board), "repos": [str(board.parent)]}}})
        env.update(TICKETS_DIR=str(board), HOME=str(home), TICKETS_CACHE_DIR=str(tmp / "cache"),
                   ATMAN_BOARD_CONFIG=str(cfg), PYTHONUNBUFFERED="1")
        args = [sys.executable, str(TOOL), "ui", "--port", str(self.port), "--app-dir", str(ROOT / "ui" / "dist")]
        if operator:
            args += ["--operator", operator]
        self.proc = subprocess.Popen(args, env=env, cwd=str(board.parent), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        deadline = time.time() + 30
        import urllib.request
        while time.time() < deadline:
            try:
                with urllib.request.urlopen("http://127.0.0.1:%d/api/v1/projects" % self.port, timeout=2) as r:
                    if r.status == 200:
                        return
            except Exception:
                if self.proc.poll() is not None:
                    raise RuntimeError("atm ui exited: " + (self.proc.stdout.read() if self.proc.stdout else ""))
                time.sleep(0.1)
        raise RuntimeError("atm ui never came up")

    @property
    def url(self):
        return "http://127.0.0.1:%d/app/" % self.port

    def stop(self):
        self.proc.kill()
        self.proc.wait()


def chromium_path():
    exe = os.environ.get("ATMAN_SHOTS_CHROMIUM")
    if exe:
        return exe
    cache = Path(os.path.expanduser("~/Library/Caches/ms-playwright"))
    for d in sorted(cache.glob("chromium_headless_shell-*"), reverse=True):
        for cand in d.rglob("chrome-headless-shell"):
            if cand.is_file():
                return str(cand)
    return None


VIEWPORTS = {"1440": (1440, 900), "390": (390, 844)}


def overflow(page):
    return page.evaluate("""() => {
        const w = window.innerWidth; const bad = [];
        for (const el of document.querySelectorAll('body *')) {
            const r = el.getBoundingClientRect();
            if (r.width && r.right > w + 1 && getComputedStyle(el).position !== 'fixed') bad.push(el.tagName + '.' + (el.className && el.className.baseVal === undefined ? String(el.className).split(' ')[0] : ''));
        }
        return {scrollWidth: document.documentElement.scrollWidth, innerWidth: w, bad: bad.slice(0, 8)};
    }""")


def settle(page):
    page.wait_for_selector("[data-testid=card], [data-testid=board-empty], [data-testid=need-item], [data-testid=empty-needs]", timeout=20000)
    page.wait_for_timeout(400)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "docs" / "design" / "app-v3" / "build"))
    ap.add_argument("--only", default="")
    ap.add_argument("--keep", action="store_true", help="keep the temp boards")
    a = ap.parse_args()
    from playwright.sync_api import sync_playwright
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    only = set(x for x in a.only.split(",") if x)
    tmp = Path(tempfile.mkdtemp(prefix="app-v3-shots-"))
    results = []
    exe = chromium_path()
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
        for state in ("empty", "fresh", "working", "needs-you", "stale", "blocked", "accepted", "offline", "self-review", "read-only"):
            if only and state not in only:
                continue
            for vp, (w, h) in VIEWPORTS.items():
                # A fresh board per viewport: the flows below write to it
                # (accept, assign), and the 390 pass must start from the state.
                root = tmp / ("%s-%s" % (state, vp))
                board = root / "demo" / ".tickets"
                seed(state, board)
                srv = Server(board, root, operator="" if state == "read-only" else "boss")
                try:
                    ctx = browser.new_context(viewport={"width": w, "height": h}, color_scheme="light")
                    page = ctx.new_page()
                    errors = []
                    page.on("pageerror", lambda e: errors.append(str(e)))
                    page.goto(srv.url, wait_until="networkidle")
                    settle(page)
                    shot = lambda name, full=True: page.screenshot(path=str(out / ("%s-%s.png" % (vp, name))), full_page=full)
                    ov = overflow(page)
                    shot(state)
                    results.append((vp, state, ov, list(errors)))
                    if state == "needs-you":
                        page.click("[data-testid=accept]")
                        page.wait_for_selector("[data-testid=dialog]")
                        shot("needs-you-accept", full=False)
                        page.fill("#accept-notes", "read schema.sql and the test output; ULIDs are fine")
                        page.click("[data-testid=accept-submit]")
                        page.wait_for_selector("[data-testid=result]")
                        page.wait_for_timeout(300)
                        shot("after-accept")
                        # the next board read moves it to Done
                        page.reload(wait_until="networkidle")
                        settle(page)
                        page.click("[data-testid=section-done] .fold")
                        page.wait_for_timeout(300)
                        shot("after-accept-next-read")
                        ctx.close()
                        srv.stop()
                        shutil.rmtree(root)
                        seed(state, board)
                        srv = Server(board, root)
                        ctx = browser.new_context(viewport={"width": w, "height": h}, color_scheme="light")
                        page = ctx.new_page()
                        page.goto(srv.url, wait_until="networkidle")
                        settle(page)
                        page.click("text=Accept >> nth=0") if False else None
                        page.click("[data-testid=section-needs] .filter-chip:nth-child(3)")
                        page.wait_for_timeout(200)
                        shot("needs-you-filter")
                        page.click("[data-testid=section-needs] .filter-chip:nth-child(1)")
                        page.click("[role=switch]")
                        page.wait_for_timeout(200)
                        shot("details")
                        page.click("[role=switch]")
                        page.click("[data-testid=card][data-ticket=T-001] .open")
                        page.wait_for_selector("[data-testid=ticket-panel] [data-testid=panel-thread] [data-testid=message]")
                        page.wait_for_timeout(300)
                        shot("panel", full=False)
                        page.keyboard.press("Escape")
                        page.wait_for_timeout(200)
                        if vp == "390":
                            page.click("[role=tab]:has-text('Talk')")
                            page.wait_for_timeout(200)
                        page.click("#composer")
                        page.type("#composer", "@")
                        page.wait_for_selector("[data-testid=autocomplete]")
                        shot("autocomplete", full=False)
                        page.fill("#composer", "")
                        if vp == "390":
                            page.click("[role=tab]:has-text('Board')")
                    if state == "stale":
                        t = json.loads((board / "T-001.json").read_text())
                        t["review_head"] = SHA2
                        t["commit"] = "worker/t-001@" + SHA2
                        t["notes"].append({"by": "worker", "at": stamp(0), "text": "REVIEW: " + REVIEW_NOTES2})
                        (board / "T-001.json").write_text(json.dumps(t))
                        page.click("[data-testid=accept]")
                        page.wait_for_selector("[data-testid=dialog]")
                        page.fill("#accept-notes", "looks right")
                        page.click("[data-testid=accept-submit]")
                        page.wait_for_selector("[data-testid=stale]")
                        shot("stale-accept", full=False)
                        page.click("text=Review again")
                        page.wait_for_timeout(200)
                        shot("stale-review-again", full=False)
                    if state == "self-review":
                        page.click("[data-testid=card][data-ticket=T-001] .open")
                        page.wait_for_timeout(300)
                        shot("self-review-panel", full=False)
                    if state == "fresh":
                        page.click("[data-testid=section-working] .btn.primary")
                        page.wait_for_selector("[data-testid=dialog]")
                        shot("assign", full=False)
                        page.keyboard.press("Escape")
                        page.click("[data-testid=section-working] .btn.quiet")
                        page.wait_for_selector("[data-testid=dialog]")
                        shot("start-agent", full=False)
                        page.keyboard.press("Escape")
                        page.click("[data-testid=card][data-ticket=T-001] .btn:has-text('Assign')")
                        page.wait_for_selector("[data-testid=dialog]")
                        page.check("input[name=seat][value=worker]")
                        page.fill("#assign-why", "worker is free")
                        page.click("[data-testid=assign-submit]")
                        page.wait_for_selector("[data-testid=result]")
                        page.wait_for_timeout(300)
                        shot("after-assign")
                    if state == "blocked":
                        page.click("[data-testid=section-done] .fold")
                        page.wait_for_timeout(300)
                        shot("done-expanded")
                        page.click("[data-testid=card][data-ticket=T-002] .btn:has-text('Answer')")
                        page.wait_for_timeout(300)
                        shot("answer-focus", full=False)
                    if state == "accepted" and vp == "390":
                        page.click("[role=tab]:has-text('Talk')")
                        page.wait_for_timeout(300)
                        shot("accepted-talk")
                    if state == "working" and vp == "390":
                        page.click("[role=tab]:has-text('Talk')")
                        page.wait_for_timeout(300)
                        shot("working-talk")
                    if state == "offline":
                        page.click("[data-testid=seat][data-presence=limited]")
                        page.wait_for_timeout(300)
                        shot("offline-talk", full=(vp == "1440"))
                    if errors:
                        results.append((vp, state + " (after clicks)", ov, list(errors)))
                    ctx.close()
                finally:
                    srv.stop()
        browser.close()
    if not a.keep:
        shutil.rmtree(tmp, ignore_errors=True)
    bad = 0
    for vp, state, ov, errors in results:
        flag = ""
        if ov["scrollWidth"] > ov["innerWidth"] + 1 or ov["bad"]:
            flag = " OVERFLOW %s" % ov
            bad += 1
        if errors:
            flag += " PAGEERROR %s" % errors
            bad += 1
        print("%s %-14s scrollWidth=%d innerWidth=%d%s" % (vp, state, ov["scrollWidth"], ov["innerWidth"], flag))
    print("shots in", out, "·", "%d problem(s)" % bad if bad else "no overflow, no page errors")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
