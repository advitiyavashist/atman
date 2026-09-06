import { useState } from "react";
import { Modal } from "../components/Modal";
import { toast } from "../components/Toast";
import { connectPreviewNote, formatDateTime } from "../copy";
import enrollmentResponse from "../fixtures/data/agents/response-enrollment.json";

type EnrollmentResponse = typeof enrollmentResponse;
const enrollment = enrollmentResponse as EnrollmentResponse;

const ROLES = ["backend", "frontend", "verification", "master"];

export function ConnectDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [name, setName] = useState("claude-backend");
  const [role, setRole] = useState(ROLES[0]);
  const [worktree, setWorktree] = useState("/projects/example/.worktrees/backend");
  const [checked, setChecked] = useState(false);

  return (
    <Modal open={open} onClose={onClose} labelledBy="connect-title">
      <div className="dialog-head" style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <h2 id="connect-title" style={{ margin: 0 }}>
          Connect a Claude agent
        </h2>
        <span className="spacer" />
        <button type="button" onClick={onClose} aria-label="Close">
          ✕
        </button>
      </div>
      <p>Choose a role and connect from the agent's worktree.</p>

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

      <label htmlFor="connect-worktree">Worktree</label>
      <input id="connect-worktree" value={worktree} onChange={(e) => setWorktree(e.target.value)} />

      <p className="notice">{connectPreviewNote}</p>

      <code className="block">
        {enrollment.install_command}
        <br />
        Enrollment code: delivered once over stdin — never shown here or logged.
        <br />
        Expires: {formatDateTime(enrollment.expires_at)}
      </code>

      <h3>Configuration changes</h3>
      <ul className="checklist">
        {enrollment.config_changes.map((c, i) => (
          <li key={i}>
            <span className="tag">{c.change}</span> {c.summary}
          </li>
        ))}
      </ul>

      <p>Next: verify config → receive test event → confirm session adoption.</p>

      <div className="dialog-actions">
        <button type="button" onClick={onClose}>
          Cancel
        </button>
        <button
          className="primary"
          onClick={() => {
            setChecked(true);
            toast(
              "Fixture: config found and test event received. Actual session adoption still needs a real Claude session.",
            );
          }}
        >
          Preview connection check
        </button>
      </div>
      {checked && (
        <p className="tag" role="status">
          Preview only — no enrollment was created for {name} ({role}, {worktree}).
        </p>
      )}
    </Modal>
  );
}
