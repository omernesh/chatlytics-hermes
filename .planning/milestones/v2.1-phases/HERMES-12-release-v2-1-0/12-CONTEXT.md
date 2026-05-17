---
phase: 12
phase_name: Release v2.1.0
project_code: HERMES
status: in_progress
depends_on: [HERMES-07, HERMES-08, HERMES-09, HERMES-10, HERMES-11]
closes:
  - 05-MED-01 (docs)
  - 04-LOW-02 (docs)
  - PR-review MED-04 (plugin.yaml phase-ID leak)
last_updated: "2026-05-17T00:00:00.000Z"
---

# HERMES-12 — Release v2.1.0 (CONTEXT)

## Goal

Wrap milestone v2.1 with documentation, version bump, and a **LOCAL** `v2.1.0`
git tag. Release is additive (not breaking) — v2.0.0 → v2.1.0 path is a
drop-in upgrade that closes one BLOCKER (BL-01), two HIGHs (HI-01, HI-03),
plus the MED/LOW backlog from the v2.0 milestone-wide reviews.

## Operator constraints (LOCKED — DO NOT BREAK)

<decisions>
- **LOCAL TAG ONLY.** Run `git tag v2.1.0` only. NEVER run `git push`,
  `git push --tags`, `git push origin v2.1.0`. The operator pushes manually
  after Phase 12 review passes.
- **NO PyPI publish.** No `python -m build`, no `twine upload`, no
  `gh release create`. The operator lock from v2.0 carries forward
  unchanged.
- **NO remote-facing actions of any kind.** Phase 12 ends with a clean
  local tag and a fix-passed REVIEW.md — nothing leaves the workstation.
- **Additive, NOT breaking.** v2.1.0 must be a drop-in replacement for
  v2.0.0. No public-API surface changes; no tool removed; the 21-tool
  surface stays exactly 21. v2.0 invariants (Hermes pin `>=0.14,<0.15`,
  httpx outbound, aiohttp embedded inbound only, `{"success": bool, ...}`
  response shape, MIT license, `chatlytics-hermes` package name) all
  preserved.
- **Lead with security.** The CHANGELOG and README "what's new" must lead
  with the BL-01 / HI-01 / HI-03 fixes — they are the reason this release
  exists. v2.0.0 has known BLOCKER + HIGH issues; v2.1.0 is the version
  users should run.
- **Minimal README edits.** Do NOT rewrite the README. Additive sections
  only: "What's new in v2.1" near the top, `CHATLYTICS_UPLOAD_ALLOWED_ROOTS`
  in Security (already present from Phase 8 — verify), `smoke.sh --fast`
  in Development (already present from Phase 11 — verify).
</decisions>

## Required actions (LOCKED scope — do NOT add others)

### 1. CHANGELOG 2.1.0 entry (additive, NOT breaking)

Prepend `## [2.1.0] — 2026-05-17` block above the existing 2.0.0 entry.
Sections, in this order:

- **Security** — the BL-01 / HI-01 / HI-03 fixes. Lead with these.
  - BL-01: `_keep_typing` rewrite (plain coroutine matching upstream base;
    `_typing_scope` async-cm preserves in-plugin ergonomics)
  - HI-01: `CHATLYTICS_UPLOAD_ALLOWED_ROOTS` env-configured path allowlist
    for `filePath` uploads (default-deny when unset)
  - HI-03: `**kwargs` added to `send_image` / `send_animation` for
    upstream-signature forward-compat
- **Added** — new public surface
  - `CHATLYTICS_UPLOAD_ALLOWED_ROOTS` env var
  - `scripts/smoke.sh --fast` flag (host-venv pytest, no docker)
  - `tests/test_live_loader.py` — gateway-loader integration smoke + 21-tool
    registry assertion
  - `tests/test_concurrency.py` — `_resolve_media_url` off-loop regression
    guard
  - `tests/test_observability.py` — log-level + dropped-metadata assertions
  - `webhook_path` `__init__` validation (rejects empty / no leading slash
    / `/health` collision)
- **Changed** — non-breaking behavior shifts
  - `send_typing` transport-error logs WARNING → DEBUG (log hygiene; no
    user-facing impact)
  - `chatlytics_login` returns `{"success": False, "error": "..."}` when
    upstream API succeeds but `webhook_registered=false` (aligns with MCP
    bundle behavior)
  - `_make_send_result` / `_standalone_send` / `tools._ok` now share a
    single success-shape coercion helper (MD-01 dedup)
- **Fixed** — LOW/MED items
  - Silent `ctx.get_platform` failures in `_make_tool_handler` now emit
    a DEBUG log
  - Dropped reserved-name metadata keys in `send()` now emit a WARNING
  - `plugin.yaml` `optional_env` descriptions no longer leak internal
    phase identifiers (`(HERMES-03)`, `(HERMES-04)` removed)
  - Conftest session-autouse platform_registry fixture now teardown-clean
    (snapshot at session start, restore at session end)
  - `_FakePlatformConfig` test fixture consolidated into `tests/_fixtures.py`
    (was duplicated across 7 test files)
