import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { useBoard } from "../state/BoardProvider";
import { useResource } from "../state/useResource";
import { useMutation } from "../state/useMutation";
import { ConnectionBanner } from "../components/ConnectionBanner";
import { ErrorNotice } from "../components/ErrorNotice";
import { EmptyStateView } from "../components/EmptyStateView";
import { Modal } from "../components/Modal";
import {
  deliveryReceiptLabel,
  formatWhen,
  memberAvailabilityLabel,
  memberKindLabel,
  pendingWriteCopy,
} from "../copy";
import type {
  AddChannelMemberRequest,
  CreateChannelRequest,
  SendMessageRequest,
  SendMessageResponse,
  SendTaskRequest,
  SendTaskResponse,
  SendTaskRouting,
  SendTaskTicketLink,
} from "../api/types";
import type {
  Channel,
  ChannelListResponse,
  ChannelMember,
  Member,
  MemberListResponse,
  Message,
  MessageListResponse,
  Thread,
} from "../types";

/**
 * Full Messages UI (T-189). Channels, DMs, task composer and delivery
 * receipts, built against the frozen T-187 contract exactly the way T-184
 * built the other screens: typed client methods + runtime response
 * validation + fixture-mocked tests. No route here has been exercised
 * against a live messaging server — that is T-190's job.
 */

// jsdom / very old browsers without crypto.randomUUID: same fallback shape
// as useMutation.ts's newRequestId, kept local since it is not exported.
function newId(): string {
  const c = globalThis.crypto;
  if (c && typeof c.randomUUID === "function") return c.randomUUID();
  return "req-" + Math.random().toString(16).slice(2) + Date.now().toString(16);
}

const ACTOR_TYPE_LABEL: Record<string, string> = {
  human: "Human",
  agent: "Agent",
  master: "Master",
  system: "System",
};

function ticketChipFor(message: Message, threads: Thread[] | undefined): string | null {
  if (message.ticket_id) return message.ticket_id;
  const asRoot = threads?.find((t) => t.root_message_id === message.id);
  if (asRoot?.ticket_id) return asRoot.ticket_id;
  return null;
}

function threadFor(message: Message, threads: Thread[] | undefined): Thread | null {
  return threads?.find((t) => t.root_message_id === message.id) ?? threads?.find((t) => t.id === message.thread_id) ?? null;
}

/** Opens the ticket in the Tickets screen the same way Overview.tsx's onNavigate does. */
function TicketChip({ ticketId }: { ticketId: string }) {
  return (
    <button
      type="button"
      className="tag pill"
      data-testid={`ticket-chip-${ticketId}`}
      onClick={() => {
        window.location.hash = "tickets";
      }}
    >
      {ticketId}
    </button>
  );
}

function MessageReceipts({ message, response }: { message: Message; response: MessageListResponse }) {
  const deliveries = (response.deliveries ?? []).filter((d) => d.message_id === message.id);
  if (deliveries.length === 0) return null;
  return (
    <div style={{ marginTop: 6 }}>
      {deliveries.map((d) => (
        <small key={d.id} className="receipt tag" data-testid={`delivery-${d.id}`} style={{ display: "block" }}>
          {deliveryReceiptLabel(d)}
          {d.reason_detail ? ` — ${d.reason_detail}` : ""}
        </small>
      ))}
    </div>
  );
}

