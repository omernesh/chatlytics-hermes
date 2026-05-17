# Phase 2: HERMES-02 — Outbound text + control parity - Context

**Gathered:** 2026-05-17
**Status:** Ready for planning
**Mode:** Smart discuss — infrastructure-leaning phase, minimal context

<domain>
## Phase Boundary

Implement `connect()`, `disconnect()`, `send()`, `send_typing()`, `get_chat_info()` against the Chatlytics REST API via `httpx.AsyncClient`. Establish the `SendResult` return contract. No media (HERMES-04), no inbound aiohttp server (HERMES-03), no tool registration (HERMES-05).

**Phase ID:** HERMES-02 (depends on HERMES-01)

</domain>

<decisions>
## Implementation Decisions

### Locked from ROADMAP HERMES-02 spec (do not relitigate)
- HTTP client: `httpx.AsyncClient` (async — matches Hermes runtime)
- Lifecycle: open client in `connect()`, close in `disconnect()`
- Health check: `connect()` issues `GET /health`; raise on non-200
- Endpoints:
  - `POST /api/v1/send` — body `{chatId, text, accountId?, replyTo?, ...}`
  - `POST /api/v1/typing` — body `{chatId, duration}`
  - `GET /api/v1/chat?chatId={id}` — returns `{name, phone, isGroup, ...}`
- Auth: `Authorization: Bearer {api_key}` header on every request
- Timeout: 30s (matches Chatlytics gateway's own timeout)
- Return contract: `SendResult.ok=True/False` + raw response in `meta`
- Config surface: `base_url`, `api_key`, `account_id?` — from `__init__` kwargs or `register(ctx)` config block (resolve from `PlatformConfig.extra` or env vars per Hermes v0.14 convention)

### Locked from PROJECT.md (invariants — every phase preserves)
- All HTTP through Chatlytics REST. No direct WAHA.
- All HTTP uses `httpx` async.
- Tool return shape: `{"success": bool, ...}` (relevant later in HERMES-05; outbound just returns `SendResult`).
- Hermes pin `>=0.14,<0.15` (canonical adapter pattern: see `/tmp/hermes-ref-v0.14.0/plugins/platforms/irc/adapter.py` and `simplex/adapter.py` for outbound shapes).

### Out of scope (LOCKED)
- Media handlers (HERMES-04)
- aiohttp inbound server (HERMES-03)
- Tool registration via `ctx.register_tool()` (HERMES-05)
- Retry / circuit breaker — Chatlytics gateway handles upstream retry

### Claude's Discretion
- File layout for the httpx wrapper: ROADMAP says `src/chatlytics_hermes/client.py` (thin wrapper with auth + timeout). Use that.
- Exception → `SendResult.ok=False` mapping: catch `httpx.HTTPStatusError`, `httpx.RequestError`, generic exceptions; put the response body (or exception repr) in `meta["error"]`.
- Logging: `logger = logging.getLogger("chatlytics_hermes.client")`; INFO on connect/disconnect, DEBUG on per-request bodies (redact api_key + phone numbers per Hermes convention).

### Forward action items from HERMES-01 review (address opportunistically)
- MED-01: `adapter_factory` lambda style — consider `adapter_factory=ChatlyticsAdapter` direct reference if the IRC pattern allows. Re-check `irc/adapter.py:927` register() shape.
- MED-02: `PYTHONPATH=src` bare-import workaround — superseded once `pip install -e .` is part of the normal dev loop.
- LOW items: see `01-REVIEW.md` § LOW — cleanup-grade.

</decisions>

<code_context>
## Existing Code Insights

- `src/chatlytics_hermes/adapter.py` (from HERMES-01): `ChatlyticsAdapter(BasePlatformAdapter)` skeleton with abstract methods raising `NotImplementedError`. This phase fills in: `connect`, `disconnect`, `send`, `send_typing`, `get_chat_info`.
- `src/chatlytics_hermes/__init__.py`: re-exports `register`. No changes expected.
- `plugin.yaml`: env declarations include `CHATLYTICS_BASE_URL`, `CHATLYTICS_API_KEY`, `CHATLYTICS_ACCOUNT_ID`. Adapter `__init__` reads from `PlatformConfig.extra` / env per Hermes v0.14 pattern.
- Reference outbound implementations: `/tmp/hermes-ref-v0.14.0/plugins/platforms/{irc,teams,google_chat,simplex}/adapter.py` — study connect/send/send_typing shapes.

</code_context>

<specifics>
## Specific Ideas

- Test fixture pattern: use `respx` for httpx mocking (consistent with prior v1.x `tests/test_adapter.py` — already in optional-dep `test` group from HERMES-01 pyproject).
- All 8 acceptance criteria from ROADMAP HERMES-02 are unit-test driven. No live Chatlytics calls. Mock responses for each endpoint.
- `Authorization: Bearer` header assertion in every mock (criterion #8) — use a respx-side hook or assert in test.

</specifics>

<deferred>
## Deferred Ideas

- HMAC signature verification (HERMES-03 inbound side)
- aiohttp embedded server (HERMES-03)
- Media handlers (HERMES-04)
- `_keep_typing` 30s heartbeat (HERMES-04)
- `standalone_sender_fn` + `cron_deliver_env_var` (HERMES-04)
- Tool surface (HERMES-05)

</deferred>
