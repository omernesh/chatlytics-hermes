---
gsd_state_version: 1.0
milestone: v2.0
milestone_name: — Hermes plugin v2.0 (upstream-contract rebuild)
status: complete
stopped_at: v2.0 milestone shipped 2026-05-17 — 6/6 phases, 45/45 tests, 21 tools, v2.0.0 tagged local
last_updated: "2026-05-17T00:00:00.000Z"
last_activity: 2026-05-17 -- v2.0 milestone shipped (6/6 phases, 45/45 tests, v2.0.0 tagged local)
progress:
  total_phases: 6
  completed_phases: 6
  total_plans: 6
  completed_plans: 6
  percent: 100
---

# Project State

## Project Reference

See: `.planning/PROJECT.md`

**Core value:** A first-class Hermes Agent platform plugin that exposes the full Chatlytics REST API surface (text, media, reactions, groups, contacts, channels, search, polls, presence, profile, etc.) as Hermes tools, plus inbound WhatsApp event ingestion via the canonical `BasePlatformAdapter` contract.

**Current focus:** v2.0 — full upstream-contract rebuild against `hermes-agent>=0.14,<0.15`. The v1.x standalone-shim API is being discarded (never published, no compatibility shims).

## Current Position

Phase: -- (v2.0 milestone complete)
Plan: --
Status: complete
Last activity: 2026-05-17 -- v2.0 SHIPPED (6/6 phases, 45/45 tests, v2.0.0 tagged local). Audit: `.planning/v2.0-MILESTONE-AUDIT.md`. Archive: `.planning/milestones/v2.0-ROADMAP.md`.

## v2.0 Phase Plan (6 phases, HERMES-01 → HERMES-06)

| Phase | Status | Depends on | Notes |
|-------|--------|------------|-------|
| HERMES-01 — Upstream contract scaffolding | Ready | Nothing | Bare `BasePlatformAdapter` subclass + `plugin.yaml` + `register(ctx)` + pinned `hermes-agent>=0.14,<0.15`. Acceptance: `register(MockCtx())` registers platform `chatlytics`. |
| HERMES-02 — Outbound text + control parity | Ready | HERMES-01 | Implement `connect/disconnect/send/send_typing/get_chat_info` via httpx → Chatlytics REST. `SendResult.ok=True` against mocked Chatlytics. |
| HERMES-03 — Inbound transport migration | Ready | HERMES-02 | aiohttp inbound server inside `connect()` (NOT separate Flask thread). Normalize webhook JSON → `MessageEvent` via `MessageType.{TEXT,IMAGE,AUDIO,...}` → `self.handle_message(event)`. |
| HERMES-04 — Media + UX polish + cron | Ready | HERMES-03 | All 6 media-send variants: `send_image / send_voice / send_video / send_document / send_animation / send_image_file` wired to Chatlytics media endpoints. `_keep_typing()` 30s heartbeat. `cron_deliver_env_var="CHATLYTICS_HOME_CHANNEL"` + `standalone_sender_fn`. |
| HERMES-05 — Full Chatlytics tool surface | Ready | HERMES-04 | EVERY Chatlytics action exposed as a Hermes tool via `ctx.register_tool()`. Source the tool list from the Claude Code plugin's MCP server bundle (`chatlytics-mcp.bundle.js`) and Chatlytics `/api/v1/actions` enumeration. |
| HERMES-06 — Release + smoke test | Ready | HERMES-05 | README rewrite (drop v1.1.0 standalone-shim language), CHANGELOG `2.0.0 (BREAKING)`, smoke: `pip install -e .[dev] hermes-agent==0.14.0 && hermes plugins ls \| grep chatlytics`. Tag `v2.0.0`. **NO PyPI publish in this milestone.** |

## v2.0 Architectural Invariants (every phase preserves)

- Pinned upstream dep: `hermes-agent>=0.14,<0.15`. Bump only when the entire plugin re-verified against the new minor.
- Inbound transport lives **inside** `connect()` via aiohttp — no separate threads, no Flask. The plugin owns its own event loop integration.
- Outbound goes through Chatlytics REST (`/api/v1/send`, `/api/v1/send-media`, `/api/v1/actions`, `/api/v1/typing`, `/api/v1/chat`). No direct WAHA calls.
- All tool handlers return `{"success": bool, ...}` shape compatible with Hermes tool result conventions.
- `chatlytics-hermes` package name is preserved (per pyproject.toml `name = "chatlytics-hermes"`). v2.0 only changes API surface, not distribution name.
- License: MIT (preserved from v1.x).

## Verification Ceiling

Autonomous-only: pytest + mocked aiohttp/respx + smoke install in a clean venv against real `hermes-agent==0.14.0`. No live Chatlytics gateway calls in unit tests. No PyPI publish in this milestone.

## Session Continuity

Last session: 2026-05-17 -- v2.0 milestone shipped end-to-end (autonomous orchestration). All 6 HERMES phases executed, reviewed, and verified. v2.0.0 annotated tag created locally; NOT pushed.
Stopped at: milestone lifecycle complete; awaiting operator push.
Resume file: next milestone -- not yet scoped.
Next action: operator push (`git push origin main && git push origin v2.0.0`), then scope v2.1.

## Operator Next Steps

- `git push origin main` -- push milestone-complete commits
- `git push origin v2.0.0` -- push the v2.0.0 annotated tag (autonomously created, NOT pushed)
- (optional, later) PyPI publish via `python -m build && twine upload dist/*` -- explicitly deferred per ROADMAP lock
- Scope v2.1 milestone: live-loader integration smoke (06-MED-01), `_keep_typing` async-cm shape decision (04-MED-01), `send_typing` log flood fix (02-LOW-02), see `.planning/v2.0-MILESTONE-AUDIT.md`
