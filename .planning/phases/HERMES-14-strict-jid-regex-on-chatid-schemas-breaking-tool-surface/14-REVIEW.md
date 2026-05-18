---
phase: 14
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

# HERMES-14 — Code Review

## Scope

Files reviewed (changed in Phase 14 commits `73c963c`, `b0a0bbf`,
`4bc4486`, `51807b6`, `785e714`):

- `src/chatlytics_hermes/tools.py` — replaced `_CHAT_ID_PATTERN` with
  strict `_JID_PATTERN`; renamed permissive constant to
  `_PERMISSIVE_ID_PATTERN`; updated `_chat_id_field()` /
  `_message_id_field()` defaults + docstrings; updated `SEND_SCHEMA`
  chatId description.
- `tests/test_tool_schemas.py` — added `TestJidValidation` class
  (4 valid + 8 invalid parametrized + 1 audit test).
- `tests/test_validation.py` — flipped two permissive-accept tests to
  strict-reject with `# v3.0 schema tightening` comment + docstring
  cross-reference.
- `tests/test_outbound.py` — `CHAT_ID` updated from `"chat-001"` to
  `"120363100000000000@g.us"` with documentation comment.
- `tests/test_inbound.py` — same `CHAT_ID` update.
- `tests/test_tools.py` — annotated `chatId="not-a-jid"` handler-direct
  call with comment explaining the schema layer in production.
- `CHANGELOG.md` — appended HERMES-14 bullet under
  `[Unreleased]/Breaking`.

## Summary

**BLOCKER: 0  HIGH: 0  MED: 0  LOW: 1  INFO: 2** — verdict **CLEAN**.

Implementation matches the locked phase brief and the plan exactly.
The strict JID regex matches the sibling JS bundle's `looksLikeJid`
pattern in shape; case-sensitivity is the one deliberate divergence
(documented in the constant block, pinned by a test, justified by
real-world WAHA JID conventions).

All 15 chatId-bearing tool schemas use the strict pattern (verified
by the new audit test `test_jid_validator_applied_to_all_15_chat_id_schemas`).
`_message_id_field()` correctly stays permissive (matching JS
canonical, per the brief). The 21-tool invariant is preserved.

No regressions in the 98-test baseline (88 v2.1 + 10 Phase 13). Two
permissive-accept tests were flipped to strict-reject as required;
they retain their structure and add `# v3.0 schema tightening`
comments referencing the CHANGELOG.

Phase 13's `_error: "<code>"` contract on `chatlytics_get_chat_info`
is untouched. Schema-layer rejections correctly surface via
`jsonschema.ValidationError` for the Hermes framework to handle —
the implementation does NOT manually catch and re-shape, per the
brief's explicit guidance.

## Findings

### LOW-01 — Pre-existing test fixture env-var fragility (carry-forward)

**Location:** `tests/test_outbound.py`, `tests/test_tools.py`,
`tests/test_validation.py` (fixture `adapter` / `client`), inherits
from `ChatlyticsAdapter.__init__` env-var precedence at
`src/chatlytics_hermes/adapter.py:298-299`.

