---
gsd_state_version: 1.0
milestone: v3.0
milestone_name: — Breaking-change harmonization + first public release
status: Awaiting next milestone
stopped_at: v3.0 scaffolding complete; awaiting operator launch of `/gsd-autonomous --from 13 --to 21`.
last_updated: "2026-05-18T11:25:41.501Z"
last_activity: 2026-05-18 — Milestone v3.0 completed and archived
progress:
  total_phases: 9
  completed_phases: 4
  total_plans: 9
  completed_plans: 4
  percent: 44
---

# Project State

## Project Reference

See: `.planning/PROJECT.md`

**Core value:** A first-class Hermes Agent platform plugin that exposes the full Chatlytics REST API surface as Hermes tools, plus inbound WhatsApp event ingestion via the canonical `BasePlatformAdapter` contract. **v3.0 adds:** first public PyPI + npm publish, breaking tool-surface harmonization, coordinated release with the sibling Claude Code MCP bundle.

**Current focus:** v3.0 — close every deferred breaking-change item from the v2.1 Backlog, sweep v2.1 cosmetic carry-forward nits, and ship the **first public release** of both the Python plugin (chatlytics-hermes 3.0.0 on PyPI) and the sibling JS MCP bundle (chatlytics-claude-code 1.2.0 on npm). Operator lock LIFTED. TestPyPI + npm dry-run dress rehearsals precede real publishes.

## Current Position

Phase: Milestone v3.0 complete
Plan: —
Status: Awaiting next milestone
Last activity: 2026-05-18 — Milestone v3.0 completed and archived

## v3.0 Phase Plan (9 phases, HERMES-13 → HERMES-21)

