---
phase: 17
plan_index: 1
plan_slug: hermes-0-14-api-audit
title: "Hermes 0.14 API Surface Audit (docs-only)"
project_code: HERMES
milestone: v3.0
status: ready
infra_skip: true
verification: pytest (120/120 unchanged) + audit-doc structural lint
---

# HERMES-17 Plan 1 — Hermes 0.14 API Surface Audit

## Goal

Produce `.planning/HERMES-API-AUDIT.md` — a one-page evidence-based
inventory of every Hermes-distribution symbol the `chatlytics-hermes`
plugin currently imports (under the `hermes-agent>=0.14,<0.15` pin),
together with a stability classification and a concrete migration
checklist for a hypothetical future 0.15. Append a single
cross-reference line to `.planning/PROJECT.md` so the audit is
discoverable from the project landing doc.

Closes v2.1 deferred item 5 (downgraded from "0.15 readiness" to
"0.14 API surface inventory" — hermes-agent 0.15 doesn't yet exist;
Nous Research's project, not ours).

## Scope (locked per 17-CONTEXT.md)

**In:**
- CREATE `.planning/HERMES-API-AUDIT.md` (only new file in the
  phase).
- MODIFY `.planning/PROJECT.md` (single-line append to the existing
  "Out of Scope (v3.0)" Hermes-pin bullet; no new bullet).

**Out:**
- Zero `.py` changes (no `src/`, no `tests/`).
- Zero `pyproject.toml` / `plugin.yaml` / `smoke.sh` / `README.md`
  changes.
- No new tests; existing 120 must continue to pass byte-for-byte.
- No `CHANGELOG.md` entry (Phase 19 release will mention the audit
  in passing).
- No pin bump; no version bump; no push; no publish.

## Tasks

### T1 — Author `.planning/HERMES-API-AUDIT.md`

**File:** CREATE `.planning/HERMES-API-AUDIT.md`

**Required sections (in this order):**

1. **Title** — exactly `# Hermes 0.14 API Surface Audit —
   chatlytics-hermes 3.0.0`
2. **Metadata block** — bulleted list:
   - Date: `2026-05-18`
   - hermes-agent pin: `>=0.14,<0.15`
   - hermes-agent reference tag: `v2026.5.16` (the 0.14.0 release
     used for the audit; see
     `/tmp/hermes-ref-v0.14.0/RELEASE_v0.14.0.md`)
   - Plugin target version: `3.0.0` (planned, lands in Phase 19)
   - Phase: `HERMES-17` (this audit)
3. **Purpose** — one paragraph. Must explicitly call out:
   - This is a pre-emptive inventory, not an upgrade. hermes-agent
     0.15 does not yet exist.
   - The Hermes 0.14 importable namespace is `gateway` (and
     `hermes_cli` for the CLI). `hermes-agent` is the distribution
     name; there is no top-level `hermes` package. The roadmap's
     "every `hermes.*` import" phrasing means "every import that
     comes from the hermes-agent distribution".
   - The goal is fast 0.15 migration when (if) it lands.
4. **Import Inventory Table** — markdown table, columns:
   `Module Path | First Used In | Imported Symbols | Stability |
   Notes`. One row per unique `(module, symbol-set)` pair. Use
   repo-relative file paths (`src/chatlytics_hermes/adapter.py`,
   not absolute). Required rows (from the actual grep — see
   evidence section below):

   | Module Path | First Used In | Imported Symbols | Stability | Notes |
   |---|---|---|---|---|
   | `gateway.platforms.base` | `src/chatlytics_hermes/adapter.py:264` | `BasePlatformAdapter`, `SendResult` | core | Adapter base class + send-result dataclass. Inherited via `class ChatlyticsAdapter(BasePlatformAdapter)`. Reference: `/tmp/hermes-ref-v0.14.0/gateway/platforms/base.py:1265` (`BasePlatformAdapter`), `:1037` (`SendResult`). Every platform plugin in the Hermes monorepo uses these — extremely high contract stability. |
   | `gateway.platforms.base` | `src/chatlytics_hermes/inbound.py:31` | `MessageEvent`, `MessageType` | core | Inbound event dataclass + media-type enum used by `normalize_payload`. Reference: `base.py:916` + `:894`. `MessageType` is an `Enum`; new members are additive (low break risk). Also imported lazily by `tests/test_inbound.py` (3 sites) and `tests/test_live_loader.py:308`. |
   | `gateway.config` | `src/chatlytics_hermes/adapter.py:265` | `Platform`, `PlatformConfig` | core | Platform enum + per-platform config dataclass. `ChatlyticsAdapter.__init__(self, config: "PlatformConfig", ...)`. Reference: `gateway/config.py:100` + `:281`. |
   | `gateway.session` | `src/chatlytics_hermes/inbound.py:32` | `SessionSource` | core | Session-source dataclass used when constructing `MessageEvent` in `normalize_payload`. Reference: `gateway/session.py:71`. Also imported by `tests/test_live_loader.py:309`. |
   | `gateway.platform_registry` | `tests/conftest.py:36` | `platform_registry`, `PlatformEntry` | runtime | Registry singleton + entry dataclass used **only in tests** (live-loader recorder pattern). Production plugin code never imports these directly — registration flows through `ctx.register_platform(...)` per the v0.14 plugin contract. Reference: `gateway/platform_registry.py:39` (`PlatformEntry`). Also imported by `tests/test_conftest_teardown.py` (2 sites). |

   No additional rows. There are zero `hermes_cli.*` imports
   in this codebase; zero `from hermes ...` direct imports; the
   only Hermes-namespace touchpoint is the entry-point string
   `"hermes_agent.plugins"` in `pyproject.toml` (declaration, not
   an import, so excluded from this inventory).

5. **Risk Assessment for Hypothetical 0.15** — three subsections:
   - **Low-risk surface (`core`):** Lists `BasePlatformAdapter`,
     `SendResult`, `MessageEvent`, `MessageType`, `SessionSource`,
     `Platform`, `PlatformConfig`. Rationale: contract symbols
     used by every platform plugin in the Hermes monorepo —
     renaming or removing breaks the entire plugin ecosystem, not
     just chatlytics-hermes. Additive changes (new
     `MessageType` enum members, new `BasePlatformAdapter`
     hook methods with default implementations) are the most
     likely 0.15 change shape; both are non-breaking for us.
   - **Medium-risk surface (`runtime`):** Lists `platform_registry`
     + `PlatformEntry`. Rationale: imported only by tests. The
     v0.14 plugin contract documents `ctx.register_platform(...)`
     as the canonical path; if 0.15 changes the registry's
     internal structure, only the test recorder pattern in
     `tests/conftest.py` needs updating — production code is
     unaffected. Mitigation: tests already wrap registry
     interaction in helpers; keep that boundary.
   - **High-risk surface (`internal`):** None currently. Plugin
     does not reach into any underscore-prefixed module or import
     any symbol marked private. If a future change introduces
     `from gateway._internal import ...`-style imports, this
     section must grow.
6. **Migration Checklist (for the future 0.15 upgrade)** — bulleted,
   ordered, actionable. Required items:
   1. Pre-flight: confirm hermes-agent 0.15 is actually published
      (`pip index versions hermes-agent`); if not, this checklist
      is premature.
   2. Read the upstream `RELEASE_v0.15.0.md` (or equivalent) and
      diff against `/tmp/hermes-ref-v0.14.0/RELEASE_v0.14.0.md` for
      breaking-change callouts — specifically scan for
      `BasePlatformAdapter`, `MessageEvent`, `MessageType`,
      `SendResult`, `SessionSource`, `Platform`, `PlatformConfig`,
      `platform_registry`, `PlatformEntry` mentions.
   3. Bump `pyproject.toml` pin: `hermes-agent>=0.14,<0.15` →
      `hermes-agent>=0.15,<0.16`.
   4. Run `bash scripts/smoke.sh` against the 0.15 wheel (Phase 16
      cached path will refresh automatically on pin-hash
      mismatch).
   5. Run full pytest suite under a fresh venv with hermes-agent
      0.15 installed (`pytest tests/ -q --no-header`).
   6. If any of the five `core` symbols changed shape: update the
      adapter inheritance / dataclass construction sites and
      add an entry to CHANGELOG under "BREAKING — Hermes 0.15
      compatibility".
   7. If `platform_registry` / `PlatformEntry` changed shape:
      update `tests/conftest.py` recorder + the live-loader
      assertions in `tests/test_live_loader.py`.
   8. Update `README.md` install line + `docs/BETA-INSTALL.md`
      pin reference from `v2026.5.16` to the 0.15 reference tag.
   9. Update `scripts/smoke.sh`'s `HERMES_AGENT_PIN_TAG` literal
      from `v2026.5.16` to the 0.15 reference tag.
  10. Open a fresh audit (`HERMES-API-AUDIT-v0.15.md`) reflecting
      the new import inventory + any new risk surfaces.
7. **Decision Log** — single paragraph (≥80 words). Must document:
   - Why this phase was downgraded from "0.15 readiness" to "0.14
     inventory" (hermes-agent 0.15 doesn't exist; Nous Research
     owns the upstream; this plugin tracks but does not drive it).
   - Why the audit is evidence-based (every row in the table is
     backed by an actual grep of the source tree as of phase
     commit).
   - Why production code intentionally does not import
     `platform_registry` directly (channel registration flows
     through `ctx.register_platform(...)` per the v0.14 plugin
     contract — the registry singleton is a test-only convenience).

