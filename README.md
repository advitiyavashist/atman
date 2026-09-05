# tickets

Standalone multi-agent ticket board CLI (extracted from [steer](https://github.com/advitiyavashist/steer)).

**In progress:** harden `tickets clear` (must never wipe a live board), add backup/restore, package for `pip install`.

Incident (2026-09-06): `tickets clear` deleted all `T-*.json` on the live Steer board. This repo exists so the CLI can be improved safely outside the product tree.
