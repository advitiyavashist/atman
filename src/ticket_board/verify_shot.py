"""T-1026: annotated screenshot evidence for UI verification seats.

Built on the Playwright path already used by T-986 / T-1011. This is not a
live IDE panel and not a new embedded browser: it captures a state, marks
one element, and writes attachable files (PNG + JSON sidecar + HTML overlay).

  atm shot --url URL --selector CSS --label TEXT [--ticket T-N] [--out DIR]
  atm shot --url URL --pick --label TEXT --headed
  atm shot --png existing.png --bbox x,y,w,h --label TEXT [--out DIR]

`--pick` waits for a click and needs a headed browser. `--selector` is the
agent path. A missing element, unreachable URL, or missing Playwright is a
hard failure — never a silent empty shot.
"""
from __future__ import annotations

import html
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

KIND = "annotated-screenshot"
VERSION = 1
TICKET_RE = re.compile(r"^T-\d+$", re.I)


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def slug(text, fallback="shot"):
    raw = re.sub(r"[^a-zA-Z0-9]+", "-", (text or "").strip()).strip("-").lower()
    return (raw[:48] or fallback)


def parse_bbox(text):
    """Parse 'x,y,w,h' into a page-coordinate bbox. All four must be finite numbers."""
    parts = [p.strip() for p in (text or "").split(",")]
    if len(parts) != 4:
        raise ValueError("bbox must be x,y,w,h")
    try:
        x, y, w, h = (float(p) for p in parts)
    except ValueError as exc:
        raise ValueError("bbox must be four numbers") from exc
    if w <= 0 or h <= 0:
        raise ValueError("bbox width and height must be > 0")
    if any(v != v or v in (float("inf"), float("-inf")) for v in (x, y, w, h)):
        raise ValueError("bbox values must be finite")
    return {"x": x, "y": y, "width": w, "height": h}


def ticket_slug(tid):
    tid = (tid or "").strip()
    m = re.match(r"^T-(\d+)$", tid, re.I)
    if m:
        return "t" + m.group(1)
    return tid.lower().replace(" ", "-") or "shots"


def default_out_dir(ticket, cwd=None):
    root = Path(cwd or os.getcwd())
    if ticket:
        return root / "docs" / "reviews" / ticket_slug(ticket)
    return root / "docs" / "reviews" / "shots"


def note_text(record):
    files = record.get("files") or {}
    html_name = files.get("html") or ""
    png_name = files.get("annotated_png") or files.get("png") or ""
    label = record.get("label") or ""
    selector = record.get("selector") or ""
    bbox = record.get("bbox")
    box = ""
    if bbox:
        box = " bbox=%.1f,%.1f,%.1f,%.1f" % (
            bbox["x"], bbox["y"], bbox["width"], bbox["height"])
    return (
        "evidence-shot: %s | %s | selector=%s%s | html=%s png=%s"
        % (KIND, label, selector or "(none)", box, html_name, png_name)
    )


def overlay_html(png_name, record):
    """Self-contained annotated view: the PNG plus a box + label drawn on top."""
    bbox = record.get("bbox")
    label = html.escape(record.get("label") or "")
    selector = html.escape(record.get("selector") or "")
    note = html.escape(record.get("note") or "")
    ticket = html.escape(record.get("ticket") or "")
    url = html.escape(record.get("url") or record.get("source_png") or "")
    captured = html.escape(record.get("captured_at") or "")
    mark = ""
    if bbox:
        mark = (
            '<div class="mark" style="left:%.2fpx;top:%.2fpx;width:%.2fpx;height:%.2fpx">'
            '<span class="cap">%s</span></div>'
            % (bbox["x"], bbox["y"], bbox["width"], bbox["height"], label)
        )
    return """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>shot %s</title>
<style>
  body { margin: 0; background: #111; color: #eee; font: 13px/1.4 ui-sans-serif, system-ui, sans-serif; }
  header { padding: 10px 14px; background: #1c1c1c; border-bottom: 1px solid #333; }
  header code { color: #f3d08c; }
  .stage { position: relative; display: inline-block; }
  .stage img { display: block; }
  .mark { position: absolute; border: 3px solid #ff3b30; box-shadow: 0 0 0 1px #fff;
          pointer-events: none; }
  .cap { position: absolute; left: -3px; top: -22px; background: #ff3b30; color: #fff;
         font: 700 12px/18px ui-sans-serif, system-ui, sans-serif; padding: 0 6px;
         white-space: nowrap; }
</style></head>
<body>
<header>
  <div><strong>%s</strong> %s</div>
  <div>selector <code>%s</code></div>
  <div>%s · %s</div>
  <div>%s</div>
</header>
<div class="stage">
  <img src="%s" alt="captured state">
  %s
</div>
</body></html>
""" % (label, KIND, ticket, selector or "(none)", url, captured, note, html.escape(png_name), mark)


