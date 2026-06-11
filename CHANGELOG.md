# Changelog

## [4.5.3] - 2026-06-11

### Fixed

- **Hotfix: host-injected kwargs filtered to the handler signature.** Hermes'
  `tools/registry.py:403` dispatches `entry.handler(args, **kwargs)` with
  host bookkeeping kwargs (observed: `task_id`), which `_bound` forwarded raw
  into bare handlers → `TypeError: chatlytics_health() got an unexpected
  keyword argument 'task_id'` on EVERY tool call. This was masked before
  4.5.2 (the adapter-not-connected failure returned first); the
  `_LIVE_ADAPTERS` fix unmasked it on all 5 gateways. `_make_tool_handler`
  now inspects the wrapped handler's signature once at bind time and `_bound`
  drops any kwarg the handler cannot accept — unless the handler declares
  `**kwargs` (explicit opt-in, gets everything). Robust to ANY future
  host-injected key. Inbound `client`/`adapter` keys are always dropped
  (`_bound` supplies those itself). Introspection failure fails open to the
  previous pass-everything behavior.

## [4.5.2] - 2026-06-11

### Fixed

- **chatlytics_health / chatlytics_login (and every chatlytics_* tool) now
  work on longpoll-only gateways.** Root cause: hermes-agent's real
  `hermes_cli.plugins.PluginContext` (verified on 0.16.0) exposes NEITHER
  `get_platform` NOR a `platforms` mapping — the only accessors
  `_make_tool_handler._lookup_adapter()` probed — so in production every
  tool call failed with `"adapter is not connected"` even while the
  longpoll loop was alive and healthy (observed on the BotDaddy gateway).
  Fix: `connect()` now registers the adapter in a module-level
  `_LIVE_ADAPTERS` registry (all success paths, including the degraded
  no-credential load); `_lookup_adapter` falls back to it after the ctx
  probes (ctx accessors still win when present — test harnesses
  unchanged); `disconnect()` unregisters with an identity guard so an
  overlapping reconnect is never clobbered.

### Added

- Cross-repo dedup contract test: a duplicate `question_resolved` control
  envelope (same `request_id`, entry already popped by the first delivery)
  is warn-ignored — no `asyncio.InvalidStateError`, first resolution
  stands (review-d3 informational LOW).

## [4.5.1] - 2026-06-11

Review-d3 fix pass: poll-loop resilience, question-flow robustness, and
observability hardening across the v4.3.0–v4.5.0 surface. No wire-contract
changes.

### Fixed

- **CRITICAL (X1) — poll loop can no longer die silently.** The longpoll
  loop previously caught only `CancelledError` + `httpx.RequestError`; any
  other exception killed the task with no done-callback and no log — a
  silently dead bot. The ENTIRE loop iteration is now wrapped: unexpected
  exceptions log `ERROR` with a full traceback (once per distinct error
  class, DEBUG thereafter) and take the same bounded-backoff retry path;
  the loop exits ONLY on `CancelledError`. Belt + suspenders: `connect()`
  attaches a done-callback that logs an unmissable
  `"chatlytics longpoll task EXITED"` ERROR if the task ever completes
  non-cancelled.
- **(H6) `/new` + `/stop` interrupt failures are no longer suppressed.**
  The adapter-side `interrupt_session_activity` fallbacks swapped
  `contextlib.suppress(Exception)` for try/except WARNING (with
  traceback); the `/stop` summary line now says `"FAILED to signal"`
  instead of falsely claiming the interrupt event was signalled.
- **(H7) channel-prompt harness-drift fallbacks warn once.** A missing /
  raising `resolve_channel_prompt` and a `MessageEvent` without the
  `channel_prompt` attribute each log one WARNING naming the drift (then
  DEBUG), on both the longpoll and webhook paths — previously silent.
- **(H8) longpoll ack non-200 is logged.** 401 → ERROR with
  rotate-the-token guidance; any other non-200 → WARNING (the read pointer
  did not advance; envelopes re-deliver).
- **(H9) degraded-reason CHANGE logs at the new reason's level** (e.g.
  transport error → HTTP 401), not just the healthy→degraded edge.
  Transport errors compare by a detail-free reason class so a flapping
  network still logs exactly one WARNING per episode (P3 contract kept).
- **(M1/M2 — X15) question-flow robustness.** Pending-question entries
  (and ask-primitive futures) are registered BEFORE the POST keyed by
  request_id, so a `question_resolved` envelope racing the POST response
  can never orphan the owner's reply. Transport-error / unknown POST
  outcome KEEPS the entry (the question may have been delivered; the
  owner's reply still resolves it — wait timeout / registry TTL cleans
  up). 409 `duplicate_request_id` now means "question exists — keep
  waiting", not failure. Every question POST carries `ttl_s` aligned to
  its wait (+60 s, server-clamped 60..86400). Awaiter cleanup moved to a
  cancellation-safe `finally`. `_post_question` unexpected raises log a
  full traceback; exec-approval question loss logs at ERROR.
- **(M1/M6 — X16) edit-tagged send hardening.** A non-200 on an
  edit-tagged send now retries once as a plain send (previously only
  transport errors retried) — the at-least-once double-deliver tradeoff is
  documented inline. A 200 with a non-JSON body is now a FAILURE (WARNING
  with the first 120 chars), not a silent success.
- **(M7) webhook dispatch-exception handling documented.** Kept the
  200-ack + `log.exception` choice with an inline comment explaining WHY
  (the chatlytics webhook-forwarder retries 5xx up to 3×; handler raises
  are deterministic and may follow partial dispatch → a 500 would cause
  duplicate agent turns).

### Changed (internal hygiene — X20)

- `_STATUS_BUBBLE_AFTER_DEFAULT_S` constant replaces the magic `8.0`.
- `_CONTROL_ACTIONS` is now derived from the `_CONTROL_ACTION_HANDLERS`
  map that `_handle_control_envelope` dispatches through (set and routing
  can no longer drift).
- `_envelope_to_body` extracted — `_dispatch_envelope` and
  `_control_event` no longer carry hand-synced copies of the
  envelope→body translation.
- `send()` success derivation reuses `_coerce_success_payload` +
  `_extract_message_id` (messageId/message_id fallback-order drift fixed;
  `_make_send_result` unified on the same extractor).
- `ask_approval` / `ask_clarify` deduplicated onto a shared
  `_ask_question` core.
- `client.py` stale version comment replaced with a keep-in-lockstep note.

## [4.5.0] - 2026-06-11

