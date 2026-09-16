# Atman public landing

Static page. No build step, no JavaScript, no framework. GitHub Pages
publishes this directory as https://advitiyavashist.github.io/atman/.

Copy lock (T-1047):

- **Promise (H1, same string as the repository README):** The next ticket opens only after someone else accepts that commit.
- **Four sections only:** the promise, one path (install → objective →
  two agents → accept → the dependent opens), tested vs planned (link the
  README table, do not duplicate it), how to start.
- **Contact:** GitHub issues. Byline: Built by @abnormal.
- **CLI name:** `atm` is primary. `tickets` is the compatibility alias,
  once, in Start.
- **Hero demo:** a placeholder HTML comment only. Do not add a substitute
  image; wire PR #217 on the next line when it lands.

Do not use jsDelivr, raw GitHub, or `landing/index.html` as the public URL —
those serve `text/plain`.

## Captures under `landing/assets/`

Images live next to `index.html` so a deploy that publishes only this
directory can still load them. The live page does not embed a demo image.
`t971-app-work-1440.png` remains the `og:image` and the README product
capture. Never reference `../docs/brand/evidence` from the page. Captures
are never the live board (`TICKETS_DIR` on a throwaway board).

| File | What it is |
|---|---|
| `t971-app-work-1440.png`, `-768.png` | `atm ui` Work tab from a throwaway `atm quickstart` board. Also the `og:image`. |
| `t889-work-graph-1400.png` | Work graph phases. Not on the live page after T-1047. |
| `t732-dashboard-*.png` | Earlier capture, kept because historical docs may still name it. |

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
GitHub Pages root.

**No custom domain is registered.** Do not invent one.
