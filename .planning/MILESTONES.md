# Milestones

## v3.0 — Breaking-change harmonization + first public release (Shipped: 2026-05-18)

**Phases completed:** 9 phases (HERMES-13..21), 9 plans
**Tests:** 120/120 passing (up from v2.1 baseline of 88/88; +32 net new tests; zero regressions)
**Tags:** `v3.0.0` (chatlytics-hermes) + `v1.2.0` (chatlytics-claude-code) — both pushed publicly

**First public releases:**

- **PyPI** — `chatlytics-hermes 3.0.0` LIVE at https://pypi.org/project/chatlytics-hermes/
- **npm** — `@chatlytics/claude-code 1.2.0` LIVE at https://www.npmjs.com/package/@chatlytics/claude-code (first publish under `@chatlytics` org)

**Key accomplishments:**

- **HERMES-13** — `get_chat_info` three-way contract with `_error` sentinel: `{success: true, chat: {...}}` for found, `{success: true, chat: null}` for legitimate empty, `{success: false, error, _error: <code>}` for transport/auth/server/validation errors. Closes v2.1-deferred-1. BREAKING tool surface.
- **HERMES-14** — Strict JID regex `^.+@(c\.us|g\.us|lid|newsletter)$` applied to all 15 chatId schemas. Phone numbers/display names now rejected at schema layer with `_error: "validation"` and helpful pointer to `chatlytics_search`. Matches sibling JS bundle regex. Closes v2.1-deferred-2. BREAKING tool surface.
- **HERMES-15** — Adapter `send_image` / `send_image_file` collapsed into `send_image(chatId, resource: str | Path)` with auto-detection (URL vs local path). Same for `send_animation` / `send_video` / `send_document` / `send_voice` — all use `resource` param. HI-01 allowlist consolidated into `_enforce_upload_allowlist` helper. `__getattribute__` guard blocks base-class text-fallback to prevent silent v2.x photo-send degradation. Closes v2.1-deferred-3. BREAKING library API.
- **HERMES-16** — `scripts/smoke.sh --cached` flag caches the hermes-agent wheel between docker smoke runs (via `pip download` + `pip install --no-index --find-links=`). Default behavior unchanged. Network calls down ~90% on cache hit. Closes v2.1-deferred-4.
- **HERMES-17** — `.planning/HERMES-API-AUDIT.md` inventories every `from gateway` / `from hermes_` import (5 module-symbol-set rows), classifies stability (`core` / `runtime`), and ships a 10-step migration checklist for a hypothetical hermes-agent 0.15. Docs-only.
- **HERMES-18** — Cosmetics sweep closed 6 deferred LOW/INFO nits from v2.1 Phase 9/10 + v3.0 Phase 17; 4 explicitly skipped with documented justification. Zero behavior change; 120/120 tests preserved.
- **HERMES-19** — First public PyPI publish. Local wheel-install dress rehearsal (twine check + scratch venv pip install + pytest against installed wheel) → real PyPI publish → post-publish install verification. `v3.0.0` tag + push.
- **HERMES-20** — Cross-repo sync of sibling chatlytics-claude-code JS MCP bundle to v1.2.0. Version reconciled across 3 sites; `looksLikeJid` verified byte-identical to Python `_JID_PATTERN`; `chatlytics_send` drift bug fixed (now calls `resolveChatId` matching `chatlytics_read` pattern); esbuild bundle regenerated. 8 tools preserved.
- **HERMES-21** — First public npm publish under operator's `@chatlytics` org. Manifest flipped to public + renamed to `@chatlytics/claude-code` + `files:` allowlist (9 files, 147.7 kB packed) + `engines.node >=18` + `publishConfig.access: public`. npm pack/publish dry-runs validated then real publish executed. `v1.2.0` tag + push in sibling repo.

**Audit:** `.planning/milestones/v3.0-MILESTONE-AUDIT.md` (verdict: passed — 9/9 phases satisfied, zero integration gaps, two minor tech-debt items captured for v3.1 backlog).

**Deferred to v3.1:** sibling repo `scripts.postinstall` removal (v1.2.1 patch candidate); `conftest.py` env-leak monkeypatch.

---

## v2.1 — Critical safety fixes + tech debt resolution + live-loader integration (Shipped: 2026-05-17)

**Phases completed:** 6 phases (HERMES-07 through HERMES-12)
**Tests:** 88/88 passing (up from v2.0 baseline of 45/45) — zero regressions
**Tag:** `v2.1.0` (local-only — operator push pending; operator lock preserved)

**Key accomplishments:**

- **BL-01 fixed** — `_keep_typing` rewritten as plain coroutine + new `_typing_scope` async-cm. Inbound base pipeline no longer crashes on first message. Locked under regression tests (xfail→PASS in HERMES-07/08).
- **HI-01 fixed** — `CHATLYTICS_UPLOAD_ALLOWED_ROOTS` env-var allowlist on `filePath` uploads (default-deny). Path-traversal closed; documented in README "Security: filePath upload allowlist".
- **HI-03 fixed** — `send_image` / `send_animation` accept `**kwargs: Any` for forward-compat with upstream base evolution.
- **MD-01 fixed** — three success-shape coercions consolidated into canonical `_coerce_success_payload` helper across adapter and tools.
- **Live-loader integration smoke** — new `tests/test_live_loader.py` exercises the actual `BasePlatformAdapter` base pipeline against an in-memory `PluginContext` and asserts 21 tools land. Closes GSD-MD-04 (test harness gap that hid BL-01).
- **Observability hardening** — `send_typing` transport-error logs DEBUG (was WARNING flood); diagnostic logs added to 6 silent paths; reserved-metadata WARNING per dropped key.
- **Input validation** — `webhook_path` validated at `__init__` (rejects empty, missing leading slash, `/health` collision); chatId/messageId schema tightening; `chatlytics_login` returns `success=False` on `webhook_registered != True` (aligns with MCP bundle).
- **Test infra cleanup** — conftest yield teardown + idempotency guard; `tests/_fixtures.FakePlatformConfig` consolidates 7 duplicated shims; `scripts/smoke.sh --fast` opt-in for local iteration; `pip --retries 3` hardening.
- **Release** — CHANGELOG 2.1.0 (security-led, additive); README "What's new in v2.1" + "Known issues"; pyproject/plugin.yaml bumped to 2.1.0; plugin.yaml phase-ID leaks stripped (PR-MED-04).

**Audit:** `.planning/milestones/v2.1-MILESTONE-AUDIT.md` (verdict: passed — 0 BLOCKER, 0 HIGH, 0 MED unresolved).

**Deferred to v2.2+:** Sentinel `_error` key on `get_chat_info`, strict JID regex, collapse of `send_image`/`send_image_file` (all would be breaking changes).

---
