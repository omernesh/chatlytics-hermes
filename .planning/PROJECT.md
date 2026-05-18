# Chatlytics Hermes Plugin

## What This Is

A first-class platform plugin for [Hermes Agent](https://github.com/NousResearch/hermes-agent) that connects Hermes to WhatsApp via the [Chatlytics](https://chatlytics.ai) gateway. Implements the canonical `BasePlatformAdapter` contract (`connect/disconnect/send/send_typing/get_chat_info` + media variants + inbound `MessageEvent` dispatch) and exposes Chatlytics's full action surface as Hermes tools.

## Core Value

Hermes agents get production-grade WhatsApp messaging — text, media (image/voice/video/document/animation/image-file), reactions, groups, contacts, channels, polls, presence, profile management — via a single `pip install` and a config block. Inbound webhooks arrive as Hermes-native `MessageEvent` objects with proper `MessageType` discrimination; outbound goes through Chatlytics REST with auth, retry, and gate enforcement handled upstream.

## Current State: v3.0 — Breaking-change harmonization + first public release (planning)

v2.1 shipped publicly 2026-05-18 (main + `v2.1.0` tag pushed to GitHub). Local `v2.0.0` tag deleted (was BL-01 pre-fix). 88/88 tests baseline carried into v3.0.

**v3.0 scope:** Close every deferred breaking-change item from the v2.1 Backlog, sweep v2.1 cosmetic carry-forward nits, and ship the **first public release** on PyPI (`chatlytics-hermes 3.0.0`) and npm (`@chatlytics/claude-code 1.2.0`). 9 phases (HERMES-13..21). **Operator lock LIFTED** — TestPyPI + npm dry-run dress rehearsals precede real publishes.

**Cross-repo coordination:**
- chatlytics-hermes (this repo, Python) — Phases 13-19: breaking changes + cosmetics + first PyPI publish
- chatlytics-claude-code (sibling JS repo at `C:/Users/omern/.claude/plugins/marketplaces/chatlytics-claude-code/`) — Phases 20-21: version reconciliation, JID-regex sync, first npm publish under `@chatlytics` org

**Launch:** `/gsd-autonomous --from 13 --to 21` (after configuring TestPyPI + real PyPI tokens in `~/.pypirc`; npm token already configured).

## Previous Milestone: v2.1 — Critical safety fixes + tech debt resolution + live-loader integration — SHIPPED 2026-05-18

All 6 phases delivered (HERMES-07 through HERMES-12). 88/88 tests passing. BL-01 / HI-01 / HI-03 fixed and locked under regression tests. `v2.1.0` tag pushed publicly 2026-05-18. Archive: `.planning/milestones/v2.1-ROADMAP.md`. Audit: `.planning/milestones/v2.1-MILESTONE-AUDIT.md`.

## Previous Milestone: v2.0 — Hermes plugin v2.0 (upstream-contract rebuild) — SHIPPED 2026-05-17

Full upstream-contract rebuild against `hermes-agent>=0.14,<0.15`. 6 phases, 45/45 tests green, 21 Hermes tools registered, `v2.0.0` annotated tag created locally. Archive: `.planning/milestones/v2.0-ROADMAP.md`. Audit: `.planning/v2.0-MILESTONE-AUDIT.md`. Per-phase artifacts: `.planning/milestones/v2.0-phases/HERMES-0[1-6]-*/`. NO PyPI publish per operator lock — manifest + entry point ready for future 1-command publish.

**Target features:**
- Upstream `BasePlatformAdapter` subclass with all 5 required methods + 6 media variants
- aiohttp inbound transport inside `connect()` (no Flask, no separate threads)
- `MessageType.{TEXT,IMAGE,AUDIO,VIDEO,DOCUMENT,STICKER,...}` discrimination on inbound payloads
- All Chatlytics actions exposed as Hermes tools via `ctx.register_tool()` (sourced from the Claude Code plugin's MCP server + Chatlytics `/api/v1/actions` enumeration)
- `_keep_typing()` 30s heartbeat (WhatsApp 24h window protection)
- `cron_deliver_env_var="CHATLYTICS_HOME_CHANNEL"` + `standalone_sender_fn` for scheduled deliveries
- Smoke test: `hermes plugins ls | grep chatlytics` against real `hermes-agent==0.14.0`
- README + CHANGELOG with `2.0.0 (BREAKING)` entry

**Verification ceiling (autonomous-only):** pytest + mocked aiohttp/respx + clean-venv smoke install against real `hermes-agent==0.14.0`. **No PyPI publish in this milestone** — manifest + entry point only.

## Requirements

### Validated (v2.1) — 2026-05-17

- [x] **HERMES-07** — Live-loader integration smoke; surfaces BL-01 + HI-01 + HI-03 via xfail-strict regression tests
- [x] **HERMES-08** — Critical safety fixes (BL-01 + HI-01 + HI-03 + MD-01) + async lifecycle hardening; xfails un-xfailed
- [x] **HERMES-09** — Observability + log hygiene; `send_typing` WARN→DEBUG, 6 silent paths logged, reserved-metadata WARNING
- [x] **HERMES-10** — Input validation + UX alignment; `webhook_path` validated; `chatlytics_login` semantics aligned with MCP bundle
- [x] **HERMES-11** — Test infra cleanup; conftest teardown + idempotency guard + shared `FakePlatformConfig` + `smoke.sh --fast`
- [x] **HERMES-12** — Release v2.1.0 (LOCAL tag only); CHANGELOG/README/pyproject/plugin.yaml bumped; operator lock preserved

### Active (v3.0 — planning)

- [ ] **HERMES-13** — `get_chat_info` `_error` sentinel (BREAKING tool surface)
- [ ] **HERMES-14** — Strict JID regex on `chatId` schemas (BREAKING tool surface)
- [ ] **HERMES-15** — Adapter `send_*` collapse (BREAKING library API)
- [ ] **HERMES-16** — `smoke.sh` wheel caching (additive)
- [ ] **HERMES-17** — Hermes 0.14 API audit doc (docs-only)
- [ ] **HERMES-18** — Cosmetics sweep (v2.1 LOW/INFO carry-forward)
- [ ] **HERMES-19** — Release chatlytics-hermes 3.0.0 (first public PyPI publish; TestPyPI dress rehearsal first)
- [ ] **HERMES-20** — JS bundle update for v3.0 coordination (cross-repo, sibling chatlytics-claude-code repo)
- [ ] **HERMES-21** — Release chatlytics-claude-code 1.2.0 (first public npm publish under `@chatlytics` org)

### Shipped (v2.0) — 2026-05-17

- [x] **HERMES-01** — Upstream contract scaffolding (`BasePlatformAdapter` subclass, `plugin.yaml`, `register(ctx)`, pinned dep)
- [x] **HERMES-02** — Outbound text + control parity (`connect/disconnect/send/send_typing/get_chat_info` via httpx)
- [x] **HERMES-03** — Inbound transport migration (aiohttp inside `connect()`, `MessageEvent` via `MessageType`)
- [x] **HERMES-04** — Media + UX polish + cron (6 media variants, `_keep_typing()`, `cron_deliver_env_var`)
- [x] **HERMES-05** — Full Chatlytics tool surface (every action as a Hermes tool via `ctx.register_tool()`)
- [x] **HERMES-06** — Release + smoke test (README/CHANGELOG, smoke install, tag `v2.0.0`, no PyPI publish)

### Out of Scope (v3.0)

- Hermes pin bump — `>=0.14,<0.15` stays. hermes-agent 0.15 doesn't exist yet (Nous Research's project, not ours); HERMES-17 audits the 0.14 surface so a future upgrade is fast but does not actually upgrade.
- New tools — v3.0 changes semantics of existing tools (HERMES-13/14) but keeps the count at 21. New tools require a v3.1 minor.
- Backward-compat shims for the removed adapter methods (HERMES-15) — operator preference: clean break.
- Live integration tests against a real Chatlytics gateway — autonomous ceiling preserved.
- Backporting to 2.x once 3.0 ships.

### Out of Scope (v2.1, historical)

- PyPI publish — operator lock remained (LIFTED for v3.0).
- Breaking changes — v2.1 was strictly additive/fix-only. All breaking changes deferred to v3.0.
- Live integration tests against a real Chatlytics gateway — autonomous ceiling preserved.

### Out of Scope (v2.0)

- PyPI publish — explicit operator decision, manifest + entry point only
- Backwards compatibility with v1.1.0 standalone-shim API — v1.x was never published publicly, no migration concerns
- Direct WAHA integration — plugin only talks to Chatlytics REST
- Bundled Hermes version — pinned range, plugin does not vendor or bundle Hermes
- Auto-pairing / session creation — Chatlytics owns session lifecycle

## Context

- **Runtime:** Python 3.10+
- **Upstream:** `hermes-agent>=0.14,<0.15` (tag `v2026.5.16` at v0.14.0)
- **Distribution:** GitHub-only for v2.0 (`pip install -e git+https://github.com/omernesh/chatlytics-hermes.git`)
- **License:** MIT
- **Codebase entering v2.0:** ~3 source files + ~2 test files (v1.x carry-over, will be replaced)
- **Tag history:** `hermes-1.1.0` preserved (created 2026-04-27 in monorepo phase 177)

## Constraints

- Hermes plugin contract is upstream-controlled. Plugin shape must match `plugins/platforms/{line,simplex,teams,google_chat,irc}/` canonical examples.
- Inbound transport must live inside `connect()` so the plugin can be loaded/unloaded cleanly without thread leakage.
- All HTTP calls must use `httpx` (async, matches Hermes runtime conventions); aiohttp is for the embedded inbound server only.
- No direct WAHA API calls — everything goes through Chatlytics REST.

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Discard v1.1.0 API entirely (no compat shim) | v1.x was never published publicly. Compat shims add maintenance burden for zero benefit. | Operator decision, 2026-05-17 |
| Pin `hermes-agent>=0.14,<0.15` | Latest as of 2026-05-16. Hermes plugin contract is stable within minor versions; pinning the minor protects against breaking renames in 0.15+. | v2.0 |
| Inbound via aiohttp inside `connect()`, not Flask thread | v1.x used Flask in a separate thread — leaks if plugin is reloaded. aiohttp inside `connect()` shares the Hermes event loop. | HERMES-03 |
| Expose every Chatlytics action as a tool (not just 5-8 read tools) | Operator decision. Hermes agents need the full surface — media variants, reactions, groups, etc. — not just read. | Operator decision, 2026-05-17 |
| No PyPI publish in v2.0 | Operator decision. Manifest + entry point in pyproject.toml so PyPI release is a 1-command operation later. | Operator decision, 2026-05-17 |
| Dedicated repo, not monorepo sub-package | Hermes plugins version independently against `hermes-agent` releases. Sub-package tag flow created friction. | Operator decision, 2026-05-17 |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition:**
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to a `Validated` section with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions

**After milestone close:**
1. Full review of all sections
2. Core Value check
3. Update Context with current state

---
*Last updated: 2026-05-18 -- v3.0 milestone scaffolded. 9 phases (HERMES-13..21) covering BREAKING changes + cosmetics + first public PyPI publish (chatlytics-hermes 3.0.0) + cross-repo first public npm publish (@chatlytics/claude-code 1.2.0). Operator lock LIFTED. Launch: `/gsd-autonomous --from 13 --to 21`. v2.1.0 pushed publicly 2026-05-18; v2.0.0 tag deleted (was BL-01 pre-fix).*
