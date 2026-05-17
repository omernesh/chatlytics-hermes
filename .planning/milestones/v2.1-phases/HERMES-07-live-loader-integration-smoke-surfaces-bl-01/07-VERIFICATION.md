---
phase: 7
status: passed
verified_at: 2026-05-17
verified_by: claude-opus-4-7-1m
implemented_by: claude-opus-4-7-1m
tests_total: 55
tests_passing: 49
tests_xfailed_strict: 6
tests_failing: 0
v2_regressions: 0
commits:
  - 0f0ce8d docs(07): infra-skip context (live-loader integration test scope locked)
  - 8798f4f docs(07): plan 1/1 live-loader test harness + xfail regressions for BL-01/HI-01/HI-03
  - 055f3ce feat(07): live-loader integration smoke + BL-01/HI-01/HI-03 xfail regressions
acceptance_criteria_met:
  ac_1_loader_registers_platform: passed
  ac_2_loader_registers_21_tools: passed
  ac_3_loader_handles_missing_env: passed
  ac_4_loader_isolated_from_real_chatlytics: passed
  ac_5_base_handle_message_invokes_keep_typing: xfailed_strict (expected — Phase 8 un-xfails)
  ac_6_keep_typing_is_a_coroutine: xfailed_strict (expected — Phase 8 un-xfails)
  ac_7_smoke_sh_live_loader_step: implemented (in-container run is the host-env-clean ceiling)
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

# Phase 7 Verification

## Status: PASSED

All 7 ROADMAP acceptance criteria for HERMES-07 are met. The 4 GREEN
loader tests pass. The 6 xfail-strict regression tests (BL-01x3,
HI-01x1, HI-03x2) all xfail as expected — they LOCK the failure mode
under test so Phase 8 cannot accidentally fix BL-01/HI-01/HI-03
without un-xfailing the markers (strict=True forces this).

## Test results

Run from host with `CHATLYTICS_*` env vars cleared (the canonical
test-env state, matching `scripts/smoke.sh` docker container):

```
env -u CHATLYTICS_API_KEY -u CHATLYTICS_BASE_URL -u CHATLYTICS_HOME_CHANNEL \
    -u CHATLYTICS_WEBHOOK_HOST -u CHATLYTICS_WEBHOOK_PORT \
    -u CHATLYTICS_WEBHOOK_PATH -u CHATLYTICS_WEBHOOK_SECRET \
    -u CHATLYTICS_ACCOUNT_ID python -m pytest tests/ -q --no-header

................xxxxxx.................................                  [100%]
49 passed, 6 xfailed in 22.84s
```

Breakdown:
- 45 v2.0 baseline tests — all passing (no regressions)
- 4 new GREEN tests in `tests/test_live_loader.py`:
  - `test_loader_registers_chatlytics_platform`
  - `test_loader_registers_21_tools`
  - `test_loader_handles_missing_env_vars_gracefully`
  - `test_loader_isolated_from_real_chatlytics`
- 6 new XFAIL-STRICT regression tests in `tests/test_live_loader.py`:
  - `test_keep_typing_is_a_coroutine` (BL-01)
  - `test_bl01_keep_typing_accepts_metadata_kwarg` (BL-01)
  - `test_base_process_message_invokes_keep_typing` (BL-01 — closes GSD-MD-04)
  - `test_hi01_send_file_rejects_path_outside_allowed_roots` (HI-01)
  - `test_hi03_send_image_accepts_unknown_kwargs` (HI-03)
  - `test_hi03_send_animation_accepts_unknown_kwargs` (HI-03)

## Files changed

- CREATE `tests/test_live_loader.py` (516 LOC)
- MODIFY `src/chatlytics_hermes/__init__.py` (docstring-only)
- MODIFY `scripts/smoke.sh` (step 4/4 added)
- CREATE `.planning/phases/HERMES-07-*/07-CONTEXT.md`
- CREATE `.planning/phases/HERMES-07-*/07-PLAN-1-live-loader-test-harness.md`

## Invariants preserved (audited)

- Hermes pin still `>=0.14,<0.15` (pyproject.toml unchanged)
- Tool surface still exactly 21 (`tools.TOOLS` unchanged; assertion still active)
- All HTTP outbound still through httpx async (no transport changes)
- Inbound transport still inside `connect()` via aiohttp (no transport changes)
- All tool handlers still return `{"success": bool, ...}` shape (no handler changes)
- 45/45 v2.0 tests still pass (asserted above)
- Inbound recorder pattern at `test_inbound.py:98-106` still in place (Phase 7 ADDS a base-pipeline test alongside it; does NOT remove the recorder pattern that other tests depend on)
- `chatlytics-hermes` package name preserved
- MIT license preserved
- NO PyPI publish, NO new env vars (allowlist env var lands in Phase 8 with the fix)

## Notes

- The host-environment pre-existing test fragility (test_outbound.py
  assumes env-clean Bearer header) is NOT a Phase 7 regression — same
  failure reproduces on `git stash`. Smoke runs in docker container
  where the host env doesn't leak. Test infra cleanup for this is
  Phase 11's responsibility.
- The Hermes v0.14 reference checkout at `/tmp/hermes-ref-v0.14.0/`
  confirmed the BL-01 call site at `gateway/platforms/base.py:1787-1792`
  (line numbers differ slightly from the GSD review's 3045-3057 — that
  was the line-of-text count via different parser). The structural
  finding is correct: base `_process_message_background` calls
  `asyncio.create_task(self._keep_typing(chat_id, metadata=...))`.
- The 6 xfail-strict markers WILL force Phase 8 to un-xfail them
  after the fixes land. If Phase 8 forgets to remove the markers,
  pytest will report XPASSED failures (strict=True converts xpass to
  failure). This is intentional — it forces explicit acknowledgment
  that the BLOCKER/HIGH was fixed.

## Next phase

HERMES-08: Critical safety fixes (BL-01 BLOCKER + HI-01 HIGH + HI-03
HIGH) + async lifecycle hardening. The 6 xfail-strict markers locked
here MUST be un-xfailed in Phase 8.
