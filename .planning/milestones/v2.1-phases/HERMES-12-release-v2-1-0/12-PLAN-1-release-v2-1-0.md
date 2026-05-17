---
phase: 12
plan: 1
title: Release v2.1.0 -- CHANGELOG, README, version bump, plugin.yaml strip, LOCAL tag
project_code: HERMES
status: ready
risk: LOW (docs + version + tag; no code changes; no remote actions)
estimated_loc: ~120 LOC across 4 files (CHANGELOG, README, pyproject.toml, plugin.yaml)
last_updated: "2026-05-17T00:00:00.000Z"
---

# 12-PLAN-1 — Release v2.1.0

## Scope

Single plan covering the entire Phase 12 release wrap-up. All edits are
docs / version / tag — zero source-code changes, zero test changes.

## Waves (sequential — each depends on the previous)

### Wave 1 — CHANGELOG.md prepend 2.1.0 entry

Prepend a `## [2.1.0] — 2026-05-17` block above the current `## 2.0.0`
entry in `CHANGELOG.md`. Sections in order:

1. **Security** (LEAD) — BL-01 / HI-01 / HI-03 fixes (3 bullets)
2. **Added** — env var, `--fast` flag, new test files, validation
3. **Changed** — log levels, `chatlytics_login` semantics, success-shape dedup
4. **Fixed** — silent failures, dropped metadata WARN, plugin.yaml phase-ID
   leak, conftest teardown, fixture consolidation
5. **Docs** — actions/dispatch split, get_chat_info {} semantics, "what's new"

Close with: "**Recommended for all users.** v2.0.0 has known BLOCKER + HIGH
security issues fixed in this release."

Commit: `release(HERMES-12): CHANGELOG -- prepend 2.1.0 entry (security-led, additive)`

### Wave 2 — README.md additive edits

1. Insert a `## What's new in v2.1` section directly after the `## Status`
   block (between line ~17 and `## Install`). Two short paragraphs:
   - **Security:** BL-01 / HI-01 / HI-03 fixed; v2.0.0 callers should upgrade
   - **Quality:** live-loader smoke, observability hardening, log hygiene,
     test infra cleanup; 21-tool surface preserved
2. Verify `CHATLYTICS_UPLOAD_ALLOWED_ROOTS` is documented in Configuration
   (already at README.md:47, 49-81 from Phase 8 — no edit needed if present).
3. Verify `smoke.sh --fast` is documented in Development (already from
   Phase 11 — no edit needed if present; if absent, add one line).
4. Clarify `chatlytics_actions` vs `chatlytics_dispatch` in the Tool
   catalog Directory/search and Sessions/health subsections (one sentence
   each spelling out GET catalog vs POST generic action).
5. Append a `## Known issues` section near the end (before `## License`):
   one paragraph documenting that `filename` for URL-path documents may
   or may not be honored by the gateway (track upstream).

Commit: `release(HERMES-12): README -- what's new in v2.1, known issues, tool semantics`

### Wave 3 — pyproject.toml + plugin.yaml version bump + phase-ID strip

1. `pyproject.toml`: `version = "2.0.0"` → `version = "2.1.0"` (line 7)
2. `plugin.yaml`:
   - Line 4: `version: 2.0.0` → `version: 2.1.0`
   - Line 28: strip `, filled in HERMES-03` from
     `CHATLYTICS_WEBHOOK_PORT` description
   - Line 32: strip ` (HERMES-03)` from `CHATLYTICS_WEBHOOK_SECRET`
     description
   - Line 36: strip ` (HERMES-04)` from `CHATLYTICS_HOME_CHANNEL`
     description

Commit: `release(HERMES-12): bump version to 2.1.0 + strip plugin.yaml phase-ID leaks (PR-MED-04)`

### Wave 4 — Full test verification

Run `env -u CHATLYTICS_API_KEY -u CHATLYTICS_BASE_URL -u CHATLYTICS_ACCOUNT_ID python -m pytest tests/ -q --no-header`
in a clean shell (host env vars cleared to match docker smoke contract).
Expected: 88/88 passing. If anything regresses, HALT and report — Phase 12
is docs/version only and must not touch test outcomes.

(No commit — verification only.)

### Wave 5 — Local annotated tag

Run `git tag -a v2.1.0 -m "v2.1.0 -- tech debt resolution + critical safety fixes (BL-01/HI-01/HI-03)"`.

Then verify:
- `git tag --list v2.1.0` → outputs `v2.1.0`
- `git show v2.1.0 --stat | head -3` → shows annotated tag info

**DO NOT** run any `git push` command. Operator pushes manually.

## File-by-file edit checklist

| File | Edit type | Lines affected | Wave |
|------|-----------|----------------|------|
| `CHANGELOG.md` | prepend | top of file (insert ~60 lines) | 1 |
| `README.md` | insert + clarify + append | after "Status" block; tool catalog subsections; before "License" | 2 |
| `pyproject.toml` | replace | line 7 | 3 |
| `plugin.yaml` | replace | lines 4, 28, 32, 36 | 3 |

## Verification

- Clean-env `pytest tests/` → 88/88 (Wave 4)
- `grep -n "HERMES-0" plugin.yaml` → zero matches (Wave 3)
- `grep -n "version" pyproject.toml | head -1` → `2.1.0` (Wave 3)
- `head -5 CHANGELOG.md` → `## [2.1.0]` at top (Wave 1)
- `grep -n "What's new in v2.1" README.md` → match (Wave 2)
- `git tag --list v2.1.0` → tag exists locally (Wave 5)
- Git reflog clean: no `git push` ever ran during this phase

## Risk register

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Accidental `git push` | LOW | Plan explicitly forbids; no push command in any wave |
| README rewrite drift | LOW | Plan limits to additive sections; no reformatting |
| Test regression from version bump | NIL | pyproject version is not asserted by tests |
| plugin.yaml strip breaks YAML | LOW | Only description-text changes; YAML structure preserved |
| Forgetting the security lead in CHANGELOG | MED | Wave 1 explicitly orders sections |

## Acceptance gates (must all pass before tag)

1. CHANGELOG starts with `## [2.1.0]`, Security section is first, mentions
   BL-01 / HI-01 / HI-03
2. README has `## What's new in v2.1` after Status
3. README has `## Known issues` before License
4. pyproject.toml version = "2.1.0"
5. plugin.yaml version: 2.1.0; zero HERMES-NN substrings
6. 88/88 tests pass (clean env)
7. `git tag --list v2.1.0` shows the tag

After all 7 pass → Phase 12 implementation complete; proceed to code review.
