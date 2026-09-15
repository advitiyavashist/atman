# Atman public landing

Static page. No build step, no JavaScript, no framework. One source-install example creates a sample project before opening the app.
Provider connections are explained in the first-session guide.

**Live URL (renders as HTML, including mobile Safari):**
https://advitiyavashist.github.io/atman/

Do not use jsDelivr, raw GitHub, or `landing/index.html` as the public URL —
those serve `text/plain`. GitHub Pages publishes this directory as the site
root (`text/html`).

Copy lock (T-787, renamed for `atm` in T-971):

- **Promise:** Atman coordinates the agents you already run.
- **Philosophy:** Tasks, decisions and handoffs stay with the project.
  Agents retain separate identities and worktrees; results require review.
- **Integration / product flow / efficiency / workflow dependency graph:** one
  sentence each, immediately after philosophy — not buried.
- **Contact us:** GitHub issues (`CONTRIBUTING.md`). Do not invent an email.
- **CLI name:** `atm` is primary (T-809). `tickets` is mentioned once per
  section at most, and only as the compatibility alias of the same file.

The shareable surface is **atm ui**: Objective / Team / Work / Intervene on
https://advitiyavashist.github.io/atman/ (`#app`), as a checked-in product
capture — not a hosted live board.

First-class sections (T-794), not buried in the four-sentence grid:

1. **The app** — `atm ui` capture first: Objective, Team, Work, Intervene.
   First session uses an isolated source prefix, clears ambient
   `TICKETS_DIR`, checks `atm where` and creates a sample with `atm quickstart`.
   The sample does not launch a provider agent. Directly under the capture, a three-column status strip
   says what is **on main**, **in review** and **planned**; every row must be
   checkable against `main` and open PRs on the day it is edited.
2. **Workflow dependency graph** — Work stays invisible until its dependencies
   are done. Live view is local `atm ui` Work → Graph, shown with the T-889
   capture of node phases.
3. **Engineering roadmap** — V0 today through V4. Control plane stays;
   intelligence behind `atm route` improves. No invented ship dates.
4. **Per-turn efficiency** — `—` until a done ticket reports. Unknown is not
   zero.

Spike **Clone → `atm connect` as `atman-<seat>` → `atm ui`**. Fewest turns and
measured cost stay `—` until a done ticket reports. The app images are
checked-in product captures; the page does not point visitors at a
machine-local demo.

Visual: dark `#0c0e12`, bone type, brass CTA — command-board / team roster.
Formation-dot mark. No lime, grid wallpaper, or Claude-only onboarding.

## Captures under `landing/assets/`

Images live next to `index.html` so a deploy that publishes only this directory
can still load them. Never reference `../docs/brand/evidence` from the page.

| File | What it is |
|---|---|
| `t971-app-work-1440.png`, `-768.png` | `atm ui` Work tab from `tickets.py` at the T-971 base of `main`, served against a throwaway board made by `atm quickstart` plus two extra `--deps` tickets and one claim (never the live board). Headless Chrome, 1440×1000 and 768×1100. Also the `og:image`. |
| `t889-work-graph-1400.png` | Copy of `docs/brand/evidence/t889-work-graph-dark.png`: the Work graph with done, task posted, waiting, reserved, working, in review, blocked, capture and hold as distinct phases. |
| `t732-dashboard-*.png` | Earlier live-board capture, kept because the repository README still embeds it. The page no longer uses it. |

To refresh the app capture: build a throwaway board with
`TICKETS_DIR=<scratch>/.tickets` set for every command (the spawned-worker
environment otherwise pins commands to the live board), run `atm ui --port N`
there, then shoot with headless Chrome and a poll-until-the-PNG-is-stable loop
(Chrome does not exit on its own because the page keeps polling).

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
GitHub Pages root. The product capture is `assets/t971-app-work-1440.png`
next to `index.html`.

**No custom domain is registered.** Do not invent one. Do not restore
`atman-ai.vercel.app`.