**Evidence script (re-run before authoring):**
```
# In repo root:
rg --no-heading -n '^[ \t]*from (gateway|hermes_)' src/ tests/
rg --no-heading -n '^[ \t]*import (gateway|hermes_)' src/ tests/
```
Expected output (exact, no extras):
- `src/chatlytics_hermes/adapter.py:264:    from gateway.platforms.base import BasePlatformAdapter, SendResult`
- `src/chatlytics_hermes/adapter.py:265:    from gateway.config import Platform, PlatformConfig`
- `src/chatlytics_hermes/inbound.py:31:    from gateway.platforms.base import MessageEvent, MessageType`
- `src/chatlytics_hermes/inbound.py:32:    from gateway.session import SessionSource`
- `tests/conftest.py:36:        from gateway.platform_registry import platform_registry, PlatformEntry`
- `tests/test_conftest_teardown.py:27:        from gateway.platform_registry import platform_registry`
- `tests/test_conftest_teardown.py:39:        from gateway.platform_registry import platform_registry`
- `tests/test_inbound.py:109:    from gateway.platforms.base import MessageType`
- `tests/test_inbound.py:142:    from gateway.platforms.base import MessageType`
- `tests/test_inbound.py:171:    from gateway.platforms.base import MessageType`
- `tests/test_live_loader.py:308:    from gateway.platforms.base import MessageEvent, MessageType`
- `tests/test_live_loader.py:309:    from gateway.session import SessionSource`