def overlay_js(bbox, label):
    """DOM overlay used so a Playwright screenshot bakes the mark into PNG pixels."""
    return """
(() => {
  const b = %s;
  const label = %s;
  const old = document.getElementById("atman-shot-overlay");
  if (old) old.remove();
  const wrap = document.createElement("div");
  wrap.id = "atman-shot-overlay";
  wrap.setAttribute("data-atman-shot", "1");
  wrap.style.cssText = "position:absolute;left:0;top:0;width:0;height:0;z-index:2147483647;pointer-events:none;";
  const mark = document.createElement("div");
  mark.style.cssText = [
    "position:absolute",
    "left:" + b.x + "px",
    "top:" + b.y + "px",
    "width:" + b.width + "px",
    "height:" + b.height + "px",
    "border:3px solid #ff3b30",
    "box-shadow:0 0 0 1px #fff",
    "box-sizing:border-box",
  ].join(";");
  const cap = document.createElement("div");
  cap.textContent = label;
  cap.style.cssText = "position:absolute;left:-3px;top:-22px;background:#ff3b30;color:#fff;font:700 12px/18px ui-sans-serif,system-ui,sans-serif;padding:0 6px;white-space:nowrap;";
  mark.appendChild(cap);
  wrap.appendChild(mark);
  document.documentElement.appendChild(wrap);
  return true;
})()
""" % (json.dumps(bbox), json.dumps(label))


PICK_JS = r"""
() => new Promise((resolve) => {
  const hint = document.createElement("div");
  hint.id = "atman-shot-pick-hint";
  hint.textContent = "Click the element to mark";
  hint.style.cssText = "position:fixed;left:12px;top:12px;z-index:2147483647;background:#ff3b30;color:#fff;font:700 13px/1.3 ui-sans-serif,system-ui,sans-serif;padding:6px 10px;";
  document.documentElement.appendChild(hint);
  const hover = document.createElement("div");
  hover.style.cssText = "position:absolute;border:2px solid #ff3b30;pointer-events:none;z-index:2147483646;";
  document.documentElement.appendChild(hover);
  const stable = (el) => {
    if (!el || el.nodeType !== 1) return "";
    if (el.id) return "#" + CSS.escape(el.id);
    const tid = el.getAttribute("data-testid");
    if (tid) return '[data-testid="' + tid.replace(/"/g, '\\"') + '"]';
    const aria = el.getAttribute("aria-label");
    if (aria) return el.tagName.toLowerCase() + '[aria-label="' + aria.replace(/"/g, '\\"') + '"]';
    const parts = [];
    let n = el;
    while (n && n.nodeType === 1 && n !== document.documentElement) {
      let i = 1;
      let sib = n.previousElementSibling;
      while (sib) {
        if (sib.tagName === n.tagName) i += 1;
        sib = sib.previousElementSibling;
      }
      parts.unshift(n.tagName.toLowerCase() + ":nth-of-type(" + i + ")");
      n = n.parentElement;
    }
    return parts.join(" > ");
  };
  const boxOf = (el) => {
    const r = el.getBoundingClientRect();
    return {x: r.x + window.scrollX, y: r.y + window.scrollY, width: r.width, height: r.height};
  };
  const onMove = (e) => {
    const el = e.target;
    if (!el || el === hint) return;
    const b = boxOf(el);
    hover.style.left = b.x + "px";
    hover.style.top = b.y + "px";
    hover.style.width = b.width + "px";
    hover.style.height = b.height + "px";
  };
  const onClick = (e) => {
    e.preventDefault();
    e.stopPropagation();
    const el = e.target;
    if (!el || el === hint) return;
    document.removeEventListener("mousemove", onMove, true);
    document.removeEventListener("click", onClick, true);
    hint.remove();
    hover.remove();
    resolve({selector: stable(el), bbox: boxOf(el)});
  };
  document.addEventListener("mousemove", onMove, true);
  document.addEventListener("click", onClick, true);
})
"""


PAGE_BOX_JS = r"""
(sel) => {
  const el = document.querySelector(sel);
  if (!el) return null;
  const r = el.getBoundingClientRect();
  return {x: r.x + window.scrollX, y: r.y + window.scrollY, width: r.width, height: r.height};
}
"""


