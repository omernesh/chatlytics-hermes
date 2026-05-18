---
phase: 18
plan: 1
plan_name: Deferred LOW/INFO cosmetic nits
mode: infra-skip
date: 2026-05-18
estimated_files: 3
estimated_loc: "~30"
risk: low
---

# 18-PLAN-1 — Deferred LOW/INFO Cosmetic Nits

## Goal

Close the explicitly-deferred LOW/INFO carry-forward items from the
v2.1 audit (Phase 9 + Phase 10) and the two LOW nits flagged in the
Phase 17 review. **Zero behavior change.** Tests stay at exactly
**120/120** at every commit boundary.

## Pre-flight verification

```bash
CHATLYTICS_API_KEY= CHATLYTICS_API_URL= CHATLYTICS_BASE_URL= CHATLYTICS_SESSION= \
  python -m pytest tests/ -q --no-header
# Expect: 120 passed
python -c "from chatlytics_hermes.tools import TOOLS; print(len(TOOLS))"
# Expect: 21
```

Baseline confirmed in CONTEXT.md prep (120/120, 21 tools).

## Task list (one commit per task)

### T1 — Phase 9 INFO-04: `_send_typing_once` "client is None" docstring clarification

**File:** `src/chatlytics_hermes/adapter.py`
**Site:** `_send_typing_once` (around L659-695), the `if self._client is None: return False` branch (around L673-674).

**Edit:** add a one-line docstring note explaining the
not-connected branch and why it returns `False` (rather than
raising or returning a sentinel). Behavior unchanged.

**Verification:**
```bash
CHATLYTICS_API_KEY= CHATLYTICS_API_URL= python -m pytest tests/ -q --no-header
# Expect: 120 passed
git diff --stat src/chatlytics_hermes/adapter.py
# Expect: only docstring line additions; zero logic-line changes
```

**Commit:** `style(18): document _send_typing_once not-connected branch (Phase 9 INFO-04)`

---

### T2 — Phase 9 LOW-01: `_send_typing_once` signature symmetry with `send_typing`

**File:** `src/chatlytics_hermes/adapter.py`
**Site:** `_send_typing_once` signature (around L659-663).

**Edit:** add `metadata: Optional[Dict[str, Any]] = None` kwarg to
`_send_typing_once`'s signature, between `chat_id` and `duration`,
matching `send_typing`'s shape. The new kwarg is unused
(Chatlytics typing endpoint does not consume it); the addition is
strictly for signature symmetry. Update `send_typing`'s call site
to forward `metadata` explicitly. No new behavior, no new request
field.

**Verification:**
```bash
CHATLYTICS_API_KEY= CHATLYTICS_API_URL= python -m pytest tests/ -q --no-header
# Expect: 120 passed
git diff -U0 src/chatlytics_hermes/adapter.py | grep -E '^[+-]' | grep -v '^---\|^+++'
# Expect: only signature + call-site forwarding lines; no new logic
```

**Commit:** `style(18): add metadata kwarg to _send_typing_once for signature symmetry (Phase 9 LOW-01)`

---

### T3 — Phase 9 INFO-02: promote `_reserved` set to module-level `_RESERVED_BODY_KEYS`

**File:** `src/chatlytics_hermes/adapter.py`
**Site:** module-level constants block (near `_CONTROL_CHARS` around
L138) + `send()` reserved-keys check (around L582-584).

**Edit:**
1. Add `_RESERVED_BODY_KEYS: frozenset[str] = frozenset({"chatId", "text", "accountId", "replyTo"})` near `_CONTROL_CHARS`.
2. Replace `_reserved = {"chatId", "text", "accountId", "replyTo"}` inside `send()` with a reference to the module constant.
3. Update the comment to explain the constant lifts the set to one
   place so future contributors who add a top-level body field
   remember to update the reserved set in lockstep.

No behavior change — the set membership is identical.

**Verification:**
```bash
CHATLYTICS_API_KEY= CHATLYTICS_API_URL= python -m pytest tests/test_observability.py -q --no-header
# Expect: tests pass (covers the reserved-keys WARNING contract)
CHATLYTICS_API_KEY= CHATLYTICS_API_URL= python -m pytest tests/ -q --no-header
# Expect: 120 passed
```

**Commit:** `style(18): lift send() reserved-body-keys to module constant (Phase 9 INFO-02)`

---

### T4 — Phase 10 LOW-02: deliberate-redundancy comment on `minLength` + pattern

**File:** `src/chatlytics_hermes/tools.py`
**Sites:** `_chat_id_field` return dict (around L252-257) +
`_message_id_field` return dict (around L271-276).

