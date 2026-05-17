# Phase 5: HERMES-05 — Full Chatlytics tool surface - Context

**Gathered:** 2026-05-17
**Status:** Ready for planning
**Mode:** Smart discuss — most design-heavy phase of the milestone, but core decisions are locked

<domain>
## Phase Boundary

Expose EVERY Chatlytics action as a Hermes tool via `ctx.register_tool()`. Source the canonical tool list from the Claude Code plugin's MCP server bundle (`chatlytics-mcp.bundle.js` in `omernesh/chatlytics-claude-code`) PLUS any additional actions enumerable via Chatlytics's `POST /api/v1/actions` schema. Tools validate inputs via JSON schemas and return `{"success": true, ...}` shape.

**Phase ID:** HERMES-05 (depends on HERMES-04)

</domain>

<decisions>
## Implementation Decisions

### Locked from ROADMAP HERMES-05 spec — tool surface

**Baseline 8 tools from Claude Code MCP bundle** (verified from `C:/Users/omern/.claude/plugins/marketplaces/chatlytics-claude-code/servers/chatlytics-mcp.js`):
1. `chatlytics_actions` — generic action dispatcher (POST /api/v1/actions)
2. `chatlytics_directory` — list chats / contacts / groups
3. `chatlytics_dispatch` — session lifecycle
4. `chatlytics_health` — GET /health
5. `chatlytics_login` — auth / session create
6. `chatlytics_read` — read messages from a chat
7. `chatlytics_search` — message search (POST /api/v1/actions {action: "search", query})
8. `chatlytics_send` — POST /api/v1/send

**Plus per ROADMAP scope** (messaging actions via POST /api/v1/actions or dedicated endpoints):
- `chatlytics_reply` — reply to a specific message (replyTo)
- `chatlytics_react` — emoji reaction (POST /api/v1/actions {action: "react", messageId, emoji})
- `chatlytics_edit` — edit a sent message
- `chatlytics_unsend` — recall a sent message
- `chatlytics_pin` — pin a message
- `chatlytics_unpin` — unpin a message
- `chatlytics_delete` — delete a message
- `chatlytics_poll` — create a poll

**Plus media variants from HERMES-04, also exposed as tools**:
- `chatlytics_send_image`
- `chatlytics_send_voice`
- `chatlytics_send_video`
- `chatlytics_send_file` (note: tool name is `_file`, not `_document`, for symmetry with MCP bundle naming)
- `chatlytics_send_animation`

**Total expected: 21+ tools**. The "21" is the floor; if the Chatlytics gateway's `POST /api/v1/actions` action enumeration returns additional actions at execute time, add tools for those too (test-asserted via baseline list + actual gateway-reported list intersection).

### Locked from PROJECT.md
- Tool return shape: `{"success": bool, ...response, "error": str?}` — Hermes tool result convention.
- All tools namespaced with `chatlytics_` prefix (no collision with other Hermes plugins).
- JSON schema per tool: `chatId`, `text`, `messageId`, `emoji`, etc. with `required` correctly set.
- All HTTP through the existing httpx client (from HERMES-02). Shared auth/timeout.

### Out of scope (LOCKED)
- README/CHANGELOG rewrite (HERMES-06)
- Smoke test script (HERMES-06)
- Tool result rendering (Hermes UI concern, not plugin concern)
- Tool docstrings beyond what JSON schema `description` provides

### Claude's Discretion
- Schema library: `jsonschema.Draft202012Validator` for runtime validation. Schemas defined as Python dicts (no separate `.json` files — single source of truth in `tools.py`).
- File layout:
  - `src/chatlytics_hermes/tools.py` — handler functions + schemas
  - `src/chatlytics_hermes/__init__.py` — extend `register()` to also call `ctx.register_tool(name, handler, schema)` for each tool
  - `src/chatlytics_hermes/adapter.py` — expose `client` attribute publicly (already exists from HERMES-02; tool handlers reuse it via the adapter instance)
- Handler signature: `async def chatlytics_<action>(client: ChatlyticsClient, **kwargs) -> dict` — `client` passed by the tool-registration glue; `kwargs` from validated JSON args. Returns `{"success": bool, ...}`.
- Error handling: each handler catches `httpx.HTTPStatusError` (4xx/5xx) → `{"success": False, "error": str(e), "status_code": e.response.status_code}`. Other exceptions → `{"success": False, "error": str(e)}`.

### Address forward action items from prior reviews
- 04-REVIEW MED-01 (`_keep_typing` shape diverges from upstream base coroutine — documented-only) — revisit in HERMES-05: if tool handlers use `_keep_typing` for long-running calls, ensure the async-cm shape composes well. Else document.
- 04-REVIEW MED-02 (blocking file I/O in `_resolve_media_url`) — if HERMES-05 tools call media handlers, this could surface under tool concurrency. Quick fix: wrap blocking I/O in `asyncio.to_thread`. Apply if zero-risk in HERMES-05's first commit; else defer to HERMES-06.

</decisions>

<code_context>
## Existing Code Insights

- `src/chatlytics_hermes/adapter.py` — `ChatlyticsAdapter(BasePlatformAdapter)` with full outbound (HERMES-02) + inbound (HERMES-03) + media + cron (HERMES-04). `client` attribute is the shared `ChatlyticsClient` httpx wrapper.
- `src/chatlytics_hermes/client.py` — `ChatlyticsClient` with `post`, `get`, `send_media`, `upload_file`, `post_multipart`. Tool handlers use these primitives.
- `src/chatlytics_hermes/__init__.py` — re-exports `register`. Extend to register tools alongside platform.
- `tests/conftest.py` — platform registry seed; respx fixture; localhost passthrough. Reuse for tool tests.
- Reference Hermes plugin with tools: `/tmp/hermes-ref-v0.14.0/plugins/platforms/` — search for `ctx.register_tool` usage. (Most platform plugins don't register tools; tool registration is more common in `plugins/tools/` if that dir exists.)
- Reference Claude Code MCP bundle (canonical naming): `C:/Users/omern/.claude/plugins/marketplaces/chatlytics-claude-code/servers/chatlytics-mcp.js` — 8 tool definitions with input schemas. Mirror schema field names where applicable (chatId, text, messageId, etc.) for consistency.

</code_context>

<specifics>
## Specific Ideas

- Test split:
  - `tests/test_tools.py` — handler behavior (mocked Chatlytics responses, assert correct endpoint + body + return shape)
  - `tests/test_tool_schemas.py` — schema validation (all schemas pass `jsonschema.Draft202012Validator`, required fields correct, naming convention)
- Schema validation test: loop over `registered_tools` registry, assert each schema validates and has `chatId` required for messaging tools.
- `test_tool_count_matches_claude_code_plugin_baseline` — assert registered tool count >= 8 (baseline) + 5 (media variants from HERMES-04) = 13 minimum. With reply/react/edit/unsend/pin/unpin/delete/poll = 21 expected.
- Namespace test: all tool names start with `chatlytics_`.
- Try to introspect `POST /api/v1/actions` schema at execute time via WebFetch or local sandbox — but the Chatlytics gateway URL is operator-provided and likely not reachable from this build env. Skip live enumeration; use the union of MCP bundle + ROADMAP scope list (21 tools).

</specifics>

<deferred>
## Deferred Ideas

- Per-tool docstrings beyond schema descriptions (HERMES-06 if needed)
- Tool result rendering / UI affordances (Hermes UI concern)
- Live integration test against real Chatlytics gateway
- README rewrite + CHANGELOG entry (HERMES-06)
- Smoke test (HERMES-06)

</deferred>
