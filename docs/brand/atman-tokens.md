# Atman tokens

Paste-ready CSS for the command-board UI. One dark theme.

Chrome wordmark is lowercase `atman` with tracking — never `ATMAN`.

```css
.wordmark{
  font:650 16px/1.2 ui-sans-serif,system-ui,-apple-system,Segoe UI,Helvetica,Arial,sans-serif;
  letter-spacing:.22em;
  text-transform:lowercase; /* never ATMAN */
}
```

---

## Paste-ready `:root`

```css
:root{
  --bg:#0c0e12;
  --fg:#e8e6e1;
  --mute:#8a8d96;
  --line:#22262e;
  --card:#141820;
  --acc:#5b8def;
  --chip:#1c2433;
  --ok:#3dbe7a;
  --warn:#e0a53d;
  --bad:#e85d4c;
  --review:#9b7dff;
  --blocked:#e85d4c;
  --ready:#5b8def;
  --flight:#e0a53d;
  /* Intervene CTAs — same amber as --warn. Do not mint-wash these. */
  --intervene:#e0a53d;
  /* Live seat / pulse only. Sparse. Never a page wash, never a glow field. */
  --live:#3ee8c5;
}
```

`--acc` is the everyday action blue. `--intervene` and `--live` are distinct signals.

---

## `--intervene` vs `--live`

| Token | Value | Use | Do not |
|---|---|---|---|
| `--intervene` | `#e0a53d` (warn amber) | Intervene CTAs: route, unblock, reassign, nudge, msg. Operator is about to act. | Do not replace this amber with mint. Do not use mint on primary Intervene buttons. |
| `--warn` | `#e0a53d` | Attention, aging updates, In-flight lane. Same hex as `--intervene`. | |
| `--live` | `#3ee8c5` electric mint | Pulse / live-seat indicator only. One 8px dot, or a 1-item chip. | Page wash, glow field, neon spam, Intervene CTA fill. |

`--live` is a signal, not a theme. If a control fires an intervene, it is amber. If a seat is live, it may pulse mint.

Intervene buttons should use `:focus-visible { outline: 2px solid var(--live); outline-offset: 2px }` (or `var(--acc)`) so keyboard matches visual honesty.

---

## Contrast on `--bg` `#0c0e12`

WCAG 2.x relative luminance. Icons / UI graphics need **3:1** (1.4.11). Text needs **4.5:1** (AA).

| Token | Hex | Ratio vs `#0c0e12` | Icons AA | Text AA |
|---|---|---|---|---|
| `--fg` | `#e8e6e1` | **15.49:1** | pass | pass |
| `--live` | `#3ee8c5` | **12.47:1** | pass | pass |
| `--intervene` / `--warn` | `#e0a53d` | **8.85:1** | pass | pass |
| `--ok` | `#3dbe7a` | **8.14:1** | pass | pass |
| `--review` | `#9b7dff` | **6.23:1** | pass | pass |
| `--acc` | `#5b8def` | **5.98:1** | pass | pass |
| `--mute` | `#8a8d96` | **5.82:1** | pass | pass |
| `--bad` | `#e85d4c` | **5.61:1** | pass | pass |

Mint on `#0c0e12` clears AA for icons (and for text). Still use it sparsely.

---

## Mark

- File: [assets/mark.svg](assets/mark.svg) — five `#e8e6e1` dots, no strokes between them.
- Lockup: [assets/lockup.svg](assets/lockup.svg) — mark + `atman` (`.22em` tracking). Runtime chrome uses CSS `.wordmark` (already above); the SVG is a static reference.
- Preview: [assets/preview.png](assets/preview.png) — 16 / 22 / 32 / 64 on `--bg`.
- At 16px it must read as a **team constellation**, not a flowchart. If it ever needs edges to be legible, formation-dots has failed — then linked seats, not a DAG.
- Inline SVG / data-URI when the UI needs it. No CDN, no build step, no external font file.

---

## Reject

Locks / shields, robot marks, neon glow spam, node–edge DAG icons, invented product URLs.
