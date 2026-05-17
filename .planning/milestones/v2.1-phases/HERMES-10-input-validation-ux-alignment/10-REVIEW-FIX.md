---
phase: 10
fix_date: 2026-05-17
review_source: 10-REVIEW.md
implemented_by: claude-opus-4-7-1m
status: findings_fixed
final_tests: "86/86"
fixed_findings:
  - WARNING-01
  - LOW-01
deferred_findings:
  - LOW-02
  - INFO-01
  - INFO-02
  - INFO-03
---

# Phase 10 Review Fix-Pass

Addressed the two correctness findings from `10-REVIEW.md`; deferred the documentation / cosmetic INFO/LOW items.

## Fixes applied

### WARNING-01 — `_validate_webhook_path` whitespace acceptance

**Files:** `src/chatlytics_hermes/adapter.py:99-180`

**Fix:** Removed the implicit `.strip()` from validation flow. Validator now rejects any input where `path != path.strip()` with a clear error message identifying the leading/trailing whitespace. All subsequent checks run against the original `path` (not a stripped copy) so the validator's behavior matches what aiohttp will see at route registration.

The docstring was updated to explicitly document the rule (rule 1 now reads "non-empty string AND no leading/trailing whitespace" with a sentence explaining why aiohttp's `UrlDispatcher` makes this load-bearing).

### LOW-01 — `bool` is a subclass of `int` in `chatlytics_login` session_count

**Files:** `src/chatlytics_hermes/tools.py:903-915`

**Fix:** Tightened the `int` branch to `isinstance(sessions, int) and not isinstance(sessions, bool)` so booleans fall through to the `"unknown"` branch (matching the MCP bundle's `typeof === "number"` which excludes JS booleans). Added an inline comment explaining the Python-vs-JS type-system divergence.

## Regression tests added

`tests/test_validation.py`:

1. `test_init_rejects_webhook_path_with_whitespace_padding` — parametrized over 5 whitespace variants (`"  /webhook"`, `"/webhook  "`, `"  /webhook  "`, `"\t/webhook"`, `"/webhook\n"`); each must raise ValueError.
2. `test_chatlytics_login_session_count_unknown_when_bool` — gateway returns `{"sessions": true, "webhook_registered": true}`; tool returns `success=True` AND `sessions == "unknown"` (NOT `1` or `True`).

## Deferred findings (rationale)

| Finding | Reason for deferral |
|---|---|
| LOW-02 | Cosmetic: `minLength: 1` + pattern `+` redundancy is deliberate defense-in-depth. No bug. Phase 12 docs sweep can add inline comment. |
| INFO-01 | False positive on initial sweep; docstring style already matches Phase 9 convention. |
| INFO-02 | Control-char policy duplication across modules is a cross-module refactor (v2.2+ tech debt). |
| INFO-03 | `chatlytics_login` raw_response key duplication is a feature (diagnostic visibility), not a defect. |

## Verification

```
$ CHATLYTICS_API_KEY= CHATLYTICS_BASE_URL= python -m pytest tests/ -q
86 passed in 22.61s
```

- 65 baseline tests (v2.0 + v2.1 Phases 7-9) — all green
- 21 validation tests in `tests/test_validation.py` (19 original + 2 regression tests from this fix-pass)

## v2.0/v2.1 invariants preserved

- Hermes pin `>=0.14,<0.15`
- 21 tools
- httpx outbound, aiohttp embedded inbound only
- `{"success": bool, ...}` tool response shape
- `chatlytics-hermes` package name + MIT license

## Verdict

All review findings classified as fix-before-ship are closed; deferred findings are documented with rationale. Phase 10 is ready to proceed to Phase 11.
