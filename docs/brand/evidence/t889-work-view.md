# T-889 — Work view: objective above a real dependency graph, node detail

Captured with headless Google Chrome against a throwaway board served by
`tickets ui` (never the live board). Module: `src/ticket_board/work_view.py`;
shell hooks in `tickets.py`: `_board_snapshot_body` (`work` key, passes agent
records for wake receipts), `_ui_page` (three `<!--WORK_VIEW:…-->`
placeholders), `renderGraph(g,d)` delegation, and the `reopened_at` stamp in
`cmd_reopen`. Second capture set applies the T-892 advisory review items the
CEO adopted on T-889 (evidence separation, success-to-next story, review
evidence, light-mode focus token, focus/announcement behaviour).

| File | State shown |
|---|---|
| `t889-work-graph-dark.png` | Layered graph, dark. Done → task posted (posted to @bob · read · not claimed) → waiting; reserved (no task), working, in review, blocked, capture, hold as distinct phases; dashed edges are unsatisfied `--after` links. |
| `t889-work-detail-posted-dark.png` | Detail for a child whose T-781 trigger was posted: Status "Task posted to @bob by alice · inbox read, not acknowledged · not claimed"; "Why now" says it became ready when T-001 finished (all 1 dependency done), the trigger was posted and read, and it has not begun. No "told", no "dispatched". |
| `t889-work-detail-two-parents-dark.png` | Child with two parents, one done: "Dependency T-001 completed; still waiting on T-005". Never "Freed". |
| `t889-work-detail-review-fix-light.png` | Light theme. In-review ticket whose reviewer recorded REQUEST FIX: Review row shows "FIX requested by @planner on <sha>" beside the submitted artifact, instead of a generic "awaiting review". Focus/selection use the darker light-mode token (#6b4f14, 7.3:1 on the card). |
| `t889-work-detail-done-light.png` | Completed parent: Review "Marked done; verification not recorded" (a done flag is not acceptance); Started list separates "task posted to @bob · inbox read · not claimed" from "still waits on T-005". |
| `t889-work-list-reserved-light.png` | Keyboard list fallback (`?mode=list`), reserved node selected: "Reserved for @carol · no task posted · not claimed". |
| `t889-work-390-dark.png` | 390px wide, list mode with a selection: detail stacks below the list, ends in "Back to T-002 in the list", commands and handoff intact. |
| `t889-work-no-edges-dark.png` | Tickets but no `--after` edges: "No dependencies yet" with `tickets plan`; the concrete `tickets dep` example appears only when two distinct tickets exist. No objective yet: one command to set it with an exit criterion. |

Deep links used for the captures: `/?work=T-002` selects a node, `/?mode=list`
opens the list. Reduced motion: no animation or transition is used in the view;
scrolling to the stacked detail is instant under `prefers-reduced-motion`.
Polling keeps focus on the focused node, Close/Back button or Graph/List
control; only a selection change is announced to assistive tech.
