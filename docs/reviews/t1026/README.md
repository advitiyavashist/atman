# T-1026: annotated screenshot evidence

Verification seats (T-986, T-1011) used to screenshot by hand and describe the
defect in prose. `atm shot` is the repeatable replacement. It uses the
Playwright path those seats already run, marks one element, and writes files
you can attach to the ticket. It is not a live IDE panel and not a new
embedded browser.

## Capture a live state

```
atm shot --url http://127.0.0.1:8765 --selector ".done-chip" \
  --label "counted as 1/6 done" --ticket T-986 --note "unverified done inflated the header"
```

Writes `docs/reviews/t986/<stamp>-counted-as-1-6-done.{png,html,json}` and
appends an `evidence-shot:` note on the ticket. The HTML is the annotated
view (box + label on the PNG). When Playwright can launch, a second
`*-marked.png` bakes the same mark into pixels.

`--pick --headed` waits for a click and records the resolved selector. Agents
should pass `--selector`; `--pick` is for an attended seat.

## Annotate an existing screenshot

```
atm shot --png docs/reviews/t986/07-done-without-verification.png \
  --bbox 820,12,90,22 --label "counted as 1/6 done" --ticket T-986
```

`--png` cannot resolve a CSS selector. If you cannot name the box, say so;
do not invent coordinates.

## Honesty

- Binary found is not connected; submitted is not accepted; disconnected is
  not work lost. The label is the claim you are asserting.
- A missing element, unreachable URL, or missing Playwright is a hard
  failure. An empty directory is not a capture.
- Playwright is optional at install time. `python3 -m pip install playwright
  && python3 -m playwright install chromium` is the existing path.
