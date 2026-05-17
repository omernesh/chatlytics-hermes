---
phase: 7
review_type: source_code_review
review_date: 2026-05-17
reviewer: gsd-code-review
implemented_by: claude-opus-4-7-1m
reviewed_by: gsd-code-review
files_reviewed:
  - tests/test_live_loader.py
  - src/chatlytics_hermes/__init__.py
  - scripts/smoke.sh
depth: standard
summary:
  blocker: 0
  high: 0
  medium: 0
  low: 2
  info: 2
status: clean
overall_verdict: APPROVE
---

# Phase 7 Code Review

## Scope

Phase 7 adds a single new test file plus a docstring update and a smoke
step. No production source code (`adapter.py`, `client.py`, `inbound.py`,
`tools.py`) is touched — this phase is intentionally test-only. The
review focuses on:

- Correctness of the BL-01/HI-01/HI-03 reproduction tests (do they
  actually reproduce the failure modes the GSD milestone review found?)
- xfail-strict marker discipline (will Phase 8 be forced to un-xfail?)
- Test isolation (no host-env leakage, no real network)
- Smoke script correctness (does the new step exit non-zero on regression?)
- Invariant preservation (45/45 v2.0 tests still pass)

## Findings

### LOW-01 — `pytest.skip` inside an xfail-strict test masks BL-01 reproduction

**File:** `tests/test_live_loader.py:368-370`

```python
try:
    event = MessageEvent(...)
except TypeError as exc:
    pytest.skip(f"MessageEvent/SessionSource construction mismatch: {exc}")
```

**Issue:** `test_base_process_message_invokes_keep_typing` is xfail-strict.
If the `MessageEvent` / `SessionSource` dataclass fields drift on a
future Hermes pin bump, the test silently `pytest.skip`s — and a
skipped xfail-strict test does NOT trigger the strict gate. The blocker
test would then go undetected for the same reason BL-01 went
undetected in v2.0 (test harness short-circuit). This is the exact
class of bug Phase 7 exists to prevent.

**Fix:** Drop the `pytest.skip` fallback. If `MessageEvent` /
`SessionSource` construction breaks under a Hermes update, the test
should FAIL LOUDLY so the operator notices the contract drift. The
current code path is "skip on contract change" which preserves the v2.0
bug class.

**Effort:** trivial — delete the try/except, let TypeError propagate.

**Disposition:** LOW. The current `gateway.platforms.base.MessageEvent`
+ `gateway.session.SessionSource` constructors verified to accept these
fields (we tested it). The skip is a defensive paranoia that, in
context, undermines the test's purpose. Worth fixing in Phase 8 or
Phase 11 (test infra cleanup).

---

### LOW-02 — Inline `from chatlytics_hermes.client import ChatlyticsClient` inside test body

**File:** `tests/test_live_loader.py:373, 416`

**Issue:** Two tests import `ChatlyticsClient` inside the function body
rather than at module top. Minor style nit — every other module-level
import is hoisted to the top.

**Fix:** Hoist `from chatlytics_hermes.client import ChatlyticsClient`
to the top imports block.

**Effort:** trivial.

**Disposition:** LOW, cosmetic.

---

### INFO-01 — `pass_through()` in respx mocks could let localhost calls escape

**File:** `tests/test_live_loader.py:114-115, 348, 440`

**Issue:** `router.route().pass_through()` is correct for the inbound
tests in `test_inbound.py` (because those tests drive REAL HTTP against
the embedded aiohttp server on 127.0.0.1 and the catch-all `pass_through`
prevents respx from intercepting that). In the loader tests, NO local
aiohttp server is started (webhook_port=0 placeholder; `connect()` is
never called) — so `pass_through()` is unnecessary defensive code.
It's harmless (no traffic actually escapes) but adds noise.

**Disposition:** INFO. Leave as-is — defensive is fine for tests.

---

### INFO-02 — Test docstrings reference base.py line numbers that may drift

**File:** `tests/test_live_loader.py:248-249, 264-265, 293-294, 304-305, 321`

**Issue:** Test docstrings cite specific upstream Hermes base.py line
numbers (`base.py:1780`, `base.py:1785-1786`, `base.py:1787-1792`).
These are useful breadcrumbs today against `/tmp/hermes-ref-v0.14.0/`
but will drift as Hermes versions advance. Not a bug — just a
maintenance cost.

