# Security

## Reporting a vulnerability

Do not open a public issue for a vulnerability, a leaked credential, or
anything that exposes a board, repository or model account.

Report it privately through GitHub:

**https://github.com/advitiyavashist/atman/security/advisories/new**

If that form is not available to you, contact the repository owner
[@advitiyavashist](https://github.com/advitiyavashist) on GitHub and say only
that you have a security report. Do not attach live secrets, tokens, or a dump
of a real board to any message.

Useful in a report:

- The affected path, command or endpoint.
- A reproduction on a clean clone of this repository, not on a private machine
  path.
- What an attacker gets, and a suggested fix if you have one.

## What to expect

Atman is a preview project maintained by one person alongside other work.
There is no security rota and no response-time commitment. Reports are read
and answered when the maintainer is next at the repository; if a report is out
of scope for this tree you will be told that rather than left waiting.

## Scope

Report against current `main`. There is no separate supported release line.

In scope: the `atm` CLI and board storage, the HTTP API and dashboard under
`src/` and `ui/`, the packaging and install path, and anything in this
repository that handles credentials or executes an agent command.

Out of scope: vulnerabilities in the agent harnesses Atman drives (Claude
Code, Codex, Cursor and any custom harness) and in their model providers —
report those to their own maintainers; findings that require an attacker who
already controls the machine running the board; and the terms in
[LICENSE](LICENSE), which a security report does not change.

## Maintainer setup

This file assumes GitHub private vulnerability reporting is turned on for the
repository. That is a repository-settings action only the owner can take:

- [ ] Enable private vulnerability reporting (Settings → Code security →
      Private vulnerability reporting) so the advisory link above accepts
      reports from people without write access.

Until it is enabled, the fallback in the first section is the working route.
