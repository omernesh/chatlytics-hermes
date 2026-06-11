# Features: longpoll durability, control envelopes, channel prompts, progress bubbles

> Applies to plugin **v4.5.3**. Everything below is verified behavior in
> `src/chatlytics_hermes/adapter.py`; wire contracts are noted where the
> chatlytics server is the counterpart.

## Durable longpoll (v4.2.0, hardened v4.5.1)

In `longpoll` inbound mode the poll loop is designed to survive anything
short of `disconnect()`:

- **Normal retry events** — connection refused/reset/timeout, non-200
  responses, AND the chatlytics graceful-shutdown signal (empty 200 +
  `Connection: close` on server restart) all take a bounded jittered backoff
  ladder: **1s → 2s → 5s → 15s → 30s cap**, jitter up to +25%, **never gives
  up**, resets on success. A chatlytics restart or deploy therefore needs
  **no manual gateway restart** — the consumer reconnects on its own within
  seconds.
- **State-change-only logging** — one WARNING on healthy→degraded (with an
  actionable symptom→fix hint, see [troubleshooting.md](troubleshooting.md)),
  one INFO on recovery. Per-attempt records are DEBUG, so a long outage
  cannot flood logs. Since v4.5.1 a degraded-reason *change* (e.g. transport
  error → HTTP 401) also logs at the new reason's level.
- **The loop exits ONLY on `CancelledError`** (v4.5.1). Any other exception
  — including bugs — logs an ERROR with a full traceback (once per distinct
  error class, DEBUG thereafter) and takes the same backoff path. Belt and
  suspenders: `connect()` attaches a done-callback that logs an unmissable
  `"chatlytics longpoll task EXITED"` ERROR if the task ever completes
  non-cancelled.
- **Cursor semantics** — `400 invalid_cursor` resets the cursor; a healthy
  empty batch loops immediately without acking; non-200 acks are logged
  (401 → ERROR with rotate-token guidance; otherwise WARNING — the read
  pointer did not advance and envelopes re-deliver).
- **Timeouts that fit the hold** — the server holds the GET up to 25s
  (`timeout_ms=25000`); the poll uses an explicit per-request read timeout of
  hold + 15s, so empty polls never trip the spurious
  `longpoll GET transport error ()` ReadTimeout (fixed v4.1.1).

## Control envelopes + `caps=control` (v4.3.0)

WhatsApp users can drive conversation commands by typing them in the chat:
the chatlytics server (v5.4+) intercepts slash commands **server-side**,
before bot dispatch, and forwards the conversation-control ones to the
gateway as `kind: "control"` envelopes over the existing longpoll channel.

**Capability negotiation:** every longpoll GET carries `caps=control`
alongside `cursor`/`timeout_ms`. The server records it per-poll and only
emits control envelopes to caps-advertising gateways, so pre-4.3.0 gateways
never see shapes they would mis-dispatch as message text. There is no
response handshake; ack is unchanged. (Webhook-mode gateways never advertise
the cap and do not receive control envelopes.)

Control actions the plugin executes (the action set is locked to the handler
map in `adapter.py` — `_CONTROL_ACTION_HANDLERS`):

| User types | Control action | What the plugin does |
|------------|----------------|----------------------|
| `/new` | `new_conversation` | Resets the chat's Hermes conversation via the gateway runner's own /new machinery (interrupt in-flight turn, clear queued work, evict the cached agent, rotate the session-store entry); falls back to `session_store.reset_session(...)`, then to a logged no-op. The destructive-slash confirm gate is deliberately bypassed — the chatlytics server owns the user-facing command UX. |
| `/stop` | `stop` | Real cancellation via the runner's `_interrupt_and_clear_session` (interrupts the live agent turn, drops pending/queued work); the adapter-local fallback signals the per-session interrupt event + drops the pending slot and logs clearly what could NOT be cancelled. |
| `/retry` | `retry_last` | Re-dispatches the chat's last user message through the normal inbound path, from a bounded per-chat last-message memo (LRU, 128 chats). No memo → log + ignore. |
| `/approve` `/deny` `/answer` (owner DM) | `question_resolved` | Resolves a pending owner-DM approval/clarify question — see [approvals.md](approvals.md). |