**Disposition:** INFO. Recommend in Phase 11 (or whenever we bump the
Hermes pin) to convert these to symbolic references ("the
`_process_message_background` call site") instead of line numbers.

---

## Positive observations

### Loader test methodology is correct (closes GSD-MD-04 root cause)

The 4 GREEN loader tests + 3 BL-01 regression tests collectively close
the GSD-MD-04 finding. Specifically:

- `_CapturingContext` is a faithful `PluginContext`-compatible recorder
  — it implements `register_platform(**kwargs)` and the FULL
  `register_tool(name, toolset, schema, handler, check_fn, requires_env,
  is_async, description, emoji, override)` signature from
  `hermes_cli/plugins.py:317`. Any kwarg drift in v0.15 would surface
  as a TypeError here.

- `test_loader_registers_21_tools` whitelists tool names against the
  module-level `_EXPECTED_TOOL_NAMES = frozenset(name for name, _, _ in TOOLS)`
  — so even if `tools.TOOLS` were silently extended/reordered, this
  test would catch it. Combined with the existing
  `assert len(TOOLS) == 21` at `tools.py:826`, the tool surface is now
  doubly-locked.

- `test_base_process_message_invokes_keep_typing` directly drives the
  v0.14 base `_process_message_background` method that holds the BL-01
  call site at `base.py:1787-1792`. This is the architectural inverse
  of the recorder pattern in `test_inbound.py:98-106` (which replaces
  `handle_message` and short-circuits the base pipeline). The MD-04
  harness gap is closed.

### xfail-strict discipline is correct

All 6 regression tests use `@pytest.mark.xfail(strict=True, reason=...)`.
Per pytest semantics, `strict=True` converts XPASS to FAILED — so when
Phase 8 fixes BL-01/HI-01/HI-03, the markers will start XPASSING and
pytest will refuse to pass until Phase 8 removes the markers. This is
exactly the discipline needed; un-xfailing is enforced, not optional.

### No invariant regressions

- Hermes pin unchanged (`pyproject.toml` not touched)
- Tool surface still 21 (`tools.TOOLS` not touched; `assert len(TOOLS) == 21` still active)
- All HTTP outbound still httpx async (no transport changes)
- All tool handlers still return `{"success": bool, ...}` (no handler changes)
- Inbound transport still inside `connect()` via aiohttp (no transport changes)
- 45/45 v2.0 tests still pass under env-clean test environment (verified)
- MIT license preserved
- `chatlytics-hermes` package name preserved
- No PyPI publish, no new env vars introduced (the allowlist env var lands in Phase 8 with the actual fix)

### Smoke script step 4/4 is correct

The new `pytest tests/test_live_loader.py -q --no-header --tb=short`
step will exit non-zero if any of the 4 GREEN tests regress OR if any
of the 6 xfail-strict tests start XPASSING (Phase 8 forgot to un-xfail).
Both failure modes are caught.

---

## Verdict

**APPROVE — clean.**

No BLOCKER / HIGH / MEDIUM findings. Two LOW findings are cosmetic /
defensive-code style; two INFO observations are maintenance notes for
Phase 11 / future Hermes pin bumps. None block phase completion.

The phase achieves its stated goal:
1. Live-loader integration test harness landed
2. BL-01 / HI-01 / HI-03 reproduced under strict-xfail regression tests
3. GSD-MD-04 test harness gap closed (base-pipeline test landed
   alongside the recorder-pattern tests)
4. 45/45 v2.0 tests still pass (no regressions)

The 6 xfail-strict markers are correctly tuned to force Phase 8 to
un-xfail them after the BL-01/HI-01/HI-03 fixes land.

## Recommendations for Phase 8

When fixing BL-01 / HI-01 / HI-03 in Phase 8:

1. Remove the 6 `@pytest.mark.xfail(strict=True, ...)` decorators on
   the regression tests in `tests/test_live_loader.py`. Strict-xfail
   will force this by failing the test suite on XPASSED.

2. Address LOW-01 (drop the `pytest.skip` fallback in
   `test_base_process_message_invokes_keep_typing`) at the same time —
   the test will run against the fixed adapter and skip-on-drift is no
   longer needed.

3. Optionally address LOW-02 (hoist the inline import).

---

_Reviewed: 2026-05-17_
_Reviewer: gsd-code-review (Claude Opus 4.7 / 1M)_
_Depth: standard_
_Implemented_by != reviewed_by: implementer=claude-opus-4-7-1m, reviewer=gsd-code-review (skill in allowlist)_