Native exec-approval + clarify hooks routed to the chatlytics owner-DM
question flow (chatlytics v5.4 P8, gateway half), plus per-channel prompt
injection. Dangerous-command approvals and agent clarify questions now go
to the bot's paired OWNER DM (never the triggering chat); the owner
replies `/approve <id>` / `/deny <id>` / `/answer <id> <text>` there and
the resolution rides back as a `question_resolved` control envelope on the
existing longpoll (`caps=control` advertisement unchanged — no new
capability token).

### Added

- **`send_exec_approval` hook** (runner feature-detects it on the adapter
  class): builds `"{description}:\n{command}"` (command capped at 1500
  chars; server caps question text at 2000) and POSTs
  `POST /api/v1/bot/questions {type:"approval", text, request_id,
  chat_id}`. 201 → `SendResult(success=True, message_id=request_id)` and
  the runner waits; resolution maps approved→`"once"` /
  denied→`"deny"` into `tools.approval.resolve_gateway_approval`.
  POST failure → `SendResult(success=False)`; the runner's typed-/approve
  text fallback is documented as effectively dead under chatlytics (the
  server-side slash floor intercepts `/approve`), so an unresolved
  approval times out to DENY (fail-closed) — logged loudly.
- **`send_clarify` override**: question + numbered choices ("Answer with
  the option number or your own text.") POSTed the same way; the answer
  arrives via owner-DM `/answer` → `tools.clarify_gateway.
  resolve_gateway_clarify(clarify_id, answer)` — this path deliberately
  does NOT `mark_awaiting_text`. POST failure delegates to the BASE
  in-chat numbered-text fallback (which does `mark_awaiting_text`; plain
  text is not slash-intercepted, so that degradation still works). An
  owner DENIAL resolves the clarify with the empty string — the harness's
  own no-response sentinel (`clear_session` contract) — unblocking the
  agent thread immediately without injecting a fake answer.
- **`question_resolved` control handling**: new control action (joins
  `_CONTROL_ACTIONS`) popped against a bounded pending-question registry
  (`request_id → {kind, session_key, clarify_id, future, chat_id,
  created}`; cap 64, FIFO-evict with WARNING, 2 h stale-prune on insert).
  Unknown/expired request_ids warn once then DEBUG. One retry, same
  request_id, on 502 `owner_delivery_failed` (server rolls the row back).
- **`ask_approval(text, chat_id, timeout_s=300)` / `ask_clarify(...)`**
  awaitable adapter primitives for tooling: asyncio-future-backed;
  `ask_approval` returns True ONLY on "approved" (denied/timeout/POST
  failure → False, fail-closed); `ask_clarify` returns the answer string
  on "answered", else None.
- **Per-channel prompt injection**: MESSAGE envelopes (longpoll) and
  webhook payloads may carry an additive `channel_prompt` field (source:
  the server-side bot_module_config "channel-prompts" module); it is set
  on `MessageEvent.channel_prompt` — the harness's native per-turn
  ephemeral system-prompt channel (applied at API call time, never
  persisted to transcript). Absent server value falls back to the local
  config.yaml `channel_prompts` map via the harness's
  `resolve_channel_prompt`. `retry_last` replays stay correct
  automatically (the memoized envelope carries the field).

### Unchanged

- Longpoll caps advertisement stays `caps=control` — `question_resolved`
  rides the existing control cap; the server refuses question POSTs from
  gateways that didn't advertise it.
- /new /stop /retry control handling, progress bubbles, media paths, and
  the webhook/longpoll transports are untouched.

### Tests

- `tests/test_questions.py`: POST body shape + request_id charset, 502
  retry-once (same request_id), failure SendResult, clarify
  numbered-choices text + no `mark_awaiting_text` on the owner-DM path +
  base-fallback delegation, `question_resolved` routing for
  approval/clarify/future kinds, unknown request_id warn path,
  ask_approval/ask_clarify resolution + timeout semantics, registry
  FIFO bound, channel_prompt injection (envelope, absent, config
  fallback), `_LONGPOLL_CAPS` contract guard.

## [4.4.0] - 2026-06-11

Progress-bubble edit-in-place (chatlytics v5.4 P7, gateway half). When an
agent turn outlives a threshold (default 8 s) the adapter sends ONE
"⏳ working…" bubble, then EDITS that bubble into the final reply instead
of stacking a second message. Fast turns never see a bubble — the request
stream is byte-identical to v4.3.0 below the threshold, and setting
`CHATLYTICS_STATUS_EDIT_IN_PLACE=false` disables both the bubble and the
edit entirely.

### Added

- **Config knobs** (env wins over the platform `extra` block, parsed
  defensively — bad values fall back to defaults, never raise at boot):
  - `CHATLYTICS_STATUS_EDIT_IN_PLACE` / `extra.status_edit_in_place` —
    default **true**.
  - `CHATLYTICS_STATUS_BUBBLE_AFTER_S` / `extra.status_bubble_after_s` —
    default 8.0; values <= 0 disable the bubble.
  - `CHATLYTICS_STATUS_BUBBLE_TEXT` / `extra.status_bubble_text` —
    default "⏳ working…".
- **Progress-bubble emission**: anchored on `_keep_typing` (the per-turn
  background task the gateway already runs) — a sibling timer waits the
  threshold on the SAME stop_event and POSTs `/api/v1/send` with
  `progress: true` (server suppresses reaction-feedback correlation for
  it) only if the turn is still running. Exactly ONE bubble per turn;
  bubble failures are DEBUG-only and never affect the turn (same
  philosophy as the typing heartbeat). The returned message id
  (`message_id` / legacy `messageId` / defensive `waha.id._serialized`
  digs) is memoized in a bounded per-chat LRU (cap 100, 10-minute
  staleness TTL).
- **Edit-in-place in `send()`**: a fresh pending bubble id is popped
  (consumed exactly once) and carried as `edit_message_id` so the server
  edits the bubble into the reply (bot-scoped, ownership-enforced
  server-side). The server falls back to a plain send internally on edit
  failure / unknown ownership — a 200 always means the text was
  delivered; `edited` / `edit_fallback` response fields are surfaced at
  DEBUG. Transport-level failure of the edit-tagged request retries ONCE
  as a plain send (never lose the reply over the decoration). Media
  sends do not participate; a pending bubble before a media reply is
  left behind as an acceptable residual.
- **Reserved-key hardening**: `edit_message_id` and `progress` joined
  `_RESERVED_BODY_KEYS` so caller metadata cannot inject them.

### Unchanged

- Flag false → no bubble, no edit, no new request fields (v4.3.0
  behavior). Webhook/longpoll inbound, control envelopes, media paths,
  and the v4.2.0/v4.3.0 surfaces are untouched.

### Tests

- `tests/test_status_edit.py`: fast-turn byte-identical path, slow-turn
  single bubble + edit consume, message-id parsing fallbacks, bubble
  failure isolation, edit-tagged transport retry, flag-off plain
  behavior, reserved-key rejection, LRU bound + staleness.

## [4.3.0] - 2026-06-11

Longpoll control envelopes (chatlytics v5.4 P6, gateway half). WhatsApp
users can drive /new /stop /retry conversation commands: the chatlytics
server intercepts them and emits `kind: "control"` envelopes over the
existing longpoll channel — but ONLY to gateways that advertise support,
so pre-4.3.0 gateways never see shapes they would mis-dispatch as text.

### Added

- **Capability negotiation (`caps=control`)**: every longpoll
  `GET /api/v1/bot/updates` now carries `caps=control` alongside the
  existing `cursor`/`timeout_ms` params. Server records it per-poll; no
  response handshake, ack unchanged. This is the ONLY change to the
  longpoll request shape — the v4.2.0 reconnect/backoff ladder and the
  healthy empty-batch path are untouched.
- **Control envelope handling** (`kind == "control"`, chat key =
  `entity_jid`; rides the same seq space and acks exactly like message
  envelopes):
  - `new_conversation` — resets the chat's hermes conversation via the
    gateway runner's own /new machinery (interrupt in-flight turn, clear
    queued work, evict the cached agent, rotate the session-store entry);
    falls back to `session_store.reset_session(session_key)` when the
    runner is unreachable, then to a logged no-op. The destructive-slash
    confirm gate is deliberately bypassed — the chatlytics server owns the
    user-facing command UX.
  - `stop` — real cancellation via the runner's
    `_interrupt_and_clear_session` (interrupts the live agent turn, drops
    pending/queued work); adapter-local fallback signals the per-session
    interrupt event + drops the pending slot and logs clearly what could
    NOT be cancelled.
  - `retry_last` — re-dispatches the chat's last user message through the
    normal inbound dispatch path. Backed by a new bounded per-chat
    last-message memo (LRU, max 128 chats, last message only) recorded on
    every normal message dispatch. No memo for the chat → log + ignore.
