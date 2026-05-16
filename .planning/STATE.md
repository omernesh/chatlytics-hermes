---
gsd_state_version: 1.0
milestone: v2.0
milestone_name: — Hermes plugin v2.0 (upstream-contract rebuild)
status: planning
stopped_at: STATE.md / ROADMAP.md / PROJECT.md initialized 2026-05-17
last_updated: "2026-05-17T00:00:00.000Z"
last_activity: 2026-05-17 — repo extracted from waha-oc-plugin via git-filter-repo + .planning/ scaffolded
progress:
  total_phases: 6
  completed_phases: 0
  total_plans: 0
  completed_plans: 0
  percent: 0
---

# Project State

## Project Reference

See: `.planning/PROJECT.md`

**Core value:** A first-class Hermes Agent platform plugin that exposes the full Chatlytics REST API surface (text, media, reactions, groups, contacts, channels, search, polls, presence, profile, etc.) as Hermes tools, plus inbound WhatsApp event ingestion via the canonical `BasePlatformAdapter` contract.

**Current focus:** v2.0 — full upstream-contract rebuild against `hermes-agent>=0.14,<0.15`. The v1.x standalone-shim API is being discarded (never published, no compatibility shims).

## Current Position

Phase: HERMES-01 (not started)
Plan: —
Status: planning
Last activity: 2026-05-17 — repo extracted from waha-oc-plugin

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

Last session: 2026-05-17 — repo extracted from `omernesh/openclaw-waha-plugin` monorepo via git-filter-repo (7 commits + `hermes-1.1.0` tag preserved).
Stopped at: STATE.md / ROADMAP.md / PROJECT.md initialized.
Resume file: `.planning/ROADMAP.md` + `.planning/PROJECT.md`.
Next action: `/gsd-autonomous --from HERMES-01 --to HERMES-06` to run the milestone end-to-end.

## Operator Next Steps

- From `D:\docker\chatlytics-hermes-split\`, run: `/gsd-autonomous --from HERMES-01 --to HERMES-06`
