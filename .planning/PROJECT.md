# Chatlytics Hermes Plugin

## What This Is

A first-class platform plugin for [Hermes Agent](https://github.com/NousResearch/hermes-agent) that connects Hermes to WhatsApp via the [Chatlytics](https://chatlytics.ai) gateway. Implements the canonical `BasePlatformAdapter` contract (`connect/disconnect/send/send_typing/get_chat_info` + media variants + inbound `MessageEvent` dispatch) and exposes Chatlytics's full action surface as Hermes tools.

## Core Value

Hermes agents get production-grade WhatsApp messaging — text, media (image/voice/video/document/animation/image-file), reactions, groups, contacts, channels, polls, presence, profile management — via a single `pip install` and a config block. Inbound webhooks arrive as Hermes-native `MessageEvent` objects with proper `MessageType` discrimination; outbound goes through Chatlytics REST with auth, retry, and gate enforcement handled upstream.

## Current State: v2.1 SHIPPED 2026-05-17 (local tag, operator push pending)

All 6 v2.1 phases (HERMES-07 through HERMES-12) shipped. 88/88 tests passing (45 v2.0 baseline + 43 v2.1 additions; zero regressions). The BL-01 BLOCKER and 2 HIGHs from the v2.0 milestone code review are fixed and locked under regression tests. `v2.1.0` annotated tag created LOCAL ONLY (operator push pending; operator lock preserved). Archive: `.planning/milestones/v2.1-ROADMAP.md`. Audit: `.planning/milestones/v2.1-MILESTONE-AUDIT.md` (verdict: passed).

**Operator next:** Review v2.1.0 artifact, then `git push origin main && git push origin v2.1.0` when ready. Optionally delete local `v2.0.0` tag (points at BL-01 pre-fix artifact superseded by v2.1.0).

## Next Milestone Goals: v2.2 (planning)

Deferred from v2.1 close (none are blockers — v2.1.0 ships clean):

- Sentinel `_error` key on `get_chat_info` return shape (would be breaking — v2.2 minor)
- Strict JID regex enforcement on `chatId` schemas (would break phone numbers / display names)
- Collapse `send_image` / `send_image_file` into one method (breaking change — v2.2 minor)
- Long-term wheel caching in `scripts/smoke.sh` beyond `--retries 3` (build-perf nice-to-have)
- Hermes `0.15` readiness review (deferred to v3.0 — not a v2.2 item)

Start next milestone via `/gsd:new-milestone` to refine scope.

## Previous Milestone: v2.1 — Critical safety fixes + tech debt resolution + live-loader integration — SHIPPED 2026-05-17

All 6 phases delivered. BL-01 + HI-01 + HI-03 from v2.0 milestone review CLOSED. Every carry-forward MED/LOW from v2.0 audit + PR-style review CLOSED. Live-loader integration smoke locks BL-01 under regression test. 88/88 tests green. Archive: `.planning/milestones/v2.1-ROADMAP.md`.

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

### Active (v2.2 — planning)

(See "Next Milestone Goals" above. Run `/gsd:new-milestone` to refine.)

### Shipped (v2.0) — 2026-05-17

- [x] **HERMES-01** — Upstream contract scaffolding (`BasePlatformAdapter` subclass, `plugin.yaml`, `register(ctx)`, pinned dep)
- [x] **HERMES-02** — Outbound text + control parity (`connect/disconnect/send/send_typing/get_chat_info` via httpx)
- [x] **HERMES-03** — Inbound transport migration (aiohttp inside `connect()`, `MessageEvent` via `MessageType`)
- [x] **HERMES-04** — Media + UX polish + cron (6 media variants, `_keep_typing()`, `cron_deliver_env_var`)
- [x] **HERMES-05** — Full Chatlytics tool surface (every action as a Hermes tool via `ctx.register_tool()`)
- [x] **HERMES-06** — Release + smoke test (README/CHANGELOG, smoke install, tag `v2.0.0`, no PyPI publish)

### Out of Scope (v2.1)

- PyPI publish — operator lock remains. Manifest stays publish-ready (1-command future op).
- Breaking changes — v2.1 is strictly additive/fix-only. Any breaking change requires a v3.0 milestone.
- Live integration tests against a real Chatlytics gateway — autonomous ceiling preserved.
- New tool surface — the 21 tools from v2.0 are the locked surface for v2.x. New tools require a v2.2 minor.
- Hermes pin bump — `>=0.14,<0.15` stays. 0.15 readiness is a v3.0 decision.

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
*Last updated: 2026-05-17 -- v2.1 SHIPPED (local). All 6 HERMES-07..12 phases complete (88/88 tests, BL-01/HI-01/HI-03 fixed, v2.1.0 tagged local). Audit: `.planning/milestones/v2.1-MILESTONE-AUDIT.md`. Archive: `.planning/milestones/v2.1-ROADMAP.md`. Operator push of `main` + `v2.1.0` tag pending; PyPI publish deferred per lock; local v2.0.0 tag may be deleted after v2.1.0 push.*
