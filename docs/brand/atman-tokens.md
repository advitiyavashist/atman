# Atman tokens (T-347)

Status: **CEO ACCEPT** (locked 2026-09-07). Week-1 mark **A — formation dots**. Tokens keep T-276 dark command-board roots and add `--live`.

Full direction (name, PMM lock, Steer contrast, mark shortlist) lives on **[PR #4](https://github.com/advitiyavashist/atman/pull/4)** (`docs/brand/atman-brand-direction-v1.md`). That file is not on `main` yet — do not merge PR #4 from this ticket. Stamp + assets are here so T-345 can paste without waiting on that merge.

**Do not edit `tickets.py` `UI_HTML` here.** `ui/cursor-atman-ui` owns T-345.

---

## ACCEPT stamp

| Lock | Value |
|---|---|
| Name | Soft-lock **Atman**. UI chrome `atman` / `ATMAN`. Prose **Atman**. |
| Domain | **HOLD** — do not invent URLs. |
| Mark Week-1 | **A — formation dots** (5-dot 3–2 constellation). Not a DAG / node–edge graph. |
| Alternate | **B — open ring** held. **C — overlapping seats** only if A fails 16px (it does not). |
| Palette | T-276 dark roots + `--live:#3ee8c5` sparse. |
| Reject | Steer `^`, pale lime paper/ink, lock/shield, robot, neon glow spam, external assets, build step. |

Assets: [`assets/`](assets/README.md) — `mark.svg`, `mark-16.svg`, `lockup.svg`, `mark-b-open-ring.svg` (HOLD).

---

## Paste-ready `:root` (UI_HTML)

```css
:root {
  --bg: #0c0e12;
  --fg: #e8e6e1;
  --mute: #8a8d96;
  --line: #22262e;
  --card: #141820;
  --acc: #5b8def;
  --chip: #1c2433;
  --ok: #3dbe7a;
  --warn: #e0a53d;
  --bad: #e85d4c;
  --review: #9b7dff;
  --live: #3ee8c5;
  --intervene: var(--live);
  --blocked: var(--bad);
  --ready: var(--acc);
  --flight: var(--warn);
}
```

T-276 lane aliases (`--blocked` / `--ready` / `--flight`) stay mapped to `--bad` / `--acc` / `--warn`. Do not invent a second dark theme.

---

## `--live` usage

`--live` `#3ee8c5` is electric mint for **intervene / live only**. Sparse.

| Do | Don't |
|---|---|
| Operator is on the pitch (intervene chip, live seat pulse, "someone is here") | Page wash, full-header tint, card fill |
| One small signal per surface | Glow / blur / neon field / drop-shadow bloom |
| Pair with `--fg` / `--card` on `--bg` | Recolor the formation-dots mark |
| Keep `--acc` `#5b8def` for everyday actions | Replace `--acc` or `--ok` with mint |
| `--intervene` maps to `--live` | Map `--intervene` to `--warn` unless mint is later rejected as lime-adjacent |

Steer visual temperature is light paper / ink / pale lime. `--live` is a different hue and a different job. Do not reuse Steer's `^` or lime wash.

No fake utilization percentages. Status color is evidence-backed or it is not shown.

---

## Mark in UI (inline / data-URI)

Prefer inline SVG so `currentColor` follows `--fg`. Default `color` on the SVG is `#e8e6e1`.

**Week-1 mark A** (24 viewBox — size with CSS `width`/`height`):

```html
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="24" height="24" color="#e8e6e1" fill="currentColor" role="img" aria-label="Atman"><circle cx="5.1" cy="6.8" r="2.15"/><circle cx="11.4" cy="5.6" r="2.15"/><circle cx="18.8" cy="7.4" r="2.15"/><circle cx="7.2" cy="17.6" r="2.15"/><circle cx="17.0" cy="16.6" r="2.15"/></svg>
```

**16px optical** (favicon / dense chrome):

```html
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="16" height="16" color="#e8e6e1" fill="currentColor" role="img" aria-label="Atman"><circle cx="3.4" cy="4.55" r="1.45"/><circle cx="7.6" cy="3.75" r="1.45"/><circle cx="12.55" cy="4.95" r="1.45"/><circle cx="4.8" cy="11.75" r="1.45"/><circle cx="11.35" cy="11.1" r="1.45"/></svg>
```

Data-URI (24 mark, no build step):

```
data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' width='24' height='24' color='%23e8e6e1' fill='currentColor' role='img' aria-label='Atman'%3E%3Ccircle cx='5.1' cy='6.8' r='2.15'/%3E%3Ccircle cx='11.4' cy='5.6' r='2.15'/%3E%3Ccircle cx='18.8' cy='7.4' r='2.15'/%3E%3Ccircle cx='7.2' cy='17.6' r='2.15'/%3E%3Ccircle cx='17.0' cy='16.6' r='2.15'/%3E%3C/svg%3E
```

No connecting lines. If a renderer adds edges, that is a bug — the mark is a constellation, not a graph.

---

## Name surfaces

| Surface | Form |
|---|---|
| UI chrome / wordmark | `atman` lowercase (`lockup.svg`) |
| Tight chrome / tabs | `ATMAN` |
| Prose, tickets, docs | Atman |

Atman domain: **HOLD**. Do not invent a URL.
