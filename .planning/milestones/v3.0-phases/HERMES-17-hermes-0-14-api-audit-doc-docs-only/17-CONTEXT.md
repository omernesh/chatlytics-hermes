---
phase: 17
phase_name: Hermes 0.14 API audit doc (docs-only)
mode: infra-skip
date: 2026-05-18
---

# HERMES-17 — Context (infra-skip)

## Domain (boundary)

Docs-only phase. Produce a single new artifact at
`.planning/HERMES-API-AUDIT.md` that inventories every `from hermes`
/ `from gateway` / `from hermes_*` import the `chatlytics-hermes`
plugin pulls from the `hermes-agent` package (currently pinned
`>=0.14,<0.15`), and a one-line cross-reference update in
`.planning/PROJECT.md`. Zero source code changes. Test count must
remain **exactly 120** (no new tests; no removed tests).

The audit feeds the future 0.15 migration: when (if) Nous Research
ships hermes-agent 0.15, a developer can open this doc and
immediately see (a) every Hermes symbol we touch, (b) which are
contract-stable vs deep-internal, and (c) a concrete migration
checklist.

## Decisions

- **D1 — Audit doc structure is locked.** Seven sections in order:
  (1) Title `# Hermes 0.14 API Surface Audit — chatlytics-hermes
  3.0.0`; (2) Metadata block (date `2026-05-18`, pin
  `>=0.14,<0.15`, hermes-agent tag `v2026.5.16`, plugin target
  `3.0.0`); (3) Purpose paragraph; (4) Import Inventory Table; (5)
  Risk Assessment for Hypothetical 0.15; (6) Migration Checklist;
  (7) Decision Log. Reviewer must verify all seven exist.
- **D2 — Inventory table columns are fixed.** `Module Path | First
  Used In | Imported Symbols | Stability | Notes`. One row per
  unique import line (group by module path + symbol set; if two
  files import the same symbols from the same module, list the
  primary site under "First Used In" and mention the secondary site
  in Notes). "First Used In" uses repo-relative paths
  (`src/chatlytics_hermes/adapter.py` style).
