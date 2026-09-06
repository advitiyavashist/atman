# Conformance report

Target: `http://127.0.0.1:50590`

**Target is the in-process fixture-replay stub, not a real server.** T-180 (the board API) does not exist yet. This report is a harness self-check: it proves the harness correctly matches status codes and schemas against known-good fixtures. It is not evidence about any real server's behaviour.

**67/67 rows passed.**

| Route | Kind | Expected | Actual | Result | Notes |
|---|---|---|---|---|---|
| GET /overview<br>`getOverview` | read | 200 | 200 | PASS |  |
| GET /tickets<br>`listTickets` | read | 200 | 200 | PASS |  |
| GET /tickets/{ticket_id}<br>`getTicket` | read | 200 | 200 | PASS |  |
| GET /agents<br>`listAgents` | read | 200 | 200 | PASS |  |
| GET /master<br>`getMasterPanel` | read | 200 | 200 | PASS |  |
| GET /activity<br>`listActivity` | read | 200 | 200 | PASS |  |
| GET /members<br>`listMembers` | read | 200 | 200 | PASS |  |
| GET /channels<br>`listChannels` | read | 200 | 200 | PASS |  |
| GET /messages<br>`listMessages` | read | 200 | 200 | PASS |  |
| GET /messages/{message_id}/deliveries<br>`listDeliveries` | read | 200 | 200 | PASS |  |
| GET /runners/jobs<br>`listWakeJobs` | read | 200 | 200 | PASS |  |
| POST /tickets<br>`createTicket` | mutate | 201 | 201 | PASS |  |
| POST /tickets<br>`createTicket[actor-reject]` | actor-reject | 400 | 400 | PASS |  |
| POST /tickets/{ticket_id}/claim<br>`claimTicket` | mutate | 200 | 200 | PASS |  |
| POST /tickets/{ticket_id}/claim<br>`claimTicket[actor-reject]` | actor-reject | 400 | 400 | PASS |  |
| POST /tickets/{ticket_id}/updates<br>`createTicketUpdate` | mutate | 201 | 201 | PASS |  |
| POST /tickets/{ticket_id}/updates<br>`createTicketUpdate[actor-reject]` | actor-reject | 400 | 400 | PASS |  |
| POST /tickets/{ticket_id}/reviews<br>`requestReview` | mutate | 201 | 201 | PASS |  |
| POST /tickets/{ticket_id}/reviews<br>`requestReview[actor-reject]` | actor-reject | 400 | 400 | PASS |  |
| POST /tickets/{ticket_id}/reviews/{review_id}/decision<br>`decideReview[accept]` | mutate | 200 | 200 | PASS |  |
| POST /tickets/{ticket_id}/reviews/{review_id}/decision<br>`decideReview[accept][actor-reject]` | actor-reject | 400 | 400 | PASS |  |
| POST /tickets/{ticket_id}/reviews/{review_id}/decision<br>`decideReview[reject]` | mutate | 200 | 200 | PASS |  |
| POST /tickets/{ticket_id}/reviews/{review_id}/decision<br>`decideReview[reject][actor-reject]` | actor-reject | 400 | 400 | PASS |  |
| POST /tickets/{ticket_id}/blocked<br>`setTicketBlocked` | mutate | 200 | 200 | PASS |  |
| POST /tickets/{ticket_id}/blocked<br>`setTicketBlocked[actor-reject]` | actor-reject | 400 | 400 | PASS |  |
| DELETE /agents/{agent_id}/session-lease<br>`revokeSessionLease` | mutate | 200 | 200 | PASS |  |
| DELETE /agents/{agent_id}/session-lease<br>`revokeSessionLease[actor-reject]` | actor-reject | 400 | 400 | PASS |  |
| POST /enrollments<br>`createEnrollment` | mutate | 201 | 201 | PASS |  |
| POST /enrollments<br>`createEnrollment[actor-reject]` | actor-reject | 400 | 400 | PASS |  |
| POST /sessions<br>`exchangeEnrollment` | mutate | 201 | 201 | PASS |  |
| POST /sessions<br>`exchangeEnrollment[actor-reject]` | actor-reject | 400 | 400 | PASS |  |
| POST /hook-events<br>`postHookEvent[event]` | mutate | 200 | 200 | PASS |  |
| POST /hook-events<br>`postHookEvent[event][actor-reject]` | actor-reject | 400 | 400 | PASS |  |
| POST /hook-events<br>`postHookEvent[with-note]` | mutate | 200 | 200 | PASS |  |
| POST /hook-events<br>`postHookEvent[with-note][actor-reject]` | actor-reject | 400 | 400 | PASS |  |
| POST /hook-events<br>`postHookEvent[stop]` | mutate | 200 | 200 | PASS |  |
| POST /hook-events<br>`postHookEvent[stop][actor-reject]` | actor-reject | 400 | 400 | PASS |  |
| POST /master/lease<br>`takeMasterLease` | mutate | 200 | 200 | PASS |  |
| POST /master/lease<br>`takeMasterLease[actor-reject]` | actor-reject | 400 | 400 | PASS |  |
| POST /master/pause<br>`setMasterPaused` | mutate | 200 | 200 | PASS |  |
| POST /master/pause<br>`setMasterPaused[actor-reject]` | actor-reject | 400 | 400 | PASS |  |
| POST /assignments<br>`createAssignment` | mutate | 201 | 201 | PASS |  |
| POST /assignments<br>`createAssignment[actor-reject]` | actor-reject | 400 | 400 | PASS |  |
| POST /invitations<br>`createInvitation` | mutate | 201 | 201 | PASS |  |
| POST /invitations<br>`createInvitation[actor-reject]` | actor-reject | 400 | 400 | PASS |  |
| POST /invitations/exchange<br>`exchangeInvitation` | mutate | 201 | 201 | PASS |  |
| POST /invitations/exchange<br>`exchangeInvitation[actor-reject]` | actor-reject | 400 | 400 | PASS |  |
| POST /channels<br>`createChannel` | mutate | 201 | 201 | PASS |  |
| POST /channels<br>`createChannel[actor-reject]` | actor-reject | 400 | 400 | PASS |  |
| POST /channels/{channel_id}/members<br>`addChannelMember` | mutate | 201 | 201 | PASS |  |
| POST /channels/{channel_id}/members<br>`addChannelMember[actor-reject]` | actor-reject | 400 | 400 | PASS |  |
| POST /messages<br>`sendMessage` | mutate | 201 | 201 | PASS |  |
| POST /messages<br>`sendMessage[actor-reject]` | actor-reject | 400 | 400 | PASS |  |
| POST /messages/{message_id}/task<br>`sendTask[direct]` | mutate | 201 | 201 | PASS |  |
| POST /messages/{message_id}/task<br>`sendTask[direct][actor-reject]` | actor-reject | 400 | 400 | PASS |  |
| POST /messages/{message_id}/task<br>`sendTask[via-master]` | mutate | 201 | 201 | PASS |  |
| POST /messages/{message_id}/task<br>`sendTask[via-master][actor-reject]` | actor-reject | 400 | 400 | PASS |  |
| POST /runners/register<br>`registerRunner` | mutate | 200 | 200 | PASS |  |
| POST /runners/register<br>`registerRunner[actor-reject]` | actor-reject | 400 | 400 | PASS |  |
| POST /runs/{run_id}/events<br>`postRunEvent[started]` | mutate | 200 | 200 | PASS |  |
| POST /runs/{run_id}/events<br>`postRunEvent[started][actor-reject]` | actor-reject | 400 | 400 | PASS |  |
| POST /runs/{run_id}/events<br>`postRunEvent[needs-approval]` | mutate | 200 | 200 | PASS |  |
| POST /runs/{run_id}/events<br>`postRunEvent[needs-approval][actor-reject]` | actor-reject | 400 | 400 | PASS |  |
| POST /runs/{run_id}/events<br>`postRunEvent[budget-reached]` | mutate | 200 | 200 | PASS |  |
| POST /runs/{run_id}/events<br>`postRunEvent[budget-reached][actor-reject]` | actor-reject | 400 | 400 | PASS |  |
| POST /runs/{run_id}/cancel<br>`cancelRun` | mutate | 200 | 200 | PASS |  |
| POST /runs/{run_id}/cancel<br>`cancelRun[actor-reject]` | actor-reject | 400 | 400 | PASS |  |
