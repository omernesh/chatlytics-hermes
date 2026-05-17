# ROADMAP — chatlytics-hermes

<details>
<summary>v2.0 — Hermes plugin v2.0 (upstream-contract rebuild) — SHIPPED 2026-05-17</summary>

## v2.0 — Hermes plugin v2.0 (upstream-contract rebuild) (COMPLETE)

**Shipped:** 2026-05-17. All 6 HERMES phases delivered. 45/45 tests green in dockerized `python:3.13-slim` + `hermes-agent@v2026.5.16`. 21 tools registered. `v2.0.0` annotated tag created locally (operator push pending). NO PyPI publish (operator lock). Archive: `.planning/milestones/v2.0-ROADMAP.md`. Audit: `.planning/v2.0-MILESTONE-AUDIT.md`.


Replace the v1.x standalone-shim API with a proper Hermes plugin against
`hermes-agent>=0.14,<0.15`. Six phases, designed for end-to-end execution via
`/gsd-autonomous --from HERMES-01 --to HERMES-06`.

### Phase 1: Upstream contract scaffolding
**Goal:** Bare `BasePlatformAdapter` subclass + `plugin.yaml` + `register(ctx)` entry point + pinned `hermes-agent>=0.14,<0.15` dependency. No outbound or inbound logic yet — purely the structural contract that lets Hermes load the plugin and call `register()`.

**Depends on:** Nothing (entry phase)

**In scope:**
- `src/chatlytics_hermes/__init__.py` — exports `register()` symbol
- `src/chatlytics_hermes/adapter.py` — `ChatlyticsAdapter(BasePlatformAdapter)` skeleton with platform name `chatlytics`; all abstract methods present but raising `NotImplementedError` (HERMES-02/03/04 fill them in)
- `plugin.yaml` — minimal Hermes plugin manifest (name, version, entry point, supported Hermes version range)
- `pyproject.toml` — replace v1.x deps (httpx, flask) with `hermes-agent>=0.14,<0.15`, `httpx>=0.27,<1`, `aiohttp>=3.9,<4`; add `[project.entry-points."hermes_agent.plugins"]` block pointing at `chatlytics_hermes:register`; bump `version = "2.0.0"`
- Drop the entire v1.x `src/chatlytics_adapter/` tree (operator decision: no compat shim)
- Drop v1.x `tests/test_adapter.py` + `tests/test_action_parity.py` (will be replaced phase-by-phase)

**Out of scope:**
- Outbound HTTP (HERMES-02)
- Inbound transport (HERMES-03)
- Media handlers (HERMES-04)
- Tool registration (HERMES-05)

**Files (create/modify):**
- CREATE `src/chatlytics_hermes/__init__.py`
- CREATE `src/chatlytics_hermes/adapter.py`
- CREATE `plugin.yaml`
- CREATE `tests/test_register.py`
- MODIFY `pyproject.toml`
- DELETE `src/chatlytics_adapter/` (whole tree)
- DELETE `tests/test_adapter.py`, `tests/test_action_parity.py`

**Acceptance criteria (all must pass autonomously):**
1. `python -c "from chatlytics_hermes import register; print(register.__name__)"` prints `register` (importable)
2. `tests/test_register.py::test_register_adds_chatlytics_platform` — calling `register(MockCtx())` registers a platform under name `chatlytics` on the mock context (no errors)
3. `python -c "import yaml; yaml.safe_load(open('plugin.yaml'))"` succeeds (valid YAML manifest)
4. `pip install -e .` in a clean venv with `hermes-agent==0.14.0` already installed succeeds without uninstalling Hermes
5. `pyproject.toml` declares `[project.entry-points."hermes_agent.plugins"]` with `chatlytics = "chatlytics_hermes:register"`

---

### Phase 2: Outbound text + control parity
**Goal:** Implement `connect()`, `disconnect()`, `send()`, `send_typing()`, `get_chat_info()` against the Chatlytics REST API via `httpx.AsyncClient`. Establish `SendResult` return contract. No media yet (HERMES-04), no inbound yet (HERMES-03).

**Depends on:** HERMES-01