Other conversation commands (e.g. `/usage`, `/sethome`) are handled
**entirely by the chatlytics server** — they are answered server-side and
never reach the plugin as envelopes.

**Forward-compat:** unknown `kind` values and unknown control `action`
values are logged (WARNING once per distinct value, DEBUG after, bounded
dedup set) and IGNORED — never dispatched as message text to the agent.
Normal message envelopes (absent `kind` or `kind == "message"`) flow through
the existing dispatch path unchanged, sharing the same seq/ack space.

## Per-channel prompts (v4.5.0)

Inbound MESSAGE envelopes (longpoll) and webhook payloads may carry an
additive **`channel_prompt`** field — sourced from the server-side
bot-module config ("channel-prompts" module). The adapter sets it on
`MessageEvent.channel_prompt`, the Hermes harness's native per-turn
ephemeral system-prompt channel: it is applied at API-call time and **never
persisted to the transcript**.

- When the server sends no value, the adapter falls back to the local
  `config.yaml` `channel_prompts` map via the harness's
  `resolve_channel_prompt`.
- `/retry` replays stay correct automatically — the memoized envelope
  carries the field.
- Harness drift (missing `resolve_channel_prompt`, or a `MessageEvent`
  without the `channel_prompt` attribute) logs one WARNING naming the drift,
  then DEBUG, on both transports.

## Progress-bubble edit-in-place (v4.4.0)

When an agent turn outlives a threshold (default **8s**), the adapter sends
ONE "⏳ working…" bubble, then **edits that bubble into the final reply**
instead of stacking a second message. Fast turns never see a bubble — below
the threshold the request stream is byte-identical to v4.3.0.

Knobs (env wins over `extra`, parsed defensively — see
[configuration.md](configuration.md)):

- `CHATLYTICS_STATUS_EDIT_IN_PLACE` / `extra.status_edit_in_place` — default
  **true**; `false` disables both the bubble and the edit.
- `CHATLYTICS_STATUS_BUBBLE_AFTER_S` / `extra.status_bubble_after_s` —
  default 8.0; `<= 0` disables the bubble.
- `CHATLYTICS_STATUS_BUBBLE_TEXT` / `extra.status_bubble_text` — default
  "⏳ working…".

Mechanics (all on `/api/v1/send`):

- The bubble POST carries **`progress: true`** — the server uses this to
  suppress reaction-feedback correlation for the bubble (so the bubble never
  gets treated as the bot's "answer").
- The final reply carries **`edit_message_id: <bubble id>`** — bot-scoped
  and ownership-enforced server-side. The server falls back to a plain send
  internally on edit failure, so a 200 always means the text was delivered;
  `edited` / `edit_fallback` response fields surface at DEBUG.
- Exactly ONE bubble per turn, anchored on the same `stop_event` as the
  `_keep_typing` heartbeat. Bubble failures are DEBUG-only and never affect
  the turn. Bubble ids live in a bounded per-chat LRU (cap 100, 10-minute
  staleness TTL — a stale bubble is never edited into a fresh reply).
- A transport-level failure of the edit-tagged send retries ONCE as a plain
  send (never lose the reply over the decoration); since v4.5.1 a non-200 on
  the edit-tagged send also retries plain, and a 200 with a non-JSON body is
  treated as a failure, not a silent success.
- **Media sends do not participate** — a pending bubble before a media reply
  is left behind as an acceptable residual.
- `edit_message_id` and `progress` are reserved `/api/v1/send` body keys —
  caller metadata cannot inject them.

## Session threading (P-19, carried forward)

The chatlytics `/api/v1/send` endpoint requires the WAHA `session` name.
The adapter resolves it per-send: (1) the per-chat session recorded from
inbound (the webhook handler records a payload's top-level `session`; every
longpoll envelope carries `session_id`), then (2) the
`CHATLYTICS_SESSION` / `extra.session` fallback. When neither is available
the send fails loudly with an operator-actionable error instead of silently
dropping the reply.
