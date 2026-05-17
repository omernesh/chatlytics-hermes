---
phase: 04-media-ux-polish-cron
plan: 01
status: COMPLETE
commits:
  - 2d870fd plan(hermes-04): write 04-01-PLAN.md for media + UX polish + cron
  - 4ff6e4f feat(hermes-04): client.send_media + upload_file helpers
  - 692f234 feat(hermes-04): 6 media handlers + _keep_typing + cron helpers
  - 9e00adf feat(hermes-04): register() cron hooks + check_fn (02-MED-01 fix)
  - 1c46838 test(hermes-04): media + keep_typing + cron tests (8 ACs + 3 guards)
tests: 33/33 pass (5 HERMES-01 + 8 HERMES-02 + 9 HERMES-03 + 8 media + 3 cron)
---

# HERMES-04 -- Plan 01 Summary

## Per-commit outcome

| Commit | Files | Net LOC |
|--------|-------|---------|
| `4ff6e4f` | `src/chatlytics_hermes/client.py` | +46 |
| `692f234` | `src/chatlytics_hermes/adapter.py` | +501 / -2 |
| `9e00adf` | `src/chatlytics_hermes/adapter.py` (register), `tests/test_register.py` (polarity flip) | +36 / -10 |
| `1c46838` | `tests/test_media.py` (CREATE 300), `tests/test_cron.py` (CREATE 82) | +382 |

## Acceptance criterion results

All 8 HERMES-04 acceptance criteria PASS in clean `docker python:3.13-slim`
against `hermes-agent @ v2026.5.16`. See `04-VERIFICATION.md` for the
verbatim pytest output. Zero regressions to the 22 prior HERMES-01/02/03
tests.

| AC | Test | Result |
|----|------|--------|
| 1 | `test_send_image_url_path` | PASS |
| 2 | `test_send_voice_yields_voice_message` | PASS (voice != audio) |
| 3 | `test_send_video` | PASS |
| 4 | `test_send_document_with_filename` | PASS (mediaType=file + filename) |
| 5 | `test_send_animation` | PASS (mediaType=video per Chatlytics convention) |
| 6 | `test_send_image_file_uploads_local_bytes` | PASS (upload -> URL -> send-media) |
| 7 | `test_keep_typing_heartbeats_every_30s` | PASS (initial + heartbeat + clean cancel) |
| 8 | `test_cron_deliver_env_var_routes_to_standalone_sender` | PASS |

Plus 3 guard tests (`test_keep_typing_swallows_send_typing_errors`,
`test_standalone_send_returns_error_when_env_unset`,
`test_standalone_send_accepts_extra_kwargs`) cover the behaviors that
matter at runtime but are not enumerated in the ROADMAP AC list.

## HERMES-02 + HERMES-03 forward-action-item disposition

| ID | Origin | Disposition |
|----|--------|-------------|
| HERMES-02 MED-01 (`register()` missing `check_fn`) | 02-REVIEW | ADDRESSED in commit `9e00adf` -- added `check_fn=lambda: True` |
| HERMES-02 MED-02 (conftest fixture leak) | 02-REVIEW | DEFER -> HERMES-05 (will revisit when tool registration adds more global state) |
| HERMES-02 LOW-01 (metadata reserved-key warning) | 02-REVIEW | DEFER -- HERMES-04 did not touch `send()` |
| HERMES-02 LOW-02 (send_typing log flood) | 02-REVIEW | DEFER -> HERMES-05/06 -- new `_keep_typing` heartbeat only fires every 30s in production so the log volume is bounded; revisit if observable |
| HERMES-03 LOW-01 (`webhook_path` validation) | 03-REVIEW | DEFER -- HERMES-04 did not touch the webhook init path |
| HERMES-03 LOW-02 (aiohttp body double-read) | 03-REVIEW | DEFER -- documented dependency on cached-body behavior is correct |
| HERMES-03 INFO-02 (`SessionSource.chat_type` heuristic) | 03-REVIEW | DEFER -> HERMES-05 (richer session shape lives next to tool surface) |

## Deviations from the plan

- `tests/test_media.py` ships with 8 tests (7 ACs + 1 heartbeat-error
  guard) rather than the exact 7 the plan tabulated; the heartbeat-error
  guard catches a regression class (background-task exception
  propagation) that isn't in the ROADMAP AC list but is the kind of
  silent bug that asynccontextmanager + background-task patterns regress
  toward. Net: 8 tests in test_media.py + 3 in test_cron.py = 11 new.
- `tests/test_register.py::test_register_does_not_declare_deferred_hooks`
  was renamed to `test_register_declares_hermes_04_hooks` and the
  polarity flipped. Documented inline.

## Scope discipline

- No `ctx.register_tool(...)` calls (HERMES-05).
- No README / CHANGELOG edits (HERMES-06).
- No new runtime dependencies (httpx + aiohttp + PyYAML unchanged).
- No live Chatlytics integration tests.
- All HTTP through `httpx` async (the new multipart upload and the
  `_standalone_send` ephemeral client both use `httpx.AsyncClient`).