- **Docs**
  - `chatlytics_actions` (GET catalog) vs `chatlytics_dispatch` (POST
    generic action) semantic distinction clarified in README tool catalog
  - `get_chat_info` `{}` return shape documented
  - "What's new in v2.1" section added to README

End the entry with: "**Recommended for all users.** v2.0.0 has known
BLOCKER + HIGH security issues fixed in this release."

### 2. README updates (minimal, additive)

- Add `## What's new in v2.1` section directly under `## Status`.
  Two paragraphs:
  1. Security: BL-01 / HI-01 / HI-03 fixed; v2.0.0 callers should upgrade
  2. Quality: live-loader smoke, observability hardening, log hygiene,
     test infra cleanup; tool surface unchanged at 21 tools
- VERIFY `CHATLYTICS_UPLOAD_ALLOWED_ROOTS` env var documentation is
  present in the Configuration/Security section (added in Phase 8 —
  README.md:47, 49-81). NO new edits needed if present.
- VERIFY `scripts/smoke.sh --fast` is documented in Development
  (added in Phase 11). NO new edits needed if present.
- DO NOT rewrite anything else. Don't reformat. Don't shuffle sections.

### 3. pyproject.toml version bump

- `version = "2.0.0"` → `"2.1.0"` (line 7 of `pyproject.toml`).
- No other field changes. Entry-points, deps, classifiers all unchanged.

### 4. plugin.yaml phase-ID leak removal (PR-MED-04)

- `plugin.yaml:28` — strip `, filled in HERMES-03` from
  `CHATLYTICS_WEBHOOK_PORT` description.
- `plugin.yaml:32` — strip `(HERMES-03)` from
  `CHATLYTICS_WEBHOOK_SECRET` description.
- `plugin.yaml:36` — strip `(HERMES-04)` from
  `CHATLYTICS_HOME_CHANNEL` description.
- Result: clean feature-oriented descriptions, no internal phase IDs
  leaked to the `hermes config` UI.
- Also bump `version: 2.0.0` → `2.1.0` in plugin.yaml to match
  pyproject.toml.

### 5. 04-LOW-02 + 05-MED-01 docs

- **05-MED-01** (`chatlytics_actions` vs `chatlytics_dispatch` semantic
  split): Already partly covered in README tool catalog; clarify the
  GET-vs-POST distinction in one sentence each within the existing
  "Directory / search" and "Sessions / health" subsections.
- **04-LOW-02** (`filename` injected for URL-path documents — gateway
  honor unconfirmed): Add a `## Known issues` section near the end of
  README documenting this (one paragraph: gateway may or may not honor
  `filename` for URL-path documents; track upstream).

### 6. Local tag v2.1.0

- After ALL other Phase 12 commits land, run:
  `git tag -a v2.1.0 -m "v2.1.0 — tech debt resolution + critical safety fixes"`
- Verify locally:
  - `git tag --list v2.1.0` shows the tag
  - `git show v2.1.0 --stat | head -3` shows the annotated tag info
- **NEVER** run `git push` of any flavor. Operator pushes manually.

## v2.0 invariants (every Phase 12 commit must preserve)

- 88/88 tests passing (Phase 11 baseline)
- Tool surface stays at 21 tools (no new tools, no removed tools)
- `hermes-agent>=0.14,<0.15` pin unchanged
- httpx outbound + aiohttp embedded inbound — no transport changes
- `{"success": bool, ...}` response shape preserved on every tool
- `chatlytics-hermes` package name preserved; MIT license preserved
- v2.0 backward compat: v2.1 is a drop-in upgrade

## Out of scope (NOT this phase)

- New tools / new env vars / new public API
- Live Chatlytics gateway integration (operator lock — still autonomous ceiling)
- PyPI publish
- GitHub release creation
- Remote push of any branch or tag
- v3.0 planning (Hermes 0.15 readiness — deferred milestone)

## Hard NO list (operator-confirmed)

- `git push origin main` — FORBIDDEN
- `git push origin v2.1.0` (or `--tags`) — FORBIDDEN
- `python -m build && twine upload` — FORBIDDEN
- `gh release create` — FORBIDDEN
- Any other remote-facing action — FORBIDDEN

## Acceptance criteria

1. `pyproject.toml` shows `version = "2.1.0"`
2. `plugin.yaml` shows `version: 2.1.0` and zero `HERMES-NN` substrings
3. `CHANGELOG.md` starts with `## [2.1.0] — 2026-05-17` (or current date)
   followed by Security / Added / Changed / Fixed / Docs sections; lead is
   the security block
4. `README.md` has a `## What's new in v2.1` section near the top
5. `README.md` documents `CHATLYTICS_UPLOAD_ALLOWED_ROOTS` (verify)
6. `README.md` documents `smoke.sh --fast` (verify)
7. `README.md` has a `## Known issues` section covering 04-LOW-02
8. `README.md` clarifies `chatlytics_actions` vs `chatlytics_dispatch`
9. `git tag --list v2.1.0` returns the tag (local-only)
10. NO `git push` command appears in git reflog for this phase
11. NO `python -m build` / `twine upload` / `gh release create` runs
12. 88/88 tests still passing after all edits (clean env)
