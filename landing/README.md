# Atman public landing

Static page. No build step, no JavaScript, no framework.

Copy follows the T-714 spike: Clone → Claude Code seat → `tickets ui`,
with fewest-turns / least measured cost on the hero, usage-limit recovery
early, and Brahman as optional research. The dashboard image is a checked-in
product capture; the page does not point visitors at a machine-local demo.

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
