"""Server-supplied screen copy shared by route assembly and storage envelopes."""

EMPTY_STATES = {
    "overview": {
        "headline": "No work yet. Create a ticket or import a board.",
        "detail": None,
        "primary_action": "New ticket",
    },
    "tickets": {
        "headline": "No tickets match this filter.",
        "detail": "Clear the filter or create a ticket.\n\nTurn counts stay unmeasured until tickets finish.",
        "primary_action": "New ticket",
    },
    "agents": {
        "headline": "Connect your first agent.",
        "detail": "Run tickets join <name> --roles … from the harness machine.\n\nSeats stay unmeasured until the first heartbeat.",
        "primary_action": "Connect agent",
    },
    "activity": {
        "headline": "No activity yet.",
        "detail": "This filter matches no audit events.\n\nTraffic is never invented — only recorded events appear.",
        "primary_action": None,
    },
    "channels": {
        "headline": "No channels you can read yet.",
        "detail": "Ask an owner to add you, or create a channel.\n\nDelivery receipts only — no fabricated traffic.",
        "primary_action": "New channel",
    },
    "messages": {
        "headline": "No messages in this channel yet.",
        "detail": "Send when a seat should see something.\n\nReceipts show delivery state — never implied progress on tickets.",
        "primary_action": "Send",
    },
}
