---
phase: 18
review_type: source_code_review
review_date: 2026-05-18
reviewer: gsd-code-review (inline, cosmetics-only phase)
implemented_by: claude-opus-4-7-1m
files_reviewed:
  - src/chatlytics_hermes/adapter.py
  - src/chatlytics_hermes/tools.py
  - .planning/HERMES-API-AUDIT.md
depth: standard
summary:
  blocker: 0
  high: 0
  med: 0
  low: 0
  info: 1
  total: 1
status: passed
overall_verdict: SHIP
recommendation: proceed-to-phase-19
---

# HERMES-18 — Code Review

## Scope

Phase 18 is **cosmetics-only — zero behavior change**. Reviewer
focused on the four risk vectors called out in 18-VERIFICATION.md:

1. `_RESERVED_BODY_KEYS` constant has identical membership to the
   previous local set.
2. The new `metadata` kwarg on `_send_typing_once` is not forwarded
   to the wire.
3. The two `minLength: 1` comments in `tools.py` do not alter the
   schema JSON.
4. The audit doc whitespace/wording changes do not affect any
   acceptance criterion from Phase 17.

Files reviewed (`git diff c781629^..HEAD -- src/ .planning/HERMES-API-AUDIT.md`):

- `src/chatlytics_hermes/adapter.py` (+38 / -3) — three commits
  (T1 docstring, T2 signature parity, T3 module constant)
- `src/chatlytics_hermes/tools.py` (+11 / -0) — one commit
  (T4 inline comments)
- `.planning/HERMES-API-AUDIT.md` (+2 / -2) — one commit
  (T5 whitespace strip + wording clarify)

Tests untouched. CHANGELOG / README / pyproject / plugin.yaml /
smoke.sh untouched (correctly — those belong to Phase 19).

## Risk-vector verification

### Vector 1 — `_RESERVED_BODY_KEYS` membership identity (T3)

```python
# Before (local, line ~582):
_reserved = {"chatId", "text", "accountId", "replyTo"}

# After (module-level, line 149):
_RESERVED_BODY_KEYS: frozenset = frozenset({"chatId", "text", "accountId", "replyTo"})
```

Membership identical: `{"chatId", "text", "accountId", "replyTo"}`
== `frozenset({"chatId", "text", "accountId", "replyTo"})` for the
`in` operator on string keys. The frozenset vs set distinction does
not change `__contains__` semantics for string elements.

`send()` line 597 uses `if key in _RESERVED_BODY_KEYS:` — identical
truth table to `if key in _reserved:`.

`test_send_warns_on_dropped_reserved_metadata` (locked under
v2.1 Phase 9 test surface) passed during T3 verification (120/120).

**Verdict:** PASS — no behavior change.

### Vector 2 — `metadata` kwarg threading (T2)

```python
# send_typing (line 670):
await self._send_typing_once(chat_id, duration, metadata=metadata)

# _send_typing_once (lines 672-676):
async def _send_typing_once(
    self,
    chat_id: str,
    duration: float = 3.0,
    metadata: Optional[Dict[str, Any]] = None,
) -> bool:
```

Reviewed `_send_typing_once` function body (lines 686-708):
`metadata` is **never referenced** in the function body. The
request body construction is:

```python
json={"chatId": chat_id, "duration": float(duration)}
```

Identical to the pre-Phase-18 wire shape. No metadata field is added.

Second call site (line 1331, `_keep_typing`):
`await self._send_typing_once(chat_id, duration=30.0)` — uses
positional/kwarg defaults, does not pass `metadata`. Backward-compatible
because `metadata` has a default of `None`.

**Verdict:** PASS — kwarg is accepted but unused; wire request is
byte-identical.

### Vector 3 — Schema JSON output identity (T4)

The two inline comments above `"minLength": 1` are Python source
comments. They do not appear in the dict literal output. The
returned dict is byte-identical to the pre-Phase-18 dict:

```python
{
    "type": "string",
    "minLength": 1,
    "pattern": <JID or PERMISSIVE>,
    "description": <text>,
}
```

`jsonschema.Draft202012Validator` sees the same schema; accept/reject
behavior is unchanged. `tests/test_validation.py` (19 tests covering
chatId + messageId acceptance set) passed during T4 verification.

**Verdict:** PASS — no schema change.

### Vector 4 — Audit doc changes (T5)

LOW-01: trailing whitespace on line 66 stripped. Markdown rendering
is byte-identical in every standard viewer (CommonMark, GFM, MkDocs).
The audit doc's seven-section structure is preserved; the five
inventory rows are preserved.

