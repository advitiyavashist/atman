"""Render harness results as the per-route PASS/FAIL table T-200 asks for."""

from __future__ import annotations

from .harness import Result


def render(results: list[Result], *, base_url: str, note: str) -> str:
    passed = sum(1 for r in results if r.passed)
    lines = [
        "# Conformance report",
        "",
        f"Target: `{base_url}`",
        "",
        note,
        "",
        f"**{passed}/{len(results)} rows passed.**",
        "",
        "| Route | Kind | Expected | Actual | Result | Notes |",
        "|---|---|---|---|---|---|",
    ]
    for r in results:
        route_cell = f"{r.method} {r.path}<br>`{r.label}`"
        status = "PASS" if r.passed else "FAIL"
        notes = "; ".join(r.errors) if r.errors else ""
        lines.append(
            f"| {route_cell} | {r.kind} | {r.expected_status} | "
            f"{r.actual_status if r.actual_status is not None else 'no response'} | "
            f"{status} | {notes} |"
        )
    lines.append("")
    return "\n".join(lines)
