# Atman brand assets (T-347)

**CEO ACCEPT** (2026-09-07) on the T-346 / [PR #4](https://github.com/advitiyavashist/atman/pull/4) shortlist: ship **mark A — formation dots**. Hold B. Tokens + `--live` in [`../atman-tokens.md`](../atman-tokens.md).

`docs/brand/atman-brand-direction-v1.md` is not on `main` until PR #4 merges. Do not merge PR #4 from this ticket. Do not edit `tickets.py` `UI_HTML` (T-345).

No webfont, no CDN, no build step. Inline SVG or data-URI only.

---

## Files

| File | Use |
|---|---|
| [`mark.svg`](mark.svg) | Week-1 mark A. 24×24 viewBox (in 22–32). 5 dots, 3–2, fluid spacing. |
| [`mark-16.svg`](mark-16.svg) | Optical 16px of the same formation (larger relative dots). |
| [`lockup.svg`](lockup.svg) | Mark + lowercase `atman` wordmark (paths inlined; no webfont shipped). |
| [`mark-b-open-ring.svg`](mark-b-open-ring.svg) | **HOLD B** — not Week-1. |
| [`preview.html`](preview.html) | Dark `#0c0e12` sheet at 16 / 22 / 32 / 64. Open locally. |
| [`preview.png`](preview.png) | Raster of that sheet. |

**C (overlapping seats)** was not cut. A stays readable at 16px — five distinct dots, no merge, reads as a team on a pitch, not a flowchart.

---

## 16px check

On `#0c0e12`, `mark-16.svg` at 16×16:

- Five separate discs (gutter ≥ ~1.9px). No shared outline, no edges.
- Reads as three up / two covering (Total Football coverage). Slightly irregular — not a grid, plus-sign, or hub-and-spokes.
- `currentColor` default `#e8e6e1`. Do not tint with `--live`.

`preview.png` / `preview.html` show 16 / 22 / 32 / 64 side by side.

---

## Rejected (do not add)

Steer `^` · pale lime paper/ink · lock / shield · robot · node–edge DAG · neon glow / blur · external images or fonts · a compile step for the mark.