**Edit:** add a single-line inline comment above the `minLength: 1`
key in each helper explaining the deliberate redundancy with the
pattern's `+` quantifier (per Phase 10 reviewer's explicit
recommendation: "keep both, add a comment"). No schema change.

**Verification:**
```bash
CHATLYTICS_API_KEY= CHATLYTICS_API_URL= python -m pytest tests/test_validation.py -q --no-header
# Expect: tests pass (locked schema behavior)
CHATLYTICS_API_KEY= CHATLYTICS_API_URL= python -m pytest tests/ -q --no-header
# Expect: 120 passed
```

**Commit:** `style(18): comment deliberate minLength+pattern redundancy in id-field helpers (Phase 10 LOW-02)`

---

### T5 — Phase 17 LOW-01 + LOW-02: audit doc whitespace + wording

**File:** `.planning/HERMES-API-AUDIT.md`
**Sites:**
- LOW-01: trailing whitespace on the line ending in `MessageEvent` / `SendResult` / (around L66-67 of the audit doc).
- LOW-02: Migration Checklist item 6 wording — "seven `core` symbols" → "seven `core` symbols listed in the Low-risk surface subsection" (or equivalent clarifier).

**Edit:** strip trailing whitespace; clarify the symbol-count
back-reference. Doc-only.

**Verification:**
```bash
grep -nP ' $' .planning/HERMES-API-AUDIT.md
# Expect: no matches (no trailing whitespace anywhere)
CHATLYTICS_API_KEY= CHATLYTICS_API_URL= python -m pytest tests/ -q --no-header
# Expect: 120 passed (doc-only, but proves the source is untouched)
```

**Commit:** `docs(18): close Phase 17 LOW-01 (audit doc whitespace) + LOW-02 (wording clarify)`

---

### T6 — Write 18-SUMMARY documenting skipped items

**File:** `.planning/phases/HERMES-18-cosmetics-sweep-nits/18-SUMMARY.md`

**Edit:** record the 6 addressed items (one per T1-T5 commit, with
T5 covering two items), the 4 explicitly-skipped items with the
v2.1 reviewer's own justification quoted, the final test count
(120/120), the tool count (21), the file-change stat, and the
final pre-review state.

**Verification:**
```bash
ls .planning/phases/HERMES-18-cosmetics-sweep-nits/18-SUMMARY.md
# Expect: exists
```

**Commit:** `docs(18): phase summary — 6 addressed, 4 skipped, 120/120 preserved`

---

## Out of scope (explicitly NOT touched in this plan)

- Phase 9 INFO-03 (test renaming / scope expansion) — skipped per
  scope guard "Do NOT touch tests beyond docstring/lint nits."
- Phase 10 INFO-01 (test docstring naming) — skipped per v2.1
  reviewer verdict "No change needed."
- Phase 10 INFO-02 (`_CONTROL_CHARS` cross-module dedup) — skipped
  per v2.1 reviewer's own "Out of scope" deferral.
- Phase 10 INFO-03 (`chatlytics_login` raw_response dup) — skipped
  per v2.1 reviewer verdict "leave as-is; this is a feature."
- Any change to `__init__.py`, `client.py`, `inbound.py` beyond
  docstring/lint nits (none flagged in either deferred list).
- Tests, smoke.sh, pyproject.toml, plugin.yaml, CHANGELOG, README.
- Version bump (Phase 19).

## Acceptance criteria

1. 120/120 pytest pass at every commit boundary (run pytest in T1, T2, T3, T4 verification steps).
2. 6 deferred items addressed across T1-T5; 4 skipped items justified in T6 summary.
3. `git diff main..HEAD -- src/chatlytics_hermes/` shows only:
   - docstring additions (T1, T3),
   - signature parity (T2),
   - module-level constant + reference (T3),
   - inline comments (T4).
   No logic-line changes (no new `if`, no changed return, no changed call to httpx).
4. `git diff main..HEAD -- tests/` is empty.
5. `git diff main..HEAD -- scripts/ pyproject.toml plugin.yaml CHANGELOG.md README.md` is empty.
6. Tool count: `len(TOOLS) == 21`.
7. Audit doc: no trailing whitespace; Migration item 6 wording clarified.
8. 18-SUMMARY exists and documents the skipped items with quoted
   v2.1-reviewer justifications.

## Rollback strategy

Each commit is atomic per nit. If any commit fails verification
(test count diverges, tool count diverges, or reviewer flags the
edit), `git reset --hard HEAD~1` and continue with the remaining
tasks. Skip-on-pushback policy per CONTEXT.md D5.
