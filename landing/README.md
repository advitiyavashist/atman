# Atman public landing

Static page. No build step, no JavaScript, no framework. First-seat examples
use CSS radio tabs so the ordered providers (Claude Code, Codex, Cursor, Grok
Bot, custom) can be inspected without a script.

**Live URL (renders as HTML, including mobile Safari):**
https://advitiyavashist.github.io/atman/

Do not use jsDelivr, raw GitHub, or `landing/index.html` as the public URL —
those serve `text/plain`. GitHub Pages publishes this directory as the site
root (`text/html`).

Copy lock (T-787):

- **Promise:** Atman coordinates the agents you already run.
- **Philosophy:** Not a multi-agent framework, not shared memory, not a model
  router. Mail is the ticket board. Connecting an agent should feel like using
  Atman, not the provider.
- **Integration / product flow / efficiency / workflow dependency graph:** one
  sentence each, immediately after philosophy — not buried.
- **Contact us:** GitHub issues (`CONTRIBUTING.md`). Do not invent an email.

The shareable surface is **tickets ui**: Objective / Team / Work / Intervene
on https://advitiyavashist.github.io/atman/ (`#app`), as a checked-in product
capture — not a hosted live board.

First-class sections (T-794), not buried in the four-sentence grid:

1. **The app** — `tickets ui` capture first: Objective, Team, Work, Intervene.
   First session is `tickets connect` as `atman-<seat>`. Local BYOA, not a
   hosted cloud demo.
2. **Workflow dependency graph** — Work stays invisible until its dependencies
   are done. Live view is local `tickets ui` Work → Graph.
3. **Engineering roadmap** — V0 today through V4. Control plane stays;
   intelligence behind `tickets route` improves. No invented ship dates.
4. **Per-turn efficiency** — `—` until a done ticket reports. Unknown is not
   zero.

Spike **Clone → `tickets connect` as `atman-<seat>` → `tickets ui`**. Fewest turns and measured cost
stay `—` until a done ticket reports. The dashboard image is a checked-in product
capture; the page does not point visitors at a machine-local demo.

Visual: dark `#0c0e12`, bone type, brass CTA — command-board / team roster.
Formation-dot mark. No lime, grid wallpaper, or Claude-only onboarding.

The dashboard image lives under `landing/assets/` so a deploy that publishes
only this directory can still load the capture.

**No custom domain is registered.** The public site is GitHub Pages on
`advitiyavashist/atman`.

## Open locally

From `landing/`:

```sh
python3 -m http.server 4173
```

Then open `/` on the address printed by Python. This is local preview only.

## Deploy static

GitHub Actions (`.github/workflows/pages.yml`) publishes `landing/` as the
GitHub Pages root. The product capture is `assets/t732-dashboard-1440.png`
next to `index.html`.

**No custom domain is registered.** Do not invent one. Do not restore
`atman-ai.vercel.app`.
