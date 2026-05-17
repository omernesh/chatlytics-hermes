---
phase: 05-full-chatlytics-tool-surface
plan: 01
status: completed
date: 2026-05-17
implemented_by: claude-opus-4-7-1m
---

# HERMES-05 Plan 01 — Summary

## What shipped

- **`src/chatlytics_hermes/tools.py`** (NEW, 21 tools): one async handler per Chatlytics action with a Draft 2020-12 JSON Schema and `(name, schema, handler)` triple in `TOOLS`. Locked count (`assert len(TOOLS) == 21`).
- **`src/chatlytics_hermes/adapter.py`** (MODIFIED): public `.client` property; `register(ctx)` extended to iterate `TOOLS` and call `ctx.register_tool(name=, toolset="chatlytics", schema=, handler=)` for each, feature-detected via `hasattr(ctx, "register_tool")`.
- **`pyproject.toml`** (MODIFIED): added `jsonschema>=4,<5` to runtime deps.
- **`tests/test_tools.py`** (NEW, 6 tests): handler behavior (send, react, search, 400-error, optional-fields, count).
- **`tests/test_tool_schemas.py`** (NEW, 5 tests): schema shape (validate, required-field discipline, namespace prefix, count, additionalProperties).

Tests: 44/44 passing (33 from HERMES-01..04 + 11 new in HERMES-05).

## Tool groups (21 total)

**Messaging (10):** `chatlytics_send`, `_reply`, `_react`, `_edit`, `_unsend`, `_pin`, `_unpin`, `_read`, `_delete`, `_poll`
**Media (5):** `chatlytics_send_image`, `_voice`, `_video`, `_file`, `_animation`
**Directory/search (3):** `chatlytics_directory`, `_search`, `_actions`
**Sessions/health (3):** `chatlytics_health`, `_login`, `_dispatch`

## Key design decisions

### 1. Tool handler signature: `async def chatlytics_<x>(client, *, **kwargs) -> dict`
- Non-media handlers take `client: ChatlyticsClient` + keyword-only kwargs matching their schema.
- Media handlers also take `adapter: ChatlyticsAdapter | None` — needed because uploads (`/api/v1/upload`) and the adapter's MIME-aware media-type mapping live on the adapter, not the bare client. Auto-detected via `inspect.signature` in `tools.handler_takes_adapter()`.
- Return shape is always `{"success": bool, ...}`. The `_ok` helper merges gateway payloads (re-asserting `success=True` so a payload-side `success=False` cannot override the derived flag); `_err_from_response` includes `error`, `status_code`, and `raw_response` for caller-side debugging.

### 2. Adapter resolution at call time (not registration time)
`ctx.register_tool()` runs at plugin-load time, before the gateway calls `adapter_factory(config)`. Tool handlers therefore must look up the live adapter on each call. The `_make_tool_handler` closure in `adapter.py` tries `ctx.get_platform("chatlytics")` first (v0.14 PluginContext public API), then `ctx.platforms["chatlytics"]` for older harnesses. When nothing is connected, returns `{"success": False, "error": "..."}` instead of raising.

### 3. Reconciliation with the MCP bundle naming
- The phase brief lists `chatlytics_actions` as a "generic dispatcher: POST /api/v1/actions with arbitrary body". The MCP bundle (canonical) defines `chatlytics_actions` as a **GET** that LISTS the catalog, with `chatlytics_dispatch` being the POST dispatcher. We follow **MCP bundle semantics** — preserving the 8-tool baseline meaning — and the brief's "generic dispatcher" intent is satisfied by `chatlytics_dispatch`. Documented in PLAN.md `<context>`.
- The phase brief specifies `chatlytics_send` as `POST /api/v1/send`. The MCP bundle uses `POST /api/v1/actions {action: "send", ...}`. We follow the **brief** — the adapter (HERMES-02) already uses `/api/v1/send` so the tool's behavior matches the adapter path. Operators reaching for the actions-dispatcher version can use `chatlytics_dispatch action="send"`.

### 4. JSON Schema discipline
- Draft 2020-12 via `jsonschema>=4,<5` (added to runtime deps so callers can validate at the gateway layer without extra installs).
- `additionalProperties: False` on every schema → typos in tool inputs fail loudly.
- Media tools use `anyOf: [{required: [mediaUrl]}, {required: [filePath]}]` so callers MUST supply at least one resource. `chatId` stays in top-level `required`.
- `chatlytics_actions`/`_health`/`_login` have empty `properties` and `required: []` so the schema is honest about parameterlessness.

## Forward action items addressed
- **04-REVIEW MED-01 (`_keep_typing` shape divergence from base)**: no tool handler currently composes `_keep_typing` — none of the 21 are long-running enough to need it. Document-only deferral; HERMES-06 owns the docstring note in README.
- **04-REVIEW MED-02 (blocking file I/O in `_resolve_media_url`)**: deferred to HERMES-06. The `chatlytics_send_image_file` adapter handler that does the read is reachable from the new `chatlytics_send_image` tool when `filePath` is passed, so this is now in the tool's user-visible surface. Defer rationale: wrapping in `asyncio.to_thread` is straightforward but changes I/O ordering semantics under concurrent uploads — HERMES-06 owns the wrap plus a concurrency regression test.

## Out-of-scope (preserved)
- README / CHANGELOG rewrites — HERMES-06
- Smoke test script — HERMES-06
- Tool result rendering — Hermes UI concern
- Live integration test against a real Chatlytics gateway

## Commits (6)
1. `plan(hermes-05): write 05-01-PLAN.md for full tool surface (21 tools)` — d33677c
2. `chore(hermes-05): add jsonschema>=4,<5 runtime dep` — 6dc7863
3. `feat(hermes-05): tools.py -- 21 Chatlytics tool handlers + schemas` — 2223dee
4. `feat(hermes-05): register tools in register(ctx) + public .client` — 841837f
5. `test(hermes-05): 9 tool + schema tests for the full Chatlytics surface` — d8679ed
6. (this) `docs(hermes-05): SUMMARY + VERIFICATION + REVIEW`
