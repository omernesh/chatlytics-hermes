---
phase: 17
review_status: passed-with-nits
reviewed_by: gsd-code-review (inline, docs-only phase)
review_date: 2026-05-18
files_reviewed: 2
findings_blocker: 0
findings_high: 0
findings_med: 0
findings_low: 2
findings_info: 2
recommendation: ship-with-optional-nit-cleanup
---

# HERMES-17 — Code Review

## Scope

Phase 17 is **docs-only**. No source code in `src/chatlytics_hermes/`
or `tests/` was touched (verified via `git diff 0fbf4d4..HEAD
--stat` — the Phase 16 review commit → current HEAD diff shows only
`.planning/HERMES-API-AUDIT.md` (+166) and `.planning/PROJECT.md`
(+1/-1)). Review focused on:

- `.planning/HERMES-API-AUDIT.md` (166 lines, new) — the deliverable
- `.planning/PROJECT.md` (1 line modified) — the cross-reference

The four prior infra commits (`6fa714c` CONTEXT, `567c836` PLAN,
`fa8d189` audit doc, `fa5e8de` PROJECT.md edit, `9ba87c5`
VERIFICATION) are all docs-only.

## Summary

**0 BLOCKER, 0 HIGH, 0 MED, 2 LOW, 2 INFO.** Audit doc is
evidence-grounded — every row in the Import Inventory Table
corresponds to an actual import line in the source tree (re-verified
during this review via fresh grep). All seven required sections
present in correct order. Stability rubric applied consistently.
Migration checklist is concrete and actionable. Decision Log
addresses all three required points (downgrade rationale +
evidence boundary + production-vs-test registry split). Test
count unchanged at 120/120; 21 tools preserved.

**Verdict: SHIP.** Two LOW nits (LOW-01, LOW-02) and two INFO
observations (INFO-01, INFO-02) below — none block close.
LOW-01 / LOW-02 could land in Phase 18 cosmetics sweep at most.

## Evidence verification (re-grep at review time)

Re-ran the evidence command from `17-PLAN-1.md` T1:

```
$ rg --no-heading -n '^[ \t]*from (gateway|hermes_)' src/ tests/
src/chatlytics_hermes/adapter.py:264:    from gateway.platforms.base import BasePlatformAdapter, SendResult
src/chatlytics_hermes/adapter.py:265:    from gateway.config import Platform, PlatformConfig
src/chatlytics_hermes/inbound.py:31:    from gateway.platforms.base import MessageEvent, MessageType
src/chatlytics_hermes/inbound.py:32:    from gateway.session import SessionSource
tests/conftest.py:36:        from gateway.platform_registry import platform_registry, PlatformEntry
tests/test_conftest_teardown.py:27:        from gateway.platform_registry import platform_registry
tests/test_conftest_teardown.py:39:        from gateway.platform_registry import platform_registry
tests/test_inbound.py:109:    from gateway.platforms.base import MessageType
tests/test_inbound.py:142:    from gateway.platforms.base import MessageType
tests/test_inbound.py:171:    from gateway.platforms.base import MessageType
tests/test_live_loader.py:308:    from gateway.platforms.base import MessageEvent, MessageType
tests/test_live_loader.py:309:    from gateway.session import SessionSource
```

Every hit maps to a table row or a "Notes"-column cross-reference.
No hits are missing from the audit; no audit row is speculative.
Zero `from hermes_` hits (none exist in this repo — the audit
correctly states this).

`import hermes` / `import gateway` re-grep: zero hits in source or
tests. The audit correctly excludes bare `import X` from the
inventory (none exist).

## Findings

### BLOCKER (0)
None.

### HIGH (0)
None.

### MED (0)
None.

### LOW (2)

**LOW-01 — Em-dash whitespace inconsistency on line 67 of the audit
doc.** Inside the Risk Assessment "Low-risk surface" paragraph,
line 66-67 reads `new optional fields on `MessageEvent` /
`SendResult` / \n`PlatformConfig`.` The forward-slash separators
have inconsistent spacing — the slash before `\n` is followed by
trailing whitespace before the newline (cosmetic, doesn't render
differently in most markdown viewers). Fix would be to strip
trailing whitespace on line 66. Defer to Phase 18 cosmetics if at
all; rendering is identical.

