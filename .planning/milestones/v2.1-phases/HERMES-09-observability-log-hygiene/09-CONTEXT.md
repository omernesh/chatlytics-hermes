# Phase 9: Observability + log hygiene - Context

**Gathered:** 2026-05-17
**Status:** Ready for planning
**Mode:** Auto-generated (infra-skip — fix shapes locked by ROADMAP)

<domain>
## Phase Boundary

Consolidate log levels in the plugin and add diagnostic logs to silent error paths so operators can debug from logs alone. Closes the carry-forward audit lows:

- **02-LOW-02 + 05-LOW-01 + LO-11:** `send_typing` flooded at WARNING on every gateway hiccup; downgrade transport-level and non-200-status hiccups to DEBUG. WARNING is reserved for `_keep_typing` initial-fire (already aligned in Phase 8) and truly unexpected states.
- **02-LOW-01 + 05-LOW-01:** Silent error paths in the plugin — `_make_tool_handler`'s `get_platform` swallow, `send()`'s reserved-metadata drop, `tools.py` JSON-decode swallows, and `adapter.py:_standalone_send` JSON-decode swallow — get a DEBUG (for noise-y but expected paths) or WARNING (for caller-visible drops) log so operators are not blind during incidents.

No behavior change. No tool surface change. Tests cover the level-and-presence assertions; existing log-bearing tests must remain green.
</domain>

<decisions>
## Implementation Decisions

### Log level convention (documented at module top of adapter.py)

- **DEBUG** — steady-state telemetry, expected transient hiccups, swallowed-by-design exceptions on non-critical paths (typing heartbeats, JSON-decode fallbacks, ctx accessor probes)
- **INFO** — lifecycle events (connect, disconnect, server start/stop)
- **WARNING** — operator-actionable degraded states (initial-fire failure of `_keep_typing`, dropped reserved metadata key, bind error reporting, HMAC reject, webhook reject)
- **ERROR / EXCEPTION** — `handle_message` dispatch failures, teardown failures (already in place at adapter.py:309-310, 334-335, 210; not churned)

### Fix 1: send_typing log volume (02-LOW-02 + LO-11)

`src/chatlytics_hermes/adapter.py:446-453` — currently `logger.warning` on:
- non-200 response (line 447-451)
- httpx.RequestError (line 453)

Both downgrade to `logger.debug`. Rationale: `send_typing` is a UX hint (the typing bubble); a flapping gateway should not produce hundreds of WARNINGs. The `_keep_typing` initial-fire WARNING (line 909) already provides operator-actionable surface area for sustained failures.

### Fix 2: silent ctx.get_platform failure (05-LOW-01)

`src/chatlytics_hermes/adapter.py:1109-1114` — `get_platform("chatlytics")` exception swallowed silently into `entry = None`. Add `logger.debug("_make_tool_handler ctx.get_platform raised: %s; falling back to ctx.platforms", exc)` before the fallback. DEBUG (not WARNING) because the fallback chain is by design; operators only need to see this during deep debugging.

### Fix 3: dropped reserved metadata key (02-LOW-01)

`src/chatlytics_hermes/adapter.py:376-384` — when caller passes `metadata={"chatId": "...", ...}`, the reserved-key check silently drops. Emit `logger.warning("send() ignoring reserved metadata key %r (would shadow body field)", key)` ONCE per dropped key per call. WARNING because caller intent is unrealized.

### Fix 4: tools.py JSON-decode fallback (02-LOW-01)

`src/chatlytics_hermes/tools.py:79-80, 119-120, 149-150` — three `except Exception: payload = {...}` swallows. Add `logger.debug("response JSON decode failed; using raw_text fallback")` so operators tracing a malformed gateway response can see why the response wrapper landed at `raw_text`. DEBUG because this is a known-tolerated fallback path.

### Fix 5: adapter.py:_standalone_send JSON-decode fallback (02-LOW-01)

`src/chatlytics_hermes/adapter.py:1052-1053` — same pattern, add the same DEBUG log.

### Fix 6: adapter.py get_chat_info JSON-decode silence (02-LOW-01)

`src/chatlytics_hermes/adapter.py:485-488` — `try: payload = response.json() / except Exception: return {}` silently returns `{}` on a malformed body. Add `logger.debug("get_chat_info JSON decode failed; returning {}")` so the `{}` return is not confused with empty-success.

### Fix 7: adapter.py send() JSON-decode fallback log

`src/chatlytics_hermes/adapter.py:398-401` — JSON-decode swallowed; add a `logger.debug("send response was not JSON; using raw_text fallback")` for the same reason.

### Documentation

Add a module-level docstring section in `src/chatlytics_hermes/adapter.py` (right after the existing module docstring) summarizing the log-level convention so future contributors do not re-introduce inconsistency.

### Tests (`tests/test_observability.py` — new file)

