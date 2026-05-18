---
phase: 17
verification_status: passed
implemented_by: gsd-execute-phase
tests_total: 120
tests_passed: 120
files_changed: 2
commits: 2
---

# HERMES-17 — Verification

## Test results

```
$ unset CHATLYTICS_API_KEY CHATLYTICS_API_URL CHATLYTICS_SESSION
$ python -m pytest tests/ -q --no-header
120 passed in 25.21s
```

**Baseline before phase:** 120 tests (Phase 16 baseline carried over
unchanged).
**Tests added this phase:** 0 (docs-only).
**Net delta:** 120 → 120 (zero change). All baseline tests still pass.

**Env-leak note:** A first pytest invocation surfaced 10 auth-related
failures because the orchestrator shell had `CHATLYTICS_API_KEY` /
`CHATLYTICS_API_URL` / `CHATLYTICS_SESSION` set from a previous
session — these env vars leak into the test process and override
the fixture-injected `test-api-key-abc123` value. Unsetting the
three vars before pytest restored the clean 120/120 result. This
is an environmental quirk, not a regression in this phase — the
test suite is byte-identical to Phase 16's. Future phases should
either prefix pytest with the unset or fix conftest.py to
monkeypatch.delenv these three keys at session-start (deferred to
a future cosmetics sweep — out of scope for HERMES-17).

## Acceptance criteria (per 17-PLAN-1)

| # | Criterion | Status |
|---|-----------|--------|
| 1 | `.planning/HERMES-API-AUDIT.md` exists | PASS — 166 lines created. |
| 2 | Doc contains all seven sections from T1, in order | PASS — H1 title + 6 H2 sections (Metadata, Purpose, Import Inventory Table, Risk Assessment for Hypothetical 0.15, Migration Checklist (for the future 0.15 upgrade), Decision Log) = 7 sections total. Verified via `grep -c '^## ' .planning/HERMES-API-AUDIT.md` → 6 (H2 count) plus the singular H1. |
| 3 | Inventory table has ≥ 5 rows, one per unique `(module, symbol-set)` | PASS — exactly 5 data rows: `gateway.platforms.base` (BasePlatformAdapter+SendResult), `gateway.platforms.base` (MessageEvent+MessageType), `gateway.config` (Platform+PlatformConfig), `gateway.session` (SessionSource), `gateway.platform_registry` (platform_registry+PlatformEntry). Verified via `grep -oE '\| (core\|runtime\|utility\|internal\|unknown) \|' .planning/HERMES-API-AUDIT.md \| wc -l` → 5. |
| 4 | Risk Assessment has all three subsections (low / medium / high) | PASS — three H3 subsections present: "Low-risk surface (`core`)", "Medium-risk surface (`runtime`)", "High-risk surface (`internal`)" (the last is explicitly "None currently" but the heading exists). |
| 5 | Migration Checklist has ≥ 8 actionable, ordered items | PASS — 10 numbered items (1. Pre-flight, 2. Read release notes, 3. Bump pin, 4. Run smoke, 5. Run pytest, 6. If core symbols changed, 7. If registry changed, 8. Update install docs, 9. Update smoke.sh, 10. Open fresh audit). |
| 6 | Decision Log references the 0.15-doesn't-exist downgrade rationale | PASS — paragraph opens with "This phase was downgraded from '0.15 readiness' to '0.14 inventory' because hermes-agent 0.15 does not exist yet." Also documents Nous Research ownership and evidence-based audit boundary. |
| 7 | `.planning/PROJECT.md` contains the string `HERMES-API-AUDIT.md` | PASS — line 77 of PROJECT.md now reads "...does not actually upgrade. See `.planning/HERMES-API-AUDIT.md` for the 0.14 inventory." |
| 8 | `git diff --stat` for phase commits shows exactly two files changed | PASS — Commit `fa8d189` (1 file: `.planning/HERMES-API-AUDIT.md` +166) + commit `fa5e8de` (1 file: `.planning/PROJECT.md` +1/-1). Zero `src/`, `tests/`, `pyproject.toml`, `plugin.yaml`, `smoke.sh`, `README.md`, `CHANGELOG.md` changes. |
| 9 | `python -m pytest tests/ -q --no-header` → `120 passed` | PASS — `120 passed in 25.21s` (with the three env-leak vars unset). |
| 10 | `len(TOOLS)` → `21` | PASS — `PYTHONPATH=src python -c "from chatlytics_hermes.tools import TOOLS; print(len(TOOLS))"` → `21`. |

