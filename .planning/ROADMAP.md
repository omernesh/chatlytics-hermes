# ROADMAP — chatlytics-hermes

## v3.0 — Breaking-change harmonization + first public release (PyPI + npm)

Close every deferred breaking-change item from the v2.1 Backlog, sweep v2.1 cosmetic carry-forward nits, and ship the **first public release** on PyPI (`chatlytics-hermes 3.0.0`) and npm (`@chatlytics/claude-code 1.2.0`). Nine phases (HERMES-13..21), designed for end-to-end execution via `/gsd-autonomous --from 13 --to 21`. Operator lock LIFTED — TestPyPI/npm dry-run dress rehearsals precede real publishes.

### Phase 13: `get_chat_info` `_error` sentinel (BREAKING tool surface)
**Goal:** Disambiguate empty-success vs error on `get_chat_info`. New return shape: `{success: true, chat: {...}}` for chat-found, `{success: true, chat: null}` for chat-not-found (legitimate empty), `{success: false, error: "<human msg>", _error: "<machine code>"}` for transport/auth/server errors. Closes v2.1 deferred item 1 (sentinel `_error` key on `get_chat_info`).

**Depends on:** v2.1 shipped (88/88 baseline)

**In scope:**
- `src/chatlytics_hermes/tools.py::chatlytics_get_chat_info` — update return shape
- `src/chatlytics_hermes/adapter.py::get_chat_info` — three-way return (chat dict | None | raise)
- Update existing v2.1 tests that asserted `{}` ambiguous shape → new explicit shape
- New tests covering all three branches (found, empty, error)
- Docstring + README "Tool reference" section updated