function MessageRow({
  message,
  response,
  membersById,
}: {
  message: Message;
  response: MessageListResponse;
  membersById: Map<string, Member>;
}) {
  const chipTicketId = ticketChipFor(message, response.threads);
  const thread = threadFor(message, response.threads);
  const isRootWithThread = thread && thread.root_message_id === message.id;

  return (
    <article className="card" style={{ marginBottom: 12 }} data-testid={`message-${message.id}`}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <strong>{message.author.display_name}</strong>
        <span className="tag">{ACTOR_TYPE_LABEL[message.author.type] ?? message.author.type}</span>
        {message.intent === "receipt" && <span className="tag">System receipt</span>}
        {message.intent === "task" && <span className="tag">Task</span>}
        <span className="spacer" />
        <time className="tag" dateTime={message.created_at} data-testid={`message-time-${message.id}`}>
          {formatWhen(message.created_at)}
        </time>
        {chipTicketId && <TicketChip ticketId={chipTicketId} />}
      </div>
      <p style={{ whiteSpace: "pre-wrap", marginBottom: 4 }}>{message.body}</p>
      {message.mentions.length > 0 && (
        <p className="tag">
          Mentions: {message.mentions.map((id) => membersById.get(id)?.display_name ?? id).join(", ")}
        </p>
      )}
      {isRootWithThread && thread.reply_count > 0 && (
        <p className="tag" data-testid={`thread-replies-${thread.id}`}>
          {thread.reply_count} {thread.reply_count === 1 ? "reply" : "replies"}
        </p>
      )}
      <MessageReceipts message={message} response={response} />
    </article>
  );
}

