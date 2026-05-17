# Phase 6: HERMES-06 — Release + smoke test - Context

**Gathered:** 2026-05-17
**Status:** Ready for planning
**Mode:** Smart discuss — release-engineering phase, mostly mechanical

<domain>
## Phase Boundary

Rewrite README.md from v1.x standalone-shim perspective to v2.0 first-class-plugin perspective. CHANGELOG `2.0.0 (BREAKING)` entry. Smoke test against real `hermes-agent==0.14.0` in a clean venv (or v2026.5.16 GitHub tag — v0.14 not yet on PyPI). Tag `v2.0.0`. **NO PyPI publish** (operator-locked).

**Phase ID:** HERMES-06 (depends on HERMES-05)

</domain>

<decisions>
## Implementation Decisions

### Locked from ROADMAP HERMES-06 spec
- README.md REWRITE:
  - Drop all v1.x `ChatlyticsAdapter()` constructor snippets
  - Drop "standalone shim" / "duck-typed" language
  - Add v2.0 install: `pip install -e git+https://github.com/omernesh/chatlytics-hermes.git` (or local clone)
  - Add `register(ctx)` usage block (canonical Hermes v0.14 plugin pattern)
  - Document config: `base_url`, `api_key`, `account_id`, `webhook_port`, `webhook_secret`, `CHATLYTICS_HOME_CHANNEL` env var
  - Tool catalog summary — link to schemas in code, do NOT duplicate every signature (21 tools is too many to inline)
  - Hermes compatibility note: `hermes-agent>=0.14,<0.15` + install vector caveat (v0.14 not on PyPI yet → use Git tag `v2026.5.16`)
- CHANGELOG.md ENTRY (prepend):
  - `## 2.0.0 (2026-05-17) — BREAKING`
  - Breaking changes: removed `ChatlyticsAdapter` standalone class; entry point now `chatlytics_hermes:register`; minimum `hermes-agent` is 0.14
  - Migration guide: NONE (v1.x never published, no users)
- pyproject.toml VERIFY (already correct from HERMES-01..05): `version = "2.0.0"`, entry-points block present, deps clean (`hermes-agent>=0.14,<0.15`, `httpx>=0.27,<1`, `aiohttp>=3.9,<4`, `jsonschema>=4,<5`)
- `scripts/smoke.sh`:
  - Create fresh venv (or use docker python:3.13-slim, consistent with prior phases)
  - `pip install hermes-agent @ git+https://github.com/NousResearch/hermes-agent.git@v2026.5.16` (PyPI fallback if v0.14 ships during this session: `pip install hermes-agent>=0.14,<0.15`)
  - `pip install -e .[dev]`
  - `hermes plugins ls` (or whatever the v0.14 CLI command is) — assert output contains `chatlytics`
  - `pytest tests/` — assert 0 failures across all 44 tests from HERMES-01..05
- Git tag `v2.0.0`, push to origin

### Locked from PROJECT.md
- **NO PyPI publish** (operator decision, explicit). No `python -m build`, no `twine upload`. Manifest + entry point only — PyPI publish becomes 1-command future operation when operator chooses.
- License: MIT (preserved).
- Package name: `chatlytics-hermes` (preserved).

### Out of scope (LOCKED)
- PyPI publish (operator decision lock — even tempted "preview" tags)
- Marketplace listing / external announcement
- Live integration against real Chatlytics gateway — autonomous ceiling per PROJECT.md "Verification Ceiling"

### Claude's Discretion
- README structure: Brief intro → Install → Config (env vars + YAML) → Usage (registered automatically via entry point) → Tool catalog summary (3-line description per tool group, link to `tools.py` for schemas) → Development (smoke.sh, pytest) → License.
- CHANGELOG follows Keep-a-Changelog format. v1.x entries below are existing; prepend 2.0.0 at the top.
- `scripts/smoke.sh` MUST be a single bash script that runs in docker python:3.13-slim. Use:
  ```bash
  docker run --rm -v "$PWD:/work" -w /work python:3.13-slim sh -c '
    apt-get update && apt-get install -y --no-install-recommends git ca-certificates && \
    pip install --no-cache-dir hermes-agent @ git+https://github.com/NousResearch/hermes-agent.git@v2026.5.16 && \
    pip install --no-cache-dir -e .[dev] && \
    python -c "from chatlytics_hermes import register; print(register.__name__)" && \
    pytest tests/ -q
  '
  ```