**Out of scope:**
- Other tools' error shape (only `get_chat_info` changes in this phase; broader rollout if needed is a v3.1 minor)
- Caller migration (callers using the old `{}` shape break by design — that's the point)

**Files (create/modify):**
- MODIFY `src/chatlytics_hermes/tools.py`
- MODIFY `src/chatlytics_hermes/adapter.py`
- MODIFY `tests/test_adapter.py` (or wherever `get_chat_info` is tested — check existing v2.1 test files)
- MODIFY `README.md` — Tool reference section
- MODIFY `CHANGELOG.md` — under unreleased / 3.0.0 BREAKING entries

**Acceptance criteria (all must pass autonomously):**
1. `pytest tests/ -q` — 88/88 baseline + N new tests for the three branches; zero regressions outside the explicitly-updated `get_chat_info` tests
2. `chatlytics_get_chat_info(chatId="<known>")` returns `{success: true, chat: {...}}`
3. `chatlytics_get_chat_info(chatId="<unknown>")` returns `{success: true, chat: null}`
4. `chatlytics_get_chat_info(chatId="<causes-500>")` returns `{success: false, error: "<msg>", _error: "<code>"}` (code drawn from a small set: `transport`, `auth`, `server`, `validation`)
5. CHANGELOG 3.0.0 has BREAKING entry: `### Breaking — chatlytics_get_chat_info return shape`

---

### Phase 14: Strict JID regex on `chatId` schemas (BREAKING tool surface)
**Goal:** Tighten `chatId` validation across all tool schemas to match the JS bundle's JID regex `/@(c\.us|g\.us|lid|newsletter)$/i`. Reject phone numbers, display names, and ambiguous strings at the schema layer — chat-resolution becomes the caller's responsibility (call `chatlytics_search` first). Closes v2.1 deferred item 2.

**Depends on:** HERMES-13 (sequencing — schema tightening AFTER return-shape change so error path is uniform)

**In scope:**
- Replace v2.1's permissive `_chat_id_field` helper (rejects empty + control chars only) with strict JID-only validator
- All 15 chatId-bearing schemas in `src/chatlytics_hermes/tools.py` get the strict validator
- Rejected inputs return `{success: false, error: "Invalid chatId (expected JID format @c.us|@g.us|@lid|@newsletter)", _error: "validation"}` per the Phase 13 shape
- Update v2.1's `tests/test_validation.py` — 21 schemas were tightened in v2.1; those tests need updating to assert the *new* strict accept-set
- Optional: helpful error message points caller to `chatlytics_search` for resolution

**Out of scope:**
- Server-side JID validation (Chatlytics REST already does this — this phase is local schema enforcement for better UX)
- Phone-number-to-JID auto-resolution (caller's job; we won't be magic)

**Files (create/modify):**
- MODIFY `src/chatlytics_hermes/tools.py` — `_chat_id_field` helper + 15 schema sites
- MODIFY `tests/test_validation.py` — flip v2.1 permissive-accept assertions to v3.0 strict-reject assertions
- MODIFY `README.md` — document strict JID requirement + caller responsibility
- MODIFY `CHANGELOG.md` — BREAKING entry

**Acceptance criteria:**
1. `chatlytics_send(chatId="12025551234", text="hi")` → `{success: false, error: "Invalid chatId...", _error: "validation"}` (was: passed through to API in v2.1)
2. `chatlytics_send(chatId="12025551234@c.us", text="hi")` → proceeds normally (JID format accepted)
3. `chatlytics_send(chatId="Omer Nesher", text="hi")` → rejected with helpful error mentioning `chatlytics_search`
4. All 4 JID families accepted: `@c.us`, `@g.us`, `@lid`, `@newsletter`
5. pytest passes; v2.1 permissive-accept tests are now flipped to strict-reject assertions (NOT deleted — converted, with CHANGELOG cross-ref)

---

### Phase 15: Adapter `send_*` collapse (BREAKING library API)
**Goal:** Merge `adapter.send_image(chatId, mediaUrl, ...)` and `adapter.send_image_file(chatId, filePath, ...)` into one `adapter.send_image(chatId, resource: str | Path, ...)` where `resource` is auto-detected as a URL or local path. Same collapse for `send_animation`, `send_video`, `send_file`. **Tool surface unchanged** — `chatlytics_send_image` and friends already unify both at the tool layer (see `tools.py:712-748`); this phase collapses the lower adapter layer. Closes v2.1 deferred item 3.

**Depends on:** HERMES-14 (sequencing only — no functional dependency)

**In scope:**
- `src/chatlytics_hermes/adapter.py` — collapse paired methods; old method names removed (no deprecation alias — clean break)
- `src/chatlytics_hermes/tools.py::_resolve_resource` simplifies (no longer needs to dispatch to two adapter methods)
- Adapter-layer tests updated to call the new unified method
- Resource detection: starts with `http://` or `https://` → URL; otherwise → local path (validated against `CHATLYTICS_UPLOAD_ALLOWED_ROOTS` per v2.1 Phase 8 — DO NOT REGRESS the allowlist check)

**Out of scope:**
- Tool surface changes (already unified in v2.0 at tool layer)
- Deprecation aliases for old adapter methods (operator preference: clean break)
- Adding new media types (only collapse existing image/animation/video/file)

**Files (create/modify):**
- MODIFY `src/chatlytics_hermes/adapter.py`
- MODIFY `src/chatlytics_hermes/tools.py` (only `_resolve_resource` and call sites within tool handlers)
- MODIFY `tests/test_adapter.py` (or wherever paired methods are tested)
- MODIFY `CHANGELOG.md` — BREAKING entry under "Library API" sub-section

**Acceptance criteria:**
1. `adapter.send_image(chatId, "https://example.com/cat.jpg")` → uploads via URL path (no local file access)
2. `adapter.send_image(chatId, "/allowed/root/cat.jpg")` → uploads via file path (allowlist enforced)
3. `adapter.send_image(chatId, "/etc/passwd")` → rejected with same error as v2.1 HI-01 fix (allowlist preserved)
4. `adapter.send_image_file` symbol is gone — `getattr(adapter, "send_image_file", None) is None` (NOT a deprecation alias)
5. Same for `send_animation`, `send_video`, `send_file`
6. pytest passes; all v2.1 HI-01 regression tests still pass (allowlist unchanged)

---

### Phase 16: `smoke.sh` wheel caching (additive)
**Goal:** Cache the `hermes-agent` wheel between smoke runs to cut docker rebuild time. Non-breaking, opt-in via `--cached` flag (default behavior unchanged). Closes v2.1 deferred item 4. Closes PR-MED-03's remaining open portion (v2.1 only added `--retries 3`).

**Depends on:** HERMES-15

**In scope:**
- `scripts/smoke.sh` — new `--cached` flag (default off for back-compat)
- When `--cached` is on: `pip download hermes-agent==<pinned-version> -d .smoke-cache/` once, then `pip install --no-index --find-links=.smoke-cache/ hermes-agent` for subsequent runs
- Cache miss (e.g., version pin changed) → fall back to network download + repopulate cache
- `.smoke-cache/` added to `.gitignore`
- README "Development" section documents `--cached`

**Out of scope:**
- Pre-built docker base image (heavier solution — defer to v3.1 if needed)
- CI cache integration (no CI exists yet)
- Caching plugin dependencies (only hermes-agent — the slow one)

**Files (create/modify):**
- MODIFY `scripts/smoke.sh`
- MODIFY `.gitignore` — add `.smoke-cache/`
- MODIFY `README.md` — Development section

**Acceptance criteria:**
1. `bash scripts/smoke.sh` (no args) — behaves exactly as v2.1 (`--retries 3`, no caching)
2. `bash scripts/smoke.sh --cached` (first run) — downloads to `.smoke-cache/`, then installs from cache, runs tests
3. `bash scripts/smoke.sh --cached` (second run) — installs from cache only (network calls down by ≥ 90%)
4. `bash scripts/smoke.sh --cached --fast` — works (composes with v2.1's `--fast` flag)
5. Cache miss (e.g., delete .smoke-cache/wheel, re-run) — falls back to network gracefully

---

### Phase 17: Hermes 0.14 API audit doc (docs-only)
**Goal:** Inventory every `hermes.*` import in chatlytics-hermes + which 0.14 module/version introduced it + likely breaking surface for a future 0.15. Writes `.planning/HERMES-API-AUDIT.md`. No code changes. v2.1 deferred item 5 downgraded from "0.15 readiness" (hermes-agent 0.15 doesn't exist; Nous Research's project, not ours) to "0.14 API surface inventory."

**Depends on:** HERMES-16 (sequencing only)

**In scope:**
- Grep all `from hermes` and `import hermes` in `src/` and `tests/`
- For each symbol, document: module path, what it's used for, public-vs-private (does it start with `_`?), how stable (check `/tmp/hermes-ref-v0.14.0/RELEASE_v0.14.0.md` for change frequency)
- Identify risk surface: which imports are most likely to break in a hypothetical 0.15 (e.g., underscore-prefixed internals, recently-added APIs)
- Recommendation section: if 0.15 lands, here are the imports to check first

**Out of scope:**
- Actually upgrading anything (0.15 doesn't exist)
- Writing a compat shim (premature without a real 0.15 to compat against)
- Modifying any source file

**Files (create/modify):**
- CREATE `.planning/HERMES-API-AUDIT.md`

**Acceptance criteria:**
1. `.planning/HERMES-API-AUDIT.md` exists
2. Lists ≥ all `from hermes` / `import hermes` lines in `src/chatlytics_hermes/`
3. Each entry has: import path, used-where, public-vs-private flag, stability note
4. Has a "Risk surface for future 0.15" recommendations section
5. References `/tmp/hermes-ref-v0.14.0/RELEASE_v0.14.0.md` for change-frequency signals
6. No `.py` files modified in the phase commits (audit is doc-only)

---

### Phase 18: Cosmetics sweep (nits)
**Goal:** Close v2.1 audit's deferred LOW/INFO carry-forward: Phase 9 LOW-01 + INFO-02..04, Phase 10 LOW-02 + INFO-01..03. Log-level/style consistency in adapter+tools, docstring tightening, minor lint nits. No behavior change.

**Depends on:** HERMES-17 (sequencing — cosmetics last before release)

**In scope:**
- Read `.planning/milestones/v2.1-phases/HERMES-09-observability-log-hygiene/09-REVIEW.md` and `HERMES-10-input-validation-ux-alignment/10-REVIEW.md` (or REVIEW-FIX) to extract the specific deferred LOW/INFO items
- Apply each fix individually — atomic commits per nit
- Run pytest after each fix to confirm zero behavior change

**Out of scope:**
- New features
- Anything that changes test counts (only style/doc/comment changes)
- Re-opening closed findings — only the explicitly-deferred ones

**Files (create/modify):**
- MODIFY various source files for style nits (the v2.1 REVIEW docs spell them out)

**Acceptance criteria:**
1. Every LOW/INFO item from v2.1 deferred audit list is addressed (or has a written "still deferred" justification in CHANGELOG)
2. pytest 88/88 still passes (or 88+N if Phase 13/14/15 added tests; the regression bar is "no test count decreases")
3. `git diff --stat` shows only style/doc/comment-line changes
4. Final REVIEW pass clean or fix-pass closes any remaining nits

---

### Phase 19: Release chatlytics-hermes 3.0.0 (PyPI)
**Goal:** First public PyPI publish of chatlytics-hermes. CHANGELOG 3.0.0 (BREAKING) entry, README rewrite for breaking changes, pyproject + plugin.yaml bumped to 3.0.0. TestPyPI dress rehearsal validates the artifact before real PyPI publish. Tag `v3.0.0`, push main + tag.

**Depends on:** HERMES-13..18 (all v3.0 substantive work must land first)

**In scope:**
- CHANGELOG 3.0.0 entry, BREAKING-led: `get_chat_info` shape (Phase 13), JID regex (Phase 14), adapter collapse (Phase 15), plus additive items (16, 18) and docs (17). Migration notes for breaking changes.
- README rewrite: "v3.0 Breaking Changes" section near the top, migration section, updated tool reference for Phase 13/14 changes
- `pyproject.toml` — `version = "3.0.0"`, double-check `[project.urls]` are correct for public publish (Homepage, Repository, Documentation)
- `plugin.yaml` — `version: 3.0.0`
- Build: `python -m build` → produces sdist + wheel in `dist/`
- `twine check dist/*` — validate metadata
- **TestPyPI publish:** `twine upload --repository testpypi dist/*` (needs TestPyPI token; HALT if missing)
- Install from TestPyPI in a scratch venv: `pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ chatlytics-hermes==3.0.0` (the second index is for transitive deps that don't exist on TestPyPI)
- Run `pytest` against the installed wheel → must pass
- **Real PyPI publish:** `twine upload dist/*` (needs real PyPI token; HALT if missing)
- Verify on https://pypi.org/project/chatlytics-hermes/
- Install from real PyPI in another scratch venv → run pytest → must pass
- `git tag v3.0.0` (annotated) + `git push origin main && git push origin v3.0.0`
- HALT conditions: TestPyPI/PyPI credentials missing; package name `chatlytics-hermes` already taken (highly unlikely but check at publish time); smoke install from TestPyPI fails; `twine check` finds metadata errors

**Out of scope:**
- npm publish (Phase 21)
- JS bundle work (Phase 20)
- Backporting to 2.x

**Files (create/modify):**
- MODIFY `CHANGELOG.md`
- MODIFY `README.md`
- MODIFY `pyproject.toml`
- MODIFY `plugin.yaml`
- CREATE `scripts/release.sh` (optional — codifies the build + TestPyPI + PyPI + tag flow for reproducibility)

**Acceptance criteria:**
1. `chatlytics-hermes==3.0.0` installs from `pip install chatlytics-hermes` (real PyPI) in a scratch venv
2. After install, `from chatlytics_hermes import register; register.__name__ == "register"` works
3. Full pytest suite passes against the installed wheel (88+N tests)
4. Tag `v3.0.0` exists on GitHub: `git ls-remote --tags origin | grep v3.0.0`
5. PyPI page at https://pypi.org/project/chatlytics-hermes/ shows version 3.0.0 with correct description + repository link
6. `twine check dist/*` — clean (no metadata warnings)
7. CHANGELOG 3.0.0 lists every breaking change with migration guidance

---

### Phase 20: JS bundle update for v3.0 coordination (cross-repo)
**Goal:** Bring the sibling chatlytics-claude-code JS MCP bundle in sync with chatlytics-hermes 3.0.0. Bump `1.1.2` → `1.2.0` (MINOR — no JS API breaks). Reconcile drifted version/tool-count documentation. Tighten `looksLikeJid()` to match the Python plugin's stricter rule. Fix `chatlytics_send` to call `resolveChatId()` (currently bypasses it — drift bug). Rebuild esbuild bundle.

**Depends on:** HERMES-19 (Python release published — JS bundle CHANGELOG can reference the published 3.0.0)

**In scope (in `C:/Users/omern/.claude/plugins/marketplaces/chatlytics-claude-code/`):**
- `servers/chatlytics-mcp.js` — tighten `looksLikeJid()` regex to match the Python regex from HERMES-14; add `resolveChatId` call to `chatlytics_send` handler (currently only `chatlytics_read` resolves)
- `package.json` + `servers/package.json` — version bump `1.1.2` → `1.2.0`; **prepare for publish** by adding `files:` allowlist (do not flip `private` here — that's Phase 21)
- `CHANGELOG.md` — 1.2.0 entry, mention sync with chatlytics-hermes 3.0.0
- `README.md` — reconcile tool count (was "6 MCP tools" in 1.1.0; actually 8 since 1.1.x — was drifted)
- Rebuild bundle: `npm --prefix servers run build` (or esbuild command per package.json scripts)
- `servers/chatlytics-mcp.bundle.js` — regenerated artifact (committed as in v1.1.x pattern)
- Run smoke test: `npm --prefix servers test`

**Out of scope:**
- npm publish (Phase 21)
- Flipping `private: true` → `false` (Phase 21)
- New tools / new dependencies

**Files (create/modify) — all in `C:/Users/omern/.claude/plugins/marketplaces/chatlytics-claude-code/`:**
- MODIFY `package.json`
- MODIFY `servers/package.json`
- MODIFY `servers/chatlytics-mcp.js`
- MODIFY `servers/chatlytics-mcp.bundle.js` (rebuild output)
- MODIFY `CHANGELOG.md`
- MODIFY `README.md`

**Acceptance criteria:**
1. `node servers/chatlytics-mcp.bundle.js --version` (or equivalent) reports 1.2.0
2. `looksLikeJid()` regex matches Python's strict rule
3. `chatlytics_send` test exercises name-resolution path (search-based) for non-JID inputs
4. `npm --prefix servers test` passes
5. CHANGELOG 1.2.0 entry references chatlytics-hermes 3.0.0 release
6. README tool count is accurate (8 tools)
7. Commits land cleanly in the sibling repo (separate `git log` from chatlytics-hermes-split)

---

### Phase 21: Release chatlytics-claude-code 1.2.0 (npm)
**Goal:** First public npm publish under operator's `@chatlytics` org. Flip `"private": true` → `false`, rename package to `@chatlytics/claude-code` (scoped), add `files:` allowlist, dress-rehearse via `npm publish --dry-run`, then real `npm publish --access=public`. Tag `v1.2.0` + push.

**Depends on:** HERMES-20

**In scope (in `C:/Users/omern/.claude/plugins/marketplaces/chatlytics-claude-code/`):**
- `package.json` — `"name": "@chatlytics/claude-code"` (was `chatlytics-claude-code`), `"private": false` (was `true`), `"files": ["servers/", "skills/", "README.md", "CHANGELOG.md", "LICENSE"]` (or appropriate allowlist)
- `servers/package.json` — same private flag flip if applicable; keep its `private: true` IF it's an internal sub-package not meant to publish separately (verify)
- `.npmignore` — ensure `.planning/`, `node_modules/`, build artifacts (other than bundle.js) aren't published
- `npm pack` — generates a tarball; inspect with `tar tzf <tarball>.tgz` to confirm only intended files
- `npm publish --dry-run --access=public` — validates manifest, simulates publish (no auth needed)
- `npm publish --access=public` — real publish (`~/.npmrc` token configured)
- Verify on https://www.npmjs.com/package/@chatlytics/claude-code
- `git tag v1.2.0` (annotated) + push tag + push main on sibling repo
- HALT conditions: `@chatlytics` org doesn't accept publish (token scope insufficient); `@chatlytics/claude-code` name taken or reserved; `npm publish --dry-run` reports manifest errors; published tarball doesn't install cleanly in `npm install @chatlytics/claude-code` smoke test

**Out of scope:**
- Python repo work (already done in Phase 19)
- GitHub Actions / CI setup (none exists; defer)
- Backporting to 1.1.x

**Files (create/modify) — all in `C:/Users/omern/.claude/plugins/marketplaces/chatlytics-claude-code/`:**
- MODIFY `package.json`
- MODIFY `servers/package.json` (review private flag)
- CREATE `.npmignore` (if not present)
- MODIFY `CHANGELOG.md` — note "First npm publish" in 1.2.0 entry

**Acceptance criteria:**
1. `@chatlytics/claude-code@1.2.0` installs from `npm install @chatlytics/claude-code` in a scratch directory
2. Installed package contains `servers/chatlytics-mcp.bundle.js` and runs as an MCP stdio server (smoke: pipe an MCP initialize handshake, expect proper response)
3. npm page at https://www.npmjs.com/package/@chatlytics/claude-code shows 1.2.0
4. Tag `v1.2.0` on sibling repo (NOT chatlytics-hermes-split); pushed to origin
5. `npm publish --dry-run` output (logged in VERIFICATION.md) shows the intended file list
6. No accidental file leaks — `.planning/`, `node_modules/`, large test artifacts NOT in the published tarball
7. After milestone close: cross-repo summary (this repo's milestone archive notes the JS bundle release as the cross-repo deliverable)

---

## Recommended /gsd-autonomous sequence (v3.0)

```
/gsd-autonomous --from 13 --to 21
```

Sequential single-repo work for HERMES-13..19. Cross-repo phases (20, 21) operate in `C:/Users/omern/.claude/plugins/marketplaces/chatlytics-claude-code/` via subagent `cd`. Halt only on credential gaps (TestPyPI / PyPI token missing at Phase 19; npm publish errors at Phase 21) or real publish failures.

---

<details>
<summary>v2.0 — Hermes plugin v2.0 (upstream-contract rebuild) — SHIPPED 2026-05-17</summary>

## v2.0 — Hermes plugin v2.0 (upstream-contract rebuild) (COMPLETE)

**Shipped:** 2026-05-17. All 6 HERMES phases delivered. 45/45 tests green in dockerized `python:3.13-slim` + `hermes-agent@v2026.5.16`. 21 tools registered. `v2.0.0` annotated tag created locally (operator push pending). NO PyPI publish (operator lock). Archive: `.planning/milestones/v2.0-ROADMAP.md`. Audit: `.planning/v2.0-MILESTONE-AUDIT.md`.


Replace the v1.x standalone-shim API with a proper Hermes plugin against
`hermes-agent>=0.14,<0.15`. Six phases, designed for end-to-end execution via
`/gsd-autonomous --from HERMES-01 --to HERMES-06`.

### Phase 1: Upstream contract scaffolding
**Goal:** Bare `BasePlatformAdapter` subclass + `plugin.yaml` + `register(ctx)` entry point + pinned `hermes-agent>=0.14,<0.15` dependency. No outbound or inbound logic yet — purely the structural contract that lets Hermes load the plugin and call `register()`.

**Depends on:** Nothing (entry phase)

**In scope:**
- `src/chatlytics_hermes/__init__.py` — exports `register()` symbol
- `src/chatlytics_hermes/adapter.py` — `ChatlyticsAdapter(BasePlatformAdapter)` skeleton with platform name `chatlytics`; all abstract methods present but raising `NotImplementedError` (HERMES-02/03/04 fill them in)
- `plugin.yaml` — minimal Hermes plugin manifest (name, version, entry point, supported Hermes version range)
- `pyproject.toml` — replace v1.x deps (httpx, flask) with `hermes-agent>=0.14,<0.15`, `httpx>=0.27,<1`, `aiohttp>=3.9,<4`; add `[project.entry-points."hermes_agent.plugins"]` block pointing at `chatlytics_hermes:register`; bump `version = "2.0.0"`
- Drop the entire v1.x `src/chatlytics_adapter/` tree (operator decision: no compat shim)
- Drop v1.x `tests/test_adapter.py` + `tests/test_action_parity.py` (will be replaced phase-by-phase)

**Out of scope:**
- Outbound HTTP (HERMES-02)
- Inbound transport (HERMES-03)
- Media handlers (HERMES-04)
- Tool registration (HERMES-05)

**Files (create/modify):**
- CREATE `src/chatlytics_hermes/__init__.py`
- CREATE `src/chatlytics_hermes/adapter.py`
- CREATE `plugin.yaml`
- CREATE `tests/test_register.py`
- MODIFY `pyproject.toml`
- DELETE `src/chatlytics_adapter/` (whole tree)
- DELETE `tests/test_adapter.py`, `tests/test_action_parity.py`

**Acceptance criteria (all must pass autonomously):**
1. `python -c "from chatlytics_hermes import register; print(register.__name__)"` prints `register` (importable)
2. `tests/test_register.py::test_register_adds_chatlytics_platform` — calling `register(MockCtx())` registers a platform under name `chatlytics` on the mock context (no errors)
3. `python -c "import yaml; yaml.safe_load(open('plugin.yaml'))"` succeeds (valid YAML manifest)
4. `pip install -e .` in a clean venv with `hermes-agent==0.14.0` already installed succeeds without uninstalling Hermes
5. `pyproject.toml` declares `[project.entry-points."hermes_agent.plugins"]` with `chatlytics = "chatlytics_hermes:register"`

---

### Phase 2: Outbound text + control parity
**Goal:** Implement `connect()`, `disconnect()`, `send()`, `send_typing()`, `get_chat_info()` against the Chatlytics REST API via `httpx.AsyncClient`. Establish `SendResult` return contract. No media yet (HERMES-04), no inbound yet (HERMES-03).

**Depends on:** HERMES-01

**In scope:**
- `httpx.AsyncClient` lifecycle: open in `connect()`, close in `disconnect()`
- `connect()` calls Chatlytics `GET /health`; raises if not 200
- `send(chat_id, text, **extras)` → `POST /api/v1/send` with `{chatId, text, accountId?, replyTo?, ...}`; returns `SendResult.ok=True/False` + raw response in `meta`
- `send_typing(chat_id, duration=3.0)` → `POST /api/v1/typing` with `{chatId, duration}`
- `get_chat_info(chat_id)` → `GET /api/v1/chat?chatId={chat_id}`; returns dict with `name`, `phone`, `isGroup`, etc.
- Config surface: `base_url`, `api_key`, `account_id?` (optional default session); read from `__init__` kwargs or `register(ctx)` config block
- Auth header: `Authorization: Bearer {api_key}` on every request
- Timeout: 30s on every request (matches Chatlytics gateway's own timeout)

**Out of scope:**
- Media (`send_image`, `send_voice`, etc.) — HERMES-04
- Inbound aiohttp server — HERMES-03
- Tool registration via `ctx.register_tool()` — HERMES-05
- Retry / circuit breaker — Chatlytics gateway handles upstream retry

**Files (create/modify):**
- MODIFY `src/chatlytics_hermes/adapter.py` (fill in 5 methods + `__init__` config)
- CREATE `src/chatlytics_hermes/client.py` (thin httpx wrapper with auth + timeout)
- CREATE `tests/test_outbound.py`

**Acceptance criteria (all must pass autonomously):**
1. `tests/test_outbound.py::test_connect_succeeds_on_200_health` — mocked `GET /health` returns 200 → `await adapter.connect()` does not raise
2. `tests/test_outbound.py::test_connect_raises_on_non_200_health` — mocked `GET /health` returns 503 → `await adapter.connect()` raises
3. `tests/test_outbound.py::test_send_returns_ok_true_on_200` — mocked `POST /api/v1/send` returns 200 + `{success: true, messageId: "..."}` → `result.ok is True`
4. `tests/test_outbound.py::test_send_returns_ok_false_on_400` — mocked `POST /api/v1/send` returns 400 → `result.ok is False` and error reason in `result.meta`
5. `tests/test_outbound.py::test_send_typing_calls_typing_endpoint` — `await adapter.send_typing(chat_id, duration=2.0)` issues `POST /api/v1/typing` with the right body
6. `tests/test_outbound.py::test_get_chat_info_returns_dict` — mocked `GET /api/v1/chat?chatId=...` returns `{name, phone, isGroup}` → adapter returns that dict
7. `tests/test_outbound.py::test_disconnect_closes_client` — `await adapter.disconnect()` closes the underlying `httpx.AsyncClient` (no resource warning)
8. All outbound HTTP requests carry `Authorization: Bearer {api_key}` header (asserted in mocks)

---

### Phase 3: Inbound transport migration
**Goal:** Replace v1.x Flask-in-a-thread inbound with an aiohttp server started **inside** `connect()` and stopped in `disconnect()`. Normalize webhook JSON → Hermes `MessageEvent` via `MessageType.{TEXT,IMAGE,AUDIO,VIDEO,DOCUMENT,STICKER}`, then dispatch via `await self.handle_message(event)`.

**Depends on:** HERMES-02

**In scope:**
- aiohttp `web.Application` started inside `connect()` (after the health check), bound to `(host, port)` from config; stopped cleanly in `disconnect()`
- Single inbound route: `POST /webhook` (configurable path)
- Webhook payload normalization in `src/chatlytics_hermes/inbound.py`:
  - Detect `mediaType` field → map to `MessageType.IMAGE/AUDIO/VIDEO/DOCUMENT/STICKER`
  - Default to `MessageType.TEXT` when no media
  - Extract `chatId`, `text`, `senderId`, `timestamp`, `messageId`, optional `replyTo`
  - Construct `MessageEvent` with all required Hermes fields
- Dispatch: `await self.handle_message(event)` (canonical Hermes inbound entry)
- Optional HMAC verification: if `webhook_secret` is configured, verify `X-Chatlytics-Signature` header against HMAC-SHA256 of the body; reject mismatch with 401
- Health route: `GET /health` returns 200 — used by Chatlytics for webhook delivery confirmation

**Out of scope:**
- Outbound media handlers — HERMES-04
- Tool surface — HERMES-05
- Outbound HMAC signing — Chatlytics handles that upstream

**Files (create/modify):**
- MODIFY `src/chatlytics_hermes/adapter.py` (start/stop aiohttp server in connect/disconnect)
- CREATE `src/chatlytics_hermes/inbound.py` (payload normalizer + aiohttp request handler)
- CREATE `tests/test_inbound.py`

**Acceptance criteria (all must pass autonomously):**
1. `tests/test_inbound.py::test_webhook_text_payload_dispatches_text_message_event` — POST text payload to embedded server → fake gateway's `handle_message` recorder receives `MessageEvent(type=MessageType.TEXT, text=..., chat_id=...)`
2. `tests/test_inbound.py::test_webhook_image_payload_dispatches_image_event` — POST `{mediaType: "image", mediaUrl: ...}` → recorder gets `MessageEvent(type=MessageType.IMAGE, ...)`
3. `tests/test_inbound.py::test_webhook_audio_payload_dispatches_audio_event` — POST audio payload → recorder gets `MessageType.AUDIO`
4. `tests/test_inbound.py::test_webhook_health_returns_200` — `GET /health` on the embedded server returns 200
5. `tests/test_inbound.py::test_connect_starts_aiohttp_server` — after `await adapter.connect()`, the server is listening on the configured port
6. `tests/test_inbound.py::test_disconnect_stops_aiohttp_server` — after `await adapter.disconnect()`, the port is no longer listening (no thread/socket leak)
7. `tests/test_inbound.py::test_hmac_verification_rejects_bad_signature` — when `webhook_secret` is set, mismatched `X-Chatlytics-Signature` returns 401 and does NOT dispatch
8. `tests/test_inbound.py::test_hmac_verification_accepts_good_signature` — matching HMAC dispatches normally

---

### Phase 4: Media + UX polish + cron
**Goal:** Implement all 6 `BasePlatformAdapter` media-send variants — `send_image`, `send_voice`, `send_video`, `send_document`, `send_animation`, `send_image_file` — wired to Chatlytics media endpoints. Add `_keep_typing()` 30s heartbeat (WhatsApp 24h window protection). Wire `cron_deliver_env_var="CHATLYTICS_HOME_CHANNEL"` + `standalone_sender_fn` for scheduled deliveries.

**Depends on:** HERMES-03

**In scope:**
- 6 media handlers, each calling `POST /api/v1/send-media` (or `/api/v1/actions` with `action: "send_image" | "send_voice" | "send_video" | "send_file" | ...`) with the right `mediaType` / `mediaUrl` / `caption` / `file` payload shape
- `send_image(chat_id, url_or_bytes, caption=None)` — URL path → `mediaUrl`; bytes path → upload to Chatlytics's file endpoint first, then send
- `send_voice(chat_id, url_or_bytes)` — voice bubble (NOT regular audio)
- `send_video(chat_id, url_or_bytes, caption=None)`
- `send_document(chat_id, url_or_bytes, filename=None, caption=None)`
- `send_animation(chat_id, url_or_bytes, caption=None)` — gif/mp4
- `send_image_file(chat_id, file_path, caption=None)` — read local path, upload bytes
- `_keep_typing(chat_id, interval=30.0)` — async coroutine that re-issues `send_typing(chat_id, duration=30.0)` every `interval` seconds; cancelable via context manager; used by long-running tool handlers
- Cron support: `register(ctx)` reads `os.environ["CHATLYTICS_HOME_CHANNEL"]` (default channel for scheduled deliveries); `standalone_sender_fn` is a top-level coroutine that Hermes can call without instantiating the full plugin (matches upstream cron pattern)

**Out of scope:**
- Tool surface (HERMES-05)
- Release / README (HERMES-06)

**Files (create/modify):**
- MODIFY `src/chatlytics_hermes/adapter.py` (6 media methods + `_keep_typing` + `cron_deliver_env_var` + `standalone_sender_fn`)
- MODIFY `src/chatlytics_hermes/client.py` (add `send_media(...)` helper)
- CREATE `tests/test_media.py`
- CREATE `tests/test_cron.py`

**Acceptance criteria (all must pass autonomously):**
1. `tests/test_media.py::test_send_image_url_path` — `send_image(chat_id, "https://...")` → `POST /api/v1/send-media` with `mediaType: "image"`, `mediaUrl: "https://..."`, optional `caption`
2. `tests/test_media.py::test_send_voice_yields_voice_message` — payload has `mediaType: "voice"` (NOT `"audio"`)
3. `tests/test_media.py::test_send_video` — `mediaType: "video"` + `caption`
4. `tests/test_media.py::test_send_document_with_filename` — `mediaType: "file"` + `filename`
5. `tests/test_media.py::test_send_animation` — `mediaType: "video"` or `"gif"` per Chatlytics convention + animation hint
6. `tests/test_media.py::test_send_image_file_uploads_local_bytes` — local-path variant reads bytes and uploads via Chatlytics file endpoint, then references the returned URL in the send call
7. `tests/test_media.py::test_keep_typing_heartbeats_every_30s` — `async with adapter._keep_typing(chat_id):` issues a typing request immediately and again ~30s later; context-manager exit cancels cleanly
8. `tests/test_cron.py::test_cron_deliver_env_var_routes_to_standalone_sender` — when `CHATLYTICS_HOME_CHANNEL` is set, `standalone_sender_fn(text)` posts to that channel via `POST /api/v1/send`

---

### Phase 5: Full Chatlytics tool surface
**Goal:** Expose EVERY Chatlytics action as a Hermes tool via `ctx.register_tool()`. Source the canonical tool list from the Claude Code plugin's MCP server bundle (`servers/chatlytics-mcp.bundle.js` in `omernesh/chatlytics-claude-code`) plus any additional actions enumerable via Chatlytics's `POST /api/v1/actions` schema. Tools must validate inputs via JSON schemas and return `{"success": true, ...}` shape.

**Depends on:** HERMES-04

**In scope:**
- `src/chatlytics_hermes/tools.py` — one async handler per Chatlytics action:
  - Messaging: `chatlytics_send`, `chatlytics_reply`, `chatlytics_react`, `chatlytics_edit`, `chatlytics_unsend`, `chatlytics_pin`, `chatlytics_unpin`, `chatlytics_read`, `chatlytics_delete`, `chatlytics_poll`
  - Media (also direct adapter methods from HERMES-04, but exposed as tools too): `chatlytics_send_image`, `chatlytics_send_voice`, `chatlytics_send_video`, `chatlytics_send_file`, `chatlytics_send_animation`
  - Directory: `chatlytics_directory`, `chatlytics_search`, `chatlytics_actions` (generic action dispatcher)
  - Sessions / health: `chatlytics_health`, `chatlytics_login`, `chatlytics_dispatch`
  - Plus any additional surface from `POST /api/v1/actions` enumeration at plan time
- JSON schema per tool: `chatId`, `text`, `messageId`, `emoji`, etc. with `required` correctly set
- Each handler: validates args, calls Chatlytics REST, returns `{"success": bool, ...response, "error": str?}`
- All tools registered in `register(ctx)` via `ctx.register_tool(name, handler, schema)`

**Out of scope:**
- Release / smoke (HERMES-06)
- Tool result rendering (Hermes UI concern)

**Files (create/modify):**
- CREATE `src/chatlytics_hermes/tools.py`
- MODIFY `src/chatlytics_hermes/__init__.py` (register tools alongside platform)
- MODIFY `src/chatlytics_hermes/adapter.py` (expose `client` attribute for tool handlers to share auth/timeout)
- CREATE `tests/test_tools.py`
- CREATE `tests/test_tool_schemas.py`

**Acceptance criteria (all must pass autonomously):**
1. `tests/test_tool_schemas.py::test_every_tool_has_valid_json_schema` — every registered tool's schema validates via `jsonschema.Draft202012Validator`
2. `tests/test_tool_schemas.py::test_every_tool_has_required_chat_id_field_when_applicable` — messaging tools require `chatId`; directory/search tools require `query` or similar
3. `tests/test_tools.py::test_chatlytics_send_calls_send_endpoint` — handler invokes `POST /api/v1/send` and returns `{"success": True, "messageId": ...}` on 200
4. `tests/test_tools.py::test_chatlytics_react_calls_react_action` — handler invokes `POST /api/v1/actions` with `{action: "react", messageId, emoji}` and returns `{"success": True, ...}` on 200
5. `tests/test_tools.py::test_chatlytics_search_returns_results_list` — `POST /api/v1/actions` with `{action: "search", query}` → tool returns `{"success": True, results: [...]}`
6. `tests/test_tools.py::test_tool_returns_success_false_on_400` — mocked 400 → tool returns `{"success": False, "error": "..."}`
7. `tests/test_tools.py::test_tool_count_matches_claude_code_plugin_baseline` — registered tool count is at least the 8 documented in `omernesh/chatlytics-claude-code` MCP bundle PLUS the media variants from HERMES-04 (asserted against a known baseline list checked into the test)
8. `tests/test_tool_schemas.py::test_all_tools_namespace_chatlytics_` — every tool name starts with `chatlytics_` (avoid collisions with other Hermes plugins)

---

### Phase 6: Release + smoke test
**Goal:** Rewrite README.md from the v1.x standalone-shim perspective to the v2.0 first-class-plugin perspective. CHANGELOG entry `2.0.0 (BREAKING)`. Smoke test against real `hermes-agent==0.14.0` in a clean venv. Tag `v2.0.0`. **NO PyPI publish.**

**Depends on:** HERMES-05

**In scope:**
- README.md rewrite:
  - Drop all v1.x `ChatlyticsAdapter()` constructor snippets
  - Drop "standalone shim" / "duck-typed" language
  - Add v2.0 install: `pip install -e git+https://github.com/omernesh/chatlytics-hermes.git` (or local clone)
  - Add `register(ctx)` usage block
  - Document config: `base_url`, `api_key`, `account_id`, `webhook_port`, `webhook_secret`, `CHATLYTICS_HOME_CHANNEL` env var
  - Tool catalog summary (link to schemas in code, do not duplicate every signature)
  - Hermes version compatibility note: `hermes-agent>=0.14,<0.15`
- CHANGELOG.md entry:
  - `## 2.0.0 (2026-MM-DD) — BREAKING`
  - Bullet list of breaking changes: removed `ChatlyticsAdapter` standalone class; entry point now `chatlytics_hermes:register`; minimum `hermes-agent` is 0.14
  - Migration guide: none (v1.x never published, no users to migrate)
- pyproject.toml: confirm `version = "2.0.0"`, entry point present, deps clean
- Smoke test script `scripts/smoke.sh`:
  - Create fresh venv
  - `pip install hermes-agent==0.14.0`
  - `pip install -e .[dev]`
  - `hermes plugins ls` — assert output contains `chatlytics`
  - `pytest tests/` — assert all tests pass
- Git tag `v2.0.0`, push to origin

**Out of scope:**
- PyPI publish (`python -m build && twine upload`) — explicit operator decision, NOT executed in this phase
- Marketplace listing / external announcement
- Live integration against a real Chatlytics gateway — autonomous ceiling

**Files (create/modify):**
- REWRITE `README.md`
- MODIFY `CHANGELOG.md` (prepend 2.0.0 entry)
- CREATE `scripts/smoke.sh`
- VERIFY `pyproject.toml` is clean
- Git tag `v2.0.0` + push (NO PyPI command)

**Acceptance criteria (all must pass autonomously):**
1. `bash scripts/smoke.sh` exits 0 in a clean container/venv
2. `hermes plugins ls` output contains the string `chatlytics`
3. `pytest tests/` reports 0 failures across all test files from HERMES-01 to HERMES-05
4. README.md contains zero occurrences of `ChatlyticsAdapter(` (no leftover v1.x constructor snippets)
5. CHANGELOG.md has a `## 2.0.0` entry at the top with `BREAKING` marker
6. `pyproject.toml` has `version = "2.0.0"` and the entry point `chatlytics = "chatlytics_hermes:register"`
7. `git tag --list v2.0.0` returns a tag
8. NO `python -m build` or `twine upload` runs anywhere in the phase artifacts (operator decision lock)

---

## Recommended /gsd-autonomous wave sequence (v2.0 — historical)

All 6 phases are strictly sequential (each depends on the previous). No parallelization possible — the contract built in HERMES-01 is the foundation for HERMES-02, which is required by HERMES-03, etc.

```
/gsd-autonomous --from HERMES-01 --to HERMES-06
```

This runs discuss → plan → execute → review → commit for each phase in sequence, then halts at the milestone boundary.

</details>

---

<details>
<summary>v2.1 — Critical safety fixes + tech debt resolution + live-loader integration — SHIPPED 2026-05-17</summary>

## v2.1 — Critical safety fixes + tech debt resolution + live-loader integration (COMPLETE)

**Shipped:** 2026-05-17. All 6 HERMES phases (07-12) delivered end-to-end. 88/88 tests green (45 v2.0 baseline + 43 new v2.1 tests; zero regressions). v2.0 BLOCKER (BL-01) + 2 HIGHs (HI-01, HI-03) + every MED/LOW finding from `.planning/v2.0-MILESTONE-CODE-REVIEW.md` + `.planning/v2.0-MILESTONE-PR-REVIEW.md` closed. `v2.1.0` annotated tag created locally (operator push pending). NO PyPI publish (operator lock preserved). Archive: `.planning/milestones/v2.1-ROADMAP.md`. Audit: `.planning/milestones/v2.1-MILESTONE-AUDIT.md`.

**Phases:**
- HERMES-07 — Live-loader integration smoke (surfaces BL-01) — reproduced BL-01/HI-01/HI-03 under xfail-strict regression tests
- HERMES-08 — Critical safety fixes (BL-01 BLOCKER + HI-01 HIGH + HI-03 HIGH) + async lifecycle hardening — un-xfailed the regressions
- HERMES-09 — Observability + log hygiene
- HERMES-10 — Input validation + UX alignment
- HERMES-11 — Test infra cleanup
- HERMES-12 — Release v2.1.0 (LOCAL tag only)

**Operator next:** Review v2.1.0 artifact, then `git push origin main && git push origin v2.1.0` when ready. Optionally delete local `v2.0.0` tag (points at the BL-01 pre-fix artifact superseded by v2.1.0).

</details>

---

## Backlog

(Items deferred to v2.2+ — collected during v2.1 close.)

- Sentinel `_error` key on `get_chat_info` return shape (breaking change)
- Strict JID regex enforcement on `chatId` schemas (would break phone numbers / display names)
- Collapse `send_image` / `send_image_file` into one method (breaking change)
- Long-term wheel caching in `scripts/smoke.sh` beyond `--retries 3` (build-perf nice-to-have)
- Hermes `0.15` readiness review (v3.0 decision; not a v2.2 item)
