# Hermes 0.14 API Surface Audit — chatlytics-hermes 3.0.0

## Metadata

- **Date:** 2026-05-18
- **hermes-agent pin:** `>=0.14,<0.15`
- **hermes-agent reference tag:** `v2026.5.16` (0.14.0 release used for
  this audit; see `/tmp/hermes-ref-v0.14.0/RELEASE_v0.14.0.md`)
- **Plugin target version:** `3.0.0` (planned, lands in Phase 19)
- **Phase:** HERMES-17 (this audit)

## Purpose

This is a pre-emptive inventory of every Hermes-distribution symbol
the `chatlytics-hermes` plugin currently imports. It is **not** an
upgrade: `hermes-agent` 0.15 does not yet exist (Nous Research owns
the upstream project; this plugin tracks but does not drive it).
The goal is to make a future 0.15 migration fast: a single page
that lists every contract touchpoint, classifies it by stability,
and spells out the migration checklist a future developer should
follow.

Note on naming: the Hermes 0.14 importable namespace is `gateway`
(for adapters, configs, sessions, platform registry) and
`hermes_cli` (for the CLI). `hermes-agent` is only the
distribution / PyPI name; there is no top-level `hermes` package.
The roadmap's "every `hermes.*` import" phrasing is shorthand for
"every import that comes from the hermes-agent distribution". In
this codebase that means `from gateway.*` (four unique modules)
and zero `hermes_cli.*` imports.

## Import Inventory Table

| Module Path | First Used In | Imported Symbols | Stability | Notes |
|---|---|---|---|---|
| `gateway.platforms.base` | `src/chatlytics_hermes/adapter.py:264` | `BasePlatformAdapter`, `SendResult` | core | Adapter base class + send-result dataclass. Inherited via `class ChatlyticsAdapter(BasePlatformAdapter)`. Reference: `/tmp/hermes-ref-v0.14.0/gateway/platforms/base.py:1265` (`BasePlatformAdapter`), `:1037` (`SendResult`). Every platform plugin in the Hermes monorepo uses these — extremely high contract stability. |
| `gateway.platforms.base` | `src/chatlytics_hermes/inbound.py:31` | `MessageEvent`, `MessageType` | core | Inbound event dataclass + media-type enum used by `normalize_payload`. Reference: `base.py:916` (`MessageEvent`), `base.py:894` (`MessageType`). `MessageType` is an `Enum`; new members are additive (low break risk). Also imported lazily by `tests/test_inbound.py` (lines 109, 142, 171) and `tests/test_live_loader.py:308`. |
| `gateway.config` | `src/chatlytics_hermes/adapter.py:265` | `Platform`, `PlatformConfig` | core | Platform enum + per-platform config dataclass. Used in `ChatlyticsAdapter.__init__(self, config: "PlatformConfig", ...)` and via `Platform.CHATLYTICS` enum membership. Reference: `gateway/config.py:100` (`Platform`), `:281` (`PlatformConfig`). |
| `gateway.session` | `src/chatlytics_hermes/inbound.py:32` | `SessionSource` | core | Session-source dataclass used when constructing `MessageEvent` in `normalize_payload`. Reference: `gateway/session.py:71`. Also imported by `tests/test_live_loader.py:309`. |
| `gateway.platform_registry` | `tests/conftest.py:36` | `platform_registry`, `PlatformEntry` | runtime | Registry singleton + entry dataclass used **only in tests** (live-loader recorder pattern). Production plugin code never imports these directly — registration flows through `ctx.register_platform(...)` per the v0.14 plugin contract. Reference: `gateway/platform_registry.py:39` (`PlatformEntry`). Also imported by `tests/test_conftest_teardown.py` (lines 27, 39). |

**Inventory boundaries:** There are zero `hermes_cli.*` imports in
this codebase, zero direct `from hermes ...` imports, and zero
underscore-prefixed (private) imports. The only Hermes-namespace
touchpoint outside the rows above is the entry-point group string
`"hermes_agent.plugins"` in `pyproject.toml` — that is a
declaration consumed by `importlib.metadata.entry_points(...)` at
plugin-discovery time, not an import, and so is excluded from the
table.

## Risk Assessment for Hypothetical 0.15

### Low-risk surface (`core`)

`BasePlatformAdapter`, `SendResult`, `MessageEvent`, `MessageType`,
`SessionSource`, `Platform`, `PlatformConfig`.

These are the symbols every platform plugin in the Hermes
monorepo (LINE, Discord, Telegram, Slack, Signal, WhatsApp,
Teams, …) uses. Renaming or removing any of them would break the
entire plugin ecosystem at once, not just chatlytics-hermes — so
upstream has strong incentive to keep them stable across minor
releases. The most likely shape of a 0.15 change is **additive**:
new `MessageType` enum members (e.g., a new media type), new
`BasePlatformAdapter` hook methods with default implementations,
or new optional fields on `MessageEvent` / `SendResult` / 
`PlatformConfig`. All three patterns are non-breaking for this
plugin.

