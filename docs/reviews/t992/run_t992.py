#!/usr/bin/env python3
"""T-992 throwaway-board browser pass (author evidence). Never the live board."""
from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[3]
TOOL = ROOT / "tickets.py"
BASE = Path("/tmp/atman-t992-verify")
REPO = BASE / "repo"
BOARD = REPO / ".tickets"
HOME = BASE / "home"
SHOTS = ROOT / "docs" / "reviews" / "t992"
PORT = 18792
URL = "http://127.0.0.1:%d" % PORT
PY = sys.executable
SHA = subprocess.run(["git","rev-parse","HEAD"],cwd=str(ROOT),capture_output=True,text=True).stdout.strip()

RESULTS = []


def env_for(agent: str) -> dict:
    e = dict(os.environ)
    e["TICKETS_DIR"] = str(BOARD)
    e["TICKET_AGENT"] = agent
    e["HOME"] = str(HOME)
    e["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + e.get("PYTHONPATH", "")
    for var in (
        "CLAUDE_CODE_SESSION_ID",
        "CODEX_SESSION_ID",
        "CURSOR_SESSION_ID",
        "TERM_SESSION_ID",
        "TICKETS_STOP_HOOK",
        "TICKET_SEAT",
        "TICKETS_PY",
        "TICKETS_RUN_ID",
        "TICKETS_RUN_NO",
        "CLAUDE_CODE_CHILD_SESSION",
        "CLAUDE_CODE_SESSION_ATTENDED",
    ):
        e.pop(var, None)
    e["TICKET_SESSION_ID"] = "t992-session-" + (agent or "anon")
    return e


def run(*args, agent="boss", check=True):
    r = subprocess.run(
        [PY, str(TOOL), *args],
        capture_output=True,
        text=True,
        env=env_for(agent),
        cwd=str(REPO),
    )
    if check and r.returncode != 0:
        raise RuntimeError("cmd %s failed rc=%s\n%s\n%s" % (args, r.returncode, r.stdout, r.stderr))
    return r


def git(*args):
    return subprocess.run(
        ["git", "-c", "user.email=t992@test", "-c", "user.name=t986", *args],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        check=True,
    )


def load_json(name):
    return json.loads((BOARD / name).read_text())


def save_json(name, data):
    (BOARD / name).write_text(json.dumps(data, indent=2) + "\n")


def note(name, ok, detail):
    RESULTS.append({"shot": name, "ok": bool(ok), "detail": detail})
    print("%s %s — %s" % ("PASS" if ok else "FAIL", name, detail), flush=True)


def wait_port(host, port, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = socket.socket()
        s.settimeout(0.3)
        try:
            s.connect((host, port))
            s.close()
            return True
        except OSError:
            time.sleep(0.1)
        finally:
            try:
                s.close()
            except OSError:
                pass
    return False


def init_empty_board():
    if BASE.exists():
        shutil.rmtree(BASE)
    REPO.mkdir(parents=True)
    HOME.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(REPO)], check=True)
    git("commit", "--allow-empty", "-m", "t992 throwaway init")
    run("join", "boss", "--roles", "master", "--harness", "cursor")
    run("master", "take", agent="boss")


def seed_populated():
    run("objective", "--set", "Ship the coherent Atman app",
        "--exit", "named blocker and next teammate visible", agent="boss")
    run("join", "alice", "--roles", "backend", "--harness", "cursor", agent="alice")
    run("join", "bob", "--roles", "docs", "--harness", "claude", agent="bob")
    run("join", "reviewer", "--roles", "verification", "--harness", "cursor", agent="reviewer")
    run("create", "Ready work", "--role", "backend", agent="boss")
    run("create", "Child waits", "--role", "docs", "--deps", "T-001", agent="boss")
    run("create", "Parked", "--role", "docs", "--body", "HOLD until tester week", agent="boss")
    run("create", "Unassigned next", "--role", "backend", agent="boss")
    run("create", "Needs a verdict", "--role", "backend", agent="boss")
    run("create", "Finished without review", "--role", "docs", agent="boss")
    assert run("next", agent="alice").returncode == 0
    full = git("rev-parse", "HEAD").stdout.strip()
    t5 = load_json("T-005.json")
    t5["status"] = "review"
    t5["owner"] = "alice"
    t5["commit"] = "alice@%s" % full[:7]
    t5.setdefault("notes", []).append({
        "by": "alice",
        "at": "2026-09-15T00:00:00Z",
        "text": "REVIEW: alice@%s -- paths: tickets.py; I finished the work" % full[:7],
    })
    save_json("T-005.json", t5)
    t6 = load_json("T-006.json")
    t6["status"] = "done"
    t6["owner"] = "bob"
    t6["done_at"] = "2026-09-15T00:01:00Z"
    t6.setdefault("notes", []).append({
        "by": "bob",
        "at": "2026-09-15T00:01:00Z",
        "text": "I completed it in chat",
    })
    save_json("T-006.json", t6)
    alice = load_json("agents/alice.json")
    alice["auth_check"] = {
        "state": "login_required",
        "detail": "cursor CLI found; not logged in",
        "login_cmd": "agent login",
        "harness": "cursor",
        "authoritative": True,
    }
    save_json("agents/alice.json", alice)
    bob = load_json("agents/bob.json")
    bob["auth_check"] = {
        "state": "expired",
        "detail": "claude binary found; token expired",
        "login_cmd": "claude login",
        "harness": "claude",
        "authoritative": True,
    }
    save_json("agents/bob.json", bob)
    sys.path.insert(0, str(ROOT))
    import session_adapters as sa
    sa.write_endpoint(str(BOARD), "alice", {
        "seat": "alice",
        "provider": "cursor",
        "mode": "native",
        "pid": 1,
        "at": "now",
        "heartbeat_epoch": time.time() - 3600,
    })
    run("reserve", "T-004", "--for", "alice", agent="boss")
    run("msg", "please pick up T-004", "--to", "alice", "--re", "T-004", agent="boss")
    run("msg", "task: work T-001", "--to", "alice", "--re", "T-001", "--task", agent="boss")
    return full


