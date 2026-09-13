# T-889 — Work view: objective above a real dependency graph, node detail

Captured with headless Google Chrome against a throwaway board served by
`tickets ui` (never the live board). Module: `src/ticket_board/work_view.py`;
shell hooks in `tickets.py`: `_board_snapshot_body` (`work` key), `_ui_page`
(three `<!--WORK_VIEW:…-->` placeholders), `renderGraph(g,d)` delegation.

| File | State shown |
|---|---|
| `t889-work-graph-dark.png` | Layered graph, dark. Done → dispatched (told @bob · unseen) → waiting; blocked, capture, hold, ready, in review as distinct phases; dashed edges are unsatisfied `--after` links. |
| `t889-work-detail-dispatched-dark.png` | Node detail for a dispatched child: status evidence, "Why now" (freed by T-001, trigger told @bob, has not begun), acceptance, cause/change, inherited handoff, artifact, verdict, commands. |
| `t889-work-detail-done-light.png` | Light theme. Completed parent shows which children it freed and whether each began (T-002 told @bob, not begun; T-006 began by @carol). |
| `t889-work-list-blocked-light.png` | Keyboard list fallback (`?mode=list`), blocked node selected with reason and `tickets reopen` command. |
| `t889-work-graph-390-dark.png` | 390px wide: graph scrolls horizontally inside its own container, detail stacks below. |
| `t889-work-no-edges-dark.png` | Tickets but no `--after` edges: one useful first action (`tickets dep … --after …`). No objective yet: one command to set it. |

Deep links used for the captures: `/?work=T-002` selects a node, `/?mode=list`
opens the list. Reduced motion: no animation or transition is used in the view.
