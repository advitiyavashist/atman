/**
 * THE EXECUTION PLAN — the centre of the screen, from `GET /api/v1/plan`.
 *
 * Every step shows its owner, its state and, when it cannot move, a typed
 * blocker chip. The two dependency blockers are deliberately different words:
 * *dependency not accepted* (the dep is finished but nobody accepted it) and
 * *dependency still open* (the dep has not finished). They mean different
 * things to whoever has to unblock it, and the API types them apart.
 *
 * Work that is done without a structured accept never renders as accepted.
 * `acceptState` derives the label from the record, so a `status_label` that
 * claimed otherwise could not put the word on screen.
 *
 * Layers come from the API (`layers`, dependency order), so the plan reads
 * top-down in the order the work can actually happen.
 */

import type { Plan, PlanNode } from "../api/types";
import { acceptState, blockerDetail, ownerLabel, runningLabel, stepBlockers } from "../lib/map";
import { Chip, Command, Failure, Missing } from "./bits";

type PlanSummaryShape = {
  finishing?: Array<{ id: string; title?: string; owner?: string; who?: string }>;
  blocked?: Array<{ id: string; title?: string; text?: string; kind?: string }>;
  next?: { id: string; title?: string; who?: string; owner?: string; who_kind?: string } | null;
};

function Objective({ plan }: { plan: Plan }) {
  const o = plan.objective as { text?: string; exit_criterion?: string; state?: string };
  const text = (o?.text || "").trim();
  const exit = (o?.exit_criterion || "").trim();
  return (
    <div className="objective" data-testid="objective">
      <h3>Objective</h3>
      {text ? <p>{text}</p> : <Missing what="no objective recorded on this board" />}
      <p className="muted">
        Done when: {exit ? exit : <Missing what="no exit criterion recorded" />}
        {o?.state ? ` · ${o.state}` : ""}
      </p>
    </div>
  );
}

/**
 * The board's next-owner / blocker strip — the same facts the legacy Work view
 * puts under FINISHING / BLOCKED / NEXT STEP. Required at 390px so /app/ is not
 * only a lead picker.
 */
/** `@seat` when someone holds or is reserved for it; "none yet" instead of "@?" (T-1481). */
function summaryOwner(who: string | undefined): string {
  return who ? `@${who}` : "none yet";
}

function PlanSummary({ plan }: { plan: Plan }) {
  const s = (plan.summary || {}) as PlanSummaryShape;
  const finishing = Array.isArray(s.finishing) ? s.finishing : [];
  const blocked = Array.isArray(s.blocked) ? s.blocked : [];
  const next = s.next && typeof s.next === "object" ? s.next : null;
  if (!finishing.length && !blocked.length && !next) return null;
  return (
    <div className="plan-summary" data-testid="plan-summary">
      <div className="plan-summary-row" data-testid="plan-finishing">
        <span className="field-label">Finishing</span>
        {finishing.length ? (
          <ul>
            {finishing.map((n) => (
              <li key={n.id}>
                <b>{n.id}</b> {n.title || <Missing what="untitled" />}
                <span className="muted"> · {summaryOwner(n.owner || n.who)}</span>
              </li>
            ))}
          </ul>
        ) : (
          <span className="muted">—</span>
        )}
      </div>
      <div className="plan-summary-row" data-testid="plan-blocked">
        <span className="field-label">Blocked</span>
        {blocked.length ? (
          <ul>
            {blocked.map((n) => (
              <li key={n.id}>
                <b>{n.id}</b> {n.title || <Missing what="untitled" />}
                {n.text ? <span className="blocker-text"> — {n.text}</span> : null}
              </li>
            ))}
          </ul>
        ) : (
          <span className="muted">—</span>
        )}
      </div>
      <div className="plan-summary-row" data-testid="plan-next-owner">
        <span className="field-label">Next owner</span>
        {next ? (
          <p>
            <b>{next.id}</b> {next.title || <Missing what="untitled" />}
            <span className="muted"> · {summaryOwner(next.owner || next.who)}</span>
          </p>
        ) : (
          <span className="muted">—</span>
        )}
      </div>
    </div>
  );
}

