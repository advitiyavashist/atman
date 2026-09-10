# Atman public landing

Static page. No build step, no JavaScript, no framework.

Copy follows the T-713 lock: **team runtime / BYOA**, spike **Clone → any first
seat → `tickets ui`**. Example seats, in order: Claude Code, Codex, Cursor,
Grok Bot, custom. Fewest turns and least measured cost are **secondary**
(promise strip; `—` until a done ticket reports). Usage-limit recovery stays
early. Brahman is optional research. The dashboard image is a checked-in
product capture; the page does not point visitors at a machine-local demo.

Do not make this page resemble Steer (Try the API, evaluate CTA, DLP, caret
logo). Packet: [`docs/brand/t713-brand-acceptance-2026-09-10.md`](../docs/brand/t713-brand-acceptance-2026-09-10.md).

## Open locally

From the repository root, serve the whole checkout so the page can load the
checked-in dashboard capture:

```sh
python3 -m http.server 4173
```

Then open `/landing/` on the address printed by Python. This is local preview
only.

## Deploy static

Publish the repository root and use `/landing/` as the page path so relative
links to checked-in evidence remain valid. GitHub Pages is optional.

**No custom domain is registered.** Do not invent one.