- **Forward-compat ignore**: unknown `kind` values and unknown control
  `action` values are logged (WARNING once per distinct value, DEBUG
  after) and IGNORED — never dispatched as message text to the agent.

### Unchanged

- Message envelopes (absent `kind` or `kind == "message"`) flow through
  the existing dispatch path byte-for-byte (plus the retry memo record).
  Webhook mode, ack/cursor semantics, and the v4.2.0 survivability
  surfaces are untouched.

### Tests

- `tests/test_control.py`: caps param on every poll, unchanged message
  dispatch + ack, control routing for all three actions (gateway runner
  path + fallbacks), retry memo recording/LRU bound/no-memo ignore,
  unknown kind/action ignored (and ack still advances), warn-once dedup.

## [4.2.0] - 2026-06-11

Plugin survivability (P3). Kills the "gateway boots with 2 platforms instead
of 3 and the bot goes silent for days" failure class from four sides: a
no-downgrade guard, unmissable boot logging, a `doctor` self-check CLI, and
longpoll reconnect resilience.

### Added

- **No-downgrade guard** (`diagnostics.check_hermes_agent_version`):
  `register()` compares the installed `hermes-agent` against the plugin floor
  (`>=0.14`, lockstep with the pyproject dep floor) and logs a clear ERROR —
  with the `--no-deps` reinstall fix — when the environment has a DOWNGRADED
  hermes-agent (the v4.1.1 `==0.14.0` pin once dragged production
  0.15.1 → 0.14.0 via a plain pip install). Never blocks an otherwise-working
  load; undeterminable versions never flag.
- **Doctor self-check CLI** (`python -m chatlytics_hermes.doctor`): PASS/FAIL
  per check — plugin dir present, token configured, hermes-agent floor,
  `GET /health` reachable, `GET /api/v1/bot/me` token valid (prints bot name),
  `GET /api/v1/bot/updates` longpoll reachable. Exits non-zero on any FAIL.
  Supports `--base-url` / `--token` / `--plugins-dir` overrides.
- **Boot-time loud failure + confirmation**: every partial load failure
  (missing token, unreachable base_url, health non-200, webhook bind failure,
  `register()` raise, token rejected) now emits ONE ERROR line prefixed
  `CHATLYTICS PLUGIN FAILED TO LOAD:` with the reason and the fix — a stable
  grep target. On success, `register()` logs one INFO confirmation and
  `connect()` logs `chatlytics platform registered, authenticated as <bot>`
  via a best-effort `GET /api/v1/bot/me` identity probe (never fails
  connect(); legacy servers / operator-api_key deployments load unchanged).
- **base_url symptom → fix mapping** (`diagnostics.map_connect_error`), used
  by connect(), the longpoll loop, and doctor: timeout → dead Tailscale-style
  IP (use LAN `http://192.168.1.133:8050` on-prem or
  `https://node.chatlytics.ai`); connection refused → wrong host/port;
  401 → bad/rotated token (rotate via chatlytics admin); 502 → Cloudflare
  tunnel longpoll limit (switch to LAN base_url).

### Changed

- **Longpoll reconnect resilience**: connection refused/reset/timeout, non-200
  responses, AND the chatlytics v5.4 graceful-shutdown signal (empty 200 +
  `Connection: close` on server restart) are now treated as NORMAL retry
  events with a bounded jittered backoff ladder (1s → 2s → 5s → 15s → 30s cap,
  never gives up, reset on success). Logging is state-change-only: one WARNING
  on healthy→degraded (with the actionable `map_connect_error` hint), one INFO
  on recovery — per-attempt records are DEBUG, so a long outage cannot flood
  logs. Healthy path preserved byte-for-byte: a normal empty JSON batch still
  loops immediately without acking; 400 invalid_cursor still resets the cursor.
- `scripts/test.sh` rewritten for the split repo: docker path uses a
  disposable container; the host path no longer runs `pip install` at all
  (pytest `pythonpath=src` makes installs unnecessary), closing the
  plain-install-into-host-venv downgrade vector. README + BETA-INSTALL now
  document the `--no-deps` install rule for existing gateway venvs.

### Tests

- `tests/test_survivability.py` (+30): version-guard comparisons, backoff
  ladder/jitter bounds, error-mapping classifier, state-change logging
  (exactly one WARNING/INFO per transition), empty-200 graceful-shutdown
  handling, healthy-path no-backoff regression, boot identity probe (INFO /
  401-ERROR / 404-silent), register loud-failure, and respx-mocked doctor
  scenarios. Full suite: 169 passed.

