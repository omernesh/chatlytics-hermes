---
phase: 04-media-ux-polish-cron
verified: 2026-05-17
status: PASSED
total_tests: 33
passed: 33
failed: 0
environment: docker python:3.13-slim + hermes-agent@v2026.5.16
---

# HERMES-04 Verification

## Test session output (dockerized clean-room)

```
$ MSYS_NO_PATHCONV=1 docker run --rm -v "/d/docker/chatlytics-hermes-split:/work" -w /work python:3.13-slim sh -c "
    apt-get update -qq && apt-get install -y -qq --no-install-recommends git >/dev/null 2>&1
    pip install --quiet 'hermes-agent @ git+https://github.com/NousResearch/hermes-agent.git@v2026.5.16'
    pip install --quiet -e '.[dev]'
    pytest tests/ -v --tb=short
  "

============================= test session starts ==============================
platform linux -- Python 3.13.13, pytest-9.0.3, pluggy-1.6.0 -- /usr/local/bin/python3.13
cachedir: .pytest_cache
rootdir: /work
configfile: pyproject.toml
plugins: anyio-4.13.0, asyncio-1.3.0, respx-0.23.1
asyncio: mode=Mode.AUTO, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collecting ... collected 33 items

tests/test_cron.py::test_cron_deliver_env_var_routes_to_standalone_sender PASSED [  3%]
tests/test_cron.py::test_standalone_send_returns_error_when_env_unset PASSED [  6%]
tests/test_cron.py::test_standalone_send_accepts_extra_kwargs PASSED     [  9%]
tests/test_inbound.py::test_webhook_text_payload_dispatches_text_message_event PASSED [ 12%]
tests/test_inbound.py::test_webhook_image_payload_dispatches_image_event PASSED [ 15%]
tests/test_inbound.py::test_webhook_audio_payload_dispatches_audio_event PASSED [ 18%]
tests/test_inbound.py::test_webhook_health_returns_200 PASSED            [ 21%]
tests/test_inbound.py::test_connect_starts_aiohttp_server PASSED         [ 24%]
tests/test_inbound.py::test_disconnect_stops_aiohttp_server PASSED       [ 27%]
tests/test_inbound.py::test_connect_is_idempotent PASSED                 [ 30%]
tests/test_inbound.py::test_hmac_verification_rejects_bad_signature PASSED [ 33%]
tests/test_inbound.py::test_hmac_verification_accepts_good_signature PASSED [ 36%]
tests/test_media.py::test_send_image_url_path PASSED                     [ 39%]
tests/test_media.py::test_send_voice_yields_voice_message PASSED         [ 42%]
tests/test_media.py::test_send_video PASSED                              [ 45%]
tests/test_media.py::test_send_document_with_filename PASSED             [ 48%]
tests/test_media.py::test_send_animation PASSED                          [ 51%]
tests/test_media.py::test_send_image_file_uploads_local_bytes PASSED     [ 54%]
tests/test_media.py::test_keep_typing_heartbeats_every_30s PASSED        [ 57%]
tests/test_media.py::test_keep_typing_swallows_send_typing_errors PASSED [ 60%]
tests/test_outbound.py::test_connect_succeeds_on_200_health PASSED       [ 63%]
tests/test_outbound.py::test_connect_raises_on_non_200_health PASSED     [ 66%]
tests/test_outbound.py::test_send_returns_ok_true_on_200 PASSED          [ 69%]
tests/test_outbound.py::test_send_returns_ok_false_on_400 PASSED         [ 72%]
tests/test_outbound.py::test_send_typing_calls_typing_endpoint PASSED    [ 75%]
tests/test_outbound.py::test_get_chat_info_returns_dict PASSED           [ 78%]
tests/test_outbound.py::test_disconnect_closes_client PASSED             [ 81%]
tests/test_outbound.py::test_all_requests_carry_bearer_auth PASSED       [ 84%]
tests/test_register.py::test_register_is_callable PASSED                 [ 87%]
tests/test_register.py::test_register_adds_chatlytics_platform PASSED    [ 90%]
tests/test_register.py::test_register_declares_hermes_04_hooks PASSED    [ 93%]
tests/test_register.py::test_plugin_yaml_is_valid PASSED                 [ 96%]
tests/test_register.py::test_pyproject_declares_hermes_entry_point PASSED [100%]

============================== 33 passed in 1.56s ==============================
```

## HERMES-04 acceptance-criterion mapping

| AC | Test | Status |
|----|------|--------|
| 1. send_image url path -> POST /api/v1/send-media (mediaType=image, mediaUrl, caption?) | `tests/test_media.py::test_send_image_url_path` | PASS |
| 2. send_voice yields mediaType=voice (NOT "audio") | `tests/test_media.py::test_send_voice_yields_voice_message` | PASS |
| 3. send_video yields mediaType=video + caption | `tests/test_media.py::test_send_video` | PASS |
| 4. send_document yields mediaType=file + filename | `tests/test_media.py::test_send_document_with_filename` | PASS |
| 5. send_animation yields mediaType in {video, gif} | `tests/test_media.py::test_send_animation` | PASS (emits "video" per Chatlytics convention) |
| 6. send_image_file reads bytes, uploads via /api/v1/upload, references returned URL in send-media | `tests/test_media.py::test_send_image_file_uploads_local_bytes` | PASS |
| 7. _keep_typing fires immediately + every ~interval seconds; clean cancel on context-manager exit | `tests/test_media.py::test_keep_typing_heartbeats_every_30s` | PASS |
| 8. _standalone_send posts to /api/v1/send with chatId from CHATLYTICS_HOME_CHANNEL | `tests/test_cron.py::test_cron_deliver_env_var_routes_to_standalone_sender` | PASS |

## Regression posture

- All 5 HERMES-01 register/plugin tests: PASS (with the polarity flip
  on `test_register_does_not_declare_deferred_hooks` -> renamed to
  `test_register_declares_hermes_04_hooks` and now asserts the
  HERMES-04 hooks ARE present).
- All 8 HERMES-02 outbound tests: PASS unchanged.
- All 9 HERMES-03 inbound + idempotency tests: PASS unchanged.

## Forward verification (HERMES-05/06)

- `register()` now declares `check_fn=lambda: True`, addressing the
  HERMES-02 MED-01 forward-action item. HERMES-06 release smoke
  against the real `PluginContext.register_platform(...)` should
  succeed without the `TypeError: missing 'check_fn'` predicted in
  the 02-REVIEW.
- The `_standalone_send` hook is in place; HERMES-06 smoke can wire
  `hermes cron` with `deliver=chatlytics` and verify out-of-process
  delivery against a real (or staging) Chatlytics gateway.
