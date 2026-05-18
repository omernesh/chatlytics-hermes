---
phase: 18
phase_name: Cosmetics sweep (nits)
mode: infra-skip
date: 2026-05-18
---

# HERMES-18 — Context (infra-skip)

## Domain (boundary)

**Cosmetics-only sweep. ZERO behavior change.** Close the
explicitly-deferred LOW/INFO carry-forward items from the v2.1 audit
(Phase 9 + Phase 10) and the two LOW nits noted in Phase 17's review.
No functional logic edits. Test count must remain **exactly 120**
(no new tests, no removed tests, no behavior-change diffs).

The deferred-item inventory comes from the explicit `deferred_findings`
lists in the v2.1 REVIEW frontmatter:

- `.planning/milestones/v2.1-phases/HERMES-09-observability-log-hygiene/09-REVIEW.md`
  → `deferred_findings: [LOW-01, INFO-02, INFO-03, INFO-04]`
- `.planning/milestones/v2.1-phases/HERMES-10-input-validation-ux-alignment/10-REVIEW.md`
  → `deferred_findings: [LOW-02, INFO-01, INFO-02, INFO-03]`

Plus the two LOW nits flagged in Phase 17's review (this milestone):

- `.planning/phases/HERMES-17-hermes-0-14-api-audit-doc-docs-only/17-REVIEW.md`
  → LOW-01 (audit doc trailing whitespace), LOW-02 (audit doc
  wording clarification)

## Decisions

- **D1 — Allowed change categories (locked):**
  1. Log-level adjustments per v2.1 review notes (only if the
     specific note recommends a level change — no speculative tweaks).
  2. Log message style alignment (prefix consistency, f-string vs
     %-format, active voice) — pick the style already dominant in
     the file and align outliers.
  3. Docstring tightening: condense rambling docstrings, add missing
     `Returns:` / `Raises:` where the v2.1 review flagged absence;
     never change documented behavior.
  4. Minor lint nits (trailing whitespace, unused imports, quote
     style) — fix only what's already called out in a v2.1 review
     note or what `python -m py_compile` / a quick grep surfaces
     trivially.
  5. Comment additions that document deliberate-redundancy (e.g.,
     Phase 10 LOW-02 says "keep both, add a comment").

- **D2 — Out of scope (locked; route to a future cleanup milestone):**
  - Renaming functions / classes / variables.
  - Reordering parameters.
  - Changing default values.
  - Adding/removing logger handlers.
  - Refactoring control flow.
  - Adding new functionality.
  - Touching tests beyond docstring/lint nits.
  - Touching `smoke.sh`, `pyproject.toml`, `plugin.yaml`, CHANGELOG,
    README (those belong to Phase 19).
  - Bumping version.
  - Pushing / publishing anything.
  - Cross-module refactors (e.g., Phase 10 INFO-02 `_CONTROL_CHARS`
    duplication — explicit "out of scope" in the v2.1 review).
  - Items the v2.1 review explicitly resolved as "leave as-is" (e.g.,
    Phase 10 INFO-01 test docstring naming → review verdict "No
    change needed"; Phase 10 INFO-03 raw_response duplication →
    review verdict "leave as-is; this is a feature").

- **D3 — Per-item inventory and disposition (locked before edits):**

  | Source | Item | Site (v3.0 line) | Disposition | Allowed-category |
  |---|---|---|---|---|
  | v2.1 Phase 9 | LOW-01 | `adapter.py:_send_typing_once` (~L659) | Add `metadata: Optional[Dict[str, Any]] = None` kwarg for base-class signature symmetry (unused, parity-only) | (1) — but reclassify as docstring + signature symmetry; no log-level change. |
  | v2.1 Phase 9 | INFO-02 | `adapter.py:send()` `_reserved` set (~L582) | Promote to module-level `_RESERVED_BODY_KEYS: frozenset[str]` constant; `send()` references the constant. No new behavior. | (3) — refactor flagged as cosmetic by v2.1 reviewer. |
  | v2.1 Phase 9 | INFO-03 | `tests/test_observability.py:test_no_api_key_in_any_log_record` | **SKIP** — out of scope per scope guard ("Do NOT touch tests beyond docstring/lint nits"). Document the skip in 18-SUMMARY. | n/a (skip) |
  | v2.1 Phase 9 | INFO-04 | `adapter.py:_send_typing_once` "client is None" branch (~L673) | Clarify docstring to call out the "not-connected → returns False" branch; behavior unchanged. | (3) — docstring tightening. |
  | v2.1 Phase 10 | LOW-02 | `tools.py:_chat_id_field` / `_message_id_field` `minLength: 1` (~L254, L273) | Add inline comment explaining deliberate redundancy with the pattern `+` quantifier. Reviewer's explicit recommendation: "keep both, add a comment." | (5) — comment-only. |
  | v2.1 Phase 10 | INFO-01 | `tests/test_validation.py` docstring | **SKIP** — v2.1 review verdict: "No change needed." | n/a (skip) |
  | v2.1 Phase 10 | INFO-02 | `_CONTROL_CHARS` cross-module duplication | **SKIP** — v2.1 review explicit "Out of scope for v2.1 (cross-module refactor)"; same rule applies in v3.0 cosmetic sweep. | n/a (skip) |
  | v2.1 Phase 10 | INFO-03 | `chatlytics_login` raw_response duplication | **SKIP** — v2.1 review verdict: "leave as-is; this is a feature, not a defect." | n/a (skip) |
  | v3.0 Phase 17 | LOW-01 | `.planning/HERMES-API-AUDIT.md` line 66-67 trailing whitespace | Strip trailing whitespace on the offending line. Doc-only. | (4) — lint nit. |
  | v3.0 Phase 17 | LOW-02 | `.planning/HERMES-API-AUDIT.md` Migration Checklist item 6 | Clarify "seven `core` symbols" wording to reference the Low-risk surface subsection explicitly. Doc-only. | (3) — docstring/doc tightening. |

  **Inventory summary:** 10 items considered. 6 to address (4 source
  + 2 audit-doc). 4 to explicitly skip with documented justification.
  No item invents new work outside the deferred lists.

