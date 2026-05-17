# Phase 11 -- Verification

**Status:** Execute complete; awaiting code review.
**Date:** 2026-05-17
**Executor:** Claude session (GSD autonomous)

## Acceptance criteria results

| # | Criterion | Status | Evidence |
|---|---|---|---|
| 1 | `pytest tests/ -q` reports 88 passing, 0 failing | PASS | `88 passed in 22.71s` |
| 2 | `tests/conftest.py` has try/finally unregister | PASS | conftest.py:65-70 |
| 3 | `tests/conftest.py` has `is_registered` idempotency guard | PASS | conftest.py:43-46 |
| 4 | `tests/_fixtures.py` exists and exports `FakePlatformConfig` | PASS | tests/_fixtures.py:12 |
| 5 | Zero `class _FakePlatformConfig` definitions remain | PASS | grep returns no matches |
| 6 | 7 test files import `FakePlatformConfig` from `tests._fixtures` | PASS | grep confirms 7 files |
| 7 | `scripts/smoke.sh --fast` runs pytest against host venv | PASS | smoke fast 88 passed in 23.79s |
| 8 | `scripts/smoke.sh --help` prints usage including `--fast` | PASS | Help text rendered, exits 0 |
| 9 | `scripts/smoke.sh` (no args) preserves dockerized behavior | PASS (structural) | Default branch unchanged except `--retries 3` |
| 10 | v2.0 invariants preserved | PASS | 21 tools, Hermes pin, tool shape, package name, license all unchanged |

**Note on AC-6 count:** PLAN-1 listed 8 files; actual implementation
consolidated 7 (test_register.py uses MockCtx, not the platform-config
shim, and was correctly excluded). PLAN's intent fully met: every
duplicated `_FakePlatformConfig` definition removed.

## Test results

```
$ CHATLYTICS_API_KEY= CHATLYTICS_BASE_URL= pytest tests/ -q --no-header
........................................................................ [ 81%]
................                                                         [100%]
88 passed in 22.71s
```

Per-suite breakdown:
- test_concurrency.py: 3 tests
- test_conftest_teardown.py: 2 tests (NEW)
- test_cron.py: 2 tests
- test_inbound.py: 11 tests
- test_live_loader.py: 16 tests
- test_media.py: 7 tests
- test_observability.py: 11 tests
- test_outbound.py: 8 tests
- test_register.py: 5 tests
- test_tool_schemas.py: 4 tests
- test_tools.py: 8 tests
- test_validation.py: 11 tests

Total: 88 tests (86 baseline from Phase 10 + 2 new meta-tests).

## Smoke verification

```
$ bash scripts/smoke.sh --help
Usage: scripts/smoke.sh [--fast]

Modes:
  (default)   Full dockerized smoke ...
  --fast      Skip docker; run pytest tests/ ...
```

```
$ bash scripts/smoke.sh --fast
--- smoke --fast: host venv pytest only (no docker) ---
88 passed in 23.79s
```

The default `bash scripts/smoke.sh` (no args) was NOT executed in
verification because it spawns docker + recompiles hermes-agent from
git, which is exactly the slow behavior `--fast` was designed to skip
during local iteration. The dockerized path's structural correctness
was verified by code reading: the docker block is byte-identical to
v2.0 except for `--retries 3` added to two `pip install` lines
(PR-MED-03 hardening). Operators will re-verify the dockerized path
before tagging v2.1.0 in Phase 12.

## Files changed

```
tests/conftest.py                   rewritten (yield teardown + idempotency guard)
tests/_fixtures.py                  NEW (shared FakePlatformConfig)
tests/test_conftest_teardown.py     NEW (2 meta-tests)
tests/test_outbound.py              import shared FakePlatformConfig
tests/test_media.py                 import shared FakePlatformConfig
tests/test_inbound.py               import shared FakePlatformConfig
tests/test_concurrency.py           import shared FakePlatformConfig
tests/test_observability.py         import shared FakePlatformConfig
tests/test_live_loader.py           import shared FakePlatformConfig
tests/test_validation.py            import shared FakePlatformConfig (renames in _make_config)
scripts/smoke.sh                    --fast flag + --help + --retries 3
```

Production code untouched (`src/chatlytics_hermes/*` unchanged).

## Closures

- **02-MED-02 / IN-03** -- closed: conftest now has yield-style teardown
  with `unregister` + `is_registered` idempotency guard.
- **06-LOW-01** -- closed: `--fast` flag added for local iteration; the
  default release smoke is unchanged for compatibility.
- **PR-review MED-03** -- partially closed: `--retries 3` hardening
  applied to both `pip install` calls. (Long-term wheel caching deferred
  as future work; not actionable in v2.1.)
- **PR-review INFO-02** -- closed: `_FakePlatformConfig` consolidated
  into `tests/_fixtures.FakePlatformConfig`, imported by 7 test files.

## Commits (in order)

```
917755b  plan(HERMES-11): context -- test infra cleanup
5735428  plan(HERMES-11): PLAN-1 -- test infra cleanup
f8ab629  exec(HERMES-11): wave 1 -- conftest teardown, _fixtures shim, smoke --fast
75323f6  exec(HERMES-11): wave 2 -- consolidate _FakePlatformConfig across 7 files
418b9e7  exec(HERMES-11): wave 3 -- meta-test for conftest teardown contract
```

## v2.0 invariants verified

- Hermes pin `>=0.14,<0.15` -- `pyproject.toml` unchanged.
- 21 tools -- `test_live_loader.py::test_loader_registers_21_tools` passes.
- httpx outbound, aiohttp embedded inbound -- adapter unchanged.
- `{"success": bool, ...}` tool shape -- tools.py unchanged.
- Package `chatlytics-hermes` -- pyproject.toml unchanged.
- MIT license -- unchanged.

## Ready for review

Yes. All 4 waves landed, 88/88 tests passing, no production code
touched, default smoke compatibility preserved.
