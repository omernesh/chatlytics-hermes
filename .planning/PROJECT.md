# Chatlytics Hermes Plugin

## What This Is

A first-class platform plugin for [Hermes Agent](https://github.com/NousResearch/hermes-agent) that connects Hermes to WhatsApp via the [Chatlytics](https://chatlytics.ai) gateway. Implements the canonical `BasePlatformAdapter` contract (`connect/disconnect/send/send_typing/get_chat_info` + media variants + inbound `MessageEvent` dispatch) and exposes Chatlytics's full action surface as Hermes tools.

## Core Value

Hermes agents get production-grade WhatsApp messaging — text, media (image/voice/video/document/animation/image-file), reactions, groups, contacts, channels, polls, presence, profile management — via a single `pip install` and a config block. Inbound webhooks arrive as Hermes-native `MessageEvent` objects with proper `MessageType` discrimination; outbound goes through Chatlytics REST with auth, retry, and gate enforcement handled upstream.

## Current Milestone: v2.0 — Hermes plugin v2.0 (upstream-contract rebuild)

**Goal:** Replace the v1.x standalone-shim API with a proper Hermes plugin against `hermes-agent>=0.14,<0.15`. Full upstream contract — `BasePlatformAdapter` subclass + `plugin.yaml` + `register(ctx)` entry point — plus the complete Chatlytics action surface as Hermes tools.

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

### Active (v2.0) -- COMPLETE 2026-05-17

- [x] **HERMES-01** -- Upstream contract scaffolding (`BasePlatformAdapter` subclass, `plugin.yaml`, `register(ctx)`, pinned dep)
- [x] **HERMES-02** -- Outbound text + control parity (`connect/disconnect/send/send_typing/get_chat_info` via httpx)
- [x] **HERMES-03** -- Inbound transport migration (aiohttp inside `connect()`, `MessageEvent` via `MessageType`)
- [x] **HERMES-04** -- Media + UX polish + cron (6 media variants, `_keep_typing()`, `cron_deliver_env_var`)
- [x] **HERMES-05** -- Full Chatlytics tool surface (every action as a Hermes tool via `ctx.register_tool()`)
- [x] **HERMES-06** -- Release + smoke test (README/CHANGELOG, smoke install, tag `v2.0.0`, no PyPI publish)

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
