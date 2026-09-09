# Atman tokens

Paste-ready light and dark tokens for the command-board UI. These implement the
accepted four-product family direction while retaining Atman's formation-dot
mark and command vocabulary.

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
  --bg:#0b1416;
  --fg:#e6eeea;
  --mute:#8fa4a6;
  --line:#243236;
  --card:#121c1e;
  --surface:#0f191b;
  --chip:#182427;
  --acc:#c8f04a;
  --on-acc:#142022;
  --ok:#5ec7b0;
  --warn:#e0a53d;
  --bad:#e85d4c;
  --progress:#6f8c8f;
}

body[data-theme=light]{
  color-scheme:light;
  --bg:#f3f6f4;
  --fg:#142022;
  --mute:#6a7c7f;
  --line:#d5ded9;
  --card:#fbfdfc;
  --surface:#eef2f0;
  --chip:#e8eeea;
  --ok:#187a67;
  --warn:#93610a;
  --bad:#b43a31;
  --progress:#789396;
}
```

Lime is the single action accent. Use it for the primary or next action,
selected navigation, the portfolio caret, and keyboard focus. It never means
healthy, connected, ready, or working. Progress bars use `--progress`.

Mint, amber, and red are semantic status colors:

| Token | Meaning | Examples |
|---|---|---|
| `--ok` | Confirmed positive state | Connected board, acknowledged message |
| `--warn` | Attention is required | Reconnecting, pending acknowledgement, aging work |
| `--bad` | Blocked or failed | Board unavailable, blocked work, usage limit |

Status always includes text and a shape. Do not rely on color alone. Do not
pulse a live-agent indicator or add glow.

## Migration from the first Atman palette

| Retired token | Replacement |
|---|---|
| Blue `--acc:#5b8def` | Lime `--acc:#c8f04a` for actions only |
| Amber `--intervene` | `--acc` for ordinary actions; `--warn` only when the state warrants attention |
| Mint `--live` | `--ok` for a confirmed status with an adjacent text label |
| Dark-only `--bg`, `--fg`, `--card`, `--line`, `--mute` | Theme mappings above |

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
