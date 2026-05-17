# Milestones

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