## Sanity introspection

```
$ wc -l .planning/HERMES-API-AUDIT.md
166 .planning/HERMES-API-AUDIT.md

$ grep -c '^## ' .planning/HERMES-API-AUDIT.md
6

$ grep -oE '\| (core|runtime|utility|internal|unknown) \|' .planning/HERMES-API-AUDIT.md | wc -l
5

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

$ PYTHONPATH=src python -c "from chatlytics_hermes.tools import TOOLS; print(len(TOOLS))"
21
```

Evidence-matching: every row in the inventory table corresponds to
a `rg` hit above. No row is speculative; no `rg` hit is unmentioned
(test-only sites are aggregated into the registry row's Notes
column or the `gateway.platforms.base` MessageType row's Notes).

## Invariants preserved

- `assert len(TOOLS) == 21` — passes (docs-only phase).
- Hermes pin `>=0.14,<0.15` — unchanged in `pyproject.toml`.
- All HTTP outbound via `httpx`; aiohttp only for inbound server —
  unchanged (no source change).
- Phase 13 contract (`{success: false, error, _error: "<code>"}`
  on `chatlytics_get_chat_info`) — unchanged.
- Phase 14 strict JID regex on chatId schemas — unchanged.
- Phase 15 `send_*` collapse + `_enforce_upload_allowlist` —
  unchanged.
- Phase 16 `--cached` smoke flag — unchanged.
- Default `bash scripts/smoke.sh` (no flags) — unchanged.

## Commits

```
fa5e8de docs(17): cross-reference HERMES-API-AUDIT.md from PROJECT.md
fa8d189 docs(17): add Hermes 0.14 API surface audit at .planning/HERMES-API-AUDIT.md
```

(Plus the 2 pre-execute infrastructure commits: `567c836`
plan-1 + `6fa714c` 17-CONTEXT.md, which are doc-only scaffolding,
not part of the implementation deliverable.)

## Files changed

- `.planning/HERMES-API-AUDIT.md` — CREATED. 166 lines, seven
  sections (Title + Metadata + Purpose + Import Inventory Table +
  Risk Assessment + Migration Checklist + Decision Log). Five
  inventory rows, all `core` or `runtime` stability. Ten-item
  ordered migration checklist. Evidence boundary explicitly noted
  in Decision Log.
- `.planning/PROJECT.md` — MODIFIED (+1/-1). Appended "See
  `.planning/HERMES-API-AUDIT.md` for the 0.14 inventory." to the
  existing "Hermes pin bump" bullet under "Out of Scope (v3.0)".
  No restructuring; no new bullets.

## Out-of-scope changes

None. Scope locked to "one new audit doc + one PROJECT.md
cross-reference" per 17-CONTEXT.

## Deviations from plan

**None of substance.** Two cosmetic differences worth flagging for
the reviewer:

1. The plan's T3 acceptance criterion 4 said "all three
   subsections" must exist; the audit doc uses H3 (`###`) for the
   three risk subsections rather than H4. This matches the
   roadmap's structural spec (top-level audit sections use H2,
   subsections use H3) and is more conventional markdown.
2. The plan listed an env-leak workaround risk (R-not-listed); the
   actual execute surfaced this risk as a real shell-state issue,
   resolved inline with `unset` before pytest. Documented in the
   Test results section above so the next phase author isn't
   surprised. Long-term fix (monkeypatch.delenv in conftest.py)
   deferred to HERMES-18 cosmetics sweep at most — not in scope
   here.

## Notes for review

- The audit is deliberately concise (166 lines) — it's a
  one-page migration playbook, not a comprehensive reference. The
  Risk Assessment hedges every prediction ("most likely shape",
  "if 0.15 lands") to avoid speculation about an upstream that
  hasn't released.
- The Decision Log paragraph hits 156 words (target was ≥ 80) and
  covers all three required points (downgrade rationale +
  evidence-based scope + production-vs-test registry split).
- Cross-reference in PROJECT.md is a single-sentence append to
  the existing bullet, not a new bullet — minimizes diff and
  keeps the project landing doc structurally unchanged.
- Inventory boundaries are explicit: the table lists zero
  `hermes_cli.*` imports (none exist), zero direct `from hermes`
  imports (none exist), and excludes the `pyproject.toml`
  entry-point group string `"hermes_agent.plugins"` (it's a
  declaration, not an import). A reviewer expecting to see those
  in the table will find the explanation in the paragraph
  immediately below the table.
