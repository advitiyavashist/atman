# T-862: honest catalog quota states

Extends the T-793 `INTEGRATION_CATALOG`. There is no second provider registry.

| State | Meaning | Blocks spawn / dispatch? |
| --- | --- | --- |
| `ok` | Supported adapter produced remaining **and** reset | no |
| `unknown` | Missing binary, unsupported adapter, or incomplete snapshot | no |
| `exhausted` | Remaining is genuinely `0` | yes |
| `FAIL` | Auth / login error (or `TICKETS_HARNESS_FAIL`) | yes |

Supported parsers (fixtures only; no paid probe):

- **Codex** — `account/rateLimits/read` / `updated`; remaining = `100 - used_percent`
- **Claude** — status-line `rate_limits.five_hour` / `seven_day`
- **Agy** — status-line `quota[bucket].remaining_fraction`; remaining = `100 * fraction`
- **Cursor team/org** — optional pooled-usage JSON when `ATMAN_CURSOR_ADMIN_USAGE` is set

Personal Cursor, Grok, Devin, and Gemini stay `unknown` even if a stub prints remaining/reset.

Installation, auth, quota, and wake stay separate. No credential dumps, cookie scraping, or automatic overages.