| Phase | Repo | Type | Status | Depends on | Closes | Notes |
|-------|------|------|--------|------------|--------|-------|
| HERMES-13 — `get_chat_info` `_error` sentinel | chatlytics-hermes | **BREAKING tool** | Ready | v2.1 shipped | v2.1-deferred-1 | Return shape changes: `{success: false, error, _error: "code"}` for error; `{success: true, chat: {...}\|null}` for success/empty. Explicit empty-vs-error disambiguation. Tool surface stays at 21 tools; `get_chat_info` semantics break. |
| HERMES-14 — Strict JID regex on `chatId` schemas | chatlytics-hermes | **BREAKING tool** | Ready | HERMES-13 | v2.1-deferred-2 | Match the JS bundle's JID regex `/@(c\.us\|g\.us\|lid\|newsletter)$/i`. Reject phones/display names at schema level — resolution becomes caller's responsibility (use `search` first). 15 chatId schemas + 6 messageId schemas affected. |
| HERMES-15 — Adapter `send_*` collapse | chatlytics-hermes | **BREAKING lib API** | Ready | HERMES-14 | v2.1-deferred-3 | Merge `adapter.send_image`/`adapter.send_image_file` into `send_image(resource: str \| Path)`. Same for `send_animation`, `send_video`, `send_file`. **Tool surface unchanged** (tool layer already unified); internal adapter API is the break. Affects library users, NOT MCP users. |
| HERMES-16 — `smoke.sh` wheel caching | chatlytics-hermes | additive | **DONE** (120/120 tests; 0B/0H/0M/2L/2I) | HERMES-15 | v2.1-deferred-4 | Cache the hermes-agent wheel between smoke runs via `pip download` to `.smoke-cache/` + `pip install --no-index --find-links=.smoke-cache/`. Falls back to network if cache miss. Non-breaking, opt-in via `--cached` flag (default: existing behavior). |
| HERMES-17 — Hermes 0.14 API audit doc | chatlytics-hermes | docs-only | Ready | HERMES-16 | v2.1-deferred-5 (downgraded) | hermes-agent 0.15 doesn't exist yet (Nous Research project, not ours). Audit becomes inventory: every `hermes.*` import + which 0.14 module/version it came from + likely breaking surface for a future 0.15. Writes `.planning/HERMES-API-AUDIT.md`. **No code changes** — pure documentation. |
| HERMES-18 — Cosmetics sweep | chatlytics-hermes | nits | **DONE** (120/120 tests; 0B/0H/0M/0L/1I) | HERMES-17 | v2.1 Phase 9 LOW-01 + INFO-02..04, Phase 10 LOW-02 + INFO-01..03 | Close v2.1 audit's deferred LOW/INFO nits: log-level/style consistency in adapter+tools, docstring tightening, minor lint nits. No behavior change. Optional skip if reviewer pushes back. |
| HERMES-19 — Release chatlytics-hermes 3.0.0 (PyPI) | chatlytics-hermes | **release** | **DONE** (v3.0.0 LIVE on PyPI; tag pushed; main pushed) | HERMES-13..18 | first public PyPI publish | CHANGELOG 3.0.0 (BREAKING), README rewrite, pyproject + plugin.yaml bumped to 3.0.0. Build sdist + wheel. **Local wheel-install dress rehearsal** (`twine check` + scratch venv pip install + pytest against installed wheel) → **real PyPI publish** → post-publish install verification. Tag `v3.0.0` + push tag + push main. **HALT conditions:** `twine check` errors; local dress-rehearsal pytest fails; package name already taken on PyPI (`pip index versions chatlytics-hermes` pre-check); `twine upload` auth failure. |
| HERMES-20 — JS bundle update for v3.0 coordination | chatlytics-claude-code | cross-repo | **DONE** (4 sibling commits + 3 Python commits; npm pack OK; 0/0/0/0/1 INFO review) | HERMES-19 | bundle version reconciliation | **Cross-repo done.** Bumped `1.1.0`→`1.2.0` across 3 sites (drift between package.json `1.1.0` and CHANGELOG `1.1.2` reconciled). `looksLikeJid` already aligned with Python Phase 14 (verified, no code change). `chatlytics_send` resolveChatId drift bug FIXED (mirror of `chatlytics_read` pattern). Esbuild bundle regenerated (714.4 KB). 8 tools (unchanged). README already correct. Phase 21 scope guard preserved (no publish/rename/files/private-flip/tag). |
| HERMES-21 — Release chatlytics-claude-code 1.2.0 (npm) | chatlytics-claude-code | **release** | **DONE** (v1.2.0 LIVE on npm; tag pushed; main pushed; post-publish install verified) | HERMES-20 | first public npm publish | **Cross-repo**: flip `"private": true` → `false` on both `package.json` files. Add `files:` allowlist (servers/, README.md, CHANGELOG.md, LICENSE, skills/ if applicable). Rename package to `@chatlytics/claude-code` (operator's `@chatlytics` npm org). `npm pack` → `npm publish --dry-run` (no auth needed) → `npm publish --access=public` (auth via `~/.npmrc`). Tag `v1.2.0` + push. **HALT conditions:** `@chatlytics` org doesn't accept publish (token scope insufficient); `@chatlytics/claude-code` name taken; npm validation rejects manifest. |

## v3.0 Architectural Invariants (every phase preserves)

- **Hermes pin stays `>=0.14,<0.15`** — 0.15 doesn't exist yet (Nous Research's project, not ours); HERMES-17 audits the surface so a future upgrade is fast
- Inbound transport stays inside `connect()` via aiohttp (no Flask, no threads)
- Tool surface stays at **21 tools** (count unchanged; HERMES-13/14 change semantics within that count)
- All HTTP outbound through `httpx` async; aiohttp ONLY for embedded inbound server
- All tool handlers return `{"success": bool, ...}` shape; **error responses additionally include `_error: "<code>"` per HERMES-13** (extension of v2.1 contract, not replacement)
- `chatlytics-hermes` Python package name preserved
- `@chatlytics/claude-code` npm package name (new — first publish under operator's `@chatlytics` org)
- MIT license preserved (both repos)
- **OPERATOR LOCK LIFTED** for v3.0 — TestPyPI dress rehearsal precedes real PyPI publish; npm dry-run precedes real npm publish; both publishes go live this milestone
- v2.1 deliverables — 88/88 tests, 21 tools, BL-01/HI-01/HI-03 fixed — **DO NOT REGRESS**. Every v3.0 phase must show v2.1 tests still pass *except* where HERMES-13/14 explicitly change behavior (those tests get updated, not deleted; old assertion → new assertion with breaking-change note in CHANGELOG)

## Verification Ceiling (v3.0)

Autonomous-only base (unchanged from v2.1):

- pytest + mocked aiohttp/respx + clean-venv docker smoke against real `hermes-agent @ v2026.5.16`
- Live-loader integration smoke via respx-mocked `PluginContext`
- Still no live Chatlytics gateway calls

**v3.0 additions:**

- TestPyPI dress rehearsal: `python -m build && twine upload --repository testpypi` → `pip install --index-url https://test.pypi.org/simple/ chatlytics-hermes` in a clean venv → run full pytest suite against the installed wheel
- Real PyPI publish + post-publish install verification (same flow against real PyPI)
- `npm publish --dry-run` validates manifest + tarball without going live
- Real npm publish + `npm install @chatlytics/claude-code` in a scratch directory to verify the published artifact loads

## Credentials Required (v3.0)

| Credential | Required for | Status | Halt if missing |
|------------|--------------|--------|-----------------|
| Real PyPI token (in `~/.pypirc[pypi]`) | HERMES-19 real publish | **CONFIGURED** | — |
| TestPyPI token | (not used — replaced with local wheel-install dress rehearsal) | n/a | n/a |
| npm token (in `~/.npmrc`) | HERMES-21 real publish | **CONFIGURED** (verified `npm whoami` → omernesh) | — |

**Token scope note (npm):** Token is a granular access token. Can publish, cannot introspect orgs (`npm org ls` returns 403). Phase 21 publish-attempt is the actual gate for `@chatlytics` org membership / package-name availability.

**Dress rehearsal note (PyPI):** Operator chose local wheel-install dress rehearsal over TestPyPI (avoids managing two tokens). Phase 19 runs `twine check dist/*` + scratch-venv install + pytest against the installed wheel. PyPI runs the same validators at upload time, so metadata errors are caught BEFORE public indexing.

## Session Continuity

Last session: 2026-05-18 — v2.1 milestone shipped publicly (main + v2.1.0 tag pushed). v3.0 milestone scaffolded with 9 phases (HERMES-13..21) covering breaking-change harmonization, cosmetic cleanup, first public PyPI publish, and cross-repo coordination with the chatlytics-claude-code JS bundle for first npm publish.
Stopped at: v3.0 scaffolding complete; awaiting operator launch of `/gsd-autonomous --from 13 --to 21`.
Resume file: `.planning/ROADMAP.md` v3.0 section + this STATE.md.
Next action: `/gsd-autonomous --from 13 --to 21`. HERMES-13..18 are sequential single-repo work; HERMES-19 publishes to PyPI (TestPyPI first); HERMES-20..21 cross-repo into `C:/Users/omern/.claude/plugins/marketplaces/chatlytics-claude-code/` for npm publish.

## Operator Next Steps

- Start the next milestone with /gsd:new-milestone
