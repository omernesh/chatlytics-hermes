# Owner-DM approvals & clarify questions (v4.5.0)

> Applies to plugin **v4.5.3**. Server counterpart: chatlytics v5.4 P8
> (`POST /api/v1/bot/questions` + `question_resolved` control envelopes).

Dangerous-command approvals and agent clarify questions are routed to the
bot's **paired owner DM** on WhatsApp — never to the triggering chat. The
owner replies there:

```
/approve <id>      → approval granted (one-shot)
/deny <id>         → approval denied
/answer <id> <text> → clarify answered
```

The resolution rides back to the gateway as a `question_resolved` control
envelope on the existing longpoll. **This flow requires longpoll inbound
mode** — `question_resolved` rides the `caps=control` advertisement, and the
server refuses question POSTs from gateways that didn't advertise it
(409 `gateway_not_control_capable`).

## Wire contract

```
POST /api/v1/bot/questions   (bot bearer auth)
  { type: "approval"|"clarify", text: str (<=2000),
    request_id: str 8..64 [A-Za-z0-9_-], chat_id: str,
    ttl_s?: int 60..86400 }
  201 → {request_id, short_id, status:"pending", expires_at}
  409 gateway_not_control_capable / owner_unresolved / duplicate_request_id
  429 too_many_pending_questions
  502 owner_delivery_failed (row rolled back — safe to retry once)

control envelope:
  { kind:"control", action:"question_resolved", request_id,
    resolution:"approved"|"denied"|"answered", answer?: str, ... }
```

The plugin posts `ttl_s` aligned to its own wait (+60s margin,
server-clamped to 60..86400) so dead questions don't linger in the owner DM
long after the gateway-side wait has resolved.

## Exec approvals: `send_exec_approval` (runner hook)

The Hermes runner feature-detects `send_exec_approval` **on the adapter
class** and calls it when a dangerous command is pending. The adapter:

1. Builds `"{description}:\n{command}"` (command capped at 1500 chars —
   headroom under the server's 2000-char text cap).
2. Registers the pending question **before** POSTing (so a
   `question_resolved` envelope racing the POST response can never orphan
   the owner's reply), then POSTs `type:"approval"`.
3. On 201, returns `SendResult(success=True, message_id=request_id)` — the
   runner waits. When the owner's `/approve` or `/deny` arrives, the
   resolution maps approved→`"once"` / anything-else→`"deny"` into
   `tools.approval.resolve_gateway_approval(session_key, choice)`.

**Fail-closed:** an unresolved approval times out to **DENY**. Note that on
POST failure the runner's typed-`/approve` text fallback is *effectively
dead under chatlytics* — the server-side slash floor intercepts `/approve`
before it can reach the runner — so the adapter logs this loudly at ERROR
and the approval simply times out to deny.

## Clarify questions: `send_clarify`

Same POST flow with `type:"clarify"`; choices are rendered as a numbered
list plus "Answer with the option number or your own text.". The answer
arrives via the owner-DM `/answer` →
`tools.clarify_gateway.resolve_gateway_clarify(clarify_id, answer)`. This
path deliberately does **not** `mark_awaiting_text` — the answer comes from
the owner DM, not from the next message in the triggering chat.

- **POST failure** delegates to the BASE in-chat numbered-text fallback
  (which *does* `mark_awaiting_text`). Plain text is not slash-intercepted
  by chatlytics, so unlike the approval fallback this degradation actually
  works.
- **Owner denial** resolves the clarify with the empty string — the
  harness's own no-response sentinel — unblocking the agent thread
  immediately instead of pinning it for the full clarify timeout.

## Awaitable primitives: `ask_approval` / `ask_clarify`

For tooling and library callers, the adapter exposes asyncio-future-backed
primitives:

```python
approved: bool = await adapter.ask_approval(
    "Deploy v5 to production?", chat_id, timeout_s=300.0
)
answer: str | None = await adapter.ask_clarify(
    "Which environment?", chat_id, timeout_s=300.0
)
```

- `ask_approval` returns `True` **only** on resolution `"approved"`;
  denied, timeout, and POST failure all return `False` (**fail-closed
  DENY**).
- `ask_clarify` returns the owner's answer string on `"answered"`, else
  `None`.

## Robustness contract (v4.5.1/v4.5.2 hardening)

- Pending entries (and ask-primitive futures) are registered **before** the
  POST, keyed by `request_id` (uuid4 hex), so a racing resolution can never
  be orphaned.
- Transport-error / unknown POST outcomes **keep** the entry — the question
  may have been delivered; the owner's reply still resolves it, and the wait
  timeout / registry TTL cleans up. Only a definitive server rejection pops
  it.
- `409 duplicate_request_id` means "the question already exists — keep
  waiting", not failure. `502 owner_delivery_failed` retries exactly once
  with the **same** `request_id` (the server rolls the row back first).
- A **duplicate** `question_resolved` envelope (entry already popped) is
  warn-ignored — first resolution stands, no `InvalidStateError`.
- The pending-question registry is bounded: cap 64 (FIFO-evict with a
  WARNING — an evicted question can no longer be resolved and times out
  fail-closed), 2h stale-prune on insert. Unknown/expired `request_id`s warn
  once then DEBUG.
- Harness drift tolerance: if the installed hermes-agent lacks
  `tools.approval` / `tools.clarify_gateway`, the resolution is logged as
  undeliverable (the wait times out on its own) without breaking control
  routing for other actions.
