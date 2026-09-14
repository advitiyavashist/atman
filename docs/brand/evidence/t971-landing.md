# T-971 — Landing reworked for `atm` and the app on main

Captured with headless Google Chrome against `python3 -m http.server 4173
--directory landing` at the T-971 head. The app captures embedded in the page
(`landing/assets/t971-app-work-*.png`) were taken the same way against
`tickets.py ui` on a throwaway board created with an explicit `TICKETS_DIR`
(quickstart + two `--deps` tickets + one claim), never the live board.

| File | State shown |
|---|---|
| `t971-landing-1440.png` | 1440×1000 first viewport: kicker names `atm`, promise line unchanged, lede says `atm` is the control plane, CTAs "See the app" and "Connect as Atman". |
| `t971-landing-1440-full.png` | 1440×5600: app section with the current `atm ui` Work capture, the on-main / in-review / planned status strip, command board, philosophy, four pillars, workflow graph with the T-889 phase capture. |
| `t971-landing-768.png` | 768×1100: hero and app heading wrap without horizontal scroll; ia-strip collapses to two columns. |

Not captured: 390px. Headless Chrome on this machine clamps widths under 500
(see `docs/brand/evidence/t889-work-view.md`), so a true phone-width shot needs
a device-metrics override; the 620px media query is unchanged from T-794.

Facts the page now states, and where each was checked on 2026-09-14:

- `atm` primary, `tickets` alias: PR #129 merged (T-809); `install.sh` links both names to one file; `pyproject.toml` `atm` entry point.
- App on main = Objective / Team / Work / Intervene, Work → Graph with objective + done-when above real `--after` edges and node detail: `UI_HTML` tabs in `tickets.py`; PR #127 merged (T-889).
- In review, not on main: first screen with titled next / named blockers, channel delivery receipts, node → compose. PR #156 open (T-810) at the time of writing.
- Planned / not published: Homebrew tap (T-865 formula in repo, T-898 publish open; repository README "not published yet"); connect/reconnect, remote control, metrics dashboards "not ready yet" per repository README.
