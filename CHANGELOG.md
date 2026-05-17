# Changelog

## 2.0.0 (2026-05-17) -- BREAKING

Full rebuild of `chatlytics-hermes` as a first-class Hermes Agent plugin
against `hermes-agent==0.14` (tag `v2026.5.16`). v1.x was a standalone
duck-typed shim that never published to PyPI; v2.0 is a clean break with
no migration burden (no users to migrate).

### Removed

- `ChatlyticsAdapter` standalone class (duck-typed shim flavor).
- Flask-based inbound webhook server / Flask background thread.
- All v1.x duck-typed surface (custom `connect`/`disconnect` signatures,
  `start_webhook_server`, `on_message` decorator).
- `flask` runtime dependency.
- Phase-169 "vendor into hpg6 Hermes monorepo" guidance from the README
  -- v2.0 IS the in-Hermes-plugin pattern.

### Added

- `BasePlatformAdapter` subclass (`src/chatlytics_hermes/adapter.py`) that
  fits the canonical Hermes v0.14 plugin contract.
- `register(ctx)` entry point exposed at
  `[project.entry-points."hermes_agent.plugins"]`
  (`chatlytics = "chatlytics_hermes:register"`) for auto-discovery.
- aiohttp inbound webhook server started inside `connect()` and stopped
  inside `disconnect()` -- same event loop as outbound sends.
- HMAC-SHA256 `X-Chatlytics-Signature` verification on inbound webhooks
  (env: `CHATLYTICS_WEBHOOK_SECRET`).
- Six media handlers: `send_image`, `send_voice`, `send_video`,
  `send_document`, `send_animation`, `send_image_file`.
- `_keep_typing` 30 s async-contextmanager heartbeat for long-running
  tool handlers; fires immediately on enter and re-fires every 30 s.
- Cron-delivery hook: `standalone_sender_fn` +
  `cron_deliver_env_var="CHATLYTICS_HOME_CHANNEL"` for unattended
  notification delivery from Hermes cron jobs.
- **21 Hermes tools** registered via `ctx.register_tool(...)` under the
  `chatlytics` toolset:
  - 10 messaging (`send`, `reply`, `react`, `edit`, `unsend`, `pin`,
    `unpin`, `read`, `delete`, `poll`)
  - 5 media (`send_image`, `send_voice`, `send_video`, `send_file`,
    `send_animation`)
  - 3 directory/search (`directory`, `search`, `actions`)
  - 3 sessions/health (`health`, `login`, `dispatch`)
- Every tool schema is JSON Schema Draft 2020-12 + validated under
  `jsonschema.Draft202012Validator` at test time.
- `aiohttp>=3.9,<4` and `jsonschema>=4,<5` runtime dependencies.
- `plugin.yaml` manifest with `requires_env` / `optional_env` blocks for
  the Hermes config UI.
- `scripts/smoke.sh` -- dockerized clean-room verification script.
- 45 tests (5 register + 8 outbound + 9 inbound + 8 media + 3 cron +
  11 tools + 1 concurrency regression).

### Changed

- Minimum `hermes-agent` is now `>=0.14,<0.15` (was `>=0.11`).
- `httpx>=0.27,<1` (was unpinned).
- Package version bumped to `2.0.0`.
- Local-file branch of `_resolve_media_url` now reads via
  `asyncio.to_thread` (fixes 04-REVIEW MED-02 / surfaced in 05-REVIEW
  MED-02) so concurrent media-tool invocations no longer stall the
  event loop on multi-MB file reads.

### Migration

**None.** v1.x was never published to PyPI; there are no installed users
to migrate. Anyone on a pre-2.0 git install should rebuild against the
v2.0 plugin contract from scratch.

## 1.1.0 (2026-04-27)

- Verified compatible with `hermes-agent==0.11.0` (tag `v2026.4.23`).
- Removed bogus `[project.entry-points."hermes.adapters"]` block from
  `pyproject.toml` — Hermes's plugin entry-point group is `hermes_agent.plugins`
  and is meant for tools/hooks/commands, not platform adapters; platform
  adapters are hardcoded in `gateway/run.py:_create_adapter()` per
  `gateway/platforms/ADDING_A_PLATFORM.md`.
- Stripped the `from hermes_agent import Agent / Agent(platform=adapter)`
  README snippet — that API does not exist in upstream Hermes. README now
  documents the standalone-shim use case and points at phase 169 for the
  in-monorepo vendor pattern.
- Stashed unmerged WIP test files (`tests/test_actions.py`,
  `tests/test_in_reply_to.py`, `tests/test_inbound.py`) into
  `.wip-stash/` — they imported symbols that never landed
  (`LAST_INBOUND_MAX_ENTRIES`, `MESSAGE_DEDUP_MAX_ENTRIES`,
  `_default_session`, `webhook_secret` ctor param, `send_image`,
  `send_voice`) and broke `pytest` collection. Baseline test count back to
  the 12 that actually run against the shipped adapter.

## 1.0.0 (2026-04-20)

- Initial release
- Hermes Agent platform adapter for Chatlytics WhatsApp gateway
- Async HTTP client (httpx) for outbound messaging
- Flask webhook server for inbound messages
- Send messages, typing indicators, and chat info queries
- Bearer token authentication