function ChannelRail({
  channels,
  selectedId,
  onSelect,
}: {
  channels: Channel[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  const groups: [label: string, items: Channel[]][] = [
    ["Channels", channels.filter((c) => c.kind === "channel")],
    ["Direct messages", channels.filter((c) => c.kind === "dm")],
  ];

  return (
    <nav aria-label="Conversations" className="card" style={{ minWidth: 200 }}>
      {groups.map(([label, items]) =>
        items.length === 0 ? null : (
          <div key={label} style={{ marginBottom: 16 }}>
            <h2 style={{ fontSize: 13, color: "var(--muted)", margin: "0 0 8px" }}>{label}</h2>
            {items.map((c) => (
              <button
                key={c.id}
                type="button"
                aria-current={c.id === selectedId ? "true" : undefined}
                onClick={() => onSelect(c.id)}
                data-testid={`channel-${c.id}`}
                style={{ display: "flex", width: "100%", alignItems: "center", gap: 8, marginBottom: 4, textAlign: "left" }}
              >
                <span>{c.kind === "channel" ? `# ${c.name}` : c.name}</span>
                <span className="spacer" />
                {c.designated_master && (
                  <span className="tag" title="Tasks route through the master" aria-label="Has a designated master">
                    M
                  </span>
                )}
                {c.unread_count > 0 && (
                  <span className="pill" data-testid={`channel-unread-${c.id}`}>
                    {c.unread_count}
                  </span>
                )}
              </button>
            ))}
          </div>
        ),
      )}
    </nav>
  );
}

function MembersSidebar({
  members,
  selectedChannel,
  onAdded,
}: {
  members: Member[];
  selectedChannel: Channel | null;
  onAdded: (result: ChannelMember) => void;
}) {
  const [addingMemberId, setAddingMemberId] = useState("");
  const addMember = useMutation<[], ChannelMember>((client, requestId) => {
    const request: AddChannelMemberRequest = { request_id: requestId, member_id: addingMemberId };
    return client.addChannelMember(selectedChannel!.id, request);
  });

  return (
    <aside className="card" style={{ minWidth: 240 }}>
      <h2 style={{ fontSize: 13, color: "var(--muted)", margin: "0 0 10px" }}>Team members</h2>
      {members.map((m) => (
        <div key={m.id} className="row" data-testid={`member-${m.id}`}>
          <div>
            <strong>{m.display_name}</strong>
            <div>
              <span className="tag">{memberKindLabel[m.kind]}</span>{" "}
              <span className="tag">{m.role}</span>{" "}
              <span className="tag" data-testid={`member-availability-${m.id}`}>
                {memberAvailabilityLabel[m.availability]}
              </span>
              {m.state === "revoked" && <span className="tag">Revoked</span>}
            </div>
          </div>
        </div>
      ))}

      {selectedChannel && (
        <div style={{ marginTop: 16 }}>
          <label htmlFor="add-channel-member">Add to {selectedChannel.name}</label>
          <select
            id="add-channel-member"
            value={addingMemberId}
            onChange={(e) => setAddingMemberId(e.target.value)}
          >
            <option value="">Choose a member</option>
            {members
              .filter((m) => m.state === "active")
              .map((m) => (
                <option key={m.id} value={m.id}>
                  {m.display_name}
                </option>
              ))}
          </select>
          {addMember.error && <ErrorNotice error={addMember.error} />}
          {addMember.phase === "succeeded" && addMember.result && (
            <p className="notice" role="status" data-testid="add-member-result">
              Added to {selectedChannel.name}.
            </p>
          )}
          <div className="dialog-actions" style={{ justifyContent: "flex-start", marginTop: 8 }}>
            <button
              type="button"
              disabled={!addingMemberId || addMember.phase === "pending"}
              onClick={async () => {
                const result = await addMember.run();
                if (result) {
                  onAdded(result);
                  setAddingMemberId("");
                }
              }}
              data-testid="add-channel-member-confirm"
            >
              Add
            </button>
          </div>
        </div>
      )}

      <p className="tag" style={{ marginTop: 16 }}>
        Idle agents start on addressed messages. Busy agents queue follow-ups. Offline runners retain pending work.
      </p>
    </aside>
  );
}

function NewChannelDialog({ onClose }: { onClose: () => void }) {
  const [name, setName] = useState("");
  const [visibility, setVisibility] = useState<"public" | "private">("public");
  const [topic, setTopic] = useState("");

  const create = useMutation((client, requestId) => {
    const request: CreateChannelRequest = {
      request_id: requestId,
      name: name.trim(),
      visibility,
      ...(topic.trim() ? { topic: topic.trim() } : {}),
    };
    return client.createChannel(request);
  });

  const ready = /^[a-z0-9][a-z0-9_-]{0,59}$/.test(name.trim());

  return (
    <Modal open onClose={onClose} labelledBy="new-channel-title">
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <h2 id="new-channel-title" style={{ margin: 0 }}>
          New channel
        </h2>
        <span className="spacer" />
        <button type="button" onClick={onClose} aria-label="Close">
          ✕
        </button>
      </div>

      <label htmlFor="channel-name">Name</label>
      <input id="channel-name" data-initial-focus value={name} onChange={(e) => setName(e.target.value)} />

      <label htmlFor="channel-visibility">Visibility</label>
      <select
        id="channel-visibility"
        value={visibility}
        onChange={(e) => setVisibility(e.target.value as "public" | "private")}
      >
        <option value="public">Public</option>
        <option value="private">Private — requires explicit membership</option>
      </select>

      <label htmlFor="channel-topic">Topic (optional)</label>
      <input id="channel-topic" value={topic} onChange={(e) => setTopic(e.target.value)} />

      {create.phase === "pending" && <p className="tag" role="status">{pendingWriteCopy}</p>}
      {create.error && <ErrorNotice error={create.error} onRetry={() => create.run()} />}
      {create.phase === "succeeded" && create.result && (
        <p className="notice" role="status" data-testid="create-channel-result">
          Created #{create.result.name}.
        </p>
      )}

      <div className="dialog-actions">
        <button type="button" onClick={onClose}>
          Close
        </button>
        <button
          className="primary"
          onClick={() => create.run()}
          disabled={!ready || create.phase === "pending"}
          data-testid="create-channel"
        >
          Create channel
        </button>
      </div>
    </Modal>
  );
}

function Composer({
  channel,
  members,
  onSent,
}: {
  channel: Channel;
  members: Member[];
  onSent: () => void;
}) {
  const [body, setBody] = useState("");
  const [taskOpen, setTaskOpen] = useState(false);
  const [outcome, setOutcome] = useState("");
  const [routingMode, setRoutingMode] = useState<"direct" | "via_master">("direct");
  const [routingAgentId, setRoutingAgentId] = useState("");
  const [ticketMode, setTicketMode] = useState<"existing" | "new">("existing");
  const [existingTicketId, setExistingTicketId] = useState("");
  const [newTicketTitle, setNewTicketTitle] = useState("");
  const [newTicketRole, setNewTicketRole] = useState("");
  const bodyRef = useRef<HTMLTextAreaElement>(null);

  const routingAgents = members.filter((m) => (m.kind === "agent" || m.kind === "master") && m.agent_id);

  function resetComposer() {
    setBody("");
    setTaskOpen(false);
    setOutcome("");
    setRoutingAgentId("");
    setExistingTicketId("");
    setNewTicketTitle("");
    setNewTicketRole("");
  }

  // "Send" never carries an author field — SendMessageRequest (../api/types.ts)
  // has none to set, so a 403 sender_identity_rejected is structurally
  // impossible from this composer.
  const send = useMutation<[], SendMessageResponse>((client, requestId) => {
    const request: SendMessageRequest = {
      request_id: requestId,
      channel_id: channel.id,
      body: body.trim(),
      intent: "message",
    };
    return client.sendMessage(request);
  }, { onSuccess: () => { resetComposer(); onSent(); } });

  // Two-step by contract: /messages/{id}/task acts on an existing message id,
  // so "Send task" first creates the message, then attaches the task to the
  // id that call returns, in one mutation instance (docs/contracts/openapi.yaml
  // "Turn a message into a task").
  const sendTask = useMutation<[], SendTaskResponse>(async (client, requestId) => {
    const sent = await client.sendMessage({
      request_id: requestId,
      channel_id: channel.id,
      body: body.trim(),
      intent: "message",
    });
    const routing: SendTaskRouting =
      routingMode === "direct" ? { mode: "direct", agent_id: routingAgentId } : { mode: "via_master" };
    const ticket: SendTaskTicketLink =
      ticketMode === "existing"
        ? { existing_ticket_id: existingTicketId.trim() }
        : { new_ticket: { title: newTicketTitle.trim(), ...(newTicketRole.trim() ? { role: newTicketRole.trim() } : {}) } };
    const request: SendTaskRequest = { request_id: newId(), outcome: outcome.trim(), routing, ticket };
    return client.sendTask(sent.message.id, request);
  }, { onSuccess: () => { resetComposer(); onSent(); } });

  const sendReady = body.trim().length > 0 && send.phase !== "pending";
  const dispatchReady =
    body.trim().length > 0 &&
    outcome.trim().length > 0 &&
    (routingMode === "via_master" || routingAgentId.length > 0) &&
    (ticketMode === "existing" ? existingTicketId.trim().length > 0 : newTicketTitle.trim().length > 0) &&
    sendTask.phase !== "pending";

  function onBodyKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (sendReady) send.run();
    }
    // Shift+Enter: default textarea behavior inserts a newline, untouched.
  }

  return (
    <div className="card composer" style={{ marginTop: 12 }}>
      <p className="tag">
        To: {channel.kind === "channel" ? `#${channel.name}` : channel.name}
        {channel.designated_master && " — tasks route through the master"}
      </p>
      <label htmlFor="composer-body">Message</label>
      <textarea
        id="composer-body"
        ref={bodyRef}
        rows={3}
        value={body}
        onChange={(e) => setBody(e.target.value)}
        onKeyDown={onBodyKeyDown}
        placeholder="Describe the task or ask a question. Enter sends, Shift+Enter adds a line."
      />

      {taskOpen && (
        <fieldset style={{ border: "1px solid var(--line)", borderRadius: 8, padding: 12, marginTop: 10 }}>
          <legend className="tag">Send task</legend>

          <label htmlFor="task-outcome">Outcome</label>
          <textarea id="task-outcome" rows={2} value={outcome} onChange={(e) => setOutcome(e.target.value)} />

          <label htmlFor="task-routing-mode">Routing</label>
          <select
            id="task-routing-mode"
            value={routingMode}
            onChange={(e) => setRoutingMode(e.target.value as "direct" | "via_master")}
          >
            <option value="direct">Direct to agent</option>
            <option value="via_master">Route via master</option>
          </select>

          {routingMode === "direct" && (
            <>
              <label htmlFor="task-routing-agent">Agent</label>
              <select
                id="task-routing-agent"
                value={routingAgentId}
                onChange={(e) => setRoutingAgentId(e.target.value)}
              >
                <option value="">Choose an agent or master</option>
                {routingAgents.map((m) => (
                  <option key={m.id} value={m.agent_id!}>
                    {m.display_name} ({memberKindLabel[m.kind]})
                  </option>
                ))}
              </select>
            </>
          )}

          <label htmlFor="task-ticket-mode">Ticket</label>
          <select
            id="task-ticket-mode"
            value={ticketMode}
            onChange={(e) => setTicketMode(e.target.value as "existing" | "new")}
          >
            <option value="existing">Link an existing ticket</option>
            <option value="new">Create a new ticket</option>
          </select>

          {ticketMode === "existing" ? (
            <>
              <label htmlFor="task-existing-ticket-id">Ticket ID</label>
              <input
                id="task-existing-ticket-id"
                value={existingTicketId}
                onChange={(e) => setExistingTicketId(e.target.value)}
                placeholder="DEMO-14"
              />
            </>
          ) : (
            <>
              <label htmlFor="task-new-ticket-title">New ticket title</label>
              <input id="task-new-ticket-title" value={newTicketTitle} onChange={(e) => setNewTicketTitle(e.target.value)} />
              <label htmlFor="task-new-ticket-role">Role (optional)</label>
              <input id="task-new-ticket-role" value={newTicketRole} onChange={(e) => setNewTicketRole(e.target.value)} />
            </>
          )}
        </fieldset>
      )}

      {send.phase === "pending" && <p className="tag" role="status">{pendingWriteCopy}</p>}
      {send.error && <ErrorNotice error={send.error} onRetry={() => send.run()} />}
      {sendTask.phase === "pending" && <p className="tag" role="status">{pendingWriteCopy}</p>}
      {sendTask.error && <ErrorNotice error={sendTask.error} onRetry={() => sendTask.run()} />}
      {sendTask.phase === "succeeded" && sendTask.result && (
        <p className="notice" role="status" data-testid="send-task-result">
          Task attached to {sendTask.result.ticket.id}.
        </p>
      )}

      <div className="dialog-actions" style={{ justifyContent: "flex-start", marginTop: 10 }}>
        <button type="button" onClick={() => send.run()} disabled={!sendReady} data-testid="send-message">
          Send
        </button>
        {!taskOpen ? (
          <button type="button" onClick={() => setTaskOpen(true)} data-testid="open-send-task">
            Send task
          </button>
        ) : (
          <>
            <button type="button" onClick={() => setTaskOpen(false)}>
              Cancel task
            </button>
            <button
              type="button"
              className="primary"
              onClick={() => sendTask.run()}
              disabled={!dispatchReady}
              data-testid="dispatch-task"
            >
              Send task
            </button>
          </>
        )}
        <span className="tag">Enter sends · Shift+Enter adds a line</span>
      </div>
    </div>
  );
}

