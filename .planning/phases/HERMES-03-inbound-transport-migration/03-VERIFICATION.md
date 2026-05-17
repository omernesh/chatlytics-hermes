---
phase: 03-inbound-transport-migration
verified: 2026-05-17
status: PASSED
total_tests: 22
passed: 22
failed: 0
environment: docker python:3.13-slim + hermes-agent@v2026.5.16
---

## Post-review verification (after MED-01 fix)

```
collected 22 items

tests/test_inbound.py::test_webhook_text_payload_dispatches_text_message_event PASSED [  4%]
tests/test_inbound.py::test_webhook_image_payload_dispatches_image_event PASSED [  9%]
tests/test_inbound.py::test_webhook_audio_payload_dispatches_audio_event PASSED [ 13%]
tests/test_inbound.py::test_webhook_health_returns_200 PASSED            [ 18%]
tests/test_inbound.py::test_connect_starts_aiohttp_server PASSED         [ 22%]
tests/test_inbound.py::test_disconnect_stops_aiohttp_server PASSED       [ 27%]
tests/test_inbound.py::test_connect_is_idempotent PASSED                 [ 31%]
tests/test_inbound.py::test_hmac_verification_rejects_bad_signature PASSED [ 36%]
tests/test_inbound.py::test_hmac_verification_accepts_good_signature PASSED [ 40%]
tests/test_outbound.py::test_connect_succeeds_on_200_health PASSED       [ 45%]
tests/test_outbound.py::test_connect_raises_on_non_200_health PASSED     [ 50%]
tests/test_outbound.py::test_send_returns_ok_true_on_200 PASSED          [ 54%]
tests/test_outbound.py::test_send_returns_ok_false_on_400 PASSED         [ 59%]
tests/test_outbound.py::test_send_typing_calls_typing_endpoint PASSED    [ 63%]
tests/test_outbound.py::test_get_chat_info_returns_dict PASSED           [ 68%]
tests/test_outbound.py::test_disconnect_closes_client PASSED             [ 72%]
tests/test_outbound.py::test_all_requests_carry_bearer_auth PASSED       [ 77%]
tests/test_register.py::test_register_is_callable PASSED                 [ 81%]
tests/test_register.py::test_register_adds_chatlytics_platform PASSED    [ 86%]
tests/test_register.py::test_register_does_not_declare_deferred_hooks PASSED [ 90%]
tests/test_register.py::test_plugin_yaml_is_valid PASSED                 [ 95%]
tests/test_register.py::test_pyproject_declares_hermes_entry_point PASSED [100%]

============================== 22 passed in 0.95s ==============================
```

22/22 after addressing MED-01 (`connect()` idempotency guard) plus
`test_connect_is_idempotent` regression test.

# HERMES-03 Verification

## Initial verification (8/8 HERMES-03 ACs as filed)

## Test session output (dockerized clean-room)

```
$ MSYS_NO_PATHCONV=1 docker run --rm -v "/d/docker/chatlytics-hermes-split:/work" -w /work python:3.13-slim sh -c "
    apt-get update -qq && apt-get install -y -qq --no-install-recommends git
    pip install --quiet 'hermes-agent @ git+https://github.com/NousResearch/hermes-agent.git@v2026.5.16'
    pip install --quiet -e '.[dev]'
    pytest tests/ -v
  "

============================= test session starts ==============================
platform linux -- Python 3.13.13, pytest-9.0.3, pluggy-1.6.0 -- /usr/local/bin/python3.13
cachedir: .pytest_cache
rootdir: /work
configfile: pyproject.toml
plugins: anyio-4.13.0, asyncio-1.3.0, respx-0.23.1
asyncio: mode=Mode.AUTO, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collecting ... collected 21 items

tests/test_inbound.py::test_webhook_text_payload_dispatches_text_message_event PASSED [  4%]
tests/test_inbound.py::test_webhook_image_payload_dispatches_image_event PASSED [  9%]
tests/test_inbound.py::test_webhook_audio_payload_dispatches_audio_event PASSED [ 14%]
tests/test_inbound.py::test_webhook_health_returns_200 PASSED            [ 19%]
tests/test_inbound.py::test_connect_starts_aiohttp_server PASSED         [ 23%]
tests/test_inbound.py::test_disconnect_stops_aiohttp_server PASSED       [ 28%]
tests/test_inbound.py::test_hmac_verification_rejects_bad_signature PASSED [ 33%]
tests/test_inbound.py::test_hmac_verification_accepts_good_signature PASSED [ 38%]
tests/test_outbound.py::test_connect_succeeds_on_200_health PASSED       [ 42%]
tests/test_outbound.py::test_connect_raises_on_non_200_health PASSED     [ 47%]
tests/test_outbound.py::test_send_returns_ok_true_on_200 PASSED          [ 52%]
tests/test_outbound.py::test_send_returns_ok_false_on_400 PASSED         [ 57%]
tests/test_outbound.py::test_send_typing_calls_typing_endpoint PASSED    [ 61%]
tests/test_outbound.py::test_get_chat_info_returns_dict PASSED           [ 66%]
tests/test_outbound.py::test_disconnect_closes_client PASSED             [ 71%]
tests/test_outbound.py::test_all_requests_carry_bearer_auth PASSED       [ 76%]
tests/test_register.py::test_register_is_callable PASSED                 [ 80%]
tests/test_register.py::test_register_adds_chatlytics_platform PASSED    [ 85%]
tests/test_register.py::test_register_does_not_declare_deferred_hooks PASSED [ 90%]
tests/test_register.py::test_plugin_yaml_is_valid PASSED                 [ 95%]
tests/test_register.py::test_pyproject_declares_hermes_entry_point PASSED [100%]

============================== 21 passed in 0.93s ==============================
```