def start_ui():
    proc = subprocess.Popen(
        [PY, str(TOOL), "ui", "--host", "127.0.0.1", "--port", str(PORT)],
        env=env_for("boss"),
        cwd=str(REPO),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if not wait_port("127.0.0.1", PORT):
        out = proc.stdout.read() if proc.stdout else ""
        raise RuntimeError("ui did not start: %s" % out)
    return proc


def stop_ui(proc):
    if proc.poll() is None:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=3)


def shot(page, name):
    SHOTS.mkdir(parents=True, exist_ok=True)
    path = SHOTS / (name + ".png")
    page.screenshot(path=str(path), full_page=True)
    return path


def text_of(page, sel):
    loc = page.locator(sel)
    if loc.count() == 0:
        return ""
    return loc.first.inner_text()



UNVERIFIED = "Marked done; verification not recorded"


def vshot(page, name):
    SHOTS.mkdir(parents=True, exist_ok=True)
    path = SHOTS / (name + ".png")
    page.screenshot(path=str(path), full_page=False)
    return path


def tc(page, sel):
    """textContent, whitespace-normalised (inner_text applies CSS text-transform and block breaks)."""
    return page.evaluate("(s)=>{const e=document.querySelector(s);return e?e.textContent.replace(/\\s+/g,' ').trim():''}", sel)


def box(page, sel):
    return page.evaluate("""(s)=>{const e=document.querySelector(s);if(!e)return null;const r=e.getBoundingClientRect();return {top:Math.round(r.top+window.scrollY),bottom:Math.round(r.bottom+window.scrollY),h:Math.round(r.height),visible:!!(e.offsetWidth||e.offsetHeight)}}""", sel)