- `hermes plugins ls` may or may not exist in v0.14 CLI. If it doesn't, smoke can validate plugin discovery via direct Python import + `pkg_resources.iter_entry_points("hermes_agent.plugins")` enumeration. Confirm at execute time by checking `/tmp/hermes-ref-v0.14.0/hermes_cli/` for the CLI command surface.

### Address forward action items from prior reviews
- 04-REVIEW MED-02 (blocking file I/O in `_resolve_media_url`) — README "Known issues" section if not fixed; or fix now if zero-risk. Quick fix: wrap `open()/read()` in `asyncio.to_thread`. Apply if safe.
- 05-REVIEW MED-01 (`_keep_typing` shape divergence from upstream base coroutine) — document in README "Architecture notes" with rationale; or upstream PR follow-up note.
- 05-REVIEW MED-02 (whatever it is) — same treatment.
- 04-REVIEW MED-01 (`_keep_typing` async-cm shape) — same.

</decisions>

<code_context>
## Existing Code Insights

- `pyproject.toml` — already at v2.0.0 from HERMES-01. Verify entry-points + deps still correct.
- `README.md` — currently v1.x content. REWRITE in this phase.
- `CHANGELOG.md` — exists with v1.x entries. PREPEND 2.0.0 entry.
- `src/chatlytics_hermes/` — full v2.0 plugin (HERMES-01..05). 21 tools registered. 44 tests passing.
- `tests/` — 5 register + 8 outbound + 9 inbound + 8 media + 3 cron + 11 tools = 44 tests.
- `scripts/` — directory may not exist yet. CREATE with `smoke.sh`.
- `/tmp/hermes-ref-v0.14.0/hermes_cli/` — check for `hermes plugins ls` command or equivalent.

</code_context>

<specifics>
## Specific Ideas

- README sections (suggested):
  1. **What is chatlytics-hermes?** (3-5 lines)
  2. **Status** — v2.0 BETA. Requires `hermes-agent>=0.14` (install via Git tag until PyPI release).
  3. **Install** — Git clone + `pip install -e .` (or pip install from Git URL once `omernesh/chatlytics-hermes` exists publicly).
  4. **Configuration** — env vars (CHATLYTICS_BASE_URL, CHATLYTICS_API_KEY, etc.) + YAML example.
  5. **Usage** — auto-registered via Hermes plugin discovery; show `hermes gateway start` example.
  6. **Tool catalog** — 21 tools grouped (messaging / media / directory / sessions). Link to `tools.py`.
  7. **Development** — clone, `pip install -e .[dev]`, `pytest tests/`, `bash scripts/smoke.sh`.
  8. **Architecture notes** — inbound aiohttp inside connect(); outbound httpx; `_keep_typing` async-cm rationale.
  9. **License** — MIT.
- CHANGELOG 2.0.0 entry mentions specifically:
  - Removed: `ChatlyticsAdapter` standalone class, Flask inbound thread, all v1.x duck-typed surface
  - Added: `BasePlatformAdapter` subclass; `register(ctx)` entry point; aiohttp inbound inside `connect()`; 6 media handlers (image/voice/video/document/animation/image_file); `_keep_typing` 30s heartbeat; `standalone_sender_fn` + `cron_deliver_env_var="CHATLYTICS_HOME_CHANNEL"`; 21 Hermes tools (messaging/media/directory/sessions)
  - Changed: minimum Python 3.10 (no change), minimum `hermes-agent==0.14`, `httpx>=0.27`, aiohttp added, flask removed, jsonschema added, plugin.yaml manifest, `[project.entry-points."hermes_agent.plugins"]` discovery
- Git tag command: `git tag -a v2.0.0 -m "v2.0.0 — full Hermes plugin rebuild" && git push origin v2.0.0`. **Confirm with operator before push** if no `origin` is set or if there's any chance of overwriting an existing tag.

</specifics>

<deferred>
## Deferred Ideas

- PyPI publish — operator decision, future milestone
- Marketplace listing
- Live Chatlytics integration test
- Beta-tester onboarding doc (`BETA-INSTALL.md` already exists; review and update if needed)

</deferred>