- **D4 — Tests stay at EXACTLY 120/120.** No tests added, none
  removed, no test logic changes. Run pytest after each commit to
  confirm zero behavior change. If any test diff appears, STOP and
  reclassify the change as out-of-scope.

- **D5 — Skip-on-pushback policy.** Phase is marked
  "Optional skip if reviewer pushes back" in ROADMAP. If
  `gsd-code-review` raises a substantive concern about a cosmetic
  edit (e.g., "this log-level change masks a real warning"), revert
  that specific edit and continue with the rest. Do not abandon the
  whole phase for one objection.

- **D6 — Atomic per-nit commits.** Each addressed item lands in its
  own commit so any subsequent revert is surgical.

- **D7 — Run pytest after each commit.** 120/120 must hold at every
  commit boundary. If a commit breaks the count, revert that single
  commit, document the failure in 18-SUMMARY, and continue.

## Code context

**Primary source targets:**

- `src/chatlytics_hermes/adapter.py` — three items (Phase 9 LOW-01,
  INFO-02, INFO-04). Three sites: `_send_typing_once` signature
  (~L659), `send()` reserved-keys set (~L582), `_send_typing_once`
  "client is None" docstring branch (~L673).
- `src/chatlytics_hermes/tools.py` — one item (Phase 10 LOW-02).
  Sites: `_chat_id_field` (~L252-257) + `_message_id_field`
  (~L271-276).

**Doc targets:**

- `.planning/HERMES-API-AUDIT.md` — two items (Phase 17 LOW-01,
  LOW-02). One whitespace strip + one wording clarification.

**Source archives (read-only references):**

- `.planning/milestones/v2.1-phases/HERMES-09-observability-log-hygiene/09-REVIEW.md`
  — full text of every Phase 9 finding (lines 152-289).
- `.planning/milestones/v2.1-phases/HERMES-10-input-validation-ux-alignment/10-REVIEW.md`
  — full text of every Phase 10 finding (lines 132-189).
- `.planning/phases/HERMES-17-hermes-0-14-api-audit-doc-docs-only/17-REVIEW.md`
  — full text of Phase 17 LOW-01 / LOW-02 (lines 88-109).

**Explicit non-targets** (do NOT edit beyond docstring/lint nits):

- `src/chatlytics_hermes/__init__.py`
- `src/chatlytics_hermes/client.py`
- `src/chatlytics_hermes/inbound.py`
- `tests/**` (any test logic change is out of scope)
- `scripts/smoke.sh`, `pyproject.toml`, `plugin.yaml`, `CHANGELOG.md`,
  `README.md`

## Specifics (acceptance criteria for this phase)

1. **120/120 tests pass** before any edit (baseline) and after every
   commit (each cosmetic change preserves behavior).
2. **6 deferred items addressed**, **4 explicitly skipped** with the
   v2.1 reviewer's own justification quoted in 18-SUMMARY.
3. `git diff --stat` shows only source comments, signature parity,
   constants/refactor (INFO-02), docstring text, and one audit-doc
   whitespace strip + one audit-doc wording line. No new tests, no
   logic-line changes.
4. Tool count remains **exactly 21**:
   `python -c "from chatlytics_hermes.tools import TOOLS;
   print(len(TOOLS))"` → `21`.
5. Behavior identical to the pre-Phase-18 HEAD on the user-visible
   surface: send() body shape unchanged; `_send_typing_once` returns
   the same bool for the same inputs; chatId/messageId schemas accept
   and reject the same inputs.
6. Skip-on-pushback policy applied: if reviewer flags an edit, revert
   it and continue.

## Deferred (kicked to a future cleanup milestone)

- Cross-module `_CONTROL_CHARS` consolidation (Phase 10 INFO-02 — v2.1
  reviewer's own deferral).
- Test renaming / scope-expanding suggested by Phase 9 INFO-03 (test
  infra change, not cosmetic-source change).
- Any item the v2.1 reviewer explicitly resolved as "leave as-is"
  (Phase 10 INFO-01, INFO-03).
- All Phase 19+ release tasks (CHANGELOG / README / version bump /
  PyPI publish / npm publish).
- Anything beyond log-level / log-style / docstring / lint nits.
