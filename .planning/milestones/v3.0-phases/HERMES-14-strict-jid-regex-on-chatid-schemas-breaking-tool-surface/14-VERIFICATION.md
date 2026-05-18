---
phase: 14
verification_status: passed
implemented_by: gsd-execute-phase
tests_total: 111
tests_passed: 111
files_changed: 6
commits: 5
---

# HERMES-14 — Verification

## Test results

```
$ python -m pytest tests/ -q
111 passed in 27.85s
```

**Baseline before phase:** 98 tests (88 v2.1 + 10 Phase 13).
**Tests added this phase:** +13 (4 valid JID + 8 invalid JID + 1 audit
in `TestJidValidation`).
**Tests flipped this phase:** 2 in `tests/test_validation.py`
(`test_media_chat_id_accepts_phone_number` →
`test_media_chat_id_rejects_phone_number`; same for `group_name`).
Test count unchanged in that file (19).

**Net delta:** 98 → 111 (+13). All baseline tests still pass.

## Acceptance criteria (per ROADMAP HERMES-14)

| # | Criterion | Status |
|---|-----------|--------|
| 1 | `chatlytics_send(chatId="12025551234", text="hi")` → schema rejection (was: passed through to API in v2.1) | PASS — `TestJidValidation::test_jid_rejected_for_invalid_inputs[12025551234-bare phone -- was permissive in v2.1]` |
| 2 | `chatlytics_send(chatId="12025551234@c.us", text="hi")` → proceeds normally (JID accepted) | PASS — `TestJidValidation::test_jid_accepted_for_each_suffix_family[972501234567@c.us-c.us -- 1:1 contact]` (same JID family) |
| 3 | `chatlytics_send(chatId="Omer Nesher", text="hi")` → rejected (helpful error mentions `chatlytics_search`) | PASS — `TestJidValidation::test_jid_rejected_for_invalid_inputs[Omer Nesher-display name -- was permissive in v2.1]`; helpful error text in `_chat_id_field` description points to `chatlytics_search` |
| 4 | All 4 JID families accepted: `@c.us`, `@g.us`, `@lid`, `@newsletter` | PASS — `TestJidValidation::test_jid_accepted_for_each_suffix_family` parametrized over all four |
| 5 | pytest passes; v2.1 permissive-accept tests are flipped to strict-reject (NOT deleted — converted with CHANGELOG cross-ref) | PASS — `tests/test_validation.py:test_media_chat_id_rejects_phone_number` and `tests/test_validation.py:test_media_chat_id_rejects_group_name` exist with `# v3.0 schema tightening` comment + docstring reference to CHANGELOG |

## Sanity introspection

```
$ python -c "from chatlytics_hermes.tools import _JID_PATTERN; print(_JID_PATTERN)"
^.+@(c\.us|g\.us|lid|newsletter)$

$ python -c "... count chatId schemas using _JID_PATTERN ..."
15 chatId schemas use _JID_PATTERN
```

Exactly 15 chatId-bearing schemas use the strict pattern, matching
the count in 14-CONTEXT.md.

## Invariants preserved

- `assert len(TOOLS) == 21` — passes (no new tools added; existing
  schemas tightened in place).
- Hermes pin `>=0.14,<0.15` — unchanged.
- All HTTP outbound via `httpx`; aiohttp only for inbound server —
  unchanged.
- Phase 13 contract (`{success: false, error, _error: "<code>"}` on
  `chatlytics_get_chat_info`) — unchanged.
- v2.1 baseline tests outside the explicitly-flipped pair — all green.

## Commits

```
785e714 docs(14)!: changelog Unreleased entry for strict JID regex
51807b6 test(14): update test-side chatId literals to use JID format
4bc4486 test(14): flip permissive-accept tests to strict-reject (v3.0)
b0a0bbf test(14): TestJidValidation covers 4 valid + 8 invalid JID cases
73c963c feat(14)!: strict JID regex on chatId schemas (matches JS bundle)
```

## Files changed

- `src/chatlytics_hermes/tools.py` — strict `_JID_PATTERN`, renamed
  permissive constant, updated helper docstrings + `SEND_SCHEMA`
  description.
- `tests/test_tool_schemas.py` — added `TestJidValidation` class with
  4 valid + 8 invalid parametrized cases + 1 audit test.
- `tests/test_validation.py` — flipped two permissive-accept tests to
  strict-reject; updated module docstring with v3.0 cross-reference.
- `tests/test_outbound.py` — `CHAT_ID` updated from synthetic
  `"chat-001"` to JID `"120363100000000000@g.us"` with `# v3.0
  schema tightening` comment.
- `tests/test_inbound.py` — same `CHAT_ID` update with comment.
- `tests/test_tools.py` — annotated the `chatId="not-a-jid"` handler-
  direct call with a comment explaining production calls hit schema
  validation first.
- `CHANGELOG.md` — appended HERMES-14 bullet under
  `[Unreleased]/Breaking`.

## Out-of-scope changes

None. Scope locked to chatId/messageId schema layer per 14-CONTEXT.

## Notes for review

- The audit test
  `TestJidValidation::test_jid_validator_applied_to_all_15_chat_id_schemas`
  guards against future drift — adding a chatId-bearing tool with a
  hand-rolled schema (bypassing `_chat_id_field()`) will fail this
  test loudly.
- `_message_id_field()` stays permissive intentionally — the sibling
  JS bundle does NOT regex-validate `messageId`. Documented in the
  helper docstring + the constant comment block + CHANGELOG entry.
- Case-sensitivity: pattern is case-sensitive (no `/i` equivalent in
  JSON Schema Python validator). Documented in the constant comment;
  real-world WAHA JIDs are lowercase so no production impact. The new
  parametrized test
  `[12025551234@C.US-uppercase suffix (case-sensitive pattern)]`
  pins this behavior.
