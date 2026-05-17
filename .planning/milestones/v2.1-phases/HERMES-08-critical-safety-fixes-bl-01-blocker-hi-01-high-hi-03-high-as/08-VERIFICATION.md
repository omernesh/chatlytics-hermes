---
phase: 8
status: passed
verified_at: 2026-05-17
verified_by: claude-opus-4-7-1m
implemented_by: claude-opus-4-7-1m
tests_total: 58
tests_passing: 58
tests_xfailed_strict: 0
tests_failing: 0
v2_regressions: 0
commits:
  - bd100bb docs(08): fix-locked context — BL-01 + HI-01 + HI-03 + async lifecycle
  - 336699b docs(08): plan 1/1 — BL-01 + HI-01 + HI-03 + MD-01 + concurrency regression
  - fbfe5fd fix(08): BL-01 + HI-01 + HI-03 + MD-01 critical safety fixes
  - 1df649d test(08): un-xfail BL-01/HI-01/HI-03 regressions + migrate _typing_scope + concurrency
  - be1629d docs(08): document CHATLYTICS_UPLOAD_ALLOWED_ROOTS allowlist (HI-01)
acceptance_criteria_met:
  ac_1_keep_typing_is_coroutine: passed
  ac_2_typing_scope_async_cm: passed
  ac_3_resolve_media_url_allowlist: passed
  ac_4_send_image_animation_kwargs: passed
  ac_5_coerce_success_payload_helper: passed
  ac_6_phase_7_xfail_markers_removed: passed
  ac_7_keep_typing_heartbeats_still_passes: passed
  ac_8_test_concurrency_file_added: passed
  ac_9_readme_documents_upload_allowed_roots: passed
  ac_10_v2_baseline_45_pass_no_regressions: passed
gaps_found: []
invariants_preserved:
  hermes_pin_0_14: true
  tool_surface_21: true
  httpx_async_outbound: true
  aiohttp_inbound_only: true
  tool_success_shape: true
  package_name: chatlytics-hermes
  license: MIT
  v2_45_tests_pass: true
---

# Phase 8 Verification

## Status: PASSED

All BLOCKER + HIGH findings from the v2.0 milestone code review are fixed
under regression tests. The 6 xfail-strict markers Phase 7 wrote are
removed; the corresponding tests now PASS. Three new concurrency tests
lock the v2.0 `asyncio.to_thread` fix + the `_keep_typing` lifecycle
fixes under regression coverage.

## Test results

Run from host with `CHATLYTICS_*` env vars cleared (canonical test-env
state, matches `scripts/smoke.sh` docker container):

```
env -u CHATLYTICS_API_KEY -u CHATLYTICS_BASE_URL -u CHATLYTICS_HOME_CHANNEL \
    -u CHATLYTICS_WEBHOOK_HOST -u CHATLYTICS_WEBHOOK_PORT \
    -u CHATLYTICS_WEBHOOK_PATH -u CHATLYTICS_WEBHOOK_SECRET \
    -u CHATLYTICS_ACCOUNT_ID -u CHATLYTICS_UPLOAD_ALLOWED_ROOTS \
    python -m pytest tests/ -q --no-header

..........................................................               [100%]
58 passed in 22.76s
```

Breakdown:
- 45 v2.0 baseline tests — all passing (no regressions)
- 4 GREEN loader tests from Phase 7 — all passing
- 6 previously-xfail-strict tests from Phase 7 — now PASS:
  - `test_keep_typing_is_a_coroutine` (BL-01)
  - `test_bl01_keep_typing_accepts_metadata_kwarg` (BL-01)
  - `test_base_process_message_invokes_keep_typing` (BL-01 / GSD-MD-04)
  - `test_hi01_send_file_rejects_path_outside_allowed_roots` (HI-01)
  - `test_hi03_send_image_accepts_unknown_kwargs` (HI-03)
  - `test_hi03_send_animation_accepts_unknown_kwargs` (HI-03)
