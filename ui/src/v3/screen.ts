/**
 * What every section, card, dialog and composer of the one screen is handed:
 * the records it draws from and the few things it can ask the shell to do.
 */

import type { AgentRow, PlanNode } from "../api/types";
import type { ResultLine } from "../components/Buttons";

export type DialogSpec =
  | { kind: "accept"; id: string }
  | { kind: "reject"; id: string }
  | { kind: "assign"; id: string }
  | { kind: "start"; seat?: string }
  | { kind: "objective" }
  | { kind: "create" };

export interface Screen {
  project: string;
  /** "" when atm ui was started without --operator: the app is read-only. */
  operator: string;
  readOnly: boolean;
  nodes: ReadonlyMap<string, PlanNode>;
  agents: ReadonlyArray<AgentRow>;
  details: boolean;
  now: Date;
  /** Open a ticket's thread panel. */
  open: (id: string) => void;
  /** Focus the composer with a recipient and an `about` (§6.3 Message / Answer). */
  message: (to: string, about: string) => void;
  /** Open a dialog. */
  act: (dialog: DialogSpec) => void;
  /** Result lines after an action ran, by ticket id or item id (§6.1). */
  results: ReadonlyMap<string, ResultLine>;
}

/** The seat's `atm review --notes` sentence, from the plan node's last note. */
export function reviewNotesOf(node: PlanNode | null | undefined): string {
  const text = (node?.last_note?.text || "").trim();
  return /^REVIEW:/i.test(text) ? text.replace(/^REVIEW:\s*/i, "") : "";
}

/** The head a card was drawn with: the verified review head, else the pinned artifact sha. */
export function headOf(node: PlanNode | null | undefined): string {
  return (node?.review?.head || node?.artifact?.sha || "").trim();
}
