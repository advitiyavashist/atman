#!/usr/bin/env python3
"""Split the kit's coordinator prompt into a plan-only and an accept-only prompt.

rehearsal.py writes one ceo.prompt that plans AND then waits for review. Running it
as one agent turn in a recording makes the coordinator either stall on screen or,
if re-run, plan twice (duplicate tickets). The take runs planning and acceptance as
two separate coordinator turns, so it needs two prompts derived from the kit's text.
"""
import pathlib, sys

run = pathlib.Path(sys.argv[1])
ceo = (run / "ceo.prompt").read_text()
marker = "Wait for T-001 to be IN REVIEW using atm show T-001."
if marker not in ceo:
    sys.exit("ceo.prompt changed shape; update split_prompts.py")
head, tail = ceo.split(marker, 1)
(run / "ceo-plan.prompt").write_text(
    head.strip() + "\nStop after the two tickets exist and are reserved. "
    "Do not wait for review and do not plan anything else.\n")
(run / "ceo-accept.prompt").write_text(
    "You are the reviewer on this board. T-001 is IN REVIEW. " + tail.strip()
    + "\nDo not set an objective and do not run atm plan; the plan already exists.\n")
print("wrote ceo-plan.prompt and ceo-accept.prompt in", run)
