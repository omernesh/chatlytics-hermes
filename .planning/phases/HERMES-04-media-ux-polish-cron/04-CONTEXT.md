# Phase 4: HERMES-04 — Media + UX polish + cron - Context

**Gathered:** 2026-05-17
**Status:** Ready for planning
**Mode:** Smart discuss — infrastructure-leaning, minimal context

<domain>
## Phase Boundary

Implement all 6 `BasePlatformAdapter` media-send variants — `send_image`, `send_voice`, `send_video`, `send_document`, `send_animation`, `send_image_file` — wired to Chatlytics media endpoints. Add `_keep_typing()` 30s heartbeat (WhatsApp 24h-window protection). Wire `cron_deliver_env_var="CHATLYTICS_HOME_CHANNEL"` + `standalone_sender_fn` for scheduled out-of-process deliveries.

**Phase ID:** HERMES-04 (depends on HERMES-03)

</domain>

<decisions>
## Implementation Decisions

### Locked from ROADMAP HERMES-04 spec
- 6 media handlers — each POSTs to Chatlytics media endpoint (`/api/v1/send-media` or `/api/v1/actions` with `action: "send_image" | "send_voice" | ...`)
- Method signatures (match `BasePlatformAdapter` overrides):
  - `send_image(chat_id, url_or_bytes, caption=None) -> SendResult` — URL → `mediaUrl`; bytes → upload to Chatlytics file endpoint first, then send
  - `send_voice(chat_id, url_or_bytes) -> SendResult` — voice bubble (`mediaType: "voice"`, NOT `"audio"`)
  - `send_video(chat_id, url_or_bytes, caption=None) -> SendResult`
  - `send_document(chat_id, url_or_bytes, filename=None, caption=None) -> SendResult`
  - `send_animation(chat_id, url_or_bytes, caption=None) -> SendResult` — gif/mp4
  - `send_image_file(chat_id, file_path, caption=None) -> SendResult` — read local path, upload bytes
- `_keep_typing(chat_id, interval=30.0)` — async coroutine, re-issues `send_typing(chat_id, duration=30.0)` every `interval` seconds; cancelable via async context manager (`async with adapter._keep_typing(chat_id): ...`); used by long-running tool handlers
- Cron support:
  - `register(ctx)` reads `os.environ["CHATLYTICS_HOME_CHANNEL"]` (default channel for scheduled deliveries)
  - `cron_deliver_env_var="CHATLYTICS_HOME_CHANNEL"` in `ctx.register_platform()` call
  - `standalone_sender_fn` is a top-level coroutine that Hermes can call without instantiating the full plugin (matches upstream cron pattern — see `/tmp/hermes-ref-v0.14.0/plugins/platforms/irc/adapter.py` `_standalone_send` for canonical shape)

### Locked from PROJECT.md
- Outbound goes through Chatlytics REST. No direct WAHA.
- All HTTP through `httpx` async.
- Tool return shape (later, HERMES-05): `{"success": bool, ...}`. SendResult is the canonical return here.

### Out of scope (LOCKED)
- Tool surface (HERMES-05)
- README/CHANGELOG (HERMES-06)

### Claude's Discretion
- Endpoint choice between `/api/v1/send-media` vs `/api/v1/actions` — pick based on what already exists in v1.x `src/chatlytics_adapter/` git history (since deleted) OR query `/tmp/hermes-ref-v0.14.0/` examples. Most consistent: `/api/v1/send-media` for media-specific endpoint, `/api/v1/actions` for the generic action dispatcher (used in HERMES-05). Use `/api/v1/send-media` here.
- File upload endpoint for bytes/local-path variants: `POST /api/v1/upload` (returns `{url: "..."}`) — then reference the returned URL in the send call. Confirm exact endpoint from upstream Chatlytics docs or v1.x history during execute.
- `_keep_typing` context manager pattern: use `contextlib.asynccontextmanager`. Yield, run heartbeat task in background, cancel on exit. Tear down cleanly even if the body raises.
- `_standalone_send` shape: `async def _standalone_send(text: str, **kwargs) -> dict` — open a fresh httpx client, POST to `/api/v1/send` with channel from `CHATLYTICS_HOME_CHANNEL`, close client. Returns `{"success": bool, ...}` per the cron contract.

### Address HERMES-02 + HERMES-03 forward action items
- MED-01 from `02-REVIEW.md` (`register()` missing `check_fn`) — HERMES-04 touches the `register()` block (adding `cron_deliver_env_var` + `standalone_sender_fn`). Add `check_fn=lambda: True` (or a real check that the dep is importable) as part of this commit.
- Other LOW/MED items — defer to HERMES-05 or HERMES-06 unless trivially fixable.

</decisions>

<code_context>
## Existing Code Insights

- `src/chatlytics_hermes/client.py` — httpx wrapper from HERMES-02. ADD `send_media(...)` helper here. Reuse for media + file upload.
- `src/chatlytics_hermes/adapter.py` — ADD 6 media methods + `_keep_typing` + `cron_deliver_env_var` + `standalone_sender_fn` parameters in `register()`. Preserve all HERMES-02/03 code.
- `src/chatlytics_hermes/inbound.py` — no changes.
- Reference cron pattern: `/tmp/hermes-ref-v0.14.0/plugins/platforms/irc/adapter.py` `_env_enablement` + `_standalone_send` + `cron_deliver_env_var="IRC_HOME_CHANNEL"`.

</code_context>

<specifics>
## Specific Ideas

- All 8 acceptance criteria in ROADMAP are unit-test driven via `respx`-mocked Chatlytics endpoints. No live calls.
- `test_keep_typing_heartbeats_every_30s` — use `asyncio.sleep` patching or `freezegun` or short-interval override (`_keep_typing(chat_id, interval=0.05)` in test) to avoid 30s real-time waits. Override interval is cleanest.
- `test_cron_deliver_env_var_routes_to_standalone_sender` — set `CHATLYTICS_HOME_CHANNEL` via monkeypatch, call `_standalone_send("text")`, assert POST to `/api/v1/send` with the channel as `chatId`.

</specifics>

<deferred>
## Deferred Ideas

- Tool surface (`chatlytics_send`, `chatlytics_react`, etc.) — HERMES-05
- README/CHANGELOG rewrite — HERMES-06
- Live integration against real Chatlytics gateway — out of milestone

</deferred>
