---
phase: 13
status: passed
tests_total: 98
tests_passed: 98
files_changed: 5
commits: 6
implemented_by: gsd-execute-phase
---

# HERMES-13 — Verification

## Outcome

**PASSED.** 88 baseline + 10 new tests = 98 passing. Zero regressions
outside the explicitly-updated `get_chat_info` shape contract.

## Test results

```
98 passed in 25.50s
```

## Acceptance criteria (from ROADMAP)

1. `pytest tests/ -q` — 88 baseline + N new tests; zero regressions
   outside the explicitly-updated `get_chat_info` tests. **PASS** (98/98).
2. `chatlytics_get_chat_info(chatId="<known>")` returns
   `{success: true, chat: {...}}`. **PASS** —
   `test_tool_wrapper_returns_success_true_with_chat_on_found`.
3. `chatlytics_get_chat_info(chatId="<unknown>")` returns
   `{success: true, chat: null}`. **PASS** —
   `test_tool_wrapper_returns_success_true_with_null_on_empty`
   (legitimate empty branch: HTTP 200 + JSON `null`).
4. `chatlytics_get_chat_info(chatId="<causes-500>")` returns
   `{success: false, error: "<msg>", _error: "<code>"}`. **PASS** —
   `test_tool_wrapper_returns_error_with_underscore_error_on_500`
   (`_error == "server_error"`), plus
   `test_tool_wrapper_returns_validation_error_on_404`
   (`_error == "validation_error"`).
5. CHANGELOG has BREAKING entry. **PASS** — `## [Unreleased] / ### Breaking`
   block in CHANGELOG.md.

## Invariants preserved

- `assert len(TOOLS) == 21` — still satisfied (wrapper exposed as
  module-level coroutine, not in TOOLS tuple).
- Hermes pin `>=0.14,<0.15` — unchanged.
- All HTTP outbound via `httpx` — unchanged.
- v2.1 baseline tests (78 tests outside `get_chat_info`) — all still
  green.

## Files changed

| File | Change kind |
|------|-------------|
| `src/chatlytics_hermes/adapter.py` | Added `ChatlyticsLookupError`; rewrote `get_chat_info` with three-way contract. |
| `src/chatlytics_hermes/tools.py` | Added module-level `chatlytics_get_chat_info` wrapper (NOT added to TOOLS). |
| `tests/test_outbound.py` | Annotated AC-6 with v3.0 cross-ref; added 10 new branch-coverage tests. |
| `tests/test_validation.py` | Docstring updated to mark v2.1 semantics as superseded. |
| `CHANGELOG.md` | `## [Unreleased] / ### Breaking` entry. |

## Commits

| SHA | Subject |
|-----|---------|
| `ef2a905` | docs(13): infra-skip context for get_chat_info _error sentinel |
| `9a782ab` | docs(13): plan get_chat_info three-way contract with _error sentinel |
| `4f9b298` | feat(13)!: get_chat_info three-way contract with ChatlyticsLookupError |
| `0c55275` | feat(13): add chatlytics_get_chat_info tool-layer wrapper |
| `46bf8bf` | test(13): branch-coverage tests for get_chat_info _error contract |
| `a847f44` | docs(13)!: changelog Unreleased breaking entry for get_chat_info |

## Next phase

HERMES-14 — Strict JID regex on `chatId` schemas (depends on this
phase's error-shape uniformity).
