# Atman public landing

Static page. No build step, no JavaScript, no framework.

Copy follows the T-380 CEO-accepted brief (Hero / What it is / How / Metrics /
Start / Close). `docs/product/pm-atman-landing-v1.md` lives on the Steer
pm-work tree and was not readable from this repo.

## Open locally

From the repository root:

```sh
python3 -m http.server 4173 --directory landing
```

Then open [http://127.0.0.1:4173/](http://127.0.0.1:4173/).

Or open `landing/index.html` directly in a browser.

## Deploy static

Point any static host at this folder. GitHub Pages is optional (`/landing`).

**No custom domain is registered.** Do not invent one.