## [4.1.5] - 2026-06-07

### Changed

- **Onboarding: `connect()` now tolerates a missing `CHATLYTICS_BOT_TOKEN`**
  (loads degraded + warns) instead of failing gateway boot; data tools return
  a get-a-token prompt (Web UI + CLI routes) on no-token use. Token'd
  deployments unchanged.

## [4.1.4] - 2026-06-06

### Changed

- **Ship as a Hermes user _directory plugin_.** Added a repo-root `__init__.py`
  shim so the repository root is loadable directly via
  `hermes plugins install omernesh/chatlytics-hermes` (clones the repo root into
  `$HERMES_HOME/plugins/chatlytics/`). Hermes imports the root `__init__.py` as
  `hermes_plugins.chatlytics` and calls `register(ctx)`; the shim prepends the
  bundled `src/` to `sys.path` and re-exports `register` + `__version__` from
  `chatlytics_hermes`. Because directory plugins live under
  `$HERMES_HOME/plugins/` (not the venv `site-packages`), the plugin now
  **survives hermes-agent updates** — `setup-hermes.sh`'s `rm -rf venv` no
  longer wipes it. The pip entry-point install is retained for back-compat.
  Pinned `[tool.setuptools]` `packages` / `package-dir` explicitly so the new
  root `__init__.py` does not trip setuptools flat-layout auto-discovery.
  No runtime / behavior change.

## [4.1.3] - 2026-06-06

### Fixed

- **Cron sender (`_standalone_send`) now prefers `CHATLYTICS_BOT_TOKEN`** over
  the legacy `CHATLYTICS_API_KEY`, mirroring `ChatlyticsAdapter`'s `_auth_token`
  precedence (HERMES-V2 / Phase 336). Closes the v4.1.2 MD-01 review finding:
  a token-only cron deployment (`sk_bot_*` set, `api_key` absent) previously
  no-sent silently — the env-config guard tripped on the missing `api_key` even
  with a valid bot token present, and the gateway's `/api/v1/send` rejects an
  empty Bearer. The credential guard + error message now name `BOT_TOKEN` first.
- Added `test_standalone_send_prefers_bot_token_over_api_key` and
  `test_standalone_send_bot_token_only`; isolated existing cron tests from a
  stray `CHATLYTICS_BOT_TOKEN` in the ambient env.

## [4.1.2] - 2026-06-06

Token-only onboarding. The minimal setup is now a single secret —
`CHATLYTICS_BOT_TOKEN`. The gateway URL defaults to
`https://node.chatlytics.ai`, so `CHATLYTICS_BASE_URL` is optional.

### Changed

- **`base_url` now defaults to `https://node.chatlytics.ai`** and is
  optional. Resolution: env `CHATLYTICS_BASE_URL` → config `extra.base_url`
  → DNS default. Applies to the adapter (`ChatlyticsAdapter.base_url`), the
  standalone cron send path (`_standalone_send`), and `ChatlyticsClient`
  (constructor `base_url` now defaults instead of raising on omission — only
  an explicitly-passed empty string still raises).
- **`CHATLYTICS_BASE_URL` removed from `required_env`** (adapter
  `register()`) and moved from `requires_env` to `optional_env` in
  `plugin.yaml`. `CHATLYTICS_BOT_TOKEN` is now the sole required field; the
  one-of auth requirement (BOT_TOKEN or API_KEY) stays enforced at
  `connect()` time.
- **`USER_AGENT`** bumped to `chatlytics-hermes/4.1.2`.

### Fixed

- **Dependency-pin downgrade landmine (critical).** `hermes-agent` pin
  widened from `>=0.14,<0.15` to `>=0.14,<1.0`. The old `<0.15` upper bound
  EXCLUDED the live 0.15.x host, so `pip install`-ing the adapter silently
  **downgraded** Hermes. The adapter imports and runs on 0.15.x; the
  `<0.15`-vs-`<1.0` regression is now guarded by a test in
  `tests/test_register.py`.

## [4.1.1] - 2026-05-29

### Fixed

- **Longpoll `transport error ()` after chatlytics restart.** The longpoll
  GET inherited the client-level `DEFAULT_TIMEOUT_SECONDS=30.0`, leaving only
  ~5s of margin over the server's 25s long-poll hold (`timeout_ms=25000`).
  Every empty poll that the server held the full 25s then occasionally tripped
  an httpx `ReadTimeout` — which stringifies to `()`, producing the
  `longpoll GET transport error (); backing off` log spam and stalling inbound
  pickup. `_poll_loop` now passes an explicit per-request
  `httpx.Timeout(connect=10, read=40, write=10, pool=10)` (read = hold + 15s)
  to the GET, and `ChatlyticsClient.get()`/`.post()` gained an optional
  `timeout` param (defaulting to `httpx.USE_CLIENT_DEFAULT`, so all other
  callers are unchanged). The hold is now a named constant
  `_LONGPOLL_TIMEOUT_MS` shared by the `timeout_ms` query param and the read
  window. The ack POST stays on the 30s default (fast call).

## [4.1.0] - 2026-05-29

v4.1 adds a **longpoll inbound consumer** so the plugin can PULL inbound
messages from chatlytics v4.0 instead of receiving webhook POSTs. This
restores inbound for deployments behind NAT / without a reachable webhook
URL (the bot's `webhook_url` is set to `null`; chatlytics queues envelopes
per-bot and the plugin drains them via long-poll).

### Added

- **Longpoll inbound consumer** (`CHATLYTICS_INBOUND_MODE=longpoll`).
  `ChatlyticsAdapter._poll_loop()` long-polls
  `GET /api/v1/bot/updates?cursor=<opaque>&timeout_ms=25000` and acks
  processed batches via `POST /api/v1/bot/updates/ack {cursor}`. The GET
  does NOT advance the per-bot read pointer — the consumer acks AFTER
  processing every non-empty batch, so an in-flight crash re-delivers
  unprocessed envelopes. Error discipline: exponential backoff (1s→30s)
  on transport/non-200, cursor reset on `400 invalid_cursor`, 30s backoff
  on `401 bot_token_required`; the loop never dies silently and exits
  cleanly on `disconnect()`.
- **`CHATLYTICS_INBOUND_MODE`** env var / `extra.inbound_mode` config key:
  `webhook` (default — existing aiohttp PUSH server, unchanged) or
  `longpoll` (v4.1 PULL transport). In `longpoll` mode `connect()` skips
  the webhook-server startup and spawns the poll task; `disconnect()`
  cancels it.
