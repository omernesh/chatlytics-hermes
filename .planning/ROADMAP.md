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

<details>
<summary>v2.1 — Critical safety fixes + tech debt resolution + live-loader integration — SHIPPED 2026-05-17</summary>

## v2.1 — Critical safety fixes + tech debt resolution + live-loader integration (COMPLETE)

**Shipped:** 2026-05-17. All 6 HERMES phases (07-12) delivered end-to-end. 88/88 tests green (45 v2.0 baseline + 43 new v2.1 tests; zero regressions). v2.0 BLOCKER (BL-01) + 2 HIGHs (HI-01, HI-03) + every MED/LOW finding from `.planning/v2.0-MILESTONE-CODE-REVIEW.md` + `.planning/v2.0-MILESTONE-PR-REVIEW.md` closed. `v2.1.0` annotated tag created locally (operator push pending). NO PyPI publish (operator lock preserved). Archive: `.planning/milestones/v2.1-ROADMAP.md`. Audit: `.planning/milestones/v2.1-MILESTONE-AUDIT.md`.

**Phases:**
- HERMES-07 — Live-loader integration smoke (surfaces BL-01) — reproduced BL-01/HI-01/HI-03 under xfail-strict regression tests
- HERMES-08 — Critical safety fixes (BL-01 BLOCKER + HI-01 HIGH + HI-03 HIGH) + async lifecycle hardening — un-xfailed the regressions
- HERMES-09 — Observability + log hygiene
- HERMES-10 — Input validation + UX alignment
- HERMES-11 — Test infra cleanup
- HERMES-12 — Release v2.1.0 (LOCAL tag only)

**Operator next:** Review v2.1.0 artifact, then `git push origin main && git push origin v2.1.0` when ready. Optionally delete local `v2.0.0` tag (points at the BL-01 pre-fix artifact superseded by v2.1.0).

</details>

---

## Backlog

(Items deferred to v2.2+ — collected during v2.1 close.)

- Sentinel `_error` key on `get_chat_info` return shape (breaking change)
- Strict JID regex enforcement on `chatId` schemas (would break phone numbers / display names)
- Collapse `send_image` / `send_image_file` into one method (breaking change)
- Long-term wheel caching in `scripts/smoke.sh` beyond `--retries 3` (build-perf nice-to-have)
- Hermes `0.15` readiness review (v3.0 decision; not a v2.2 item)
