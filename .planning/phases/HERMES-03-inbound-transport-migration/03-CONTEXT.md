# Phase 3: HERMES-03 — Inbound transport migration - Context

**Gathered:** 2026-05-17
**Status:** Ready for planning
**Mode:** Smart discuss — infrastructure-leaning, minimal context

<domain>
## Phase Boundary

Replace the v1.x Flask-in-a-thread inbound with an **aiohttp** `web.Application` started **inside** `connect()` and stopped in `disconnect()`. Normalize webhook JSON → Hermes `MessageEvent` via `MessageType.{TEXT,IMAGE,AUDIO,VIDEO,DOCUMENT,STICKER}`, then dispatch via `await self.handle_message(event)`. Optional HMAC verification on `X-Chatlytics-Signature`. No outbound media yet (HERMES-04), no tool surface yet (HERMES-05).

**Phase ID:** HERMES-03 (depends on HERMES-02)

</domain>

<decisions>
## Implementation Decisions

### Locked from ROADMAP HERMES-03 spec
- Inbound transport: aiohttp `web.Application`, NOT Flask. NOT a separate thread. Inside `connect()`.
- Bound to `(host, port)` from config (`PlatformConfig.extra` keys `webhook_host`, `webhook_port`; defaults `0.0.0.0:9090` per v1.x convention)
- Routes:
  - `POST {webhook_path}` (default `/webhook`) — normalize + dispatch
  - `GET /health` — returns 200 (used by Chatlytics for webhook delivery confirmation)
- Webhook payload normalization in `src/chatlytics_hermes/inbound.py`:
  - `mediaType` field → `MessageType.IMAGE/AUDIO/VIDEO/DOCUMENT/STICKER`
  - Default to `MessageType.TEXT` when no media
  - Extract `chatId`, `text`, `senderId`, `timestamp`, `messageId`, optional `replyTo`
  - Construct `MessageEvent` per Hermes v0.14 contract
- Dispatch: `await self.handle_message(event)` (canonical inbound entry per `BasePlatformAdapter`)
- Optional HMAC: if `webhook_secret` is configured, verify `X-Chatlytics-Signature` against HMAC-SHA256 of body; mismatch → 401, no dispatch
- Cleanup: `disconnect()` shuts down the aiohttp app cleanly (await `app_runner.cleanup()`), no socket/thread leaks

### Locked from PROJECT.md invariants
- Inbound transport lives **inside** `connect()` — no separate threads (v1.x Flask thread leaked on plugin reload)
- aiohttp shares the Hermes event loop
- All HTTP through `httpx` (outbound); aiohttp ONLY for the embedded inbound server

### Out of scope (LOCKED)
- Outbound media handlers (HERMES-04)
- Tool surface (HERMES-05)
- Outbound HMAC signing — Chatlytics handles that upstream

### Claude's Discretion
- HMAC implementation: use stdlib `hmac` + `hashlib.sha256`. Compare via `hmac.compare_digest` (constant-time).
- aiohttp lifecycle: `AppRunner` + `TCPSite` pattern (canonical for embedded servers — see Hermes v0.14 line/teams adapters for aiohttp patterns if present).
- Webhook path config key name: `webhook_path` (default `/webhook`).
- `MessageEvent` construction: study `base.py::MessageEvent` dataclass and the IRC/teams adapters' inbound paths for the canonical field set.

### Address HERMES-02 forward action items
- MED-01 from `02-REVIEW.md` (`register()` omits `check_fn`) — defer to HERMES-05 (when full plugin spec is consolidated) unless this phase touches the register block (it shouldn't).
- LOW items from 02-REVIEW.md: apply if zero-risk in first commit; defer otherwise.

</decisions>

<code_context>
## Existing Code Insights

- `src/chatlytics_hermes/adapter.py` — `ChatlyticsAdapter.connect()` (from HERMES-02) currently opens the `httpx.AsyncClient` and issues `GET /health`. HERMES-03 ADDS aiohttp server startup AFTER the health check; `disconnect()` ALSO shuts the aiohttp app down before closing the httpx client.
- `src/chatlytics_hermes/client.py` — outbound httpx wrapper from HERMES-02. No changes needed (this phase is inbound only).
- `src/chatlytics_hermes/__init__.py` — re-exports `register`. No change.
- Reference inbound patterns: `/tmp/hermes-ref-v0.14.0/gateway/platforms/api_server.py` (the gateway's own API server pattern), `plugins/platforms/teams/adapter.py` (Teams uses inbound webhooks), `plugins/platforms/google_chat/adapter.py` (Google Chat similar). Look for `aiohttp.web` usage, `AppRunner`, `TCPSite`.
- `tests/conftest.py` (from HERMES-02) — seeds platform registry; reuse for inbound tests.

</code_context>

<specifics>
## Specific Ideas

- Test fixtures: use `aiohttp.test_utils.TestClient` or `aiohttp.test_utils.unused_port()` for ephemeral ports in tests. `aiohttp_client` pytest fixture from `pytest-aiohttp` if added (add to optional-dep `test` group if needed).
- The 8 acceptance criteria test:
  1. text payload → MessageEvent(TEXT)
  2. image payload → MessageEvent(IMAGE)
  3. audio payload → MessageEvent(AUDIO)
  4. GET /health → 200
  5. connect() starts server (port listening)
  6. disconnect() stops server (port no longer listening)
  7. HMAC bad signature → 401, no dispatch
  8. HMAC good signature → dispatches normally
- The "fake gateway's handle_message recorder" mentioned in AC-1/2/3 means: patch `adapter.handle_message` with an `AsyncMock` (or a list-recorder) and assert what it received after the POST.

</specifics>

<deferred>
## Deferred Ideas

- Outbound media handlers (HERMES-04)
- `_keep_typing` heartbeat (HERMES-04)
- `standalone_sender_fn` + cron env var (HERMES-04)
- Tool surface (HERMES-05)
- README rewrite (HERMES-06)

</deferred>