- **D3 — Stability classification rubric.** `core` =
  `BasePlatformAdapter`, `MessageEvent`, `MessageType`,
  `SendResult`, `SessionSource`, `PlatformConfig`, `Platform`
  (the Hermes platform-plugin public contract — symbols every
  platform plugin uses; renaming these would break every platform
  in the Hermes monorepo, so they are the most stable). `runtime`
  = `PlatformEntry`, `platform_registry` (registration helpers used
  by tests only — Hermes itself uses these internally but they're
  imported across plugin code so they're stable-by-coupling).
  `utility` = none in this codebase currently. `internal` = none in
  this codebase currently. `unknown` = anything that doesn't fit
  with a note explaining why.
- **D4 — Evidence-based only.** Every row in the table corresponds
  to an actual import line that exists in the source tree TODAY.
  Verified via `Grep "from gateway"` and `Grep "from hermes_"` in
  `src/` and `tests/`. Do not speculate about Hermes internals; if
  a symbol's stability is genuinely unknown, mark it `unknown` and
  add a note "stability inferred from name only; verify against
  hermes-agent docs before 0.15 migration".
- **D5 — Naming reality.** The Hermes top-level package in v0.14 is
  `gateway` (not `hermes`). `hermes-agent` is the **distribution**
  name; the importable namespace is `gateway` for adapters/configs
  + `hermes_cli` for the CLI. The roadmap's wording "every
  `hermes.*` import" is a hand-wave for "every import that comes
  from the hermes-agent distribution"; in practice that means
  `from gateway.*` (currently 4 unique modules) and any
  `hermes_cli.*` (currently 0). The audit must call this out
  explicitly in the Purpose section so a future reader doesn't
  conclude the inventory missed `hermes.*` imports (there are
  none).
- **D6 — PROJECT.md cross-reference.** Append a single sentence to
  the existing "Hermes pin bump" bullet under "Out of Scope (v3.0)"
  (or wherever the v2.1-deferred-5 callout lives) so the audit is
  discoverable from PROJECT.md without restructuring the file.
- **D7 — No CHANGELOG entry needed.** Docs-only; the v3.0
  CHANGELOG (Phase 19) will mention "added Hermes 0.14 API surface
  audit at .planning/HERMES-API-AUDIT.md" as a one-line additive
  bullet — not authored in this phase.

## Code context

**Grep targets (source of truth for the inventory):**
- `src/chatlytics_hermes/__init__.py` — entry-point re-export only;
  no Hermes imports
- `src/chatlytics_hermes/adapter.py` — `from gateway.platforms.base
  import BasePlatformAdapter, SendResult` + `from gateway.config
  import Platform, PlatformConfig` (lines 264-265, inside `try`
  block with `_HERMES_AVAILABLE` fallback)
- `src/chatlytics_hermes/inbound.py` — `from gateway.platforms.base
  import MessageEvent, MessageType` + `from gateway.session import
  SessionSource` (lines 31-32, same `try/except ImportError`
  pattern)
- `src/chatlytics_hermes/client.py` — no Hermes imports (pure
  httpx)
- `src/chatlytics_hermes/tools.py` — no Hermes imports (pure tool
  registry; symbols flow in via adapter handlers, not import-time)

**Test-side imports (cited in the doc for completeness but not
authoritative for plugin contract):**
- `tests/conftest.py:36` — `from gateway.platform_registry import
  platform_registry, PlatformEntry`
- `tests/test_conftest_teardown.py:27,39` — `from
  gateway.platform_registry import platform_registry`
- `tests/test_inbound.py:109,142,171` — `from
  gateway.platforms.base import MessageType` (lazy imports inside
  test bodies; deliberate so the module imports without
  hermes-agent)
- `tests/test_live_loader.py:308-309` — `from
  gateway.platforms.base import MessageEvent, MessageType` + `from
  gateway.session import SessionSource`

**Output files:**
- CREATE `.planning/HERMES-API-AUDIT.md`
- MODIFY `.planning/PROJECT.md` (one-line append to existing
  "Hermes pin bump" / "0.15 readiness" bullet)

## Specifics

- **ZERO source changes.** No `.py` files in `src/` or `tests/` get
  touched. No `pyproject.toml` change. No `smoke.sh` change. No
  `CHANGELOG.md` change. No `README.md` change.
- **Tests stay at 120/120.** Phase 16 baseline. The execute step
  runs `pytest tests/ -q --no-header` and asserts the integer 120
  appears in the output. No new tests added (audit is docs-only).
- **Output:** exactly one new file (`.planning/HERMES-API-AUDIT.md`)
  + exactly one PROJECT.md edit (one bullet appended-to, not a new
  bullet added).
- **Hermes pin unchanged.** `pyproject.toml` keeps
  `hermes-agent>=0.14,<0.15` literal. Audit doc cites this as the
  pin under inventory but does not modify it.
- **Plugin version unchanged.** Still `2.1.0` in pyproject; bumps
  to `3.0.0` in Phase 19.
- **No push / publish.** Phase 17 commits land on local main; Phase
  19 handles tagging + push + PyPI.

## Deferred

- **Actual 0.15 upgrade.** `hermes-agent` 0.15 does not exist yet;
  the project is Nous Research's, not ours. Audit gives us a
  one-page migration playbook for whenever 0.15 lands. No code is
  changed in this phase — by design.
- **Compat shim.** Premature without a real 0.15 to compat
  against; the audit explicitly notes "wrap-in-adapter" as the
  mitigation strategy if a 0.15 break lands in any of the symbols
  currently classified `core` or `runtime`.
- **CHANGELOG 3.0.0 entry mentioning the audit.** Lands in Phase 19
  (release phase), not Phase 17.

## Acceptance pre-conditions

- Phase 16 verification frontmatter shows `verification_status:
  passed`, `tests_total: 120`, `tests_passed: 120` (confirmed via
  `.planning/phases/HERMES-16-smoke-sh-wheel-caching-additive/16-VERIFICATION.md`).
- `git status` clean on entry (no untracked source-tree changes).
- All grep targets above exist and contain the cited line numbers
  (re-verify in plan phase if any drift since this CONTEXT was
  written).