**LOW-02 — "seven `core` symbols" wording in migration checklist
item 6 conflates symbol count.** Item 6 says "If any of the seven
`core` symbols changed shape". The Low-risk surface lists 7
symbols (BasePlatformAdapter, SendResult, MessageEvent,
MessageType, SessionSource, Platform, PlatformConfig) — the count
is correct. But a reader checking via the Import Inventory Table
sees 5 rows and might be confused. Optional clarification: "If any
of the seven `core` symbols listed in the Low-risk surface
subsection changed shape". Defer to Phase 18 at most; the current
wording is accurate, just terse.

### INFO (2)

**INFO-01 — Audit doc filename does not encode the audited
version.** The file is `.planning/HERMES-API-AUDIT.md` (no version
suffix). The Migration Checklist item 10 says "Open a fresh audit
(`HERMES-API-AUDIT-v0.15.md` or bump this file in place)" — i.e.,
the next audit can be either a new file with version suffix or an
in-place bump. This is fine for the first audit but suggests the
naming convention should be decided one way or the other before
the second audit. Not blocking; just a future-decision
flag.

**INFO-02 — Audit doc references a `/tmp/hermes-ref-v0.14.0/`
path that's developer-machine-specific.** Lines 8, 36, 113 cite
`/tmp/hermes-ref-v0.14.0/RELEASE_v0.14.0.md` and
`/tmp/hermes-ref-v0.14.0/gateway/platforms/base.py:1265` as
references. This path is a local checkout convention on the
operator's dev machine; a fresh contributor or CI runner won't
have it. The references are still useful (the Hermes 0.14.0 tag
is publicly available at
`https://github.com/NousResearch/hermes-agent/tree/v2026.5.16`),
but a future reviewer might prefer GitHub permalinks. Not blocking;
the paths are evidence-grounding for the audit author, not
production code.

## PROJECT.md edit review

The one-line append (`See \`.planning/HERMES-API-AUDIT.md\` for
the 0.14 inventory.`) lands at the end of the existing "Hermes pin
bump" bullet under "Out of Scope (v3.0)" (line 77). Diff is +1/-1
(one line replaced with same line + appended sentence). No
restructuring; no new bullets. Renders correctly in markdown
(period before "See" preserves sentence break). PASS.

## Acceptance criteria recheck (per 17-PLAN-1)

All 10 criteria pass per `17-VERIFICATION.md` — re-verified during
review:

1. `.planning/HERMES-API-AUDIT.md` exists — PASS
2. Seven sections in order — PASS (H1 + 6 H2)
3. ≥ 5 inventory rows — PASS (exactly 5)
4. Risk Assessment has three subsections — PASS (all H3)
5. Migration Checklist ≥ 8 items — PASS (10)
6. Decision Log references downgrade rationale — PASS (first
   sentence)
7. PROJECT.md contains `HERMES-API-AUDIT.md` — PASS (line 77)
8. `git diff --stat` exactly 2 files — PASS (audit + PROJECT.md)
9. 120 tests pass — PASS (verified in 17-VERIFICATION.md)
10. 21 tools — PASS (verified in 17-VERIFICATION.md)

## Invariants preserved

All v3.0 invariants intact (none could be broken — phase touched
zero source code):

- Hermes pin `>=0.14,<0.15` — pyproject untouched
- 21 tools — `from chatlytics_hermes.tools import TOOLS; len(TOOLS)`
  → 21 (re-verified)
- Phase 13 `_error` sentinel contract — untouched
- Phase 14 strict JID regex — untouched
- Phase 15 adapter `send_*` collapse — untouched
- Phase 16 `--cached` smoke flag — untouched
- v2.1 deliverables (88 baseline tests + 21 tools + BL-01/HI-01/
  HI-03 fixes) — all preserved

## Recommendation

**SHIP.** Phase 17 closes cleanly. Zero blocking findings. The
two LOW nits are cosmetic and could roll into Phase 18 cosmetics
sweep alongside the v2.1 carry-forward LOW/INFO items — or be
ignored entirely. The two INFO observations are future-decision
flags, not defects.

Proceed to Phase 18.