function Step({
  node,
  project,
  selected,
  onSelect,
}: {
  node: PlanNode;
  project: string;
  selected: boolean;
  onSelect: (id: string) => void;
}) {
  const state = acceptState(node);
  const { chips, acceptCmd } = stepBlockers(node);
  const running = runningLabel(node, project);
  return (
    <li className="step" data-testid="plan-step" data-ticket={node.id}>
      <button
        type="button"
        className={`step-btn${selected ? " step-selected" : ""}`}
        aria-pressed={selected}
        onClick={() => onSelect(node.id)}
      >
        <span className="step-id">{node.id}</span>
        <span className="step-title" title={node.title || undefined}>
          {node.title || <Missing what="untitled" />}
        </span>
        <span className="step-owner">{ownerLabel(node, project)}</span>
        <Chip tone={state.tone} title={state.corrected ? `the record's own label said "${state.corrected}"` : undefined}>
          {state.label}
        </Chip>
        {running ? (
          <Chip tone="running" title="a run is open on this step right now">
            running · {running}
          </Chip>
        ) : null}
      </button>
      {state.corrected ? (
        <p className="failure" role="status">
          This step is not accepted; the label on the record read “{state.corrected}”.
        </p>
      ) : null}
      {acceptCmd ? (
        // The state chip above has already said this step is not accepted;
        // this is only the command that would change that.
        <Command cmd={acceptCmd} />
      ) : null}
      {chips.length ? (
        <ul className="blockers" data-testid="blockers">
          {chips.map((c, i) => {
            const detail = blockerDetail(c);
            return (
              <li key={i}>
                <Chip tone={c.tone} title={c.text}>
                  {c.label}
                  {c.on && c.on !== node.id ? ` · ${c.on}` : ""}
                </Chip>
                {detail ? <span className="blocker-text">{detail}</span> : null}
                {c.cmd ? <Command cmd={c.cmd} /> : null}
              </li>
            );
          })}
        </ul>
      ) : null}
    </li>
  );
}

export function PlanPane({
  plan,
  error,
  project,
  selected,
  onSelect,
}: {
  plan: Plan | null;
  error: unknown;
  project: string;
  selected: string;
  onSelect: (id: string) => void;
}) {
  if (error) return <Failure what="The plan" error={error} />;
  if (!plan) return <p className="muted">Reading the plan…</p>;
  if (!plan.available) {
    return (
      <section className="pane pane-plan" aria-label="Execution plan">
        <p className="failure" role="alert">
          The plan could not be built from this board, so no steps are shown. Nothing here is a claim that the
          board is empty.
        </p>
      </section>
    );
  }

  // The contract says nodes and layers are arrays. If a payload ever says
  // otherwise, the screen names that instead of throwing: the operator needs to
  // know the plan could not be read, not watch the column disappear.
  const nodes = Array.isArray(plan.nodes) ? plan.nodes : [];
  const shapeWrong = !Array.isArray(plan.nodes) || !Array.isArray(plan.layers);
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const declared = Array.isArray(plan.layers) ? plan.layers.filter((l) => Array.isArray(l)) : [];
  const layers = declared.length ? declared : [nodes.map((n) => n.id)];
  const placed = new Set(layers.flat());
  const loose = nodes.filter((n) => !placed.has(n.id));

  return (
    <section className="pane pane-plan" aria-label="Execution plan">
      {shapeWrong ? (
        <p className="failure" role="alert">
          The plan payload is not the shape the contract describes (nodes and layers must be lists), so some of it
          cannot be shown. Nothing here is a claim about the board.
        </p>
      ) : null}
      <Objective plan={plan} />
      <PlanSummary plan={plan} />
      <div className="counts">
        {Object.entries(plan.counts || {}).map(([k, v]) => (
          <span key={k} className="count">
            <b>{v}</b> {k.replace(/_/g, " ")}
          </span>
        ))}
      </div>
      <div className="layers">
        {layers.map((ids, i) => (
          <div className="layer" key={i}>
            <h4 className="layer-h">
              {i === 0 ? "ready first" : `after layer ${i}`} <span className="muted">{ids.length} steps</span>
            </h4>
            <ul className="steps">
              {ids.map((id) => {
                const node = byId.get(id);
                if (!node) {
                  return (
                    <li key={id} className="step">
                      <Missing what={`${id} is in the order but not in nodes`} />
                    </li>
                  );
                }
                return (
                  <Step key={id} node={node} project={project} selected={selected === id} onSelect={onSelect} />
                );
              })}
            </ul>
          </div>
        ))}
        {loose.length ? (
          <div className="layer">
            <h4 className="layer-h">
              not placed in a layer <span className="muted">{loose.length} steps</span>
            </h4>
            <ul className="steps">
              {loose.map((node) => (
                <Step key={node.id} node={node} project={project} selected={selected === node.id} onSelect={onSelect} />
              ))}
            </ul>
          </div>
        ) : null}
        {!nodes.length ? <p className="muted">This board's plan has no steps.</p> : null}
      </div>
    </section>
  );
}
