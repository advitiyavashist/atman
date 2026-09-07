# Atman public landing

Static page. No build step, no JavaScript, no framework.

## Open locally

From the repository root:

```sh
python3 -m http.server 4173 --directory landing
```

Then open [http://127.0.0.1:4173/](http://127.0.0.1:4173/).

Or open `landing/index.html` directly in a browser.

## Deploy static

Point any static host at this folder (`index.html` is the root).

GitHub Pages is optional: set the source to `/landing` (or publish this folder
as the site root). GitHub will then assign a `*.github.io` URL.

**No custom domain is registered.** Do not invent one. A provider URL exists
only after you enable a host.

## Copy sources

Hero and positioning follow `docs/brand/` and the T-371 promise
(*Finish more work at least cost — in the fewest turns*). PM files
`docs/product/pm-atman-product-promise-v1.md` and
`docs/product/pm-atman-v1-features-ux.md` were not in the tree when this
shipped.