- **`ChatlyticsAdapter._dispatch_envelope()`** — translates an
  `InboundEnvelope` (`{bot_token, session_id, chat_type, entity_jid,
  sender_jid, text, dispatch, ts}`) into a webhook-shaped body, threads
  the WAHA session via `register_chat_session(entity_jid, session_id)`,
  and dispatches through the existing `normalize_payload` →
  `handle_message` path. `chat_type: "newsletter"` maps to Hermes
  `"channel"`.
- **4 new tests** (`tests/test_longpoll.py`) covering GET-with-cursor,
  envelope→MessageEvent translation + `handle_message` dispatch, ack with
  the returned cursor, `register_chat_session` population, the
  newsletter→channel mapping, `400 invalid_cursor` reset+recovery, and the
  no-ack-on-empty-batch contract.

### Changed

- **Carried-forward Hermes-compat fixes** from hpg6's running tree so the
  plugin loads under the installed Hermes and tool dispatch works:
  - Entry point is the **bare module** `chatlytics = "chatlytics_hermes"`
    (NOT `chatlytics_hermes:register`) — the colon form made Hermes'
    `PluginManager` fail to import the plugin.
  - `_make_tool_handler._bound(args=None, **kwargs)` now merges the
    positional `args` dict that `tools.registry.dispatch` passes as
    `handler(args, **kwargs)` (fixes `TypeError: takes 0 positional
    arguments but 1 was given`).
- **Carried-forward P-19 session threading** (also from hpg6): `/api/v1/send`
  now includes the WAHA `session` (resolved per-chat from inbound, falling
  back to `CHATLYTICS_SESSION` / `extra.session`); without it chatlytics
  returns `400 "chatId and session are required"`. Added
  `register_chat_session` / `_resolve_session_for_chat`, the inbound webhook
  now records the inbound `session`, and `send()` fails loudly with an
  operator-actionable error when no session can be resolved. `session` is
  now a reserved send-body key.

## [4.0.0] - 2026-05-28

v4.0 plugin release — aligns with chatlytics v4.0 **Multi-Bot Platform**.
Plugin now accepts per-bot bearer tokens (`CHATLYTICS_BOT_TOKEN`) as the
preferred auth mechanism. Legacy `CHATLYTICS_API_KEY` continues to work
as a fallback for one minor cycle so existing deployments migrate without
flag-day pressure.

### Added

- **`CHATLYTICS_BOT_TOKEN`** env var + `extra.bot_token` PlatformConfig
  field as the preferred auth credential (`sk_bot_<43-char-base64url>`
  per-bot bearer issued by `chatlytics bots create`). Resolution
  precedence (highest first): env `CHATLYTICS_BOT_TOKEN` →
  `extra.bot_token` → env `CHATLYTICS_API_KEY` (legacy) → `extra.api_key`.
- **`ChatlyticsAdapter.is_bot_token`** property — boolean predicate that
  reports `True` when the resolved auth token is a bot bearer (either
  because `bot_token` was the explicit source, or the resolved token
  carries the canonical `sk_bot_` prefix).
- **Fingerprint logging on `connect()`** — the adapter logs which
  auth-identity branch (`bot (fp=<8-char-sha256>)` vs
  `operator (legacy api_key, fp=...)`) the gateway connected with.
  Token plaintext NEVER appears in logs (INV-02 token-discipline
  parity with the chatlytics-side `tokenFingerprint` helper).
- **5 new tests** (`tests/test_bot_token.py`) covering the precedence
  matrix, env-vs-extra override semantics, the `api_key` back-compat
  fallback path with a Bearer-header assertion, and the
  `ChatlyticsConnectError` path when neither token is set.

### Changed

- **`USER_AGENT`** in `client.py` bumped from `chatlytics-hermes/2.0.0`
  to `chatlytics-hermes/4.0.0`. (Had been stuck at the v2.0 release
  string through v3.0.x — caught + corrected in this release.)
- **`plugin.yaml`** lists `CHATLYTICS_BOT_TOKEN` in `requires_env`;
  `CHATLYTICS_API_KEY` moved to `optional_env` with a `DEPRECATED in
  plugin v4.0` description prefix.
- **`ChatlyticsAdapter.connect()`** raises `ChatlyticsConnectError`
  with a clear message naming both env vars when no token is
  resolvable. The check happens at connect time (not __init__)
  so partial env loads during gateway registration don't crash
  adapter instantiation.
- **`tests/test_register.py`** version-pin assertions updated to
  `4.0.0` and required-env set updated to the new shape.

### Preserved

- **125/125 tests passing** (120 pre-existing + 5 new bot_token tests).
- **21 Hermes tools** — tool surface unchanged.
- **All v3.0.0 BREAKING changes** (HERMES-13 `get_chat_info` shape,
  HERMES-14 strict JID regex, HERMES-15 unified `send_*(resource=...)`)
  unchanged.
- **HI-01 default-deny upload allowlist** (`CHATLYTICS_UPLOAD_ALLOWED_ROOTS`)
  unchanged.
- **`BasePlatformAdapter` contract coverage** unchanged.

### Migration from 3.x

Existing `CHATLYTICS_API_KEY`-only deployments continue to work without
change — the plugin transparently falls back. To migrate to the
preferred bot-token path:

1. Provision a chatlytics v4.0 bot: `chatlytics bots create --display-name "<bot>"`.
2. Capture the `sk_bot_...` token (returned plaintext exactly ONCE on
   create — see chatlytics CLAUDE.md INV-02).