def write_pack(out_dir, record, png_bytes, annotated_bytes=None, stem=None):
    """Write PNG + sidecar + HTML. Returns the record with file names filled in."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = (record.get("captured_at") or utc_now()).replace(":", "").replace("-", "")
    stamp = stamp.replace("T", "-").rstrip("Z")
    name = "%s-%s" % (stamp, slug(record.get("label") or stem or "shot"))
    png_path = out / (name + ".png")
    html_path = out / (name + ".html")
    json_path = out / (name + ".json")
    png_path.write_bytes(png_bytes)
    files = {"png": png_path.name, "html": html_path.name, "json": json_path.name,
             "annotated_png": None}
    if annotated_bytes:
        ann = out / (name + "-marked.png")
        ann.write_bytes(annotated_bytes)
        files["annotated_png"] = ann.name
    record = dict(record)
    record["kind"] = KIND
    record["v"] = VERSION
    record["files"] = files
    html_path.write_text(overlay_html(png_path.name, record), encoding="utf-8")
    json_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    record["_paths"] = {
        "dir": str(out),
        "png": str(png_path),
        "html": str(html_path),
        "json": str(json_path),
        "annotated_png": str(out / files["annotated_png"]) if files["annotated_png"] else "",
    }
    return record


def playwright_missing_msg():
    return (
        "shot: Playwright is not installed. This command uses the existing "
        "Playwright path (not a new browser). Install with: "
        "python3 -m pip install playwright && python3 -m playwright install chromium"
    )


def capture_live(url, selector=None, pick=False, label="", headed=False,
                 viewport=(1280, 800), full_page=True, timeout_ms=15000):
    """Open URL with Playwright, resolve one element, screenshot raw + marked."""
    if pick and not headed:
        raise RuntimeError("shot: --pick needs --headed (a click in a visible window)")
    if not selector and not pick:
        raise RuntimeError("shot: --url needs --selector or --pick")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(playwright_missing_msg()) from exc

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=not headed)
        except Exception as exc:
            raise RuntimeError(
                "shot: Playwright Chromium is not installed (%s). "
                "python3 -m playwright install chromium" % exc
            ) from exc
        try:
            page = browser.new_page(viewport={"width": viewport[0], "height": viewport[1]})
            page.set_default_timeout(timeout_ms)
            try:
                page.goto(url, wait_until="domcontentloaded")
            except Exception as exc:
                raise RuntimeError("shot: could not open %s (%s)" % (url, exc)) from exc
            if pick:
                chosen = page.evaluate(PICK_JS)
                selector = (chosen or {}).get("selector") or ""
                bbox = (chosen or {}).get("bbox")
                if not selector or not bbox:
                    raise RuntimeError("shot: pick did not return an element")
            else:
                loc = page.locator(selector).first
                try:
                    loc.wait_for(state="visible")
                except Exception as exc:
                    raise RuntimeError(
                        "shot: selector %r not visible at %s" % (selector, url)
                    ) from exc
                bbox = page.evaluate(PAGE_BOX_JS, selector)
                if not bbox or bbox.get("width", 0) <= 0 or bbox.get("height", 0) <= 0:
                    raise RuntimeError(
                        "shot: selector %r has no box at %s" % (selector, url)
                    )
            raw = page.screenshot(full_page=full_page, type="png")
            page.evaluate(overlay_js(bbox, label))
            marked = page.screenshot(full_page=full_page, type="png")
            return {
                "png": raw,
                "annotated_png": marked,
                "bbox": bbox,
                "selector": selector,
                "url": url,
                "playwright": True,
                "headed": headed,
            }
        finally:
            browser.close()


def attach_ticket(board, ticket_id, text, load, save, whoami, now):
    t = load(board, ticket_id)
    who = whoami()
    t["notes"] = t.get("notes") or []
    t["notes"].append({"by": who, "at": now(), "text": text})
    save(board, t)
    return t


def cmd_shot(a, board, load=None, save=None, whoami=None, now=None):
    """CLI entry used by both tickets.py and ticket_board.cli."""
    url = (getattr(a, "url", "") or "").strip()
    png_in = (getattr(a, "png", "") or "").strip()
    selector = (getattr(a, "selector", "") or "").strip()
    pick = bool(getattr(a, "pick", False))
    label = (getattr(a, "label", "") or "").strip()
    note = (getattr(a, "note", "") or "").strip()
    ticket = (getattr(a, "ticket", "") or "").strip()
    out = (getattr(a, "out", "") or "").strip()
    bbox_text = (getattr(a, "bbox", "") or "").strip()
    headed = bool(getattr(a, "headed", False))
    as_json = bool(getattr(a, "json", False))

    if not label:
        sys.exit('shot: --label is required (the claim you are marking)')
    if ticket and not TICKET_RE.match(ticket):
        sys.exit("shot: ticket id must look like T-1026")
    if bool(url) == bool(png_in):
        sys.exit("shot: pass exactly one of --url or --png")
    if url and not selector and not pick:
        sys.exit("shot: --url needs --selector or --pick")
    if pick and png_in:
        sys.exit("shot: --pick is a live-page click; use --url")
    if png_in and not bbox_text:
        sys.exit("shot: --png needs --bbox x,y,w,h to mark the element")
    if png_in and selector:
        sys.exit("shot: --png cannot resolve a selector; pass --bbox")

    bbox = None
    if bbox_text:
        try:
            bbox = parse_bbox(bbox_text)
        except ValueError as exc:
            sys.exit("shot: %s" % exc)

    if out:
        out_dir = Path(out)
    else:
        out_dir = default_out_dir(ticket)

    captured = {
        "kind": KIND,
        "v": VERSION,
        "ticket": ticket,
        "url": url,
        "source_png": png_in,
        "selector": selector,
        "picked": pick,
        "label": label,
        "note": note,
        "bbox": bbox,
        "captured_at": utc_now(),
        "playwright": False,
        "headed": headed,
    }

    if png_in:
        src = Path(png_in)
        if not src.is_file():
            sys.exit("shot: no such png %s" % png_in)
        png_bytes = src.read_bytes()
        if png_bytes[:8] != b"\x89PNG\r\n\x1a\n":
            sys.exit("shot: %s is not a PNG" % png_in)
        pack = write_pack(out_dir, captured, png_bytes)
    else:
        try:
            live = capture_live(
                url, selector=selector or None, pick=pick, label=label,
                headed=headed,
            )
        except RuntimeError as exc:
            sys.exit(str(exc))
        captured["selector"] = live["selector"]
        captured["bbox"] = live["bbox"]
        captured["playwright"] = True
        captured["headed"] = live["headed"]
        pack = write_pack(
            out_dir, captured, live["png"], annotated_bytes=live["annotated_png"],
        )

    if ticket:
        if load is None or save is None:
            sys.exit("shot: --ticket needs the atm board helpers")
        if not board or not os.path.isdir(board):
            sys.exit("shot: --ticket needs a board (TICKETS_DIR or a live .tickets/)")
        try:
            attach_ticket(
                board, ticket, note_text(pack),
                load=load, save=save, whoami=whoami or (lambda: ""),
                now=now or utc_now,
            )
        except Exception as exc:
            sys.exit("shot: could not attach to %s (%s)" % (ticket, exc))

    if as_json:
        printable = {k: v for k, v in pack.items() if not k.startswith("_")}
        printable["paths"] = pack.get("_paths")
        print(json.dumps(printable, indent=2))
    else:
        paths = pack.get("_paths") or {}
        print("shot %s" % (pack.get("label") or ""))
        print("  html %s" % paths.get("html"))
        print("  json %s" % paths.get("json"))
        print("  png  %s" % paths.get("png"))
        if paths.get("annotated_png"):
            print("  mark %s" % paths.get("annotated_png"))
        if ticket:
            print("  attached %s" % ticket)
    return pack


def register_parser(sub):
    """Shared argparse for tickets.py and cli.py."""
    c = sub.add_parser(
        "shot",
        help="capture a UI state, mark one element, write attachable evidence",
    )
    c.add_argument("--url", default="", help="live page to capture with Playwright")
    c.add_argument("--png", default="", help="existing screenshot to annotate")
    c.add_argument("--selector", default="", help="CSS selector of the element to mark")
    c.add_argument("--pick", action="store_true",
                   help="headed click-to-pick; writes the resolved selector")
    c.add_argument("--bbox", default="", help="x,y,w,h in page pixels (required with --png)")
    c.add_argument("--label", required=True, help="the claim drawn on the mark")
    c.add_argument("--note", default="", help="extra prose stored in the sidecar")
    c.add_argument("--ticket", default="", help="attach a note on this ticket")
    c.add_argument("--out", default="",
                   help="directory for the evidence pack (default docs/reviews/<ticket>)")
    c.add_argument("--headed", action="store_true",
                   help="show the browser (required for --pick)")
    c.add_argument("--json", action="store_true", dest="json",
                   help="print the sidecar to stdout")
    return c