LOW-02: Migration Checklist item 6 wording clarified from "seven
`core` symbols" to "seven `core` symbols listed in the Low-risk
surface subsection". The seven-symbol count and the substantive
guidance ("Update the adapter inheritance / dataclass construction
sites in...") are preserved. The clarification is purely a
forward-reference pointer.

Phase 17 acceptance criteria re-checked: all 10 still pass (audit
file exists, seven sections in order, ≥ 5 inventory rows, three
risk-assessment subsections, ≥ 8 migration checklist items, etc.).

**Verdict:** PASS — doc-only changes do not regress any Phase 17
acceptance criterion.

## Findings

### BLOCKER (0)
None.

### HIGH (0)
None.

### MED (0)
None.

### LOW (0)
None.

### INFO (1)

**INFO-01 — `_RESERVED_BODY_KEYS` type annotation uses bare `frozenset`
rather than `frozenset[str]`.**

**File:** `src/chatlytics_hermes/adapter.py:149`

The constant is declared as:

```python
_RESERVED_BODY_KEYS: frozenset = frozenset({"chatId", "text", "accountId", "replyTo"})
```

A more precise annotation would be `frozenset[str]` (or
`FrozenSet[str]` if maintaining `from typing import FrozenSet`
compatibility). The bare `frozenset` is functionally correct under
mypy and pyright (treated as `frozenset[Any]`), but the parameterized
form documents intent at a glance.

**Severity:** INFO. The original v2.1 review (Phase 9 INFO-02
suggestion text) used the same convention (`_RESERVED_BODY_KEYS` to
a module-level frozenset, no element-type hint), so this matches the
reviewer's own recommendation verbatim. Not blocking.

**Recommendation:** Leave as-is. Strict-typing pass can land in a
future tech-debt sweep.

## Scope discipline

Reviewed each commit against Phase 18 CONTEXT.md D2 "Out of scope"
list:

| Forbidden change | Committed? |
|---|---|
| Renaming functions/classes/variables | NO |
| Reordering parameters | NO (metadata added at end, after duration) |
| Changing default values | NO |
| Adding/removing logger handlers | NO |
| Refactoring control flow | NO |
| Adding new functionality | NO |
| Touching tests beyond docstring/lint nits | NO (tests untouched) |
| Touching smoke.sh / pyproject.toml / plugin.yaml / CHANGELOG / README | NO |
| Bumping version | NO |
| Pushing or publishing | NO |
| Cross-module refactors | NO |

All allowed-category edits per D1 are accounted for:

- **(1) Log-level adjustments** — none made (no log-level note in
  the deferred items required one).
- **(3) Docstring tightening** — T1 (`_send_typing_once`
  not-connected branch), T2 (`metadata` kwarg note).
- **(3) Refactor (module constant)** — T3, flagged as cosmetic by
  v2.1 reviewer.
- **(4) Lint nit** — T5 LOW-01 (trailing whitespace).
- **(5) Deliberate-redundancy comment** — T4.
- **Doc clarification** — T5 LOW-02.

No items addressed outside the deferred lists.

## Skipped items disposition

4 items skipped, each with the v2.1 reviewer's own verdict quoted
in 18-SUMMARY.md:

- Phase 9 INFO-03 — out of scope (test rename / scope expansion).
- Phase 10 INFO-01 — reviewer said "No change needed."
- Phase 10 INFO-02 — reviewer said "Out of scope for v2.1
  (cross-module refactor)"; same rule applies in v3.0 cosmetics.
- Phase 10 INFO-03 — reviewer said "leave as-is; this is a feature,
  not a defect."

Honoring the v2.1 reviewer's own dispositions is the correct call.

## Invariants confirmed

- Hermes pin `>=0.14,<0.15` — unchanged (pyproject untouched).
- **21 tools** — `len(TOOLS) == 21` (verified).
- httpx outbound / aiohttp inbound — unchanged (no `_client.post`
  signature change, no aiohttp app routes added).
- `{"success": bool, ...}` tool response shape — unchanged.
- HERMES-13 `_error` sentinel — `get_chat_info` untouched.
- HERMES-14 strict JID regex — `_JID_PATTERN` untouched, only comments
  added above `minLength`.
- HERMES-15 adapter `send_*` collapse — untouched.
- HERMES-16 `--cached` smoke flag — `scripts/smoke.sh` untouched.
- HERMES-17 audit doc structure — preserved (text clarified, not
  restructured).
- v2.1 BL-01 client lifecycle — `connect()` / `disconnect()` /
  `_client` not touched.
- v2.1 HI-01 upload allowlist — `tools.py` changes are comments only;
  `_resolve_resource` untouched.
- v2.1 88 baseline tests + v3.0 32 new tests = 120/120 preserved.

## Verdict

**SHIP.** Phase 18 closes cleanly with zero blocking findings. The
single INFO observation (`frozenset` element-type hint) is cosmetic
and matches the original v2.1 reviewer's recommendation verbatim.

Zero behavior change is verified across all four risk vectors. Tests
remain at exactly 120/120. Tool count remains at exactly 21. Scope
discipline is excellent — no edits outside the deferred-item lists,
no edits in test files or release artifacts.

Proceed to Phase 19 (release chatlytics-hermes 3.0.0 to PyPI).

## Recommendation

No fix-pass required. Phase 18 is ship-ready.
