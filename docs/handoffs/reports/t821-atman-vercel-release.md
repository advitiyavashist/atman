# T-821 Atman Vercel release receipt

Release time: 2026-09-12 23:55 SGT

## Production pin

- Source: `advitiyavashist/atman` `origin/main@a44ebc08810b70df09df3c391f91b4b9c4c6830c`
- Vercel scope/project: `advitiyavashists-projects/atman`
- Project ID: `prj_qkalfTo59vqaFkTrZT4QsoVzLAbL`
- Deployment ID: `dpl_AfeoT4gd5TTfQiQAfEsHzqWfsfd9`
- Immutable deployment: `https://atman-6ecww9ynf-advitiyavashists-projects.vercel.app`
- Production alias: `https://atman-xi.vercel.app`
- Vercel state: `READY`, target `production`

Only the `landing/` directory from the pinned reviewed main commit was uploaded.
No unfinished branch content was included and no new Vercel project was created.

## Verification

The production alias returned HTTP 200 with `content-type: text/html;
charset=utf-8`. Served asset hashes exactly matched the pinned checkout:

| Asset | SHA-256 |
| --- | --- |
| `/` and `landing/index.html` | `cb77a24080381b0e2e483ac474fcd87639e34c2e88f587f5d83014c629681a59` |
| `/styles.css` and `landing/styles.css` | `0b5a5c7c4f71a156f434e0f7256e3c3a9662e69ae7800a2cac6f2cd2a303ea8e` |
| `/assets/t732-dashboard-1440.png` | `c0c7e173d9df067f0926d79082b914882b444930906db367eaaddf805975e480` |
| `/assets/t732-dashboard-390.png` | `f79df508aa4300bc7559fbd98d8ac7f7794391b73b24898689112e54f2c11744` |

Chrome DevTools device emulation checked 390x844, 768x1024, and 1440x1000.
At every width, `documentElement.scrollWidth` equalled the viewport width. The
responsive product capture selected the 390, 768, and 1440 pixel asset,
respectively. The production copy explicitly says the displayed board is a
checked-in capture and that the live board remains local; its internal CTAs
resolve to page anchors rather than a fabricated hosted board.

Landing contract verification:

```text
python3 -m pytest -q tests/test_t787_landing_promise.py \
  tests/test_t794_landing_pages.py \
  tests/test_t732_dashboard_identity.py \
  tests/test_t726_landing_packet.py \
  tests/test_t713_landing_packet.py
38 passed in 0.33s
```

The page does not visibly print a commit or release version. This receipt pins
the release through the immutable deployment ID and byte-for-byte source asset
hashes. Adding a visible release identifier is a separate product decision and
was not mixed into this main-only deployment.

## Commands and rollback

The existing project was linked from `landing/`, then released with:

```sh
vercel link --yes --project atman --scope advitiyavashists-projects
vercel deploy --prod --yes --scope advitiyavashists-projects
```

The previous production deployment was
`dpl_397FtahhYGGvAzBtw3fPSqnCjTSL` at
`https://atman-o6a1xjhhe-advitiyavashists-projects.vercel.app`. To roll the
production aliases back to it:

```sh
vercel rollback dpl_397FtahhYGGvAzBtw3fPSqnCjTSL --yes \
  --scope advitiyavashists-projects
```

After any rollback, repeat the HTTP, deployment metadata, responsive viewport,
and asset-hash checks above.