### Medium-risk surface (`runtime`)

`platform_registry`, `PlatformEntry`.

Imported only by tests (`tests/conftest.py`,
`tests/test_conftest_teardown.py`). The v0.14 plugin contract
documents `ctx.register_platform(...)` as the canonical
production registration path; the registry singleton is a
test-only convenience used by the live-loader recorder pattern.
If 0.15 reshapes the registry's internal structure (renames
`PlatformEntry` fields, splits the singleton into multiple
modules, etc.), only the test harness in `tests/conftest.py` and
the live-loader assertions in `tests/test_live_loader.py` need
updating — production code is unaffected.

**Mitigation:** Keep registry interaction confined to the
existing test helpers (`platform_registry` is only touched inside
the `_chatlytics_platform_registered` fixture and the teardown
sanity tests). If 0.15 lands, refactor those helpers in a single
sweep; production code never needs to know.

### High-risk surface (`internal`)

None currently.

The plugin does not import any underscore-prefixed module from
`gateway`, does not reach into `gateway/_internal/*` (no such
module exists in 0.14 anyway), and does not depend on any
symbol marked private (leading underscore) in upstream. If a
future change introduces a `from gateway._internal import ...`
style import or a deep-reaching `from
gateway.platforms.base._private import ...`, this section must
grow and the symbol must be reclassified `internal` with an
explicit isolation plan ("wrap in adapter to isolate" is the
default mitigation).

## Migration Checklist (for the future 0.15 upgrade)

1. **Pre-flight:** Confirm hermes-agent 0.15 is actually
   published (`pip index versions hermes-agent`). If not, this
   checklist is premature — wait.
2. **Read the upstream release notes.** Diff the
   `RELEASE_v0.15.0.md` (or equivalent) against
   `/tmp/hermes-ref-v0.14.0/RELEASE_v0.14.0.md` for
   breaking-change callouts. Specifically scan for mentions of:
   `BasePlatformAdapter`, `MessageEvent`, `MessageType`,
   `SendResult`, `SessionSource`, `Platform`, `PlatformConfig`,
   `platform_registry`, `PlatformEntry`. Anything else changing
   that affects us would be a surprise.
3. **Bump `pyproject.toml` pin:** `hermes-agent>=0.14,<0.15` →
   `hermes-agent>=0.15,<0.16`. Commit as
   `chore(deps): bump hermes-agent pin to >=0.15,<0.16`.
4. **Run `bash scripts/smoke.sh`** against the 0.15 wheel. The
   Phase 16 cached path will refresh automatically on pin-hash
   mismatch (see `scripts/smoke.sh` `HERMES_AGENT_PIN_TAG`
   logic).
5. **Run full pytest suite** under a fresh venv with hermes-agent
   0.15 installed: `python -m pytest tests/ -q --no-header`.
   Baseline must remain at least 120 passing (Phase 16 floor).
6. **If any of the seven `core` symbols changed shape:** Update
   the adapter inheritance / dataclass construction sites in
   `src/chatlytics_hermes/adapter.py` and `inbound.py`, then add
   an entry to `CHANGELOG.md` under "BREAKING — Hermes 0.15
   compatibility" describing the migration.
7. **If `platform_registry` / `PlatformEntry` changed shape:**
   Update `tests/conftest.py` recorder fixture and the
   live-loader assertions in `tests/test_live_loader.py`.
   Production code does not need to change.
8. **Update install docs:** Update `README.md` install line and
   `docs/BETA-INSTALL.md` pin reference from `v2026.5.16` to the
   0.15 reference tag.
9. **Update `scripts/smoke.sh`:** Change `HERMES_AGENT_PIN_TAG`
   literal from `v2026.5.16` to the 0.15 reference tag. The
   cache will auto-invalidate on pin-hash mismatch on the next
   `--cached` run.
10. **Open a fresh audit:** Write `HERMES-API-AUDIT-v0.15.md` (or
    bump this file in place) reflecting the new import inventory
    + any new risk surfaces that emerged during migration.

## Decision Log

This phase was downgraded from "0.15 readiness" to "0.14 inventory"
because hermes-agent 0.15 does not exist yet. The upstream
project is owned by Nous Research; chatlytics-hermes tracks but
does not drive its release cadence. Writing a compat shim or
actually bumping the pin would be premature against a release
that has not happened. The audit captures the current import
contract as evidence — every row in the table corresponds to an
actual import line in the source tree as of the phase commit
(verified by re-running `rg "^[ \t]*from (gateway|hermes_)" src/
tests/` immediately before writing this document). Production
code intentionally does not import `platform_registry` directly;
channel registration flows through `ctx.register_platform(...)`
per the v0.14 plugin contract, and the registry singleton is
a test-only convenience used by the live-loader recorder pattern
in `tests/conftest.py`. Keeping that boundary means any future
0.15 registry reshape is contained to test code only.