No DeprecationWarnings, no ResourceWarnings, no asyncio
"Task was destroyed but it is pending" messages. All 21/21 green
(5 HERMES-01 + 8 HERMES-02 + 8 HERMES-03).

## Acceptance criterion results

| ID | Criterion | Test | Result |
|----|-----------|------|--------|
| AC-1 | Text webhook -> `MessageType.TEXT` dispatched | `test_webhook_text_payload_dispatches_text_message_event` | PASS |
| AC-2 | Image webhook (`mediaType="image"`) -> `MessageType.PHOTO` | `test_webhook_image_payload_dispatches_image_event` | PASS |
| AC-3 | Audio webhook (`mediaType="audio"`) -> `MessageType.AUDIO` | `test_webhook_audio_payload_dispatches_audio_event` | PASS |
| AC-4 | `GET /health` returns 200 + `{"status": "ok"}` | `test_webhook_health_returns_200` | PASS |
| AC-5 | `connect()` starts aiohttp server (TCP socket connects) | `test_connect_starts_aiohttp_server` | PASS |
| AC-6 | `disconnect()` stops aiohttp server (ConnectionRefused) | `test_disconnect_stops_aiohttp_server` | PASS |
| AC-7 | HMAC mismatch -> 401, no dispatch | `test_hmac_verification_rejects_bad_signature` | PASS |
| AC-8 | HMAC match -> dispatched normally | `test_hmac_verification_accepts_good_signature` | PASS |

## Regression check (no prior tests broken)

All 13 HERMES-01/02 tests still pass (visible in the verbatim output
above). The `register()` block is byte-for-byte unchanged so the
HERMES-01 contract tests are unaffected, and the outbound httpx
surface is untouched.

## HERMES-02 forward-action items disposition

| ID | Concern | Disposition |
|----|---------|-------------|
| MED-01 | `register()` missing `check_fn` | DEFER -> HERMES-05 (per phase context) -- HERMES-03 explicitly does not touch `register()` |
| MED-02 | `conftest.py` session fixture has no teardown | DEFER -- HERMES-03 inbound tests do not exercise a second `register()` path, so the cross-test pollution risk has not materialized |
| LOW-01 | `send()` silently drops metadata keys named like reserved fields | DEFER -- HERMES-03 does not touch `send()` |
| LOW-02 | `send_typing` log-flood risk | DEFER -> HERMES-04 (`_keep_typing` heartbeat phase will address) |
| LOW-03 | (closed in HERMES-02) | n/a |
| LOW-04 | (closed in HERMES-02) | n/a |

## Deviations from plan

One mid-execution fix:

- **respx fixture pass-through.** The initial `mock_health` fixture used
  `assert_all_mocked=False` to let local-aiohttp traffic through, but
  respx's pass-through behavior in that mode does not actually issue
  real requests -- it short-circuits with empty 200 responses. The
  fixture was updated to register an explicit
  `router.route().pass_through()` catch-all that delegates unmatched
  URLs to the real network stack. This is documented in the commit
  message of `448d11f`. All 21 tests pass after this fix; no other
  source files were touched.

No deviations from the plan's source-file shape.
