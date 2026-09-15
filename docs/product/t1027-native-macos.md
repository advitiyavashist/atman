# T-1027: native macOS vs T3 vs the current local app

**Recommendation: do not rewrite Objective / Work / Team in SwiftUI, Tauri,
Electron, or T3.** If users want a dock icon and a real window after the
preview, wrap `atm ui` in a thin WKWebView shell. One of the two prototypes
wins: the current local app (optionally with that chrome). T-1019 already
rejected T3; a native *rewrite* fails the same test.

## (1) What native actually buys, ranked

User feedback was "a native macOS app, not a local web page." Ranked by
what that sentence asks for, and by pain we already have:

| Rank | Ask | Does a rewrite buy it? | Does a thin shell buy it? |
|---|---|---|---|
| 1 | Real window + dock icon | Yes | Yes (`prototypes/macos-ui/AtmanShell.swift`) |
| 2 | System notification when a ticket needs review | Yes, if copy is honest | Yes, if copy comes from `board_snapshot` |
| 3 | Menu-bar fleet (idle / working / LIMITED) | Yes | Yes, same snapshot (`fleet_row`) |
| 4 | Launch at login | Yes | Yes (Login Items on the shell binary) |

Nobody asked for a second implementation of the Work graph. T-986 / T-1011
caught honesty drift in *one* home. A rewrite would open a second.

Notifications are the honesty trap: they must never say Accepted for
done-without-ACCEPT. That rule is tested in
`tests/test_t1027_native_notify.py` against the same snapshot `atm ui --json`
serves.

## (2) Shell choice vs Python 3.9 + git

| Option | Runtime added | Build toolchain | Honesty home | Notes |
|---|---|---|---|---|
| Current `atm ui` | none | none | Python snapshot | Install promise we lead with. |
| Thin Swift WKWebView | shipped binary (~0.1 MB here) | `swiftc` + Cocoa/WebKit (already on this Mac) | **same** — WebView loads `atm ui` | Prototype in this ticket. |
| SwiftUI rewrite of 3 screens | shipped binary | Xcode + a second language | **second** | Repeats T-1019's failure mode. |
| Tauri | Rust binary + web UI | Rust + Node if the UI is rebuilt | second unless it only wraps `atm ui` | New toolchain for no new honesty. |
| Electron | ~150 MB Chromium | Node | second unless it only wraps `atm ui` | Heaviest; we already have Playwright Chromium for verification, not for operators. |
| T3 / Next (T-1019) | Node + 400 MB `node_modules` | `npm` + `next build` | second | Already rejected. |

Measured on this machine (2026-09-15): `swiftc -O` of `AtmanShell.swift`
produced `prototypes/macos-ui/atman-shell` at **58,856 bytes**. No Node.
The window still needs `atm ui` running — the shell is chrome, not a
runtime.

## (3) Where the honesty rules live

They stay in `board_snapshot` / `work_view.py`:

- binary found ≠ connected (`fleet_row`: `login_required` → `connected: false`)
- submitted ≠ accepted (`ticket_alert` kind `submitted`)
- done-without-ACCEPT visible (`kind: unverified-done`, copy
  "Marked done; verification not recorded")
- disconnected ≠ work lost (the WebView shows the existing OFFLINE state;
  the shell does not invent a Recover that rewrites the board)

`native_notify.py` refuses to emit the word `accepted` unless
`review.verified` is true. That is a guard on the snapshot, not a parallel
judge.

## Head-to-head

| | Current local app | T3 rewrite (T-1019) | Native rewrite | Thin native shell |
|---|---|---|---|---|
| Dock / real window | browser tab | browser tab | yes | yes |
| Install promise | Python 3.9 + git | + Node | + Swift/Xcode + second UI | + optional shipped binary |
| Honesty homes | 1 | 2 | 2 | 1 |
| T-986 unverified-done | still a Python filter fix | prototype hid it by re-coding the rule | would re-code it again | unchanged; still a Python fix |

Winner: **current local app**, with the thin shell as the only native
increment worth taking after preview. Do not build T3 and native rewrites
in parallel.

## What would falsify this

A measurement that SwiftUI (or T3) could enforce the four rules *and* we
would delete `atm ui`. Neither is on the table before the preview ships.
