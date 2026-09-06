// Full Messages UI (channels, DMs, task composer, delivery receipts) is T-189,
// which depends on this ticket (T-183) and T-187. Keeping the nav item present
// per docs/interface-v1.md's five-item nav, but not building ahead of scope.
export function Messages() {
  return (
    <>
      <div className="heading">
        <div>
          <span className="tag">PROJECT / EXAMPLE APP</span>
          <h1>Messages</h1>
        </div>
      </div>
      <div className="card empty-state">
        <h2>Messages UI ships in T-189</h2>
        <p>Channels, DMs, task composer and delivery receipts are built once T-187 (messaging API) lands.</p>
      </div>
    </>
  );
}