3. Set `CHATLYTICS_BOT_TOKEN=sk_bot_...` in the Hermes profile's `.env`
   (or `extra.bot_token` in the profile's `config.yaml`).
4. Restart the Hermes gateway. On connect, the adapter logs
   `"chatlytics adapter authenticated as bot (fp=<8char>)"` confirming
   the switch.
5. Once verified, remove `CHATLYTICS_API_KEY` from the profile's env.

The `CHATLYTICS_API_KEY` fallback is retained for one minor cycle
(through plugin v4.1) and slated for removal in plugin v5.0.

## [3.0.1] - 2026-05-18

Cosmetic release — no functional changes, no test changes, no API surface
changes. `pip install chatlytics-hermes==3.0.0` and `==3.0.1` are
behaviourally identical. The bump exists solely so the marketing-flair
description and README tagline land on the PyPI registry page (PyPI bakes
the description into the published artifact and refuses re-uploads of the
same version).

### Changed

- **Package description** (`pyproject.toml`) sharpened to
  "Production-grade WhatsApp for Hermes Agent — full upstream contract,
  21 tools, every media type, via the Chatlytics gateway"
- **README opening** rewritten with a bold superlative tagline, shields.io
  badges (PyPI version, Python compat, license), capability sweep, and
  "Why chatlytics-hermes?" section with bold-led bullets. Inspired by the
  positioning style of the deprecated `waha-openclaw-channel` npm package.

### Preserved (all from 3.0.0)

- 120/120 tests passing (unchanged)
- 21 Hermes tools (unchanged)
- `BasePlatformAdapter` contract coverage (unchanged)
- All Phase 13-18 breaking changes from 3.0.0 (unchanged)

## [3.0.0] - 2026-05-18 (BREAKING)

First public PyPI release of `chatlytics-hermes`. Closes every deferred
breaking-change item from the v2.1 backlog. Two breaking changes affect
the tool surface (`chatlytics_get_chat_info` return shape, strict JID
regex on `chatId` schemas); one breaking change affects the library API
only (adapter `send_*_file` methods collapsed into unified `send_*`).
Tool-surface count unchanged at 21 tools. v2.1 → 3.0 callers using only
the MCP tool surface need only the two tool-shape migrations; library
callers using `adapter.send_*_file` must additionally migrate to the
unified `send_*(resource=...)` form. See `### Breaking` subsections
below for migration guidance.

### Breaking

- **`get_chat_info` return shape** (HERMES-13): `ChatlyticsAdapter.get_chat_info`
  now returns `dict | None` (chat-found or legitimate-empty) and raises
  `ChatlyticsLookupError(code, message)` on transport/auth/server/validation
  errors. The v2.1 bare-`{}` return on errors is gone. Tool-layer callers
  use the new `chatlytics_get_chat_info` wrapper which returns
  `{success: bool, ...}` with error responses additionally including
  `_error: "<machine_code>"`. Codes: `transport_error`, `auth_error`,
  `server_error`, `validation_error`, `unknown_error`. **404 from the
  gateway for an unknown chatId is `validation_error`** (NOT a legitimate
  empty). Closes v2.1 deferred item 1.

- **Strict JID regex on `chatId` schemas** (HERMES-14): all 15
  chatId-bearing tool schemas in `chatlytics_hermes.tools` now enforce
  the WhatsApp JID format `<id>@<suffix>` where suffix is one of
  `c.us` (1:1), `g.us` (groups), `lid` (NOWEB linked-id), or
  `newsletter` (channels). The v2.1 permissive accept-set (which let
  bare phones and display names pass through) is gone. The regex
  matches the sibling JS bundle's canonical `looksLikeJid`:
  `/@(c\.us|g\.us|lid|newsletter)$/i`. Callers must pre-resolve
  names/phones to a JID via `chatlytics_search` before invoking any
  chatId-bearing tool. **`messageId` validation is unchanged** -- the
  JS canonical bundle does not regex-validate messageIds; the Python
  plugin matches. Closes v2.1 deferred item 2.

- **Library API: adapter `send_*` unified resource shape** (HERMES-15):
  `ChatlyticsAdapter.send_image_file` is **removed**. The unified
  `adapter.send_image(chat_id, resource: str | Path | bytes, ...)`
  auto-detects which branch to use:
  - `http://` / `https://` string -> `mediaUrl` passthrough (no upload)
  - `Path` object -> local file upload (allowlist-enforced)
  - `str` whose path exists -> local file upload (parity with `Path`)
  - `bytes` / `bytearray` -> raw upload
  - anything else -> `SendResult(success=False, error="Invalid resource: ...")`

  The other media methods (`send_animation`, `send_voice`, `send_video`,
  `send_document`) have their second positional parameter **renamed to
  `resource`** and their type hint broadened to
  `Union[str, Path, bytes, bytearray]`. **Tool surface unchanged** --
  `chatlytics_send_image` and the other four media tools keep their
  schemas and external behavior; the `_file` companion at the adapter
  layer is the only break. The `CHATLYTICS_UPLOAD_ALLOWED_ROOTS`
  default-deny allowlist (HI-01) is preserved on every file branch.

  Direct access to `adapter.send_image_file` raises a clear
  `AttributeError` pointing at `send_image` -- the base class's
  text-fallback default is explicitly blocked via `__getattribute__`
  to prevent silent degradation of v2.x photo sends to text bubbles.

  **Migration:** replace `adapter.send_image_file(chat_id, path, ...)`
  with `adapter.send_image(chat_id, Path(path), ...)` (or just
  `adapter.send_image(chat_id, path, ...)` -- the auto-detector
  handles existing string paths). Keyword-arg callers using
  `image_url=...` / `audio_path=...` / `video_path=...` /
  `animation_url=...` / `file_path=...` must switch to `resource=...`.
  Direct callers of the removed `send_image_file` symbol see an
  `AttributeError` on upgrade by design; this is a clean break with
  no deprecation wrapper per the operator's lifted-lock preference.
  Closes v2.1 deferred item 3.

### Added

- **`scripts/smoke.sh --cached` flag** (HERMES-16): caches the
  `hermes-agent` wheel between smoke runs via `pip download` to
  `.smoke-cache/` + `pip install --no-index --find-links=.smoke-cache/`.
  Falls back to network if cache miss or corruption. Non-breaking,
  opt-in via the new `--cached` flag (default behavior unchanged).
  Cache invalidates automatically when the pinned `hermes-agent` tag
  changes. Closes v2.1 deferred item 4.
- **`.planning/HERMES-API-AUDIT.md`** (HERMES-17): inventory of every
  `hermes.*` import in `chatlytics-hermes` and the 0.14 module/version
  it came from, plus the likely breaking surface for a future 0.15.
  Pure documentation; no code changes. Closes v2.1 deferred item 5
  (downgraded scope -- hermes 0.15 doesn't exist yet).

### Internal

- **Cosmetics sweep** (HERMES-18): closed six explicitly-deferred
  LOW/INFO nits from the v2.1 audit -- `_send_typing_once` docstring
  + `metadata` kwarg signature parity, `_RESERVED_BODY_KEYS` module
  constant lift-out, deliberate-redundancy comments above
  `minLength: 1` in `chatId`/`messageId` schemas, audit-doc whitespace
  + wording cleanup. **Zero behavior change** -- the 21-tool surface,
  the response shapes, and the request-layer semantics are identical
  to v2.1.x. Test count exactly preserved at 120/120.

### Migration from 2.x

If you call the MCP tool surface only:

1. **`chatlytics_get_chat_info`** -- callers checking
   `result.get("success") is False` to detect "chat not found" must
   now check `result.get("chat") is None` (success path). Error
   detection: `result.get("success") is False and result.get("_error")`
   surfaces the machine-readable code (`transport_error`, `auth_error`,
   `server_error`, `validation_error`, `unknown_error`).
2. **`chatId` validation** -- bare phone numbers and display names
   are now rejected at the schema layer. Resolve to a JID via
   `chatlytics_search` first, then pass the `@c.us`/`@g.us`/`@lid`/
   `@newsletter` JID to chatId-bearing tools.

If you call the Python adapter directly (library users):

3. **`adapter.send_image_file` / `send_animation_file` /
   `send_video_file` / `send_file_file`** -- removed. Use
   `adapter.send_image(chat_id, resource=...)` etc. The `resource`
   argument auto-detects URL vs local-path. `Path` objects,
   existing path strings, `bytes`, and `bytearray` are all accepted.
   The `CHATLYTICS_UPLOAD_ALLOWED_ROOTS` default-deny allowlist
   from v2.1 is preserved.

## [2.1.0] -- 2026-05-17

Tech-debt resolution + critical safety fixes carried over from the v2.0
milestone-wide reviews. **Additive, not breaking.** v2.1.0 is a drop-in
upgrade from v2.0.0 -- the 21-tool surface, the `BasePlatformAdapter`
contract, the `register(ctx)` entry point, and every public API signature
stay identical. Internal `_keep_typing` shape changed to match the
upstream base coroutine signature; in-plugin callers transparently use
the new `_typing_scope` async-cm helper, so no external migration is
required.

### Security

- **BL-01 (BLOCKER) fixed.** `_keep_typing` was an `@asynccontextmanager`
  in v2.0, but the upstream Hermes base calls it as
  `asyncio.create_task(self._keep_typing(chat_id, metadata=..., stop_event=...))`
  -- which would have crashed on the first production inbound message
  with a `TypeError` (async-cm return value is not awaitable; chatlytics
  also didn't accept the `metadata` kwarg). The fix rewrites the method
  as a plain coroutine matching the base signature
  `(self, chat_id, interval=30.0, metadata=None, stop_event=None)` and
  extracts a new `_typing_scope` async-cm helper for the in-plugin tool
  handler ergonomics. Hidden in v2.0 because `tests/test_inbound.py`
  replaced `handle_message` with a recorder and never exercised the
  base path.
- **HI-01 (HIGH) fixed.** Tool surface exposed an arbitrary local-file
  read primitive: the 5 media tools accepted `filePath` with zero
  validation, so a prompt-injected `chatlytics_send_file(filePath="/etc/passwd")`
  would have exfiltrated arbitrary host files to Chatlytics. v2.1.0
  introduces a new env-configured allowlist
  `CHATLYTICS_UPLOAD_ALLOWED_ROOTS` (OS-pathsep-separated absolute
  paths). When unset, local-file uploads are default-deny; every
  `filePath` value must resolve under a configured allowed root or the
  tool returns `{"success": False, "error": "..."}` without opening or
  uploading the file. URL-based uploads via `mediaUrl` are unaffected.
- **HI-03 (HIGH) fixed.** Two of six media overrides
  (`send_image`, `send_animation`) dropped `**kwargs` in v2.0, making
  the plugin brittle to upstream `BasePlatformAdapter` signature
  evolution (subsequent base-class kwargs would have been silently
  unsupported). v2.1.0 brings all six media overrides to a consistent
  shape with `**kwargs: Any` swallowed for forward-compat.

### Added

- `CHATLYTICS_UPLOAD_ALLOWED_ROOTS` env var -- OS-pathsep-separated
  absolute paths under which media tools may read local files (default
  deny when unset). See README "Security: filePath upload allowlist".
- `scripts/smoke.sh --fast` flag -- host-venv pytest only, no docker.
  ~10-20s vs ~60-90s for the full dockerized smoke. Opt-in; default
  behavior unchanged for release-gate use.
- `pip install --retries 3` on the dockerized smoke step -- transient
  GitHub outages no longer look like plugin bugs.
- `tests/test_live_loader.py` -- gateway-loader integration smoke
  asserting `register(ctx)` runs against a real `PluginContext`-shaped
  registry and all 21 tools land. Closes the test-harness gap that hid
  BL-01.
- `tests/test_concurrency.py` -- regression guard for the v2.0
  `_resolve_media_url` `asyncio.to_thread` fix; verifies concurrent
  media-tool calls don't serialize on file I/O.
- `tests/test_observability.py` -- caplog-based assertions on log
  levels, dropped-metadata WARNINGs, and api-key/Bearer-token absence
  from log records.
- `webhook_path` validation in `ChatlyticsAdapter.__init__` -- rejects
  empty, missing leading slash, contains `?` / `#`, or collides with
  the reserved `/health` route. Raises `ValueError` at construction
  (fail-fast, matches Hermes conventions).
- Conftest session-autouse `platform_registry` fixture is now
  teardown-clean: snapshots the registry at session start, restores at
  session end. Two pytest runs in succession produce identical results.
- `tests/_fixtures.py::FakePlatformConfig` -- single source of truth
  for the test fake; previously duplicated across 7 test files.

### Changed

- `send_typing` transport-error log level: WARNING -> DEBUG (log
  hygiene; users were seeing routine gateway flakiness at WARN). The
  WARNING level is reserved for truly unexpected states.
- `chatlytics_login` semantics: when the upstream API returns
  `{"success": True, "webhook_registered": False}`, the tool now
  returns `{"success": False, "error": "webhook_registered=false"}`
  -- aligns with the Chatlytics Claude Code MCP bundle's behavior so
  agents on either surface see consistent results.
- Success-shape coercion is now a single canonical helper used by
  `_make_send_result`, `_standalone_send`, and `tools._ok` (MD-01
  cross-phase consistency dedup). Identical observable behavior across
  all three call sites.

### Fixed

- Silent `ctx.get_platform("chatlytics")` failures inside
  `_make_tool_handler` now emit a DEBUG log so operators can diagnose
  toolset misconfiguration without attaching a debugger.
- `send()` reserved-name metadata keys (e.g. caller passing `chatId`
  or `text` in `**extras`) now emit a WARNING per dropped key instead
  of silently discarding.
- `plugin.yaml` `optional_env` descriptions no longer leak internal
  phase identifiers (`(HERMES-03)`, `(HERMES-04)` stripped). Closes
  PR-review **MED-04** -- end users in `hermes config` UI now see
  feature-oriented descriptions instead of milestone metadata.
- Conftest cross-test pollution: re-running the suite twice in a row
  no longer leaves a dirty platform registry between runs.

### Docs

- README has a `## What's new in v2.1` section near the top calling
  out the security fixes and the upgrade recommendation.
- README "Tool catalog" clarifies the `chatlytics_actions` (GET
  gateway action catalog) vs `chatlytics_dispatch` (POST generic
  action invocation) semantic split (closes 05-MED-01 docs).
- README has a `## Known issues` section documenting that
  `filename` for URL-path documents may or may not be honored by the
  Chatlytics gateway (closes 04-LOW-02 docs; tracks upstream).

### Test infra

- Conftest teardown contract added (closes 02-MED-02).
- `_FakePlatformConfig` consolidated into `tests/_fixtures.py`
  (closes PR-review cross-cutting fixture-duplication nit).
- 88 tests total (was 65 in v2.0): +12 live-loader, +5 path-traversal
  negatives, +3 concurrency, +7 observability, -4 retired duplicates.

### Internal

- Log hygiene sweep across `adapter.py`, `client.py`, `tools.py`,
  `inbound.py` -- no api_key or full phone numbers surface in any log
  record (verified by `tests/test_observability.py::test_no_api_key_in_any_log_record`).
- Documented loader contract findings in
  `src/chatlytics_hermes/__init__.py` docstring (Phase 7).
- `_typing_scope` async-cm extracted so in-plugin tool handlers keep
  `async with self._typing_scope(chat_id):` ergonomics while the
  base-callable `_keep_typing` matches the upstream coroutine
  contract.

**Recommended for all users.** v2.0.0 has known BLOCKER + HIGH
security issues fixed in this release.

## 2.0.0 (2026-05-17) -- BREAKING

Full rebuild of `chatlytics-hermes` as a first-class Hermes Agent plugin
against `hermes-agent==0.14` (tag `v2026.5.16`). v1.x was a standalone
duck-typed shim that never published to PyPI; v2.0 is a clean break with
no migration burden (no users to migrate).

### Removed

- `ChatlyticsAdapter` standalone class (duck-typed shim flavor).
- Flask-based inbound webhook server / Flask background thread.
- All v1.x duck-typed surface (custom `connect`/`disconnect` signatures,
  `start_webhook_server`, `on_message` decorator).
- `flask` runtime dependency.
- Phase-169 "vendor into hpg6 Hermes monorepo" guidance from the README
  -- v2.0 IS the in-Hermes-plugin pattern.

### Added

- `BasePlatformAdapter` subclass (`src/chatlytics_hermes/adapter.py`) that
  fits the canonical Hermes v0.14 plugin contract.
- `register(ctx)` entry point exposed at
  `[project.entry-points."hermes_agent.plugins"]`
  (`chatlytics = "chatlytics_hermes:register"`) for auto-discovery.
- aiohttp inbound webhook server started inside `connect()` and stopped
  inside `disconnect()` -- same event loop as outbound sends.
- HMAC-SHA256 `X-Chatlytics-Signature` verification on inbound webhooks
  (env: `CHATLYTICS_WEBHOOK_SECRET`).
- Six media handlers: `send_image`, `send_voice`, `send_video`,
  `send_document`, `send_animation`, `send_image_file`.
- `_keep_typing` 30 s async-contextmanager heartbeat for long-running
  tool handlers; fires immediately on enter and re-fires every 30 s.
- Cron-delivery hook: `standalone_sender_fn` +
  `cron_deliver_env_var="CHATLYTICS_HOME_CHANNEL"` for unattended
  notification delivery from Hermes cron jobs.
- **21 Hermes tools** registered via `ctx.register_tool(...)` under the
  `chatlytics` toolset:
  - 10 messaging (`send`, `reply`, `react`, `edit`, `unsend`, `pin`,
    `unpin`, `read`, `delete`, `poll`)
  - 5 media (`send_image`, `send_voice`, `send_video`, `send_file`,
    `send_animation`)
  - 3 directory/search (`directory`, `search`, `actions`)
  - 3 sessions/health (`health`, `login`, `dispatch`)
- Every tool schema is JSON Schema Draft 2020-12 + validated under
  `jsonschema.Draft202012Validator` at test time.
- `aiohttp>=3.9,<4` and `jsonschema>=4,<5` runtime dependencies.
- `plugin.yaml` manifest with `requires_env` / `optional_env` blocks for
  the Hermes config UI.
- `scripts/smoke.sh` -- dockerized clean-room verification script.
- 45 tests (5 register + 8 outbound + 9 inbound + 8 media + 3 cron +
  11 tools + 1 concurrency regression).

### Changed

- Minimum `hermes-agent` is now `>=0.14,<0.15` (was `>=0.11`).
- `httpx>=0.27,<1` (was unpinned).
- Package version bumped to `2.0.0`.
- Local-file branch of `_resolve_media_url` now reads via
  `asyncio.to_thread` (fixes 04-REVIEW MED-02 / surfaced in 05-REVIEW
  MED-02) so concurrent media-tool invocations no longer stall the
  event loop on multi-MB file reads.

### Migration

**None.** v1.x was never published to PyPI; there are no installed users
to migrate. Anyone on a pre-2.0 git install should rebuild against the
v2.0 plugin contract from scratch.

## 1.1.0 (2026-04-27)

- Verified compatible with `hermes-agent==0.11.0` (tag `v2026.4.23`).
- Removed bogus `[project.entry-points."hermes.adapters"]` block from
  `pyproject.toml` — Hermes's plugin entry-point group is `hermes_agent.plugins`
  and is meant for tools/hooks/commands, not platform adapters; platform
  adapters are hardcoded in `gateway/run.py:_create_adapter()` per
  `gateway/platforms/ADDING_A_PLATFORM.md`.
- Stripped the `from hermes_agent import Agent / Agent(platform=adapter)`
  README snippet — that API does not exist in upstream Hermes. README now
  documents the standalone-shim use case and points at phase 169 for the
  in-monorepo vendor pattern.
- Stashed unmerged WIP test files (`tests/test_actions.py`,
  `tests/test_in_reply_to.py`, `tests/test_inbound.py`) into
  `.wip-stash/` — they imported symbols that never landed
  (`LAST_INBOUND_MAX_ENTRIES`, `MESSAGE_DEDUP_MAX_ENTRIES`,
  `_default_session`, `webhook_secret` ctor param, `send_image`,
  `send_voice`) and broke `pytest` collection. Baseline test count back to
  the 12 that actually run against the shipped adapter.

## 1.0.0 (2026-04-20)

- Initial release
- Hermes Agent platform adapter for Chatlytics WhatsApp gateway
- Async HTTP client (httpx) for outbound messaging
- Flask webhook server for inbound messages
- Send messages, typing indicators, and chat info queries
- Bearer token authentication
