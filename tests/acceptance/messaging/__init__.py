"""T-190: message-to-task execution and team isolation, verified end to end.

Every test in this package drives the REAL router over a REAL loopback socket
(`ticket_board.server.httpd.serve`), through the REAL `RunnerClient` and the
REAL `Supervisor`. The only thing substituted is the `claude` child process,
and `test_live_claude.py` removes even that substitution when it is allowed to
spend a model call.

See `docs/runbook.md` for what each module proves, what it found, and how to
re-run the live proof.
"""