**In scope:**
- `httpx.AsyncClient` lifecycle: open in `connect()`, close in `disconnect()`
- `connect()` calls Chatlytics `GET /health`; raises if not 200
- `send(chat_id, text, **extras)` → `POST /api/v1/send` with `{chatId, text, accountId?, replyTo?, ...}`; returns `SendResult.ok=True/False` + raw response in `meta`
- `send_typing(chat_id, duration=3.0)` → `POST /api/v1/typing` with `{chatId, duration}`
- `get_chat_info(chat_id)` → `GET /api/v1/chat?chatId={chat_id}`; returns dict with `name`, `phone`, `isGroup`, etc.
- Config surface: `base_url`, `api_key`, `account_id?` (optional default session); read from `__init__` kwargs or `register(ctx)` config block
- Auth header: `Authorization: Bearer {api_key}` on every request
- Timeout: 30s on every request (matches Chatlytics gateway's own timeout)

**Out of scope:**
- Media (`send_image`, `send_voice`, etc.) — HERMES-04
- Inbound aiohttp server — HERMES-03
- Tool registration via `ctx.register_tool()` — HERMES-05
- Retry / circuit breaker — Chatlytics gateway handles upstream retry

**Files (create/modify):**
- MODIFY `src/chatlytics_hermes/adapter.py` (fill in 5 methods + `__init__` config)
- CREATE `src/chatlytics_hermes/client.py` (thin httpx wrapper with auth + timeout)
- CREATE `tests/test_outbound.py`

**Acceptance criteria (all must pass autonomously):**
1. `tests/test_outbound.py::test_connect_succeeds_on_200_health` — mocked `GET /health` returns 200 → `await adapter.connect()` does not raise
2. `tests/test_outbound.py::test_connect_raises_on_non_200_health` — mocked `GET /health` returns 503 → `await adapter.connect()` raises
3. `tests/test_outbound.py::test_send_returns_ok_true_on_200` — mocked `POST /api/v1/send` returns 200 + `{success: true, messageId: "..."}` → `result.ok is True`
4. `tests/test_outbound.py::test_send_returns_ok_false_on_400` — mocked `POST /api/v1/send` returns 400 → `result.ok is False` and error reason in `result.meta`
5. `tests/test_outbound.py::test_send_typing_calls_typing_endpoint` — `await adapter.send_typing(chat_id, duration=2.0)` issues `POST /api/v1/typing` with the right body
6. `tests/test_outbound.py::test_get_chat_info_returns_dict` — mocked `GET /api/v1/chat?chatId=...` returns `{name, phone, isGroup}` → adapter returns that dict
7. `tests/test_outbound.py::test_disconnect_closes_client` — `await adapter.disconnect()` closes the underlying `httpx.AsyncClient` (no resource warning)
8. All outbound HTTP requests carry `Authorization: Bearer {api_key}` header (asserted in mocks)

---

### Phase 3: Inbound transport migration
**Goal:** Replace v1.x Flask-in-a-thread inbound with an aiohttp server started **inside** `connect()` and stopped in `disconnect()`. Normalize webhook JSON → Hermes `MessageEvent` via `MessageType.{TEXT,IMAGE,AUDIO,VIDEO,DOCUMENT,STICKER}`, then dispatch via `await self.handle_message(event)`.

**Depends on:** HERMES-02

**In scope:**
- aiohttp `web.Application` started inside `connect()` (after the health check), bound to `(host, port)` from config; stopped cleanly in `disconnect()`
- Single inbound route: `POST /webhook` (configurable path)
- Webhook payload normalization in `src/chatlytics_hermes/inbound.py`:
  - Detect `mediaType` field → map to `MessageType.IMAGE/AUDIO/VIDEO/DOCUMENT/STICKER`
  - Default to `MessageType.TEXT` when no media
  - Extract `chatId`, `text`, `senderId`, `timestamp`, `messageId`, optional `replyTo`
  - Construct `MessageEvent` with all required Hermes fields
- Dispatch: `await self.handle_message(event)` (canonical Hermes inbound entry)
- Optional HMAC verification: if `webhook_secret` is configured, verify `X-Chatlytics-Signature` header against HMAC-SHA256 of the body; reject mismatch with 401
- Health route: `GET /health` returns 200 — used by Chatlytics for webhook delivery confirmation

**Out of scope:**
- Outbound media handlers — HERMES-04
- Tool surface — HERMES-05
- Outbound HMAC signing — Chatlytics handles that upstream

**Files (create/modify):**
- MODIFY `src/chatlytics_hermes/adapter.py` (start/stop aiohttp server in connect/disconnect)
- CREATE `src/chatlytics_hermes/inbound.py` (payload normalizer + aiohttp request handler)
- CREATE `tests/test_inbound.py`

**Acceptance criteria (all must pass autonomously):**
1. `tests/test_inbound.py::test_webhook_text_payload_dispatches_text_message_event` — POST text payload to embedded server → fake gateway's `handle_message` recorder receives `MessageEvent(type=MessageType.TEXT, text=..., chat_id=...)`
2. `tests/test_inbound.py::test_webhook_image_payload_dispatches_image_event` — POST `{mediaType: "image", mediaUrl: ...}` → recorder gets `MessageEvent(type=MessageType.IMAGE, ...)`
3. `tests/test_inbound.py::test_webhook_audio_payload_dispatches_audio_event` — POST audio payload → recorder gets `MessageType.AUDIO`
4. `tests/test_inbound.py::test_webhook_health_returns_200` — `GET /health` on the embedded server returns 200
5. `tests/test_inbound.py::test_connect_starts_aiohttp_server` — after `await adapter.connect()`, the server is listening on the configured port
6. `tests/test_inbound.py::test_disconnect_stops_aiohttp_server` — after `await adapter.disconnect()`, the port is no longer listening (no thread/socket leak)
7. `tests/test_inbound.py::test_hmac_verification_rejects_bad_signature` — when `webhook_secret` is set, mismatched `X-Chatlytics-Signature` returns 401 and does NOT dispatch
8. `tests/test_inbound.py::test_hmac_verification_accepts_good_signature` — matching HMAC dispatches normally

---

### Phase 4: Media + UX polish + cron
**Goal:** Implement all 6 `BasePlatformAdapter` media-send variants — `send_image`, `send_voice`, `send_video`, `send_document`, `send_animation`, `send_image_file` — wired to Chatlytics media endpoints. Add `_keep_typing()` 30s heartbeat (WhatsApp 24h window protection). Wire `cron_deliver_env_var="CHATLYTICS_HOME_CHANNEL"` + `standalone_sender_fn` for scheduled deliveries.

**Depends on:** HERMES-03

**In scope:**
- 6 media handlers, each calling `POST /api/v1/send-media` (or `/api/v1/actions` with `action: "send_image" | "send_voice" | "send_video" | "send_file" | ...`) with the right `mediaType` / `mediaUrl` / `caption` / `file` payload shape
- `send_image(chat_id, url_or_bytes, caption=None)` — URL path → `mediaUrl`; bytes path → upload to Chatlytics's file endpoint first, then send
- `send_voice(chat_id, url_or_bytes)` — voice bubble (NOT regular audio)
- `send_video(chat_id, url_or_bytes, caption=None)`
- `send_document(chat_id, url_or_bytes, filename=None, caption=None)`
- `send_animation(chat_id, url_or_bytes, caption=None)` — gif/mp4
- `send_image_file(chat_id, file_path, caption=None)` — read local path, upload bytes
- `_keep_typing(chat_id, interval=30.0)` — async coroutine that re-issues `send_typing(chat_id, duration=30.0)` every `interval` seconds; cancelable via context manager; used by long-running tool handlers
- Cron support: `register(ctx)` reads `os.environ["CHATLYTICS_HOME_CHANNEL"]` (default channel for scheduled deliveries); `standalone_sender_fn` is a top-level coroutine that Hermes can call without instantiating the full plugin (matches upstream cron pattern)

**Out of scope:**
- Tool surface (HERMES-05)
- Release / README (HERMES-06)

**Files (create/modify):**
- MODIFY `src/chatlytics_hermes/adapter.py` (6 media methods + `_keep_typing` + `cron_deliver_env_var` + `standalone_sender_fn`)
- MODIFY `src/chatlytics_hermes/client.py` (add `send_media(...)` helper)
- CREATE `tests/test_media.py`
- CREATE `tests/test_cron.py`

**Acceptance criteria (all must pass autonomously):**
1. `tests/test_media.py::test_send_image_url_path` — `send_image(chat_id, "https://...")` → `POST /api/v1/send-media` with `mediaType: "image"`, `mediaUrl: "https://..."`, optional `caption`
2. `tests/test_media.py::test_send_voice_yields_voice_message` — payload has `mediaType: "voice"` (NOT `"audio"`)
3. `tests/test_media.py::test_send_video` — `mediaType: "video"` + `caption`
4. `tests/test_media.py::test_send_document_with_filename` — `mediaType: "file"` + `filename`
5. `tests/test_media.py::test_send_animation` — `mediaType: "video"` or `"gif"` per Chatlytics convention + animation hint
6. `tests/test_media.py::test_send_image_file_uploads_local_bytes` — local-path variant reads bytes and uploads via Chatlytics file endpoint, then references the returned URL in the send call
7. `tests/test_media.py::test_keep_typing_heartbeats_every_30s` — `async with adapter._keep_typing(chat_id):` issues a typing request immediately and again ~30s later; context-manager exit cancels cleanly
8. `tests/test_cron.py::test_cron_deliver_env_var_routes_to_standalone_sender` — when `CHATLYTICS_HOME_CHANNEL` is set, `standalone_sender_fn(text)` posts to that channel via `POST /api/v1/send`

---

### Phase 5: Full Chatlytics tool surface
**Goal:** Expose EVERY Chatlytics action as a Hermes tool via `ctx.register_tool()`. Source the canonical tool list from the Claude Code plugin's MCP server bundle (`servers/chatlytics-mcp.bundle.js` in `omernesh/chatlytics-claude-code`) plus any additional actions enumerable via Chatlytics's `POST /api/v1/actions` schema. Tools must validate inputs via JSON schemas and return `{"success": true, ...}` shape.

**Depends on:** HERMES-04

**In scope:**
- `src/chatlytics_hermes/tools.py` — one async handler per Chatlytics action:
  - Messaging: `chatlytics_send`, `chatlytics_reply`, `chatlytics_react`, `chatlytics_edit`, `chatlytics_unsend`, `chatlytics_pin`, `chatlytics_unpin`, `chatlytics_read`, `chatlytics_delete`, `chatlytics_poll`
  - Media (also direct adapter methods from HERMES-04, but exposed as tools too): `chatlytics_send_image`, `chatlytics_send_voice`, `chatlytics_send_video`, `chatlytics_send_file`, `chatlytics_send_animation`
  - Directory: `chatlytics_directory`, `chatlytics_search`, `chatlytics_actions` (generic action dispatcher)
  - Sessions / health: `chatlytics_health`, `chatlytics_login`, `chatlytics_dispatch`
  - Plus any additional surface from `POST /api/v1/actions` enumeration at plan time
- JSON schema per tool: `chatId`, `text`, `messageId`, `emoji`, etc. with `required` correctly set
- Each handler: validates args, calls Chatlytics REST, returns `{"success": bool, ...response, "error": str?}`
- All tools registered in `register(ctx)` via `ctx.register_tool(name, handler, schema)`

**Out of scope:**
- Release / smoke (HERMES-06)
- Tool result rendering (Hermes UI concern)

**Files (create/modify):**
- CREATE `src/chatlytics_hermes/tools.py`
- MODIFY `src/chatlytics_hermes/__init__.py` (register tools alongside platform)
- MODIFY `src/chatlytics_hermes/adapter.py` (expose `client` attribute for tool handlers to share auth/timeout)
- CREATE `tests/test_tools.py`
- CREATE `tests/test_tool_schemas.py`

**Acceptance criteria (all must pass autonomously):**
1. `tests/test_tool_schemas.py::test_every_tool_has_valid_json_schema` — every registered tool's schema validates via `jsonschema.Draft202012Validator`
2. `tests/test_tool_schemas.py::test_every_tool_has_required_chat_id_field_when_applicable` — messaging tools require `chatId`; directory/search tools require `query` or similar
3. `tests/test_tools.py::test_chatlytics_send_calls_send_endpoint` — handler invokes `POST /api/v1/send` and returns `{"success": True, "messageId": ...}` on 200
4. `tests/test_tools.py::test_chatlytics_react_calls_react_action` — handler invokes `POST /api/v1/actions` with `{action: "react", messageId, emoji}` and returns `{"success": True, ...}` on 200
5. `tests/test_tools.py::test_chatlytics_search_returns_results_list` — `POST /api/v1/actions` with `{action: "search", query}` → tool returns `{"success": True, results: [...]}`
6. `tests/test_tools.py::test_tool_returns_success_false_on_400` — mocked 400 → tool returns `{"success": False, "error": "..."}`
7. `tests/test_tools.py::test_tool_count_matches_claude_code_plugin_baseline` — registered tool count is at least the 8 documented in `omernesh/chatlytics-claude-code` MCP bundle PLUS the media variants from HERMES-04 (asserted against a known baseline list checked into the test)
8. `tests/test_tool_schemas.py::test_all_tools_namespace_chatlytics_` — every tool name starts with `chatlytics_` (avoid collisions with other Hermes plugins)

---

### Phase 6: Release + smoke test
**Goal:** Rewrite README.md from the v1.x standalone-shim perspective to the v2.0 first-class-plugin perspective. CHANGELOG entry `2.0.0 (BREAKING)`. Smoke test against real `hermes-agent==0.14.0` in a clean venv. Tag `v2.0.0`. **NO PyPI publish.**

**Depends on:** HERMES-05

**In scope:**
- README.md rewrite:
  - Drop all v1.x `ChatlyticsAdapter()` constructor snippets
  - Drop "standalone shim" / "duck-typed" language
  - Add v2.0 install: `pip install -e git+https://github.com/omernesh/chatlytics-hermes.git` (or local clone)
  - Add `register(ctx)` usage block
  - Document config: `base_url`, `api_key`, `account_id`, `webhook_port`, `webhook_secret`, `CHATLYTICS_HOME_CHANNEL` env var
  - Tool catalog summary (link to schemas in code, do not duplicate every signature)
  - Hermes version compatibility note: `hermes-agent>=0.14,<0.15`
- CHANGELOG.md entry:
  - `## 2.0.0 (2026-MM-DD) — BREAKING`
  - Bullet list of breaking changes: removed `ChatlyticsAdapter` standalone class; entry point now `chatlytics_hermes:register`; minimum `hermes-agent` is 0.14
  - Migration guide: none (v1.x never published, no users to migrate)
- pyproject.toml: confirm `version = "2.0.0"`, entry point present, deps clean
- Smoke test script `scripts/smoke.sh`:
  - Create fresh venv
  - `pip install hermes-agent==0.14.0`
  - `pip install -e .[dev]`
  - `hermes plugins ls` — assert output contains `chatlytics`
  - `pytest tests/` — assert all tests pass
- Git tag `v2.0.0`, push to origin

**Out of scope:**
- PyPI publish (`python -m build && twine upload`) — explicit operator decision, NOT executed in this phase
- Marketplace listing / external announcement
- Live integration against a real Chatlytics gateway — autonomous ceiling

**Files (create/modify):**
- REWRITE `README.md`
- MODIFY `CHANGELOG.md` (prepend 2.0.0 entry)
- CREATE `scripts/smoke.sh`
- VERIFY `pyproject.toml` is clean
- Git tag `v2.0.0` + push (NO PyPI command)

**Acceptance criteria (all must pass autonomously):**
1. `bash scripts/smoke.sh` exits 0 in a clean container/venv
2. `hermes plugins ls` output contains the string `chatlytics`
3. `pytest tests/` reports 0 failures across all test files from HERMES-01 to HERMES-05
4. README.md contains zero occurrences of `ChatlyticsAdapter(` (no leftover v1.x constructor snippets)
5. CHANGELOG.md has a `## 2.0.0` entry at the top with `BREAKING` marker
6. `pyproject.toml` has `version = "2.0.0"` and the entry point `chatlytics = "chatlytics_hermes:register"`
7. `git tag --list v2.0.0` returns a tag
8. NO `python -m build` or `twine upload` runs anywhere in the phase artifacts (operator decision lock)

---

## Recommended /gsd-autonomous wave sequence (v2.0 — historical)

All 6 phases are strictly sequential (each depends on the previous). No parallelization possible — the contract built in HERMES-01 is the foundation for HERMES-02, which is required by HERMES-03, etc.

```
/gsd-autonomous --from HERMES-01 --to HERMES-06
```

This runs discuss → plan → execute → review → commit for each phase in sequence, then halts at the milestone boundary.

</details>

---

## v2.1 — Critical safety fixes + tech debt resolution + live-loader integration

**RE-PRIORITIZED 2026-05-17** after the milestone-wide GSD code review (`.planning/v2.0-MILESTONE-CODE-REVIEW.md`) surfaced **1 BLOCKER (BL-01) + 2 HIGH (HI-01, HI-03)** that the per-phase reviews missed:

- **BL-01:** `_keep_typing` async-cm override will crash the inbound dispatch path on the FIRST production inbound message (`adapter.py:741-806` — upstream base calls `asyncio.create_task(self._keep_typing(chat_id, metadata=...))`; chatlytics doesn't accept `metadata` AND the asynccontextmanager returns a non-coroutine). Hidden because `tests/test_inbound.py` replaces `handle_message` with a recorder and the smoke script never starts a live gateway.
- **HI-01:** Tool surface exposes an arbitrary local file read primitive (`tools.py:611-705` media tools accept `filePath` with zero validation → `chatlytics_send_file(filePath="/etc/passwd")` exfiltrates files to Chatlytics).
- **HI-03:** Two of six media overrides drop `**kwargs` (`send_image`, `send_animation` in `adapter.py`) → brittle to upstream signature evolution.

**→ DO NOT push `v2.0.0` publicly until v2.1 lands.** The local v2.0.0 tag is fine as a checkpoint; pushing it ships a known-broken-on-first-inbound plugin. Ship as `v2.1.0` instead.

Close the BLOCKER + 2 HIGHs FIRST (HERMES-07 surfaces the BLOCKER via live-loader test; HERMES-08 fixes BL-01 + HI-01 + HI-03), then close every remaining MED/LOW from the v2.0 audit and the two milestone-wide reviews. Six phases total, additive/non-breaking from the public API perspective (BL-01 fix changes internal `_keep_typing` shape but the convenience `_typing_scope` async-cm preserves the in-plugin call sites). Ships as `v2.1.0`. NO PyPI publish (operator lock preserved). Designed for `/gsd-autonomous --from 7 --to 12`.

### Phase 7: Live-loader integration smoke (surfaces BL-01)
**Goal:** Wire `hermes.gateway.bootstrap.load_plugins()` (or whatever the v0.14 loader entry point is — confirm against `/tmp/hermes-ref-v0.14.0/hermes_cli/plugins.py`) against a respx-mocked Chatlytics backend and prove the chatlytics plugin loads, `register(ctx)` is called, and all 21 tools land on the in-memory `PluginContext` registry. CRITICALLY: include a test that exercises the BASE `handle_message` pipeline (NOT the recorder-replacement pattern from `test_inbound.py:98-106` that hid BL-01) so the BLOCKER is reproduced and locked under test. Closes 06-MED-01 + GSD-review **MD-04** (test harness bypass).

**Depends on:** v2.0 milestone (shipped, local-only)

**In scope:**
- New test file `tests/test_live_loader.py` — spin up a real `PluginContext` (or the closest reachable substitute via `hermes_cli/plugins.py:613`), invoke the gateway plugin loader, assert `chatlytics` platform is registered AND all 21 tools are registered on the same context
- respx-mocked Chatlytics backend for any in-loader HTTP probes (avoid live calls)
- **Critical regression test:** `test_base_handle_message_invokes_keep_typing` — instantiate the adapter, install an `AsyncMock` `_message_handler` (so the base path runs but returns immediately), feed it a `MessageEvent`, assert NO `TypeError` from `_keep_typing(chat_id, metadata=...)`. This test MUST FAIL on the current v2.0 code (reproducing BL-01) and pass after HERMES-08 fixes it.
- Direct `_keep_typing` test: `asyncio.create_task(adapter._keep_typing(chat_id, metadata={}, stop_event=asyncio.Event()))` — asserts the method IS a coroutine, accepts both kwargs, and respects `stop_event.set()`. Currently fails (asynccontextmanager doesn't return a coroutine).
- Add a `--live-loader` step to `scripts/smoke.sh` (or a separate `scripts/smoke-live-loader.sh`) so CI/release verification includes it
- Document the loader contract findings in `src/chatlytics_hermes/__init__.py` docstring

**Out of scope:**
- Live Chatlytics gateway calls (still operator-lock-blocked)
- The actual BL-01 / HI-01 / HI-03 fixes (those land in HERMES-08 — this phase is about REPRODUCING them under test first)
- New tools

**Files (create/modify):**
- CREATE `tests/test_live_loader.py`
- MODIFY `scripts/smoke.sh` (add live-loader step)
- MODIFY `src/chatlytics_hermes/__init__.py` (docstring update only)

**Acceptance criteria:**
1. `pytest tests/test_live_loader.py::test_loader_registers_chatlytics_platform -q` passes — loader sees and calls `register(ctx)`
2. `tests/test_live_loader.py::test_loader_registers_21_tools` — after load, the in-memory tool registry contains exactly the 21 expected tool names
3. `tests/test_live_loader.py::test_loader_handles_missing_env_vars_gracefully` — when required env vars are unset, the loader produces a clear error (not a crash) and does NOT half-register
4. `tests/test_live_loader.py::test_loader_isolated_from_real_chatlytics` — respx pass-through OFF for chatlytics endpoints; any live call attempt raises
5. `tests/test_live_loader.py::test_base_handle_message_invokes_keep_typing` is checked-in (xfail-marked in HERMES-07; un-xfailed in HERMES-08 after BL-01 fix)
6. `tests/test_live_loader.py::test_keep_typing_is_a_coroutine` is checked-in with same xfail pattern (matches the BL-01 acceptance test from the GSD review)
7. `bash scripts/smoke.sh` exit 0 with the new live-loader step included; output mentions "live-loader: chatlytics platform + 21 tools registered"

---

### Phase 8: Critical safety fixes (BL-01 BLOCKER + HI-01 HIGH + HI-03 HIGH) + async lifecycle hardening
**Goal:** Fix every BLOCKER and HIGH finding from the GSD milestone-wide review (`.planning/v2.0-MILESTONE-CODE-REVIEW.md`) so the plugin is safe to push publicly. Also closes the original v2.0-audit MED items co-located in the same code (`_keep_typing` lifecycle / 04-MED-01, 04-LOW-03, 06-LOW-02) plus concurrency regression coverage for the v2.0 `_resolve_media_url` `asyncio.to_thread` fix.

**Depends on:** Phase 7 (BL-01/HI-01 regression tests checked in xfail; this phase un-xfails them after fix)

**In scope — CRITICAL FIXES (lead these commits, before the lifecycle polish):**

1. **BL-01 fix** (`src/chatlytics_hermes/adapter.py:741-806`): Rewrite `_keep_typing` as a plain coroutine matching the upstream base signature `(self, chat_id, interval=30.0, metadata=None, stop_event=None)`. Move the existing `@asynccontextmanager` flavor to `_typing_scope` (preserves the in-plugin tool-handler ergonomics). Reference implementation in `.planning/v2.0-MILESTONE-CODE-REVIEW.md` BL-01 Option A.

2. **HI-01 fix** (`src/chatlytics_hermes/tools.py:611-705` + `adapter.py:434-501` `_resolve_media_url`): Add an env-configured path allowlist for `filePath` uploads. New env var `CHATLYTICS_UPLOAD_ALLOWED_ROOTS` (defaults to `/tmp` on POSIX, `C:\Users\Public\Documents` on Windows — or empty meaning local-file uploads disabled). `_resolve_media_url` rejects any local path not under an allowed root with `PermissionError`. The 5 media tools return `{"success": False, "error": "..."}` instead of raising.

3. **HI-03 fix** (`src/chatlytics_hermes/adapter.py`): Add `**kwargs: Any` to `send_image` (lines 605-624) and `send_animation` (lines 626-642) signatures. Document them as "swallowed for forward-compat with upstream base evolution." Bring all 6 media overrides to a consistent shape.

4. **MD-01 fix (cross-phase consistency)** (`adapter.py:_make_send_result`, `_standalone_send`, `tools.py:_ok`): Consolidate the three subtly-different success-shape coercions into one canonical helper. Pick the cleanest predicate from `tools._ok` and apply it everywhere. Tests verify identical behavior across all three call sites.

**In scope — original async-lifecycle items (after the critical fixes):**

5. Convert `_keep_typing` initial-fire to fire-and-forget so wrapped tool bodies aren't blocked up to 30s on a degraded gateway (closes 04-LOW-03)
6. Bump first-fire failure logging to WARNING (subsequent heartbeats stay at DEBUG to prevent log flood) — closes 06-LOW-02
7. Add `tests/test_concurrency.py` with regression tests asserting `_resolve_media_url` doesn't block the event loop under concurrent calls (closes the v2.0 fix at commit `5e00da9` with a real regression guard)

**Out of scope:**
- New media handlers
- Tool surface changes (still 21 tools)
- Live Chatlytics gateway calls

**Files (create/modify):**
- MODIFY `src/chatlytics_hermes/adapter.py` (`_keep_typing` rewrite + `_typing_scope` extraction + `send_image`/`send_animation` `**kwargs` + `_resolve_media_url` allowlist + `_make_send_result` consolidation)
- MODIFY `src/chatlytics_hermes/tools.py` (`_ok` canonical helper + 5 media-tool error returns)
- CREATE `tests/test_concurrency.py`
- MODIFY `tests/test_media.py` (update `test_keep_typing_heartbeats_every_30s` to use `_typing_scope`; add BL-01 fix regression test if HERMES-07 didn't already cover it)
- MODIFY `tests/test_live_loader.py` (un-xfail the BL-01 / HI-01 / HI-03 regression tests added in HERMES-07)
- MODIFY `tests/test_tools.py` (add HI-01 path-traversal negative tests for each of 5 media tools)

**Acceptance criteria:**
1. `tests/test_live_loader.py::test_base_handle_message_invokes_keep_typing` PASSES (was xfailed in HERMES-07; BL-01 fixed)
2. `tests/test_live_loader.py::test_keep_typing_is_a_coroutine` PASSES (was xfailed in HERMES-07)
3. `tests/test_media.py::test_keep_typing_heartbeats_every_30s` still passes (via `_typing_scope`, no regression)
4. `tests/test_tools.py::test_chatlytics_send_file_rejects_path_outside_allowed_roots` — calling `chatlytics_send_file(chatId="...", filePath="/etc/passwd")` returns `{"success": False, "error": "..."}` and NO file is opened, NO upload is attempted (verify via mock assertions)
5. Same path-traversal negative test for the other 4 media tools (`send_image`, `send_voice`, `send_video`, `send_animation`) — 5 tests total
6. `tests/test_concurrency.py::test_keep_typing_initial_fire_does_not_block` — wrapped body starts within 10ms even if first typing request hangs
7. `tests/test_concurrency.py::test_resolve_media_url_off_event_loop` — concurrent media uploads don't serialize on file I/O
8. `tests/test_concurrency.py::test_keep_typing_first_fire_failure_logs_warning` — caplog captures WARNING on first-fire failure
9. `inspect.signature(ChatlyticsAdapter.send_image)` AND `inspect.signature(ChatlyticsAdapter.send_animation)` both include `**kwargs` parameter (HI-03 regression)
10. Single canonical success-shape helper used by `_make_send_result`, `_standalone_send`, and `tools._ok` — MD-01 dedup verified by code reading
11. Full pre-existing 45 + new tests all pass in dockerized smoke

---

### Phase 9: Observability + log hygiene
**Goal:** Consolidate log levels across the plugin (closes 02-LOW-02, 05-LOW-01) and add diagnostic logs to silent error paths so operators can debug from logs alone (closes 02-LOW-01).

**Depends on:** Phase 8 (avoid log-level churn after lifecycle changes)

**In scope:**
- Audit every `logger.warning` and `logger.error` call in `src/chatlytics_hermes/` — verify they fire on actually-actionable events, not on routine transport hiccups
- `send_typing` log volume reduction (the original outbound path uses `logger.warning` on transport errors → DEBUG; reserve WARNING for truly unexpected states)
- Add DEBUG log in `_make_tool_handler` exception handler around `ctx.get_platform(...)` (currently swallows silently — closes 05-LOW-01)
- Add cosmetic WARNING log in `send()` when reserved-name metadata keys are dropped (closes 02-LOW-01)
- Verify no api_key or full phone numbers appear in any log output (security spot-check)

**Out of scope:**
- New log destinations (file, syslog, OpenTelemetry — out of milestone)
- Log level config surface (no new env vars)

**Files (modify):**
- MODIFY `src/chatlytics_hermes/{adapter,client,tools,inbound}.py`
- ADD test(s) in `tests/test_observability.py` asserting key log lines fire at the expected levels

**Acceptance criteria:**
1. `tests/test_observability.py::test_send_typing_transport_error_logs_at_debug` — caplog verifies DEBUG, not WARNING
2. `tests/test_observability.py::test_make_tool_handler_logs_get_platform_failure` — DEBUG log fires when `ctx.get_platform` raises
3. `tests/test_observability.py::test_send_warns_on_dropped_reserved_metadata` — WARNING captured when caller passes a reserved key in `**extras`
4. `tests/test_observability.py::test_no_api_key_in_any_log_record` — sweep over caplog records across a full smoke flow; no `api_key`/`Bearer ` substrings
5. Existing 45+ tests still pass (no log-level regressions in fixtures asserting specific messages)

---

### Phase 10: Input validation + UX alignment
**Goal:** Tighten input validation at adapter and tool boundaries (closes 03-LOW-01, 05-LOW-02 + PR-review **MED-01**), align `chatlytics_login` semantics with the Claude Code MCP bundle's expectations (closes 05-LOW-03 + PR-review **LOW-03**), and document `get_chat_info` `{}` semantics so callers can distinguish empty-success from error (closes 02-LOW-03). Also resolves the `send_image` vs `send_image_file` API inconsistency surfaced by the PR-style review (PR-review **LOW-06**).

**Depends on:** Phase 9 (validation paths will emit new diagnostic logs)

**In scope:**
- Validate `webhook_path` in `ChatlyticsAdapter.__init__`: must start with `/`, must be non-empty, must not contain `?` or `#`, **must NOT equal `/health`** (route-collision footgun — PR-review MED-01 in `adapter.py:228-229`). Raise `ValueError` on invalid config (NOT silently rewrite — fail-fast matches Hermes conventions).
- API consistency for media handlers (PR-review LOW-06): tool layer unifies `chatlytics_send_image` to dispatch URL-or-local-path based on input shape; adapter retains the split `send_image` / `send_image_file` for backwards compat (already in v2.0 surface). Document the why in `tools.py` docstring.
- Decide on `looksLikeJid` regex for media-tool `chatId` schemas: align with MCP bundle's regex (verify exact pattern in `C:/Users/omern/.claude/plugins/marketplaces/chatlytics-claude-code/servers/chatlytics-mcp.js`) OR document why we accept any non-empty string. Decision in PLAN.md.
- `chatlytics_login` tool: return `{"success": False, "error": "webhook_registered=false"}` when the underlying API call succeeds but webhook registration didn't (aligns with MCP bundle behavior)
- `get_chat_info`: document the `{}` return shape on error in the docstring + README "Tool catalog" section. Optionally add an explicit `error` key to the meta when distinguishable from upstream.

**Out of scope:**
- New env vars
- Breaking config-schema changes

**Files (modify):**
- MODIFY `src/chatlytics_hermes/{adapter,tools}.py`
- MODIFY existing tests as needed (tests asserting old looser validation will fail; update them)
- MODIFY `README.md` (Tool catalog — `get_chat_info` note)

**Acceptance criteria:**
1. `tests/test_outbound.py::test_init_rejects_webhook_path_without_leading_slash` — passing `webhook_path="webhook"` raises ValueError
2. `tests/test_outbound.py::test_init_rejects_empty_webhook_path` — passing `webhook_path=""` raises ValueError
3. `tests/test_outbound.py::test_init_accepts_valid_webhook_path` — `"/webhook"` and `"/api/webhook"` both pass
4. `tests/test_tools.py::test_chatlytics_login_returns_false_when_webhook_not_registered` — mocked API returns `{success: True, webhook_registered: False}` → tool returns `{success: False, error: "..."}`
5. If `looksLikeJid` schema decision is enforce: `tests/test_tool_schemas.py::test_media_tools_validate_chat_id_format` — schemas reject non-JID strings; existing tests update to use valid JIDs
6. If `looksLikeJid` decision is permissive: PLAN.md documents the rationale and `tests/test_tool_schemas.py::test_media_tools_accept_any_non_empty_chat_id` exists
7. README "Tool catalog" mentions `get_chat_info` `{}` semantics

---

### Phase 11: Test infra cleanup
**Goal:** Eliminate the conftest teardown gap (closes 02-MED-02), reduce smoke runtime (closes 06-LOW-01 + PR-review **MED-03**: smoke compiles hermes-agent from `git+` URL on every run — bad CI surface area), and consolidate the `_FakePlatformConfig` fixture duplicated across 3 test files (PR-review cross-cutting nit).

**Depends on:** Phase 10 (no test-suite churn during validation changes)

**In scope:**
- Add a teardown to `tests/conftest.py` for the session-autouse platform_registry seed: snapshot the registry at session start, restore at session end
- Consolidate the `_FakePlatformConfig` test fixture (currently copy-pasted across `test_register.py`, `test_outbound.py`, `test_media.py` — PR-review noted as a maintenance hazard) into `tests/conftest.py` as a shared fixture
- Smoke build optimization (PR-review MED-03): pre-built docker image cached in `scripts/smoke-base.Dockerfile` + `scripts/build-smoke-base.sh`. The base image bakes `hermes-agent @ v2026.5.16` install so the per-run `smoke.sh` only does `pip install -e .[dev]` against the cache. Cuts ~45-60s off every run.
- Optionally: add a `--fast` flag to `scripts/smoke.sh` that skips the docker run and uses the host venv directly (for local iteration)

**Out of scope:**
- CI integration (GitHub Actions, etc. — operator decision)
- Test parallelization (`pytest-xdist`)

**Files (create/modify):**
- MODIFY `tests/conftest.py` (add teardown)
- MODIFY `scripts/smoke.sh` (optimization + optional `--fast`)
- CREATE `scripts/smoke-base.Dockerfile` if option (a) chosen

**Acceptance criteria:**
1. `tests/conftest.py` teardown restores the platform registry to its pre-session state (verify via assertion in a meta-test)
2. Running `pytest tests/` twice in succession produces identical results (no cross-run pollution)
3. `bash scripts/smoke.sh` runtime drops by at least 30% from v2.0 baseline (or PLAN.md documents why optimization (a) and (b) both rejected)
4. `bash scripts/smoke.sh --fast` (if implemented) skips docker and exits 0 against host venv
5. All v2.0 + v2.1 tests still pass (no regression from conftest changes)

---

### Phase 12: Release v2.1.0
**Goal:** Document the v2.1 changes (closes 05-MED-01 docs + 04-LOW-02 docs + PR-review **MED-04** plugin.yaml phase-ID leak), bump version, tag `v2.1.0`. NO PyPI publish (operator lock).

**Depends on:** Phases 7–11

**In scope:**
- Strip phase-identifier leaks from user-facing surfaces (PR-review MED-04 in `plugin.yaml:28,32,36`): `optional_env` descriptions currently contain `(HERMES-03)` / `(HERMES-04)` phase tags that leak internal milestone structure into the user's `hermes config` UI. Replace with feature-oriented descriptions (e.g., `(HERMES-03)` → `(webhook server)`).
- CHANGELOG.md PREPEND `## 2.1.0 (2026-MM-DD)` entry (additive, NOT BREAKING):
  - **Added:** live-loader integration smoke; concurrency regression test for `_resolve_media_url`; observability hardening; `webhook_path` validation; conftest teardown; smoke build optimization
  - **Changed:** `_keep_typing` shape (rename or upstream PR); `send_typing` log levels (warning → debug for transport errors); `chatlytics_login` returns `success: False` on `webhook_registered=False`
  - **Fixed:** silent `ctx.get_platform` failures in `_make_tool_handler`; dropped reserved metadata keys in `send()` now emit a warning
  - **Docs:** `chatlytics_actions` vs `chatlytics_dispatch` semantic distinction clarified; `get_chat_info` `{}` semantics documented; tool catalog updated
- README.md updates:
  - "Architecture notes" section: mention `_keep_typing` resolution (rename or PR), live-loader smoke addition
  - "Tool catalog" section: clarify `chatlytics_actions` (GET catalog) vs `chatlytics_dispatch` (POST generic action dispatcher)
  - "Known issues" section: document `filename` injection for URL-path documents (still needs Chatlytics gateway confirmation — link to upstream issue if filed)
- `pyproject.toml`: bump `version = "2.0.0"` → `"2.1.0"`. Verify entry-points block unchanged.
- Update `scripts/smoke.sh` if any prior phase didn't already (final pass)
- Re-run full smoke; assert all tests pass
- `git tag -a v2.1.0 -m "v2.1.0 — tech debt resolution + live-loader integration"` (DO NOT push autonomously)

**Out of scope:**
- PyPI publish (operator lock, same as v2.0)
- Marketplace listing
- Live Chatlytics integration test

**Files (modify):**
- REWRITE `CHANGELOG.md` (prepend 2.1.0 entry)
- MODIFY `README.md` (Architecture notes + Tool catalog + Known issues)
- MODIFY `pyproject.toml` (version bump)
- VERIFY `scripts/smoke.sh` (final pass)

**Acceptance criteria:**
1. `bash scripts/smoke.sh` exits 0 with all v2.0+v2.1 tests passing
2. `pyproject.toml` has `version = "2.1.0"` and entry-points block unchanged
3. `CHANGELOG.md` has a `## 2.1.0` entry at the top with Added/Changed/Fixed/Docs sections; NO `BREAKING` marker (additive milestone)
4. `README.md` "Architecture notes" mentions `_keep_typing` resolution
5. `README.md` "Tool catalog" clarifies `chatlytics_actions` vs `chatlytics_dispatch`
6. `git tag --list v2.1.0` returns a tag
7. NO `python -m build` or `twine upload` runs anywhere in the phase artifacts (operator lock preserved)
8. Full per-phase REVIEW.md count: all 6 phases of v2.1 PASS with no BLOCKER/HIGH

---

## Recommended /gsd-autonomous wave sequence (v2.1)

Phases 7–11 are sequential (each builds on the previous). Phase 12 is the release wrapper. No cross-milestone parallelization with v2.0 since v2.0 is shipped.

```
/gsd-autonomous --from 7 --to 12
```

This runs discuss → plan → execute → review → commit for each phase in sequence, then halts at the milestone boundary. After all 6 phases complete, the autonomous runner invokes audit → complete-milestone → cleanup as in v2.0.