export function Messages() {
  const { connection } = useBoard();
  const [selectedChannelId, setSelectedChannelId] = useState<string | null>(null);
  const [creatingChannel, setCreatingChannel] = useState(false);

  const channels = useResource<ChannelListResponse>((client) => client.listChannels(), []);
  const members = useResource<MemberListResponse>((client) => client.listMembers(), []);

  useEffect(() => {
    if (selectedChannelId) return;
    const first = channels.data?.items[0];
    if (first) setSelectedChannelId(first.id);
  }, [channels.data, selectedChannelId]);

  const selectedChannel = channels.data?.items.find((c) => c.id === selectedChannelId) ?? null;

  const messages = useResource<MessageListResponse>(
    (client) =>
      selectedChannelId
        ? client.listMessages({ channel_id: selectedChannelId })
        : Promise.resolve<MessageListResponse>({
            items: [],
            next_cursor: null,
            stream: { state: "live", snapshot_version: 0, as_of: new Date().toISOString(), last_event_id: null },
          }),
    [selectedChannelId],
  );

  const membersById = useMemo(() => new Map((members.data?.items ?? []).map((m) => [m.id, m])), [members.data]);

  return (
    <>
      <div className="heading">
        <div>
          <span className="tag">PROJECT / MESSAGES</span>
          <h1>Messages</h1>
        </div>
        <span className="spacer" />
        <button className="primary" onClick={() => setCreatingChannel(true)} data-testid="new-channel">
          New channel
        </button>
      </div>
      <ConnectionBanner stream={channels.data?.stream ?? null} connection={connection} fetchedAt={channels.fetchedAt} />
      {channels.error && <ErrorNotice error={channels.error} onRetry={channels.refetch} onReload={channels.refetch} />}
      {members.error && <ErrorNotice error={members.error} onRetry={members.refetch} onReload={members.refetch} />}
      {channels.loading && !channels.data && <p data-testid="messages-loading">Reading the board…</p>}

      {channels.data && channels.data.empty_state && channels.data.items.length === 0 ? (
        <EmptyStateView empty={channels.data.empty_state} onPrimaryAction={() => setCreatingChannel(true)} />
      ) : (
        channels.data && (
          <div style={{ display: "flex", gap: 16, alignItems: "flex-start" }}>
            <ChannelRail
              channels={channels.data.items}
              selectedId={selectedChannelId}
              onSelect={setSelectedChannelId}
            />

            <div style={{ flex: 1, minWidth: 0 }}>
              {!selectedChannel && <p className="tag">Select a channel to read its conversation.</p>}

              {selectedChannel && (
                <>
                  <div className="heading" style={{ marginBottom: 4 }}>
                    <div>
                      <h2 style={{ margin: 0 }}>
                        {selectedChannel.kind === "channel" ? `#${selectedChannel.name}` : selectedChannel.name}
                      </h2>
                      <small className="tag">
                        {selectedChannel.kind === "channel"
                          ? selectedChannel.topic ?? "Project channel · members and agents"
                          : "Direct message · visible to participants"}
                        {selectedChannel.designated_master && " · Tasks route through the master"}
                      </small>
                    </div>
                  </div>

                  {messages.error && (
                    <ErrorNotice error={messages.error} onRetry={messages.refetch} onReload={messages.refetch} />
                  )}

                  {messages.loading && !messages.data && <p data-testid="messages-pane-loading">Reading messages…</p>}

                  {messages.data && messages.data.empty_state && messages.data.items.length === 0 ? (
                    <EmptyStateView
                      empty={messages.data.empty_state}
                      onPrimaryAction={() => document.getElementById("composer-body")?.focus()}
                    />
                  ) : (
                    messages.data && (
                      <div role="log" aria-label="Conversation">
                        {messages.data.items.map((m) => (
                          <MessageRow key={m.id} message={m} response={messages.data!} membersById={membersById} />
                        ))}
                        {messages.data.next_cursor && (
                          <p className="tag" data-testid="more-messages">
                            More messages exist earlier in this channel.
                          </p>
                        )}
                      </div>
                    )
                  )}

                  {selectedChannel && (
                    <Composer channel={selectedChannel} members={members.data?.items ?? []} onSent={messages.refetch} />
                  )}
                </>
              )}
            </div>

            <MembersSidebar
              members={members.data?.items ?? []}
              selectedChannel={selectedChannel}
              onAdded={() => channels.refetch()}
            />
          </div>
        )
      )}

      {creatingChannel && <NewChannelDialog onClose={() => setCreatingChannel(false)} />}
    </>
  );
}
