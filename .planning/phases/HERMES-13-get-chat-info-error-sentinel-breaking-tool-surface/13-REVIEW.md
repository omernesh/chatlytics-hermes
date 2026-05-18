---
phase: 13
review_status: clean
review_verdict: CLEAN
blocker_count: 0
high_count: 0
medium_count: 0
low_count: 1
info_count: 2
implemented_by: gsd-execute-phase
reviewed_by: gsd-code-review
review_depth: standard
---

# HERMES-13 — Code Review

## Scope

Files reviewed (changed in Phase 13 commits `4f9b298`, `0c55275`, `46bf8bf`, `a847f44`):

- `src/chatlytics_hermes/adapter.py` — new `ChatlyticsLookupError` + rewritten `get_chat_info`
- `src/chatlytics_hermes/tools.py` — new `chatlytics_get_chat_info` module-level wrapper
- `tests/test_outbound.py` — AC-6 annotation + 10 new branch-coverage tests
- `tests/test_validation.py` — docstring update only
- `CHANGELOG.md` — `[Unreleased] / Breaking` entry

## Summary

**BLOCKER: 0  HIGH: 0  MED: 0  LOW: 1  INFO: 2** — verdict **CLEAN**.

Implementation matches the locked phase brief and the plan exactly.
The three-way contract is correctly encoded; error codes are correctly
classified; 404-disambiguation rule is correctly applied (gateway 404
→ `validation_error`, not legitimate empty). 10 new tests cover every
branch including the 404 disambiguation case and both NEW assertions
required by the phase brief (null branch + `_error` branch).

No regressions in the v2.1 baseline. `assert len(TOOLS) == 21`
invariant preserved (wrapper exposed module-level, NOT registered).

## Findings

### LOW-01 — Pre-existing test fragility surfaced when env vars are set

**Location:** `tests/test_outbound.py` (fixture `adapter`), inherits
from `ChatlyticsAdapter.__init__` env-var precedence at
`src/chatlytics_hermes/adapter.py:298-299`.

**Observation:** When `CHATLYTICS_API_KEY` / `CHATLYTICS_BASE_URL` are
set in the shell environment, the adapter constructor's
`os.getenv(...) or extra.get(...)` precedence overrides the test
fixture's mocked credentials, causing Bearer-token assertions in
`test_outbound.py` to fail. Reproduced on this session's first pytest
run before unsetting the env vars. The 10 new HERMES-13 tests inherit
this fragility because they use the same `adapter` fixture.

**Why not a phase finding:** Pre-existing v2.0 behavior;
HERMES-13 did not introduce it. The same fragility affects the
v2.1 baseline suite identically.

**Recommendation:** Defer to Phase 18 cosmetics sweep — patch the
`adapter` fixture in `test_outbound.py` (and equivalents in
`test_media.py`, `test_validation.py`) to use `monkeypatch.delenv` on
the `CHATLYTICS_*` env-var set before constructing the adapter. Costs
~5 LOC per fixture, zero behavior change. Tracking via this REVIEW.md
so the carry-forward is visible in Phase 18 inventory.

**Severity rationale:** LOW because it does not affect production
correctness, only developer-experience reliability when the dev shell
has Chatlytics credentials exported. The dockerized smoke suite
(`scripts/smoke.sh`) is unaffected (clean venv).

### INFO-01 — Tool wrapper `client` param is unused

**Location:** `src/chatlytics_hermes/tools.py:974-1018`,
`chatlytics_get_chat_info(client, *, adapter=None, chatId)`.

**Observation:** The `client` positional parameter is declared for
signature parity with the other handlers in this module (per the
docstring note at lines 994-997) but is never read inside the body
— the lookup goes through `adapter.get_chat_info(chatId)`, which
internally uses `adapter.client`. Static analyzers may flag this as
an unused parameter.

**Why INFO not LOW:** The docstring explicitly justifies the
parameter. Renaming to `_client` would suppress lint warnings but
would also break the parity claim if a future v3.1 registers this
wrapper in `TOOLS` (where the `_make_tool_handler` infrastructure
would route `client` as the first positional).

**Recommendation:** No action. The decision is documented; lint
warnings are acceptable in exchange for forward-compat parity. If
the v3.1 minor registers the wrapper, the parameter becomes load-
bearing.

### INFO-02 — Legitimate-empty branch is currently unreachable

**Location:** `src/chatlytics_hermes/adapter.py:744-746` and
`tests/test_outbound.py::test_get_chat_info_returns_none_on_legitimate_empty`.

**Observation:** The HTTP 200 + falsy-body branch (returns `None`)
is acknowledged in both the adapter docstring (line 667-670: "Currently
unreachable for known gateway versions but the code path is defined
for forward-compat.") and the phase brief. The test uses an
explicit `content=b"null"` mock to exercise it — production gateway
versions known today do NOT respond this way.

**Why INFO:** Operator decision is documented; the code path stays
defined for forward-compat per the phase brief. Test coverage
exists. Net positive: when a future gateway version surfaces this
shape, the contract already handles it correctly.

**Recommendation:** No action. Re-evaluate if the gateway team ever
adopts a "200 + empty chat" semantic — at that point this branch
becomes load-bearing rather than forward-compat.

## Invariants verified

- `assert len(TOOLS) == 21` — passes (wrapper exposed module-level
  only, not added to the registry).
- Hermes pin `>=0.14,<0.15` — unchanged.
- All HTTP outbound via `httpx` — unchanged.
- v2.1 baseline tests (78 outside `get_chat_info`) — all green.
- 98/98 total tests passing.

## Verdict

**CLEAN** — no BLOCKER/HIGH/MED findings; 1 LOW + 2 INFO are
informational only. Phase 13 may proceed to next phase.

## Recommended next steps

1. Move on to **Phase 14** (Strict JID regex on chatId schemas) per
   the v3.0 sequencing.
2. Track LOW-01 (env-var test fragility) in Phase 18 cosmetics sweep
   backlog.