def main():
    SHOTS.mkdir(parents=True, exist_ok=True)
    init_empty_board()
    seed_populated()
    ui = start_ui()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="chrome", headless=True, args=["--disable-gpu", "--hide-scrollbars"])

            # ---------- 1280 desktop, dark ----------
            ctx = browser.new_context(viewport={"width": 1280, "height": 1000}, color_scheme="dark")
            page = ctx.new_page()
            page.goto(URL, wait_until="networkidle")
            page.wait_for_selector("#nowStrip")
            page.wait_for_timeout(600)
            shot(page, "01-desktop-1280-work-dark")
            conn = tc(page, "#connStatus")
            note("01-transport-label", conn == "Board synced", "connStatus=%r" % conn)
            chips = tc(page, "#chips")
            note("01-header-count", "0/6 accepted" in chips and "1 done, unverified" in chips, "chips=%s" % chips)
            hdr_open = page.evaluate("document.getElementById('hdrStatus').open")
            note("01-desktop-status-open", hdr_open is True, "hdrStatus.open=%r (no fold on desktop)" % hdr_open)
            legend = text_of(page, ".wv-bar .legend").replace("\n", " ")
            note("01-legend-unverified", "Done 1 · 1 unverified" in legend, "legend=%s" % legend)
            node = page.locator('.wv-node[data-id="T-006"]')
            ntxt = tc(page, '.wv-node[data-id="T-006"]')
            note("01-graph-shows-T-006", node.count() == 1 and UNVERIFIED in ntxt and "Done · unverified" in ntxt,
                 "T-006 node count=%d text=%s" % (node.count(), ntxt))
            # still not counted as accepted anywhere: T-005 REVIEW stays submitted-not-accepted
            page.goto(URL + "?work=T-005", wait_until="networkidle")
            page.wait_for_timeout(500)
            d5 = text_of(page, "#workflowGraph .wv-detail")
            note("02-T-005-submitted-not-accepted", "Awaiting review" in d5 and "Accepted" not in d5,
                 "T-005 detail=%s" % d5.replace("\n", " | ")[:300])
            page.goto(URL + "?work=T-006", wait_until="networkidle")
            page.wait_for_timeout(500)
            shot(page, "02-desktop-1280-T-006-detail")
            d6 = text_of(page, "#workflowGraph .wv-detail")
            note("02-deeplink-T-006-detail", UNVERIFIED in d6 and "Accepted" not in d6,
                 "T-006 detail=%s" % d6.replace("\n", " | ")[:300])
            page.click("#view-list")
            page.wait_for_timeout(300)
            lst = page.locator('.wv-list .wv-node[data-id="T-006"]')
            note("03-list-shows-T-006", lst.count() == 1 and UNVERIFIED in lst.first.inner_text(), "list rows=%d" % lst.count())
            shot(page, "03-desktop-1280-list-T-006")
            page.click("#themeBtn")
            page.wait_for_timeout(300)
            shot(page, "04-desktop-1280-light")
            acc = page.evaluate("getComputedStyle(document.body).getPropertyValue('--acc').trim()")
            note("04-light-acc", acc.lower() == "#6b5344", "light --acc=%s" % acc)
            errs = []
            ctx.close()

            # ---------- 390x844 phone, dark ----------
            ctx = browser.new_context(viewport={"width": 390, "height": 844}, color_scheme="dark",
                                      device_scale_factor=2, is_mobile=True, has_touch=True)
            page = ctx.new_page()
            page.on("pageerror", lambda e: errs.append(str(e)))
            page.goto(URL, wait_until="networkidle")
            page.wait_for_selector("#nowStrip")
            page.wait_for_timeout(600)
            vshot(page, "05-mobile-390x844-first-viewport")
            shot(page, "05b-mobile-390-full")
            hdr_open = page.evaluate("document.getElementById('hdrStatus').open")
            note("05-mobile-status-folded", hdr_open is False, "hdrStatus.open=%r" % hdr_open)
            brief = text_of(page, "#hdrStatusBrief")
            note("05-mobile-status-brief", "0/6 accepted" in brief and "1 unverified" in brief, "brief=%r" % brief)
            strip = box(page, "#promiseStrip")
            note("05-mobile-objective-strip-folded-on-work", strip and not strip["visible"], "promiseStrip=%r" % strip)
            obj = box(page, "#workObjective")
            fin = box(page, "#nowStrip article:nth-child(1)")
            blk = box(page, "#nowStrip article:nth-child(2)")
            nxt = box(page, "#nowStrip article:nth-child(3)")
            note("05-mobile-objective-in-first-viewport", obj and obj["bottom"] <= 844, "workObjective=%r" % obj)
            note("05-mobile-finishing-starts-in-first-viewport", fin and fin["top"] + 40 <= 844, "Finishing=%r" % fin)
            note("05-mobile-blocked-starts-in-first-viewport", blk and blk["top"] + 40 <= 844, "Blocked=%r" % blk)
            note("05-mobile-next-starts-in-first-viewport", nxt and nxt["top"] + 40 <= 844, "Next step=%r" % nxt)
            sw = page.evaluate("document.documentElement.scrollWidth")
            note("05-mobile-no-horizontal-overflow", sw <= 390, "scrollWidth=%s" % sw)
            conn = tc(page, "#connStatus")
            note("05-mobile-transport-label", conn == "Board synced", "connStatus=%r" % conn)
            # the folded data is one tap away
            page.click("#hdrStatus > summary")
            page.wait_for_timeout(300)
            vshot(page, "06-mobile-390-status-unfolded")
            chips = tc(page, "#chips")
            pulse = text_of(page, "#pulse")
            note("06-mobile-fold-reachable", "1 done, unverified" in chips and "median turns" in text_of(page, "#promiseChips") and pulse.strip() != "",
                 "chips=%s pulse=%s" % (chips, pulse))
            page.click("#hdrStatus > summary")
            page.wait_for_timeout(200)
            # objective tab still carries the standing objective strip on mobile
            page.click("#tab-objective")
            page.wait_for_timeout(300)
            strip = box(page, "#promiseStrip")
            note("07-mobile-objective-tab-keeps-strip", strip and strip["visible"], "promiseStrip on Objective tab=%r" % strip)
            page.click("#tab-board")
            page.wait_for_timeout(300)
            page.click("#view-list")
            page.wait_for_timeout(400)
            page.locator('.wv-list .wv-node[data-id="T-006"]').first.scroll_into_view_if_needed()
            page.wait_for_timeout(200)
            vshot(page, "08-mobile-390-list-T-006")
            lst = page.locator('.wv-list .wv-node[data-id="T-006"]')
            note("08-mobile-list-shows-T-006", lst.count() == 1 and UNVERIFIED in lst.first.inner_text(), "rows=%d" % lst.count())
            note("09-no-page-errors", not errs, "pageerrors=%r" % errs)
            ctx.close()
            browser.close()
    finally:
        stop_ui(ui)
    (SHOTS / "results.json").write_text(json.dumps({"sha": SHA, "results": RESULTS}, indent=2) + "\n")
    bad = [r for r in RESULTS if not r["ok"]]
    print("SHA", SHA, "PASS" if not bad else "FAIL %d" % len(bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
