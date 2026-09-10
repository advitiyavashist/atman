# Atman public landing

Static page. No build step, no JavaScript, no framework. First-seat examples
use CSS radio tabs so the ordered providers (Claude Code, Codex, Cursor, Grok
Bot, custom) can be inspected without a script.

Copy follows the T-713 / T-726 lock: **local control plane / team runtime for
multiple coding agents in one repo.** Then *Bring the agents you already use.
We make them one team.* Spike **Clone → any first seat → `tickets ui`**.
Fewest turns and measured cost stay `—` until a done ticket reports. The
dashboard image is a checked-in product capture; the page does not point
visitors at a machine-local demo.

Visual: dark `#0c0e12`, bone type, brass CTA — command-board / team roster.
Formation-dot mark. No lime, grid wallpaper, or Claude-only onboarding.

The dashboard image lives under `landing/assets/` so a deploy that publishes
only this directory can still load the capture.

## Open locally

From `landing/`:

```sh
python3 -m http.server 4173
```

Then open `/` on the address printed by Python. This is local preview only.

## Deploy static

Publish the `landing/` directory as the site root. The product capture is
`assets/t732-dashboard-1440.png` next to `index.html`. GitHub Pages is optional.

**No custom domain is registered.** Do not invent one.
