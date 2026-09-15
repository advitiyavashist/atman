# Atman T3 UI prototype (T-1019 throwaway)

This is **not** the launch UI. The preview still ships the Python `tickets ui`.

Three screens — Objective, Work, Team — read the **same board files** as the
local app (`T-*.json`, `agents/*.json`, `messages.jsonl`, `objective.json`).
Honesty is judged once in `src/server/honesty.ts` and exposed only through
tRPC. Screens never receive raw ticket JSON.

```
cd prototypes/t3-ui
npm install
npm test
ATMAN_T3_BOARD=/path/to/.tickets npm run dev   # http://127.0.0.1:3319
```

Default board is `fixtures/throwaway-board` (T-986 honesty cases plus an
isolated done-without-ACCEPT ticket). Never point this at the live Steer board.

See `COMPARISON.md` for the recommendation and the measured costs.