If actual rg output diverges from this expected set (file moved,
new import added since CONTEXT was written), update the table
accordingly BEFORE commit. The doc must match reality, not the
plan.

**Commit message:** `docs(17): add Hermes 0.14 API surface audit at .planning/HERMES-API-AUDIT.md`

### T2 — Cross-reference from `.planning/PROJECT.md`

**File:** MODIFY `.planning/PROJECT.md`

**Change:** Locate the existing bullet referencing the v2.1
deferred "Hermes pin bump" / "0.15 readiness" item (under "Out of
Scope (v3.0)" or equivalent section). Append a single sentence:
`See \`.planning/HERMES-API-AUDIT.md\` for the 0.14 inventory.`

If the bullet doesn't exist literally as worded (PROJECT.md may
have been written with slightly different phrasing), find the
closest existing "Hermes pin" / "0.15" mention in the Out of Scope
or Deferred section and append the cross-reference there. If no
suitable anchor exists, add a one-line bullet to the "Out of Scope
(v3.0)" section: `- Hermes 0.15 upgrade — not yet published
upstream. See \`.planning/HERMES-API-AUDIT.md\` for the 0.14 API
surface inventory used to fast-track the future migration.`

**Commit message:** `docs(17): cross-reference HERMES-API-AUDIT.md from PROJECT.md`

### T3 — Verification

**No new test file.** Run the existing suite and assert no
regression:
```
python -m pytest tests/ -q --no-header
```
**Expected:** `120 passed` (exactly — must not be 119, must not
be 121).

