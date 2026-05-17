---
gsd_state_version: 1.0
milestone: v2.1
milestone_name: — Critical safety fixes + tech debt resolution + live-loader integration
status: Awaiting next milestone
stopped_at: v2.1.0 LOCAL TAG created; awaiting operator review and manual push.
last_updated: "2026-05-17T14:36:06.536Z"
last_activity: 2026-05-17 — Milestone v2.1 completed and archived
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

**Current focus:** v2.1 — close every MED/LOW finding carried forward from the v2.0 audit and prove the plugin works against a real `PluginContext` end-to-end (live-loader integration smoke). Additive, non-breaking; ships as `v2.1.0`. NO PyPI publish (operator lock preserved).

## Current Position

Phase: Milestone v2.1 complete
Plan: —
Status: Awaiting next milestone
Last activity: 2026-05-17 — Milestone v2.1 completed and archived

## v2.1 Phase Plan (6 phases, HERMES-07 → HERMES-12)

| Phase | Status | Depends on | Closes | Notes |
|-------|--------|------------|--------|-------|
| HERMES-07 — Live-loader integration smoke | Ready | v2.0 shipped (local) | 06-MED-01, GSD-MD-04 | Wire `gateway.bootstrap.load_plugins()` against respx-mocked Chatlytics; assert 21 tools land. **Includes BL-01 + HI-01 + HI-03 regression tests (xfail-marked here, un-xfailed in HERMES-08).** Strongest v2.0 test gap + the harness gap that hid BL-01. |
| HERMES-08 — Critical safety fixes + async lifecycle | Ready | HERMES-07 | **BL-01 (BLOCKER), HI-01 (HIGH), HI-03 (HIGH), MD-01**, 04-MED-01, 04-LOW-03, 06-LOW-02 | **Top of the milestone in importance.** `_keep_typing` rewrite as plain coroutine + `_typing_scope` async-cm wrapper for in-plugin sites. `filePath` allowlist via `CHATLYTICS_UPLOAD_ALLOWED_ROOTS` env var. `**kwargs` on `send_image`/`send_animation`. Success-shape coercion dedup. Plus original async lifecycle items. |
| HERMES-09 — Observability + log hygiene | Done | HERMES-08 | 02-LOW-01, 02-LOW-02, 05-LOW-01, LO-11 | DONE 2026-05-17. Consolidated `send_typing` log levels (WARN→DEBUG via internal `_send_typing_once`); added diagnostic logs to 6 silent paths; reserved-metadata WARN per dropped key; new `tests/test_observability.py` (7 tests). 65/65 tests pass. REVIEW APPROVE_WITH_NITS, WARNING-01 + INFO-01 fixed in fix-pass; LOW-01 + INFO-02..04 deferred to Phase 10/12. |
| HERMES-10 — Input validation + UX alignment | Ready | HERMES-09 | 03-LOW-01, 05-LOW-02, 05-LOW-03, 02-LOW-03 | Validate `webhook_path` at `__init__`. Optional `looksLikeJid` for media-tool schemas. Align `chatlytics_login` semantics with MCP. Document `get_chat_info` `{}` shape. |
| HERMES-11 — Test infra cleanup | Done | HERMES-10 | 02-MED-02, 06-LOW-01, PR-MED-03, PR-INFO-02 | DONE 2026-05-17. Conftest yield teardown + idempotency guard; tests/_fixtures.FakePlatformConfig consolidates 7 dup shims; scripts/smoke.sh --fast (opt-in) + pip --retries 3. 88/88 tests pass. REVIEW APPROVE (0 BLOCKER, 0 HIGH, 0 MED, 1 LOW, 2 INFO; no fix-pass needed). |
| HERMES-12 — Release v2.1.0 | Done | HERMES-07..11 | 05-MED-01 docs, 04-LOW-02 docs, PR-MED-04 | DONE 2026-05-17. CHANGELOG 2.1.0 prepended (security-led, additive). README + What's new + Known issues. pyproject + plugin.yaml bumped to 2.1.0. plugin.yaml phase-IDs stripped. `v2.1.0` LOCAL tag created (no push, no PyPI per operator lock). 88/88 tests pass. REVIEW APPROVE (0 BLOCKER/HIGH/MED/LOW, 2 INFO). |

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

Last session: 2026-05-17 — v2.1 milestone shipped autonomously (all 6 phases: HERMES-07 through HERMES-12). BL-01 / HI-01 / HI-03 fixed and locked under test. CHANGELOG / README / pyproject.toml / plugin.yaml updated. `v2.1.0` annotated tag created LOCAL ONLY (no push, no PyPI per operator lock).
Stopped at: v2.1.0 LOCAL TAG created; awaiting operator review and manual push.
Resume file: `.planning/ROADMAP.md` v2.1 section + this STATE.md + `.planning/phases/HERMES-12-release-v2-1-0/`.
Next action: Operator review of v2.1.0 release artifact, then `git push origin main && git push origin v2.1.0` when ready. Optionally delete the local `v2.0.0` tag (points at known-broken artifact superseded by v2.1.0).

## Operator Next Steps

- Start the next milestone with /gsd:new-milestone
