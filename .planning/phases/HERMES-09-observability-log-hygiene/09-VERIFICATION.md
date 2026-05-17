# Phase 9 — Verification

**Phase:** HERMES-09 (Observability + log hygiene)
**Plan:** `09-PLAN-1-log-hygiene-and-diagnostics.md`
**Date:** 2026-05-17
**Status:** PASS

## Test results

```
pytest tests/ -q
64 passed in 20.38s
```

Baseline before Phase 9: 58 tests (5 test files + test_concurrency from Phase 8).
After Phase 9: 64 tests (added `tests/test_observability.py` with 6 tests).

### New tests (test_observability.py — 6 tests, all PASS)

1. `test_send_typing_non_200_logs_at_debug_not_warning` — 02-LOW-02 + LO-11
2. `test_send_typing_transport_error_logs_at_debug` — 02-LOW-02 + LO-11
3. `test_send_warns_on_dropped_reserved_metadata` — 02-LOW-01
4. `test_make_tool_handler_logs_get_platform_failure_at_debug` — 05-LOW-01
5. `test_tools_post_json_decode_failure_logs_at_debug` — 02-LOW-01
6. `test_no_api_key_in_any_log_record` — defensive regression

### Pre-existing test note

When running `pytest` directly in a shell with `CHATLYTICS_API_KEY` /
`CHATLYTICS_BASE_URL` set in the environment, 11 tests in `test_outbound.py`,
`test_concurrency.py`, and `test_media.py` fail with "Bearer <real key>"
mismatching the test fixture's `EXPECTED_AUTH`.  This is **environmental
pollution** from the operator shell, not a Phase 9 regression.  Unsetting
the env vars produces the 64/64 result above.  The dockerized
`scripts/smoke.sh` (which is the v2.1 verification ceiling) runs in a
clean container with no inherited env vars.

## Acceptance criteria mapping

| AC (ROADMAP Phase 9) | Verification |
|---|---|
| #1 `test_send_typing_transport_error_logs_at_debug` | `test_observability.py` test 2 PASS |
| #2 `test_make_tool_handler_logs_get_platform_failure` (we used DEBUG per CONTEXT) | `test_observability.py` test 4 PASS |
| #3 `test_send_warns_on_dropped_reserved_metadata` | `test_observability.py` test 3 PASS |
| #4 `test_no_api_key_in_any_log_record` | `test_observability.py` test 6 PASS |
| #5 Existing 45+ tests still pass | 58/58 pre-Phase-9 tests still pass |

## Implementation summary

### `src/chatlytics_hermes/adapter.py`
- Module docstring appended with `Log level convention` section
- `send_typing` non-200 + transport-error logs DOWNGRADED warning → DEBUG
- NEW internal `_send_typing_once(chat_id, duration) -> bool` helper that lets
  `_keep_typing` detect first-fire failure without `send_typing` having to raise
- `send()` reserved-metadata key drop now emits a WARNING per dropped key
- `send()` JSON-decode fallback now logs at DEBUG
- `get_chat_info` JSON-decode fallback now logs at DEBUG
- `_standalone_send` JSON-decode fallback now logs at DEBUG
- `_keep_typing` initial fire migrated to `_send_typing_once`; emits WARNING
  on initial-fire failure (preserves Phase 8 06-LOW-02 intent under the new
  quieter `send_typing`)
- `_make_tool_handler` `ctx.get_platform` exception swallow now logs at DEBUG
  with the exception text

### `src/chatlytics_hermes/tools.py`
- `_err_from_response` JSON-decode fallback logs DEBUG
- `_post` JSON-decode fallback logs DEBUG
- `_get` JSON-decode fallback logs DEBUG

### `tests/test_observability.py` (NEW)
- 6 tests covering all observability acceptance criteria

## Files changed

```
M  src/chatlytics_hermes/adapter.py    (+74 -16)
M  src/chatlytics_hermes/tools.py      (+13  -0)
A  tests/test_observability.py         (+306  -0)
```

## Closes

- **02-LOW-01** — silent error paths now logged
- **02-LOW-02** — `send_typing` log volume hardened
- **05-LOW-01** — `_make_tool_handler` get_platform diagnostic
- **LO-11** (GSD review carry-forward) — `send_typing` WARNING → DEBUG

## Invariants preserved

- Hermes pin `>=0.14,<0.15`: unchanged
- 21 tools exactly: unchanged
- httpx outbound, aiohttp embedded inbound only: unchanged
- `{"success": bool, ...}` tool response shape: unchanged
- `chatlytics-hermes` package name: unchanged
- MIT license: unchanged
- v2.0 + v2.1-thru-Phase-8 tests: all PASS (58/58)
- Phase 8 `_keep_typing` first-fire WARNING intent: PRESERVED via `_send_typing_once`
