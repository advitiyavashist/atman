# Atman tokens

Paste-ready dark tokens for Atman. **Do not share Steer’s lime `#c8f04a` or teal-black `#0b1416`.** T-713 retracts the T-606 family-lime lock for Atman surfaces.

Chrome wordmark is lowercase `atman` with tracking — never `ATMAN`.

```css
.wordmark{
  font:650 16px/1.2 ui-sans-serif,system-ui,-apple-system,Segoe UI,Helvetica,Arial,sans-serif;
  letter-spacing:.22em;
  text-transform:lowercase;
}
```

## Theme tokens

```css
:root{
  color-scheme:dark;
  --bg:#0c0e12;
  --fg:#ece8e1;
  --mute:#9a958c;
  --line:#2a2d34;
  --card:#161820;
  --surface:#12141a;
  --chip:#1c2028;
  --acc:#c4b49a;
  --on-acc:#14120e;
  --ok:#6f9e96;
  --warn:#e0a53d;
  --bad:#e85d4c;
  --progress:#6f8c8f;
}
```

Brass is the action accent. Use it for the primary or next action and keyboard focus. It never means healthy, connected, ready, or working. Progress bars use `--progress`. Do not wash the page with accent. Do not use a graph-paper grid (that read as Steer).

Mint/teal `--ok` is status only, with a text label. Do not pulse a live-agent indicator or add glow.

## Migration from T-606 family lime

| Retired (Steer-family) | Replacement |
|---|---|
| Lime `--acc:#c8f04a` | Brass `--acc:#c4b49a` |
| Teal-black `--bg:#0b1416` | `#0c0e12` |
| Graph-paper body grid | Flat `--bg` |
| Mint page wash | None; `--ok` on labels only |

The `^` belongs to steer.md and may appear only on portfolio navigation. It is not an Atman logo.

Mint, amber, and red are semantic status colors:

| Token | Meaning | Examples |
|---|---|---|
| `--ok` | Confirmed positive state | Connected board, acknowledged message |
| `--warn` | Attention is required | Reconnecting, pending acknowledgement, aging work |
| `--bad` | Blocked or failed | Board unavailable, blocked work, usage limit |

Status always includes text and a shape. Do not rely on color alone. Do not
pulse a live-agent indicator or add glow.

## Migration from earlier Atman palettes

| Retired token | Replacement |
|---|---|
| Blue `--acc:#5b8def` | Brass `--acc:#c4b49a` |
| Family lime `--acc:#c8f04a` | Brass `--acc:#c4b49a` |
| Amber `--intervene` | `--acc` for ordinary actions; `--warn` only when the state warrants attention |
| Mint `--live` | `--ok` for a confirmed status with an adjacent text label |

Do not keep the retired names as aliases; competing meanings make later UI
changes ambiguous.

## Mark

- [Formation-dot mark](assets/mark.svg): five dots, no connecting strokes.
- [Lockup](assets/lockup.svg): formation dots plus lowercase `atman`.
- [Size preview](assets/preview.png): 16, 22, 32, and 64 pixels.
- Render the existing geometry in `--fg` so it remains visible in both themes.
- The `^` belongs to steer.md and may appear only on portfolio navigation. It
  is not an Atman logo.

## Reject

Locks, shields, robot marks, neon glow, node-edge graphs, fake telemetry,
invented product URLs, and product-specific colorways.