- 3 NEW Phase 8 concurrency tests — all passing:
  - `test_resolve_media_url_off_event_loop`
  - `test_keep_typing_initial_fire_does_not_block`
  - `test_keep_typing_first_fire_failure_logs_warning`

## Files changed

- MODIFY `src/chatlytics_hermes/adapter.py` (+220 LOC):
  - NEW `_coerce_success_payload` module helper (MD-01)
  - `__init__` reads `CHATLYTICS_UPLOAD_ALLOWED_ROOTS` env var into
    `self.upload_allowed_roots: List[Path]` (HI-01)
  - `_resolve_media_url` local-file branch rejects paths outside the
    allowlist BEFORE opening the file (HI-01)
  - `_send_media_payload` adds explicit `PermissionError` catch with a
    distinct error message ordered before generic `OSError`
  - `_make_send_result` uses `_coerce_success_payload` (MD-01)
  - `send_image` + `send_animation` add `**kwargs: Any` (HI-03)
  - `_keep_typing` rewritten as plain `async def` coroutine matching
    upstream base signature `(chat_id, interval=30.0, metadata=None,
    stop_event=None)` (BL-01); initial fire inside the coroutine
    (04-LOW-03); WARNING level on first-fire failure (06-LOW-02)
  - NEW `_typing_scope` `@asynccontextmanager` preserves in-plugin
    tool-handler ergonomics (BL-01 dual-surface)
  - `_standalone_send` uses `_coerce_success_payload` (MD-01)
- MODIFY `src/chatlytics_hermes/tools.py`:
  - `_post` / `_get` delegate success derivation to
    `_coerce_success_payload` so `200 {"success": false}` correctly
    propagates as failure (MD-01)
- MODIFY `tests/test_live_loader.py`:
  - Remove 6 `@pytest.mark.xfail(strict=True, ...)` decorators
- MODIFY `tests/test_media.py`:
  - Migrate `_keep_typing` test sites to `_typing_scope`
  - `adapter` fixture now configures `upload_allowed_roots` =
    `tempfile.gettempdir()` so local-file tests work under default-deny
- CREATE `tests/test_concurrency.py` (~180 LOC, 3 tests)
- MODIFY `README.md`: new "Security: filePath upload allowlist" section
  + new row in env-var table

## Invariants preserved (audited)

- Hermes pin still `>=0.14,<0.15` (pyproject.toml unchanged)
- Tool surface still exactly 21 (`tools.TOOLS` unchanged; assertion still active)
- All HTTP outbound still through httpx async
- Inbound transport still inside `connect()` via aiohttp
- All tool handlers still return `{"success": bool, ...}` shape
- 45/45 v2.0 baseline tests still pass (+ 10 Phase 7 tests + 3 Phase 8 tests = 58 total)
- `chatlytics-hermes` package name preserved
- MIT license preserved
- NO PyPI publish; NO new tool added; NO new platform added

## Notes

- The v2.0 inbound recorder pattern at `test_inbound.py:98-106` is
  still in place (it serves legitimate webhook-decode assertions).
  Phase 7 added the base-pipeline test alongside it; Phase 8's
  un-xfail makes that test green.
- The HI-01 allowlist is default-deny: when the env var is unset,
  EVERY local-file upload fails with a clear error. This is the
  recommended default for production. Operators must consciously
  opt in to local-file uploads.
- The Chatlytics gateway's actual upload endpoint behavior on
  malformed `success` payloads (200 + `{success: false}`) is now
  uniformly mapped to `{"success": false, "error": "..."}` across all
  three call sites (`_make_send_result`, `_standalone_send`,
  `tools._post`/`_get`).

## Next phase

HERMES-09: Observability + log hygiene. Phase 8 already moved
`_keep_typing` first-fire failure to WARNING (06-LOW-02); Phase 9
sweeps the remaining `logger.warning` / `logger.error` sites for
level-appropriateness and adds diagnostic logs to silent error paths.
