# Atman public landing

Static page. No build step, no JavaScript, no framework. First-seat examples
use CSS radio tabs so the ordered providers (Claude Code, Codex, Cursor, Grok
Bot, custom) can be inspected without a script.

Copy follows T-726: Atman is the local team runtime. Lead line is *Bring the
agents you already use. We make them one team.* Any first seat. Fewest turns
and measured cost stay `—` until a done ticket reports. The dashboard image is a
checked-in product capture; the page does not point visitors at a machine-local
demo.

Visual direction is ink/navy surfaces, action blue, amber intervene, and mint
only on a live seat. Formation-dot mark. No Steer lime, grid wallpaper, or
Claude-only onboarding.

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
