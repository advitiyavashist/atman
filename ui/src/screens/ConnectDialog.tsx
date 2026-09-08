import { useState } from "react";
import { Modal } from "../components/Modal";
import { ErrorNotice } from "../components/ErrorNotice";
import { useBoard } from "../state/BoardProvider";
import { useResource } from "../state/useResource";
import { useMutation } from "../state/useMutation";
import { formatDateTime, pendingWriteCopy } from "../copy";
import type { CreateEnrollmentResponse } from "../api";
import type { AgentListResponse } from "../types";

const ROLES = ["backend", "console", "infra", "verification", "docs"];

/**
 * Enrollment, and the confirmation that is not a guess.
 *
 * The enrollment code is a one-time secret: `POST /enrollments` returns it
 * exactly once and the route is deliberately excluded from request_id replay,
 * because replay works by storing the response (docs/api-notes.md #2). So this
 * dialog shows it once, does not re-request it, and warns that leaving loses it.
 *
 * The confirmation step is the part worth reading. "Connected" is not something
 * this dialog may decide — it re-reads the agent from the board and reports
 * `hook_health.session_adopted`, which is only true once a real session has
 * attached. Until then it says what is still missing. A dialog that showed a
 * green tick when the operator pasted the command would be exactly the
 * optimistic false success this ticket exists to prevent, and T-183's fixture
 * build said so in a toast; now the board decides it.
 */
function EnrollmentResult({ enrollment }: { enrollment: CreateEnrollmentResponse }) {
  const { revision } = useBoard();
  const agents = useResource<AgentListResponse>((client) => client.listAgents(), []);
  const live = agents.data?.items.find((a) => a.id === enrollment.agent.id);
  // Fall back to the enrollment's own copy of the agent only until the first
  // list read lands; after that the board's current record is the truth.
  const agent = live ?? enrollment.agent;
  const adopted = agent.hook_health.session_adopted;
  const expired = new Date(enrollment.expires_at).getTime() < Date.now();

  return (
    <div data-testid="enrollment-result">
      <p className="notice" role="status">
        Enrollment {enrollment.enrollment_id} created for <strong>{agent.name}</strong>.
      </p>

      <h3>Run this in the agent's worktree</h3>
      <code className="block" data-testid="install-command">
        {enrollment.install_command}
      </code>

      <h3>Enrollment code</h3>
      <code className="block" data-testid="enrollment-code">
        {enrollment.code}
      </code>
      <p className="notice">
        Shown once. The board does not store it in a form it can show again, and re-opening this dialog issues a
        different enrollment. Expires {formatDateTime(enrollment.expires_at)}
        {expired ? " — already expired, issue a new one." : "."}
      </p>

      <h3>Configuration changes</h3>
      <ul className="checklist">
        {enrollment.config_changes.map((c, i) => (
          <li key={i}>
            <span className="tag">{c.change}</span> <code>{c.path}</code> — {c.summary}
          </li>
        ))}
      </ul>

      <h3>Connection</h3>
      <p data-testid="adoption-state" data-adopted={adopted}>
        {adopted ? (
          <>
            Session adopted — the board reports this agent connected as of{" "}
            {agent.last_heartbeat_at ? formatDateTime(agent.last_heartbeat_at) : "its first event"}.
          </>
        ) : (
          <>
            Not connected yet. The board has not seen a session attach for this agent
            {agent.hook_health.config_installed ? ", though the hook config is recorded" : ""}. This updates itself
            when the agent connects; nothing here confirms a connection the board has not seen.
          </>
        )}
      </p>
      {agents.error && <ErrorNotice error={agents.error} onRetry={agents.refetch} />}
      <div className="dialog-actions" style={{ justifyContent: "flex-start" }}>
        <button onClick={agents.refetch} data-testid="recheck-adoption">
          Check again
        </button>
        <span className="tag">checked {agents.fetchedAt ? formatDateTime(agents.fetchedAt) : "—"} · rev {revision}</span>
      </div>
    </div>
  );
}

export function ConnectDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [name, setName] = useState("");
  const [role, setRole] = useState(ROLES[0]);
  const [worktree, setWorktree] = useState("");
  const [mode, setMode] = useState<"managed" | "hook_only">("managed");

  const enroll = useMutation<[], CreateEnrollmentResponse>((client, requestId) =>
    client.createEnrollment({
      request_id: requestId,
      agent_name: name.trim(),
      role,
      connection_mode: mode,
      ...(worktree.trim() ? { worktree: worktree.trim() } : {}),
    }),
  );

  const close = () => {
    enroll.reset();
    onClose();
  };

  return (
    <Modal open={open} onClose={close} labelledBy="connect-title">
      <div className="dialog-head" style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <h2 id="connect-title" style={{ margin: 0 }}>
          Connect a Claude agent
        </h2>
        <span className="spacer" />
        <button type="button" onClick={close} aria-label="Close">
          ✕
        </button>
      </div>

      {enroll.phase === "succeeded" && enroll.result ? (
        <EnrollmentResult enrollment={enroll.result} />
      ) : (
        <>
          <p>Creates an enrollment on the board and returns a one-time code for the agent to exchange.</p>

          <label htmlFor="connect-name">Agent name</label>
          <input id="connect-name" data-initial-focus value={name} onChange={(e) => setName(e.target.value)} />

          <label htmlFor="connect-role">Role</label>
          <select id="connect-role" value={role} onChange={(e) => setRole(e.target.value)}>
            {ROLES.map((r) => (
              <option key={r} value={r}>
                {r[0].toUpperCase() + r.slice(1)}
              </option>
            ))}
          </select>

          <label htmlFor="connect-worktree">Worktree (optional)</label>
          <input id="connect-worktree" value={worktree} onChange={(e) => setWorktree(e.target.value)} />

          <label htmlFor="connect-mode">Connection mode</label>
          <select
            id="connect-mode"
            value={mode}
            onChange={(e) => setMode(e.target.value as "managed" | "hook_only")}
          >
            <option value="managed">Managed — the board can wake this agent</option>
            <option value="hook_only">Hook-only — reports in, cannot be woken</option>
          </select>

          {enroll.phase === "pending" && <p className="tag" role="status">{pendingWriteCopy}</p>}
          {enroll.error && <ErrorNotice error={enroll.error} onRetry={() => enroll.run()} />}

          <div className="dialog-actions">
            <button type="button" onClick={close}>
              Cancel
            </button>
            <button
              className="primary"
              onClick={() => enroll.run()}
              disabled={enroll.phase === "pending" || name.trim().length === 0}
              data-testid="create-enrollment"
            >
              Create enrollment
            </button>
          </div>
        </>
      )}
    </Modal>
  );
}
