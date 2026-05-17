# Phase 1: HERMES-01 — Upstream contract scaffolding - Context

**Gathered:** 2026-05-17
**Status:** Ready for planning
**Mode:** Smart discuss — infrastructure phase, minimal context

<domain>
## Phase Boundary

Bare `BasePlatformAdapter` subclass + `plugin.yaml` + `register(ctx)` entry point + pinned `hermes-agent>=0.14,<0.15` dependency. No outbound or inbound logic yet — purely the structural contract that lets Hermes load the plugin and call `register()`.

**Phase ID:** HERMES-01 (project_code=HERMES, ordinal 01)

</domain>

<decisions>
## Implementation Decisions

### Claude's Discretion
All implementation choices are at Claude's discretion — pure infrastructure phase. Use the ROADMAP phase spec (acceptance criteria 1–5, file list, in-scope/out-of-scope) and `hermes-agent>=0.14,<0.15` canonical plugin examples (`plugins/platforms/{line,simplex,teams,google_chat,irc}/`) to drive concrete shape decisions.

### Locked from PROJECT.md (do not relitigate)
- Hermes pin: `hermes-agent>=0.14,<0.15`
- Package name preserved: `chatlytics-hermes`
- License: MIT
- v1.x compat shim: NONE (operator decision, never published)
- Distribution: GitHub-only for v2.0 (no PyPI in this milestone)
- Inbound transport: aiohttp inside `connect()` (NOT Flask thread — deferred to HERMES-03)
- All HTTP: `httpx` async (Hermes runtime convention)
- Adapter platform name: `chatlytics`

### Locked from ROADMAP HERMES-01 spec
- CREATE: `src/chatlytics_hermes/__init__.py`, `src/chatlytics_hermes/adapter.py`, `plugin.yaml`, `tests/test_register.py`
- MODIFY: `pyproject.toml` (drop flask, add hermes-agent / httpx / aiohttp pins, version=2.0.0, entry point `chatlytics = "chatlytics_hermes:register"`)
- DELETE: `src/chatlytics_adapter/` (whole tree), `tests/test_adapter.py`, `tests/test_action_parity.py`
- Abstract methods raise `NotImplementedError` (filled by HERMES-02/03/04)

</decisions>

<code_context>
## Existing Code Insights

Codebase context gathered during plan-phase research. Entry-phase scaffolding — replaces v1.x carry-over (`src/chatlytics_adapter/`, `tests/test_adapter.py`, `tests/test_action_parity.py`) which are explicitly DELETED per ROADMAP.

</code_context>

<specifics>
## Specific Ideas

- `register(ctx)` signature must match the canonical Hermes plugin entry point. Plan-phase researcher should fetch the latest `hermes-agent` 0.14.0 plugin contract docs and the line/simplex platform adapters as references.
- `plugin.yaml` manifest fields: confirm exact schema from `hermes-agent` 0.14.x (name, version, entry_point, hermes_version range).
- `[project.entry-points."hermes_agent.plugins"]` block in `pyproject.toml` is the canonical discovery mechanism.

</specifics>

<deferred>
## Deferred Ideas

- Outbound HTTP methods (HERMES-02)
- aiohttp inbound server (HERMES-03)
- Media handlers (HERMES-04)
- Tool registration via `ctx.register_tool()` (HERMES-05)
- README/CHANGELOG rewrites (HERMES-06)

</deferred>