**Observation:** Same finding as Phase 13's LOW-01. When
`CHATLYTICS_API_KEY` / `CHATLYTICS_BASE_URL` are set in the shell
environment, the adapter constructor's `os.getenv(...) or
extra.get(...)` precedence overrides the test fixture's mocked
credentials, causing Bearer-token assertions to fail.

**Why not a phase finding:** Pre-existing v2.0/v2.1/Phase 13 behavior;
HERMES-14 did not introduce or worsen it. The new
`TestJidValidation` class does NOT use the adapter fixture (it only
constructs `jsonschema.Draft202012Validator` instances), so it is
immune to the env-var leak. The updated tests in `test_outbound.py`
inherit the same fragility as before.

**Recommendation:** Defer to Phase 18 cosmetics sweep (already
tracked from Phase 13's REVIEW.md). Same `monkeypatch.delenv` fix
shape covers both phases' inheritance of the issue.

**Severity rationale:** LOW because it does not affect production
correctness, only developer-experience reliability when the dev shell
has Chatlytics credentials exported. The dockerized smoke suite
(`scripts/smoke.sh`) is unaffected (clean venv).

### INFO-01 — Case-sensitivity divergence from JS bundle's `/i` flag

**Location:** `src/chatlytics_hermes/tools.py:217` (`_JID_PATTERN`
constant comment block) and `tests/test_tool_schemas.py::TestJidValidation`
parametrized case `[12025551234@C.US-uppercase suffix (case-sensitive pattern)]`.

**Observation:** The JS bundle's `looksLikeJid` uses
`/@(c\.us|g\.us|lid|newsletter)$/i` — case-insensitive. The Python
plugin's `_JID_PATTERN` is case-sensitive (JSON Schema `pattern`
flags are implementation-defined; jsonschema's Python validator
treats patterns as case-sensitive by default). The constant block
documents this divergence and justifies it: real-world WAHA JIDs are
lowercase; uppercase suffixes are a copy-paste glitch better
surfaced as a validation error than silently accepted.

**Why INFO not LOW:** The divergence is deliberate, documented in
both the constant block and the CHANGELOG entry, and pinned by a
parametrized test case so any future tightening or loosening fires
the test. Behavior matches the JS bundle for every legitimate
production input. The "match the JS regex" brief phrasing referred
to the suffix-family shape, not the case-insensitivity flag (the
latter is non-portable across JSON Schema validators).

**Recommendation:** No action. Re-evaluate only if a real-world WAHA
gateway version starts emitting uppercase-suffix JIDs (none do as of
this writing).

### INFO-02 — `_chat_id_field` default description grew ~6x

**Location:** `src/chatlytics_hermes/tools.py:236-242`.

**Observation:** The default `description` argument grew from ~40
chars in v2.1 (`"Chat JID, phone, or group identifier."`) to ~250
chars in v3.0 (instructive multi-line text pointing the caller at
`chatlytics_search` for name/phone resolution). This text is
embedded in every chatId-bearing schema (15 schemas × 250 chars =
~3.75 KB of repeated description text in the tool catalog dump).

**Why INFO:** LLM-facing descriptions are intentionally informative
— the operator brief explicitly requested "human-friendly and
instruct the caller to use `chatlytics_search` first." The Hermes
framework surfaces these descriptions to the LLM at tool-call time,
which is exactly when the actionable guidance is needed. The size
overhead per tool-catalog dump is negligible (single-digit KB).

**Recommendation:** No action. If catalog-dump size ever becomes a
concern, the description can be moved to a `$comment` or `$ref`'d
helper, but premature optimization here would obscure the
operator-friendly guidance.

## Invariants verified

- `assert len(TOOLS) == 21` — passes (no tools added or removed;
  only chatId schemas tightened in place).
- 15 chatId-bearing schemas use `_JID_PATTERN`
  (`test_jid_validator_applied_to_all_15_chat_id_schemas`).
- `_message_id_field()` uses `_PERMISSIVE_ID_PATTERN` (unchanged
  regex, renamed constant) — matches JS canonical (which does not
  regex-validate messageId).
- Phase 13's `chatlytics_get_chat_info` wrapper untouched.
- Hermes pin `>=0.14,<0.15` — unchanged.
- All HTTP outbound via `httpx`; aiohttp only for inbound — unchanged.
- 98 baseline tests + 13 new in TestJidValidation = 111/111 passing.

## Verdict

**CLEAN** — no BLOCKER/HIGH/MED findings; 1 LOW (pre-existing,
carry-forward to Phase 18) + 2 INFO (deliberate documented design
decisions) are informational only. Phase 14 may proceed to Phase 15.

## Recommended next steps

1. Move on to **Phase 15** (Adapter `send_*` collapse) per the v3.0
   sequencing.
2. LOW-01 (env-var test fragility) already tracked from Phase 13's
   REVIEW.md for Phase 18 cosmetics sweep — no separate tracking
   needed for this phase.
