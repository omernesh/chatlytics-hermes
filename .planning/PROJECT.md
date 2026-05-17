# Chatlytics Hermes Plugin

## What This Is

A first-class platform plugin for [Hermes Agent](https://github.com/NousResearch/hermes-agent) that connects Hermes to WhatsApp via the [Chatlytics](https://chatlytics.ai) gateway. Implements the canonical `BasePlatformAdapter` contract (`connect/disconnect/send/send_typing/get_chat_info` + media variants + inbound `MessageEvent` dispatch) and exposes Chatlytics's full action surface as Hermes tools.

## Core Value

Hermes agents get production-grade WhatsApp messaging — text, media (image/voice/video/document/animation/image-file), reactions, groups, contacts, channels, polls, presence, profile management — via a single `pip install` and a config block. Inbound webhooks arrive as Hermes-native `MessageEvent` objects with proper `MessageType` discrimination; outbound goes through Chatlytics REST with auth, retry, and gate enforcement handled upstream.

## Current Milestone: v2.1 — Critical safety fixes + tech debt resolution + live-loader integration

**⚠ DO NOT push the `v2.0.0` tag publicly until v2.1 lands.** The local v2.0.0 checkpoint is fine; pushing it ships a known-broken-on-first-inbound plugin per the milestone-wide GSD review (`.planning/v2.0-MILESTONE-CODE-REVIEW.md`, verdict FIX_FIRST: 1 BLOCKER, 3 HIGH). Ship as `v2.1.0` instead.

**Goal:** First close the 1 BLOCKER + 3 HIGHs the GSD milestone review surfaced (the per-phase reviews missed them — they only surface end-to-end). Then close every remaining MED/LOW carried forward from the v2.0 audit + PR-style review. Prove the plugin works against a real `PluginContext` end-to-end, not just at the entry-point discovery layer. Additive from the public API perspective (BL-01 fix changes internal `_keep_typing` shape but the convenience `_typing_scope` async-cm preserves in-plugin call sites). Ships as `v2.1.0`. NO PyPI publish (operator lock remains).

**Target features:**
- Live-loader integration smoke: wire `hermes.gateway.bootstrap.load_plugins()` (or equivalent) with a respx-mocked Chatlytics backend; assert all 21 tools land on the in-memory registry (closes 06-MED-01 — biggest v2.0 test gap)
- `_keep_typing` shape alignment with upstream base coroutine contract — rename + thin compat wrapper, OR upstream PR (closes 04-MED-01)
- Concurrency regression test for `_resolve_media_url` after the v2.0 `asyncio.to_thread` fix
- Log hygiene: consolidate `send_typing` log volume; add diagnostic logs to silent error paths; bump `_keep_typing` first-fire failure to WARNING (closes 02-LOW-02, 05-LOW-01, 06-LOW-02)
- Input validation: validate `webhook_path` at `__init__`; align `chatlytics_login` with MCP-bundle semantics; document `get_chat_info` `{}` semantics (closes 03-LOW-01, 05-LOW-03, 02-LOW-03)
- Test infra cleanup: teardown for conftest platform_registry seed; smoke build cache layer (closes 02-MED-02, 06-LOW-01)
- Tool catalog docs: clarify `chatlytics_actions` vs `chatlytics_dispatch` semantic split; document v2.0 known issues (closes 05-MED-01, 04-LOW-02)

**Verification ceiling (autonomous-only):** pytest + mocked aiohttp/respx + clean-venv smoke in docker python:3.13-slim + live-loader smoke via respx-mocked PluginContext. No live Chatlytics gateway calls. **No PyPI publish in this milestone** (operator lock preserved).

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

### Active (v2.1)

- [ ] **HERMES-07** — Live-loader integration smoke (surfaces BL-01 via base-path test; closes 06-MED-01 + GSD-review MD-04 test-harness bypass)
- [ ] **HERMES-08** — Critical safety fixes (BL-01 BLOCKER `_keep_typing` rewrite; HI-01 HIGH `filePath` allowlist; HI-03 HIGH `**kwargs` on 2 media overrides) + async lifecycle hardening (closes 04-MED-01, 04-LOW-03, 06-LOW-02, MD-01)
- [ ] **HERMES-09** — Observability + log hygiene (closes 02-LOW-01, 02-LOW-02, 05-LOW-01; consolidate `send_typing` log levels; add diagnostic logs to silent error paths; warn on dropped reserved-name metadata)
- [ ] **HERMES-10** — Input validation + UX alignment (closes 03-LOW-01, 05-LOW-02, 05-LOW-03, 02-LOW-03; validate `webhook_path` at `__init__`; optional `looksLikeJid` for media-tool schemas; align `chatlytics_login` semantics with MCP; document `get_chat_info` `{}` shape)
- [ ] **HERMES-11** — Test infra cleanup (closes 02-MED-02, 06-LOW-01; teardown for conftest platform_registry seed; smoke build cache layer or pre-built docker)
- [ ] **HERMES-12** — Release v2.1.0 (closes 05-MED-01, 04-LOW-02 docs; CHANGELOG 2.1.0 additive entry; README updates for new behavior + tool semantic clarity; pyproject bump to 2.1.0; tag `v2.1.0`; NO PyPI publish)

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
*Last updated: 2026-05-17 -- v2.0 SHIPPED. All 6 HERMES phases complete (45/45 tests, 21 tools, v2.0.0 tagged local). Audit: `.planning/v2.0-MILESTONE-AUDIT.md`. Archive: `.planning/milestones/v2.0-ROADMAP.md`. Operator push of `main` + `v2.0.0` tag pending; PyPI publish deferred per lock.*