Additional structural lint on the audit doc (manual, captured in
17-VERIFICATION.md):
- `wc -l .planning/HERMES-API-AUDIT.md` ≥ 60 (sanity: not empty,
  not a stub)
- All seven required section headings present (`grep -c '^## ' .planning/HERMES-API-AUDIT.md` ≥ 6, plus the H1 title)
- Inventory table has exactly 5 rows (header + 5 data rows; one
  per unique `(module, symbol-set)` pair listed in T1)
- Stability column uses only the four-value vocabulary: `core`,
  `runtime`, `utility`, `internal` (or `unknown` with explicit
  note). `grep -oE '\| (core|runtime|utility|internal|unknown) \|' .planning/HERMES-API-AUDIT.md | wc -l` ≥ 5
- Migration checklist has ≥ 8 bulleted items (the 10 listed in
  T1, room for editorial trimming)

**No commit for T3** — verification artifacts land in
17-VERIFICATION.md (written by execute-phase).

## Acceptance criteria (gate for execute-phase verification)

1. `.planning/HERMES-API-AUDIT.md` exists.
2. Doc contains all seven sections from T1, in order.
3. Import Inventory Table lists ≥ 5 rows, one per unique
   `(module, symbol-set)` import in the codebase (verified via
   re-running the evidence rg in T1).
4. Risk Assessment has all three subsections (`low-risk`,
   `medium-risk`, `high-risk`) — `high-risk` may be "None
   currently" but the heading must exist.
5. Migration Checklist has ≥ 8 actionable, ordered items.
6. Decision Log paragraph references the 0.15-doesn't-exist
   downgrade rationale.
7. `.planning/PROJECT.md` contains the string
   `HERMES-API-AUDIT.md` in at least one location (cross-reference
   landed).
8. `git diff --stat` for the phase commits shows exactly two
   files changed: `.planning/HERMES-API-AUDIT.md` (new) +
   `.planning/PROJECT.md` (modify). Zero `src/`, `tests/`,
   `pyproject.toml`, `plugin.yaml`, `smoke.sh`, `README.md`,
   `CHANGELOG.md` changes.
9. `python -m pytest tests/ -q --no-header` → `120 passed`
   (unchanged from Phase 16 baseline).
10. `PYTHONPATH=src python -c "from chatlytics_hermes.tools
    import TOOLS; print(len(TOOLS))"` → `21` (tool surface
    preserved).

## Invariants preserved

- Hermes pin `>=0.14,<0.15` — untouched in pyproject.
- 21 tools registered — untouched.
- Phase 13 `_error` sentinel contract — untouched.
- Phase 14 strict JID regex — untouched.
- Phase 15 adapter `send_*` collapse + `_enforce_upload_allowlist`
  — untouched.
- Phase 16 `--cached` smoke flag — untouched.
- All HTTP outbound via `httpx`; aiohttp only for inbound — N/A
  (no source change).

## Risks + mitigations

- **R1 — Drift between CONTEXT inventory and current source.**
  Mitigation: T1 says "re-run the rg before authoring; doc must
  match reality, not plan". Phase 17 is fast enough that a stale
  grep is the only realistic regression path.
- **R2 — PROJECT.md anchor sentence may not exist verbatim.**
  Mitigation: T2 documents three fallback strategies (exact-match
  → fuzzy-match → new-bullet-with-context).
- **R3 — Doc reviewer flags speculation in the Risk Assessment.**
  Mitigation: phrasing in T1 uses "additive change", "most likely
  shape", "if 0.15 lands" — all hedged, none asserted as fact.
  Decision Log explicitly states the audit's evidence boundary.
- **R4 — Test count drift between Phase 16 verification (120) and
  Phase 17 execute.** Mitigation: T3 asserts exactly 120; any
  drift halts the phase pending root-cause investigation (this is
  a docs-only phase, so 120 should be guaranteed).

## Out-of-scope (deferred to other phases)

- Actually upgrading hermes-agent to 0.15 (doesn't exist; future
  work, no phase budget).
- Writing a compat shim layer (premature; the Risk Assessment
  documents "wrap in adapter" as the mitigation strategy if any
  `core` symbol breaks).
- Phase 19 CHANGELOG entry mentioning the audit (release-phase
  work).
