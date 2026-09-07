import { describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Messages } from "../../ui/src/screens/Messages";
import { boardFetch, renderLive } from "./support/render-live";

import messagesChannelsPopulated from "../../ui/src/fixtures/data/messages/channels-populated.json";
import messagesMembers from "../../ui/src/fixtures/data/messages/members.json";
import messagesListPopulated from "../../ui/src/fixtures/data/messages/list-populated.json";
import messagesListEmpty from "../../ui/src/fixtures/data/messages/list-empty.json";
import deliveriesAgentBusy from "../../ui/src/fixtures/data/messages/deliveries-agent-busy.json";
import deliveriesAwaitingApproval from "../../ui/src/fixtures/data/messages/deliveries-awaiting-approval.json";
import deliveriesDependencyUnmet from "../../ui/src/fixtures/data/messages/deliveries-dependency-unmet.json";
import responseSend from "../../ui/src/fixtures/data/messages/response-send.json";
import responseTaskDispatched from "../../ui/src/fixtures/data/messages/response-task-dispatched.json";
import responseTaskRunnerOffline from "../../ui/src/fixtures/data/messages/response-task-runner-offline.json";
import responseTaskManualResume from "../../ui/src/fixtures/data/messages/response-task-manual-resume.json";
import errorNotChannelMember from "../../ui/src/fixtures/data/errors/403-not-channel-member.json";
import errorMembershipRevoked from "../../ui/src/fixtures/data/errors/403-membership-revoked.json";
import responseCreateChannel from "../../ui/src/fixtures/data/messages/response-create-channel.json";
import responseChannelMember from "../../ui/src/fixtures/data/messages/response-channel-member.json";

// msg_00000001 is the message every standalone deliveries-*.json fixture
// attaches its delivery to (see tests/fixtures/manifest.json) — wrapping it
// with one of those delivery lists gives a contract-exact MessageListResponse
// for a single reason/state without hand-inventing a message body.
const ROOT_MESSAGE = messagesListPopulated.items[0];
const STREAM = messagesListPopulated.stream;

function messageListWithDeliveries(deliveries: unknown[], items: unknown[] = [ROOT_MESSAGE]) {
  return {
    items,
    threads: [],
    deliveries,
    next_cursor: null,
    stream: STREAM,
  };
}

function errorResponse(body: unknown, status: number) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

