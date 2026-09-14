#!/usr/bin/env python3
"""Diff the FAILED/ERROR sets of the two full clean-env suite logs."""
import re, sys, os
SP = os.path.dirname(os.path.abspath(__file__))

def parse(path):
    fails, errs, tail = set(), set(), ""
    for line in open(path, errors="replace"):
        if line.startswith("FAILED "):
            fails.add(line.split()[1])
        elif line.startswith("ERROR "):
            errs.add(line.split()[1])
        if re.search(r"\d+ (passed|failed)", line) and ("passed" in line or "failed" in line) and " in " in line:
            tail = line.strip()
    return fails, errs, tail

cf, ce, ct = parse(os.path.join(SP, "suite-cand.log"))
bf, be, bt = parse(os.path.join(SP, "suite-base.log"))
print("cand summary:", ct)
print("base summary:", bt)
print()
for label, c, b in (("FAILED", cf, bf), ("ERROR", ce, be)):
    print("== %s: candidate-only (NEW) ==" % label)
    for t in sorted(c - b): print("  NEW  ", t)
    if not c - b: print("   (none)")
    print("== %s: base-only (fixed or base-side flake) ==" % label)
    for t in sorted(b - c): print("  GONE ", t)
    if not b - c: print("   (none)")
    print("== %s: shared / pre-existing ==" % label)
    for t in sorted(c & b): print("  BOTH ", t)
    print()
print("NEW FAILURES =", len((cf | ce) - (bf | be)))