1. `test_send_typing_non_200_logs_at_debug_not_warning` — caplog captures DEBUG, NOT WARNING, on 503 typing response
2. `test_send_typing_transport_error_logs_at_debug` — caplog captures DEBUG on httpx.RequestError
3. `test_make_tool_handler_logs_get_platform_failure_at_debug` — caplog captures DEBUG when `ctx.get_platform` raises
4. `test_send_warns_on_dropped_reserved_metadata` — caplog captures WARNING when caller passes a reserved key in metadata
5. `test_tools_logs_json_decode_fallback_at_debug` — caplog captures DEBUG when `_post`/`_get` JSON decode fails
6. `test_no_api_key_in_any_log_record` — full smoke flow; sweep caplog records; no `api_key`/`Bearer ` substrings

### Claude's discretion

- Whether to log dropped reserved keys ONCE per call (set of keys) or per-key (multiple WARNINGs). Pick "log per key" — operators want to know each one dropped.
- Exact log message phrasing (test asserts the substring, not the full message).
</decisions>

<code_context>
## Existing code insights

### Files touched (modify)
- `src/chatlytics_hermes/adapter.py` — 6 log-level / log-addition edits + module docstring update
- `src/chatlytics_hermes/tools.py` — 3 log additions on JSON-decode fallback

### Files created
- `tests/test_observability.py` — 6 tests

### Patterns observed
- `logger = logging.getLogger("chatlytics_hermes.{module}")` — already in place across all 4 modules
- caplog with `caplog.set_level(logging.DEBUG, logger="chatlytics_hermes")` is the canonical pytest pattern in existing tests (`test_concurrency.py::test_keep_typing_first_fire_failure_logs_warning` uses it)
- `pytestmark = pytest.mark.asyncio` for all new test files

### Out-of-scope log calls (DO NOT churn)
- `adapter.py:248, 297, 331, 340` — `logger.info` on connect/disconnect lifecycle (correct level)
- `adapter.py:309-310, 334-335` — `logger.exception` on teardown failures (correct level)
- `inbound.py:182-204` — `logger.warning` on HMAC reject / JSON reject / payload reject (correct level — operator-actionable)
- `inbound.py:210` — `logger.exception` on handle_message failure (correct level)
- `adapter.py:386, 691` — `logger.debug` request tracing (correct level)
- `adapter.py:909, 929` — `_keep_typing` initial-fire WARNING + heartbeat DEBUG (Phase 8 fix, correct)
- `adapter.py:447-453` non-200 response WARNING — `get_chat_info` is fine to keep at WARNING for now (separate from `send_typing`; matches expected operator behavior for chat-info lookups returning non-200)

Actually, `get_chat_info` at lines 474, 478-482 — leave at WARNING. Get-info failures are debuggable but actionable: caller relied on the response to be present, and `{}` does not surface why. WARNING is correct here.

### v2.0/v2.1 invariants (must preserve)
- Hermes pin `>=0.14,<0.15`
- 21 tools exactly
- httpx outbound, aiohttp embedded inbound only
- `{"success": bool, ...}` tool response shape
- 58/58 v2.0+v2.1 tests still passing (new observability tests will bump the total)
- `chatlytics-hermes` package name
- MIT license
</code_context>

<specifics>
## Specific implementation guides

- Tests use `caplog.set_level(logging.DEBUG, logger="chatlytics_hermes.adapter")` (or `.tools`) so DEBUG records actually populate `caplog.records`
- Reserved metadata WARNING test: `await adapter.send("chatId", "hi", metadata={"chatId": "OTHER", "extra": "ok"})` — expect ONE WARNING (one dropped key), zero for the non-reserved `extra`
- `test_no_api_key_in_any_log_record`: configure adapter with `api_key="SECRET_VALUE_TEST"`, run a connect+send+disconnect flow, then walk `caplog.records` and assert `"SECRET_VALUE_TEST" not in record.getMessage()` for every record. Note: client.py adds `Authorization: Bearer ...` but doesn't log the header; the test guards against future regressions.
- The DEBUG log on `_make_tool_handler` ctx.get_platform must use the **module-level** `logger` (already imported as `logger = logging.getLogger("chatlytics_hermes.adapter")` at adapter.py top), not a per-call one. Existing import already in place.
</specifics>

<deferred>
## Deferred ideas (DO NOT fix here — out of scope)

- `chatlytics_login` semantics alignment with MCP bundle (Phase 10 / 05-LOW-03)
- `webhook_path` validation at `__init__` (Phase 10 / 03-LOW-01)
- `looksLikeJid` regex on media-tool schemas (Phase 10)
- `get_chat_info` `{}` doc/return-shape semantics (Phase 10 / 02-LOW-03)
- Conftest teardown for platform_registry (Phase 11 / 02-MED-02)
- Smoke build cache (Phase 11 / 06-LOW-01)
- v2.1.0 CHANGELOG / README / pyproject bump (Phase 12)
- Log destination changes (file, syslog, OpenTelemetry) — out of milestone
- New env vars for log-level config — out of milestone
- Phase identifier leak in plugin.yaml (Phase 12 / PR-review MED-04)
</deferred>