describe("Messages, reading a live board", () => {
  it("renders channels and DMs with unread counts", async () => {
    renderLive(
      <Messages />,
      boardFetch({ "GET /channels": messagesChannelsPopulated, "GET /members": messagesMembers, "GET /messages": messagesListEmpty }),
    );

    await waitFor(() => expect(screen.getByTestId("channel-chn_work0001")).toBeInTheDocument());
    expect(screen.getByTestId("channel-unread-chn_work0001")).toHaveTextContent("2");
    // A DM shows up in its own "Direct messages" group, not mixed into channels.
    expect(screen.getByTestId("channel-chn_dm000001")).toBeInTheDocument();
    // A channel with no unread carries no badge at all.
    expect(screen.queryByTestId("channel-unread-chn_general1")).not.toBeInTheDocument();
  });

  it("loads the selected channel's own messages, not another channel's", async () => {
    const user = userEvent.setup();
    const harness = boardFetch({
      "GET /channels": messagesChannelsPopulated,
      "GET /members": messagesMembers,
      "GET /messages": ({ url }: { url: URL }) =>
        url.searchParams.get("channel_id") === "chn_work0001" ? messagesListPopulated : messagesListEmpty,
    });
    renderLive(<Messages />, harness);

    // Auto-selected first channel (general) has no messages.
    await waitFor(() => expect(screen.getByText(/No messages in this channel yet/)).toBeInTheDocument());

    await user.click(screen.getByTestId("channel-chn_work0001"));
    await waitFor(() => expect(screen.getByTestId(`message-${ROOT_MESSAGE.id}`)).toBeInTheDocument());
    expect(screen.getByText("Can you take the paging bug on DEMO-14?")).toBeInTheDocument();

    expect(
      harness.calls().some((c) => c.method === "GET" && c.path === "/messages"),
    ).toBe(true);
  });

  it("shows a thread's ticket chip as a real control that opens the Tickets screen", async () => {
    const user = userEvent.setup();
    window.location.hash = "";
    renderLive(
      <Messages />,
      boardFetch({
        "GET /channels": messagesChannelsPopulated,
        "GET /members": messagesMembers,
        "GET /messages": messagesListPopulated,
      }),
    );

    await waitFor(() => expect(screen.getByTestId("ticket-chip-DEMO-14")).toBeInTheDocument());
    const chip = screen.getByTestId("ticket-chip-DEMO-14");
    expect(chip.tagName).toBe("BUTTON");
    await user.click(chip);
    expect(window.location.hash).toBe("#tickets");
  });

  it("shows the thread's reply count next to its root message", async () => {
    renderLive(
      <Messages />,
      boardFetch({
        "GET /channels": messagesChannelsPopulated,
        "GET /members": messagesMembers,
        "GET /messages": messagesListPopulated,
      }),
    );
    await waitFor(() => expect(screen.getByTestId("thread-replies-thr_00000001")).toBeInTheDocument());
    expect(screen.getByTestId("thread-replies-thr_00000001")).toHaveTextContent("2 replies");
  });

  it("renders 'Queued — agent is busy with another claim' for agent_busy, never a bare state name", async () => {
    renderLive(
      <Messages />,
      boardFetch({
        "GET /channels": messagesChannelsPopulated,
        "GET /members": messagesMembers,
        "GET /messages": messageListWithDeliveries(deliveriesAgentBusy.items),
      }),
    );
    await waitFor(() => expect(screen.getByTestId(`delivery-${deliveriesAgentBusy.items[0].id}`)).toBeInTheDocument());
    const el = screen.getByTestId(`delivery-${deliveriesAgentBusy.items[0].id}`);
    expect(el).toHaveTextContent("Queued — agent is busy with another claim");
    expect(el.textContent).not.toMatch(/^queued$/i);
  });

  it("renders 'Waiting on DEMO-13' for dependency_unmet, never claiming work started", async () => {
    renderLive(
      <Messages />,
      boardFetch({
        "GET /channels": messagesChannelsPopulated,
        "GET /members": messagesMembers,
        "GET /messages": messageListWithDeliveries(deliveriesDependencyUnmet.items),
      }),
    );
    await waitFor(() => expect(screen.getByTestId(`delivery-${deliveriesDependencyUnmet.items[0].id}`)).toBeInTheDocument());
    expect(screen.getByTestId(`delivery-${deliveriesDependencyUnmet.items[0].id}`)).toHaveTextContent("Waiting on DEMO-13");
  });

  it("renders 'Needs approval' with its reason_detail for awaiting_approval", async () => {
    renderLive(
      <Messages />,
      boardFetch({
        "GET /channels": messagesChannelsPopulated,
        "GET /members": messagesMembers,
        "GET /messages": messageListWithDeliveries(deliveriesAwaitingApproval.items),
      }),
    );
    const id = deliveriesAwaitingApproval.items[0].id;
    await waitFor(() => expect(screen.getByTestId(`delivery-${id}`)).toBeInTheDocument());
    expect(screen.getByTestId(`delivery-${id}`)).toHaveTextContent("Needs approval");
    expect(screen.getByTestId(`delivery-${id}`)).toHaveTextContent(
      "Needs approval: write outside the allowlisted worktree.",
    );
  });

  it("renders 'Queued — runner offline' for runner_offline", async () => {
    renderLive(
      <Messages />,
      boardFetch({
        "GET /channels": messagesChannelsPopulated,
        "GET /members": messagesMembers,
        "GET /messages": messageListWithDeliveries(responseTaskRunnerOffline.deliveries, [responseTaskRunnerOffline.message]),
      }),
    );
    const id = responseTaskRunnerOffline.deliveries[0].id;
    await waitFor(() => expect(screen.getByTestId(`delivery-${id}`)).toBeInTheDocument());
    expect(screen.getByTestId(`delivery-${id}`)).toHaveTextContent("Queued — runner offline");
  });

  it("renders 'Manual resume required' for manual_resume_required", async () => {
    renderLive(
      <Messages />,
      boardFetch({
        "GET /channels": messagesChannelsPopulated,
        "GET /members": messagesMembers,
        "GET /messages": messageListWithDeliveries(responseTaskManualResume.deliveries, [responseTaskManualResume.message]),
      }),
    );
    const id = responseTaskManualResume.deliveries[0].id;
    await waitFor(() => expect(screen.getByTestId(`delivery-${id}`)).toBeInTheDocument());
    expect(screen.getByTestId(`delivery-${id}`)).toHaveTextContent("Manual resume required");
  });

  it("shows a not_channel_member refusal via ErrorNotice with its error code", async () => {
    renderLive(
      <Messages />,
      boardFetch({
        "GET /channels": messagesChannelsPopulated,
        "GET /members": messagesMembers,
        "GET /messages": errorResponse(errorNotChannelMember, 403),
      }),
    );
    await waitFor(() => expect(screen.getByTestId("error-notice")).toBeInTheDocument());
    expect(screen.getByTestId("error-notice")).toHaveAttribute("data-error-code", "not_channel_member");
  });

  it("shows a membership_revoked refusal via ErrorNotice with its error code", async () => {
    renderLive(
      <Messages />,
      boardFetch({
        "GET /channels": messagesChannelsPopulated,
        "GET /members": messagesMembers,
        "GET /messages": errorResponse(errorMembershipRevoked, 403),
      }),
    );
    await waitFor(() => expect(screen.getByTestId("error-notice")).toBeInTheDocument());
    expect(screen.getByTestId("error-notice")).toHaveAttribute("data-error-code", "membership_revoked");
  });

  it("sends a plain message with intent 'message' and no author field", async () => {
    const user = userEvent.setup();
    const harness = boardFetch({
      "GET /channels": messagesChannelsPopulated,
      "GET /members": messagesMembers,
      "GET /messages": messagesListEmpty,
      "POST /messages": responseSend,
    });
    renderLive(<Messages />, harness);

    await waitFor(() => expect(screen.getByLabelText("Message")).toBeInTheDocument());
    await user.type(screen.getByLabelText("Message"), "Did the paging fix land?");
    await user.click(screen.getByTestId("send-message"));

    await waitFor(() =>
      expect(harness.calls().some((c) => c.method === "POST" && c.path === "/messages")).toBe(true),
    );
    const call = harness.calls().find((c) => c.method === "POST" && c.path === "/messages")!;
    const body = call.body as Record<string, unknown>;
    expect(body.intent).toBe("message");
    expect(body.body).toBe("Did the paging fix land?");
    expect("author" in body).toBe(false);
  });

  it("Enter sends the message; Shift+Enter only adds a line", async () => {
    const user = userEvent.setup();
    const harness = boardFetch({
      "GET /channels": messagesChannelsPopulated,
      "GET /members": messagesMembers,
      "GET /messages": messagesListEmpty,
      "POST /messages": responseSend,
    });
    renderLive(<Messages />, harness);

    const textarea = await screen.findByLabelText("Message");
    await user.type(textarea, "line one{Shift>}{Enter}{/Shift}line two");
    // Shift+Enter must not have submitted: no POST yet, and both lines are in the box.
    expect(harness.calls().some((c) => c.method === "POST" && c.path === "/messages")).toBe(false);
    expect((textarea as HTMLTextAreaElement).value).toBe("line one\nline two");

    await user.type(textarea, "{Enter}");
    await waitFor(() =>
      expect(harness.calls().some((c) => c.method === "POST" && c.path === "/messages")).toBe(true),
    );
  });

  it("Send task calls POST /messages then POST /messages/{id}/task in sequence, with the composed bodies", async () => {
    const user = userEvent.setup();
    const harness = boardFetch({
      "GET /channels": messagesChannelsPopulated,
      "GET /members": messagesMembers,
      "GET /messages": messagesListEmpty,
      "POST /messages": responseSend,
      [`POST /messages/${responseSend.message.id}/task`]: responseTaskDispatched,
    });
    renderLive(<Messages />, harness);

    await waitFor(() => expect(screen.getByTestId("open-send-task")).toBeInTheDocument());
    await user.type(screen.getByLabelText("Message"), "Please fix the DEMO-14 paging boundary.");
    await user.click(screen.getByTestId("open-send-task"));

    await user.type(screen.getByLabelText("Outcome"), "DEMO-14 paging returns the final page without duplicates.");
    // Direct routing to the one agent member in the fixture (mem_backend01 / agt_backend01).
    await user.selectOptions(screen.getByLabelText("Agent"), "agt_backend01");
    await user.type(screen.getByLabelText("Ticket ID"), "DEMO-14");

    await user.click(screen.getByTestId("dispatch-task"));

    await waitFor(() =>
      expect(harness.calls().some((c) => c.method === "POST" && c.path === `/messages/${responseSend.message.id}/task`)).toBe(
        true,
      ),
    );

    const sendCall = harness.calls().find((c) => c.method === "POST" && c.path === "/messages")!;
    const sendBody = sendCall.body as Record<string, unknown>;
    expect(sendBody.intent).toBe("message");
    expect("author" in sendBody).toBe(false);

    const taskCall = harness
      .calls()
      .find((c) => c.method === "POST" && c.path === `/messages/${responseSend.message.id}/task`)!;
    const taskBody = taskCall.body as Record<string, unknown>;
    expect(taskBody.outcome).toBe("DEMO-14 paging returns the final page without duplicates.");
    expect(taskBody.routing).toEqual({ mode: "direct", agent_id: "agt_backend01" });
    expect(taskBody.ticket).toEqual({ existing_ticket_id: "DEMO-14" });
  });

  it("shows the Members sidebar with kind, availability and role, including a revoked member", async () => {
    renderLive(
      <Messages />,
      boardFetch({ "GET /channels": messagesChannelsPopulated, "GET /members": messagesMembers, "GET /messages": messagesListEmpty }),
    );
    await waitFor(() => expect(screen.getByTestId("member-mem_backend01")).toBeInTheDocument());
    const backend = within(screen.getByTestId("member-mem_backend01"));
    expect(backend.getByText("Agent")).toBeInTheDocument();
    expect(screen.getByTestId("member-availability-mem_backend01")).toHaveTextContent("Busy");

    const revoked = within(screen.getByTestId("member-mem_revoked01"));
    expect(revoked.getByText("Revoked")).toBeInTheDocument();
  });

  it("creates a channel via New channel, with the composed request and the board's own name in the result", async () => {
    const user = userEvent.setup();
    const harness = boardFetch({
      "GET /channels": messagesChannelsPopulated,
      "GET /members": messagesMembers,
      "GET /messages": messagesListEmpty,
      "POST /channels": responseCreateChannel,
    });
    renderLive(<Messages />, harness);

    await waitFor(() => expect(screen.getByTestId("new-channel")).toBeInTheDocument());
    await user.click(screen.getByTestId("new-channel"));

    await user.type(screen.getByLabelText("Name"), "incidents-2");
    await user.selectOptions(screen.getByLabelText("Visibility"), "private");
    await user.type(screen.getByLabelText("Topic (optional)"), "Production incidents for this project.");
    await user.click(screen.getByTestId("create-channel"));

    await waitFor(() =>
      expect(screen.getByTestId("create-channel-result")).toHaveTextContent(`Created #${responseCreateChannel.name}.`),
    );

    const call = harness.calls().find((c) => c.method === "POST" && c.path === "/channels")!;
    const body = call.body as Record<string, unknown>;
    expect(body.name).toBe("incidents-2");
    expect(body.visibility).toBe("private");
    expect(body.topic).toBe("Production incidents for this project.");
    expect("author" in body).toBe(false);
  });

  it("adds a member to the selected channel and reports the board's own confirmation", async () => {
    const user = userEvent.setup();
    const harness = boardFetch({
      "GET /channels": messagesChannelsPopulated,
      "GET /members": messagesMembers,
      "GET /messages": messagesListEmpty,
      [`POST /channels/${responseChannelMember.channel_id}/members`]: responseChannelMember,
    });
    renderLive(<Messages />, harness);

    await user.click(await screen.findByTestId("channel-chn_private1"));
    await waitFor(() => expect(screen.getByLabelText(/Add to incidents/)).toBeInTheDocument());
    await user.selectOptions(screen.getByLabelText(/Add to incidents/), "mem_teammate1");
    await user.click(screen.getByTestId("add-channel-member-confirm"));

    await waitFor(() => expect(screen.getByTestId("add-member-result")).toHaveTextContent("Added to incidents."));

    const call = harness
      .calls()
      .find((c) => c.method === "POST" && c.path === `/channels/${responseChannelMember.channel_id}/members`)!;
    const body = call.body as Record<string, unknown>;
    expect(body.member_id).toBe("mem_teammate1");
    expect("author" in body).toBe(false);
  });
});
