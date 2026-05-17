# Phase 9 — Plan 1: Log hygiene + diagnostic logs on silent paths

**Status:** Ready for execute
**Phase:** HERMES-09 (Observability + log hygiene)
**Depends on:** HERMES-08 (lifecycle changes landed; no log churn while those settled)

## Objective

1. Normalize `send_typing` log levels: transport error + non-200 response → DEBUG (matches heartbeat treatment).
2. Add diagnostic logs to 5 silent error paths:
   - `_make_tool_handler` ctx.get_platform exception swallow (adapter.py)
   - `send()` reserved-metadata key drop (adapter.py)
   - `tools._post` / `tools._get` / `tools._err_from_response` JSON-decode swallow (tools.py — 3 sites)
   - `_standalone_send` JSON-decode swallow (adapter.py)
   - `get_chat_info` JSON-decode swallow (adapter.py)
   - `send()` JSON-decode swallow (adapter.py)
3. Add `tests/test_observability.py` with 6 tests asserting level + presence.
4. Document the log-level convention in `adapter.py` module docstring.

## Files to change

| File | Change |
|---|---|
| `src/chatlytics_hermes/adapter.py` | (a) module docstring section appended documenting log-level convention; (b) `send_typing` non-200 + transport → DEBUG (lines 447-453); (c) `send()` reserved metadata WARNING (line 376-384 block); (d) `send()` JSON decode DEBUG (line 398-401); (e) `get_chat_info` JSON decode DEBUG (line 485-488); (f) `_standalone_send` JSON decode DEBUG (~line 1052); (g) `_make_tool_handler` get_platform DEBUG (line 1109-1114) |
| `src/chatlytics_hermes/tools.py` | (a) `_err_from_response` JSON decode DEBUG (line 79-80); (b) `_post` JSON decode DEBUG (line 119-120); (c) `_get` JSON decode DEBUG (line 149-150) |
| `tests/test_observability.py` | NEW — 6 tests |

## Wave plan

All edits + new test file are independent log-level changes. Execute in a single wave; no inter-edit ordering required.

### Wave 1 (all edits parallel-safe)

**1.1** `adapter.py` — module docstring: append a `## Log level convention` section after the existing top-of-file docstring (after line ~30) listing:
- DEBUG: steady-state telemetry, swallowed-by-design hiccups, JSON-decode fallbacks
- INFO: lifecycle events
- WARNING: operator-actionable degraded states
- ERROR/EXCEPTION: teardown failures + handle_message dispatch failures

**1.2** `adapter.py:446-453` — change both `logger.warning` calls in `send_typing` to `logger.debug`. Keep the same format strings.

**1.3** `adapter.py:376-384` — in the `if metadata:` block, when a reserved key is encountered, emit `logger.warning("send() ignoring reserved metadata key %r (would shadow body field)", key)` BEFORE the `continue`/skip. Reserved set: `{"chatId", "text", "accountId", "replyTo"}`.

**1.4** `adapter.py:398-401` — in the `try: payload = response.json() / except Exception:` block, add `logger.debug("send() response was not JSON; using raw_text fallback")` before the assignment.

**1.5** `adapter.py:485-488` — `get_chat_info`'s `try: payload = response.json() / except Exception: return {}` adds `logger.debug("get_chat_info JSON decode failed; returning {}")` before the return.

**1.6** `adapter.py` `_standalone_send` JSON decode block (~line 1052) — same DEBUG addition.

**1.7** `adapter.py:1109-1114` — `_make_tool_handler` lookup: change `except Exception:  # noqa: BLE001\n    entry = None` to log a DEBUG line with the exception before setting `entry = None`. Use `except Exception as exc:`.

**1.8** `tools.py:79-80` — `_err_from_response` JSON decode swallow: add `logger.debug("_err_from_response JSON decode failed; using raw_text fallback")`. Requires `import logging` + `logger = logging.getLogger("chatlytics_hermes.tools")` at module top (check first; may already exist).

**1.9** `tools.py:119-120` — `_post` JSON decode swallow: add `logger.debug("_post JSON decode failed; using raw_text fallback")`.

**1.10** `tools.py:149-150` — `_get` JSON decode swallow: add `logger.debug("_get JSON decode failed; using raw_text fallback")`.

**1.11** CREATE `tests/test_observability.py` with 6 tests:
- `test_send_typing_non_200_logs_at_debug_not_warning` — respx returns 503 on `/api/v1/typing`; caplog at DEBUG; assert no WARNING with "send_typing" in message, AT LEAST one DEBUG with "send_typing"
- `test_send_typing_transport_error_logs_at_debug` — respx `side_effect=httpx.RequestError("boom")`; same assertion shape
- `test_send_warns_on_dropped_reserved_metadata` — call `adapter.send("chat", "hi", metadata={"chatId": "OTHER", "extra": "ok"})`; assert ONE WARNING record containing the substring `reserved metadata key` and the key name `chatId`; assert `extra` is NOT in any WARNING
- `test_make_tool_handler_logs_get_platform_failure_at_debug` — build a fake `ctx` whose `get_platform` raises; call the wrapped handler; assert DEBUG record containing "get_platform"
- `test_tools_post_json_decode_failure_logs_at_debug` — respx returns 200 with `text/plain` body; assert DEBUG record containing "JSON decode failed"
- `test_no_api_key_in_any_log_record` — adapter `api_key="SECRET_API_KEY_TEST_42"`; respx mocks `/health` + `/api/v1/send`; run connect → send → disconnect with caplog at DEBUG; assert `"SECRET_API_KEY_TEST_42" not in record.getMessage()` AND `"Bearer " not in record.getMessage()` for every record

## Verification

1. `pytest tests/test_observability.py -q` — 6 tests pass
2. `pytest tests/ -q` — total >= 64 tests (58 baseline + 6 new); ALL pass
3. No log assertions in existing tests broken by level changes (run full suite to confirm)
4. `git grep -n "logger.warning.*send_typing" src/` returns no matches (the two WARNINGs in `send_typing` are now DEBUG)
5. Phase 8's `tests/test_concurrency.py::test_keep_typing_first_fire_failure_logs_warning` still passes (this WARNING is the `_keep_typing` initial-fire log, NOT the `send_typing` log we just downgraded)

## Acceptance criteria mapping

| Acceptance crit (ROADMAP Phase 9) | Maps to |
|---|---|
| #1 `test_send_typing_transport_error_logs_at_debug` | Test in 1.11 |
| #2 `test_make_tool_handler_logs_get_platform_failure` (we use DEBUG, not WARNING — clarified in CONTEXT) | Test in 1.11 |
| #3 `test_send_warns_on_dropped_reserved_metadata` | Test in 1.11 |
| #4 `test_no_api_key_in_any_log_record` | Test in 1.11 |
| #5 Existing 45+ tests still pass | Full-suite verification |

## Non-goals (scope guard)

- No new env vars
- No new log destinations
- No churn on `logger.info` lifecycle logs
- No churn on `logger.warning` in inbound.py HMAC/JSON/payload reject paths (already correct)
- No churn on `_keep_typing` initial-fire WARNING (Phase 8 fix)
- No churn on `get_chat_info` non-200 WARNING (correct level — caller-actionable for a one-shot lookup)
