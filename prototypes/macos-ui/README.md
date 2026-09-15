# T-1027 throwaway native shell

A real macOS window and dock icon around the **current** local app. It does
not reimplement Objective, Work, or Team. Those screens stay in `atm ui` so
the honesty rules have one home (the T-1019 lesson).

```
# throwaway board only
TICKETS_DIR=/tmp/atman-t1027-demo/.tickets atm ui --host 127.0.0.1 --port 8765
./prototypes/macos-ui/run.sh          # compiles atman-shell
ATMAN_SHELL_LAUNCH=1 ATMAN_UI_URL=http://127.0.0.1:8765 ./prototypes/macos-ui/run.sh
```

Notifications and menu-bar copy come from
`python3 -m ticket_board.native_notify --snapshot board.json`
which reads the same `atm ui --json` snapshot. A done ticket without a
structured ACCEPT is "Marked done; verification not recorded" — never
Accepted.
