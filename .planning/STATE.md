---
gsd_state_version: 1.0
milestone: v2.1
milestone_name: — Tech debt resolution + live-loader integration
status: planning
stopped_at: v2.1 ROADMAP scaffolded 2026-05-17 — 6 phases (HERMES-07..HERMES-12) scoped from v2.0 audit
last_updated: "2026-05-17T00:00:00.000Z"
last_activity: 2026-05-17 -- v2.1 milestone scaffolded; tech debt phases derived from .planning/v2.0-MILESTONE-AUDIT.md
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

**Current focus:** v2.1 — close every MED/LOW finding carried forward from the v2.0 audit and prove the plugin works against a real `PluginContext` end-to-end (live-loader integration smoke). Additive, non-breaking; ships as `v2.1.0`. NO PyPI publish (operator lock preserved).

## Current Position

Phase: HERMES-07 (not started)
Plan: —
Status: planning
Last activity: 2026-05-17 — v2.1 milestone scaffolded; 6 phases derived from v2.0 audit findings.

## v2.1 Phase Plan (6 phases, HERMES-07 → HERMES-12)

| Phase | Status | Depends on | Closes (v2.0 carry-forward) | Notes |
|-------|--------|------------|------------------------------|-------|
| HERMES-07 — Live-loader integration smoke | Ready | v2.0 shipped | 06-MED-01 | Wire `gateway.bootstrap.load_plugins()` against respx-mocked Chatlytics; assert 21 tools land on `PluginContext` registry. Strongest v2.0 test gap. |
| HERMES-08 — Async lifecycle hardening | Ready | HERMES-07 | 04-MED-01, 04-LOW-03, 06-LOW-02 | Resolve `_keep_typing` shape divergence (rename + compat wrapper OR upstream PR). Fire-and-forget initial heartbeat. Concurrency regression test for `_resolve_media_url`. |
| HERMES-09 — Observability + log hygiene | Ready | HERMES-08 | 02-LOW-01, 02-LOW-02, 05-LOW-01 | Consolidate `send_typing` log levels. Add diagnostic logs to silent error paths. Warn on dropped reserved-name metadata. |
| HERMES-10 — Input validation + UX alignment | Ready | HERMES-09 | 03-LOW-01, 05-LOW-02, 05-LOW-03, 02-LOW-03 | Validate `webhook_path` at `__init__`. Optional `looksLikeJid` for media-tool schemas. Align `chatlytics_login` semantics with MCP. Document `get_chat_info` `{}` shape. |
| HERMES-11 — Test infra cleanup | Ready | HERMES-10 | 02-MED-02, 06-LOW-01 | Teardown for conftest platform_registry seed. Smoke build cache layer or pre-built docker. Optional `--fast` flag. |
| HERMES-12 — Release v2.1.0 | Ready | HERMES-07..11 | 05-MED-01 docs, 04-LOW-02 docs | CHANGELOG 2.1.0 (additive, NOT BREAKING). README updates. pyproject bump to 2.1.0. Tag `v2.1.0`. NO PyPI publish (operator lock). |

## v2.1 Architectural Invariants (every phase preserves)

- Hermes pin stays `>=0.14,<0.15` (0.15 readiness is a v3.0 decision)
- Inbound transport stays inside `connect()` via aiohttp (no Flask, no threads)
- Tool surface stays at 21 tools (new tools would be a v2.2 minor)
- All HTTP outbound through `httpx` async; aiohttp ONLY for embedded inbound server
- All tool handlers return `{"success": bool, ...}` shape
- `chatlytics-hermes` package name preserved
- MIT license preserved
- NO PyPI publish (operator lock from v2.0 carries forward)
- v2.0 deliverables (`src/chatlytics_hermes/{__init__,adapter,client,inbound,tools}.py`, `plugin.yaml`, 21 tools, 45 tests, `scripts/smoke.sh`, README v2.0 rewrite, CHANGELOG 2.0.0) — DO NOT REGRESS. Every v2.1 phase must show 45/45 v2.0 tests still passing.

## Verification Ceiling

Autonomous-only (unchanged from v2.0): pytest + mocked aiohttp/respx + clean-venv docker smoke against real `hermes-agent @ v2026.5.16`. v2.1 ADDS: live-loader integration smoke via respx-mocked `PluginContext`. Still no live Chatlytics gateway calls. Still no PyPI publish.

## Session Continuity

Last session: 2026-05-17 — v2.0 shipped autonomously; v2.1 milestone immediately scaffolded from the v2.0 audit's carry-forward MED/LOW items. Two parallel reviews (gsd-code-review + pr-review-toolkit) running in background.
Stopped at: v2.1 milestone scaffolded, awaiting operator decision on whether to run `/gsd-autonomous --from 7 --to 12` now or after reviewing the cross-AI review outputs.
Resume file: `.planning/ROADMAP.md` v2.1 section + this STATE.md.
Next action: `/gsd-autonomous --from 7 --to 12` (after reviewing `.planning/v2.0-MILESTONE-CODE-REVIEW.md` + `.planning/v2.0-MILESTONE-PR-REVIEW.md` once both background reviews land).

## Operator Next Steps

- (v2.0 close-out) `git push origin main && git push origin v2.0.0` — operator action, still pending
- (v2.1 kick-off) After background reviews land: integrate their findings into v2.1 phases if any cross HIGH/BLOCKER thresholds, then run `/gsd-autonomous --from 7 --to 12`
- (v2.1 ship) When v2.1 completes: same operator-push pattern, then optionally bundle v2.0+v2.1 for the future PyPI publish (still operator-locked)
