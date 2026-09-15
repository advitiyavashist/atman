#!/usr/bin/env python3
"""Assert same-board review/handoff content before producing labeled app frames."""
import argparse
import json
import subprocess
import sys

from rehearsal import env_for, load_run


def main():
    from playwright.sync_api import sync_playwright, expect
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("run")
    p.add_argument("--port", type=int, default=18794)
    a = p.parse_args()
    run, manifest = load_run(a.run)
    parent = json.loads((run / "repo/.tickets/T-001.json").read_text())
    accepted_sha = parent["review_head"]
    env = env_for(run, "ceo")
    proc = subprocess.Popen([sys.executable, manifest["runtime"] + "/tickets.py", "ui", "--port", str(a.port)],
                            cwd=run / "repo/.worktrees/ceo", env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    results = []
    try:
        import socket
        import time
        for _ in range(100):
            if proc.poll() is not None:
                raise RuntimeError(proc.stderr.read().decode())
            try:
                with socket.create_connection(("127.0.0.1", a.port), timeout=.2):
                    break
            except OSError:
                time.sleep(.1)
        else:
            raise RuntimeError("demo UI did not start")
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel="chrome", headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 900}, device_scale_factor=2)
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            for width, height in ((1280, 900), (390, 844)):
                page.set_viewport_size({"width": width, "height": height})
                for tid, required in (("T-001", "Accepted by @ceo"), ("T-002", "summarize(path)")):
                    page.goto("http://127.0.0.1:%d/?work=%s" % (a.port, tid))
                    detail = page.locator(".wv-detail")
                    detail.wait_for(state="visible")
                    text = detail.inner_text()
                    assert tid in text and required in text, "wrong or missing detail: " + text
                    if tid == "T-001":
                        assert accepted_sha[:7] in text, "review frame is for another artifact"
                    else:
                        assert accepted_sha in text, "handoff frame is for another run"
                    expect(page.locator("#connStatus")).to_have_text("Board synced")
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "horizontal overflow"
                    path = run / "logs" / ("captured-app-%s-%s.png" % (tid, width))
                    detail.screenshot(path=str(path))
                    results.append({"ticket": tid, "width": width, "asserted": required, "path": str(path), "text": text})
            assert not errors, errors
            browser.close()
        (run / "logs/screenshots.json").write_text(json.dumps({"label": "Captured app frames", "runtime_sha": manifest["runtime_sha"], "checks": results}, indent=2))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


if __name__ == "__main__":
    main()
