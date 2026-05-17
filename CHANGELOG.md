# Changelog

## [2.1.0] -- 2026-05-17

Tech-debt resolution + critical safety fixes carried over from the v2.0
milestone-wide reviews. **Additive, not breaking.** v2.1.0 is a drop-in
upgrade from v2.0.0 -- the 21-tool surface, the `BasePlatformAdapter`
contract, the `register(ctx)` entry point, and every public API signature
stay identical. Internal `_keep_typing` shape changed to match the
upstream base coroutine signature; in-plugin callers transparently use
the new `_typing_scope` async-cm helper, so no external migration is
required.

### Security

- **BL-01 (BLOCKER) fixed.** `_keep_typing` was an `@asynccontextmanager`
  in v2.0, but the upstream Hermes base calls it as
  `asyncio.create_task(self._keep_typing(chat_id, metadata=..., stop_event=...))`
  -- which would have crashed on the first production inbound message
  with a `TypeError` (async-cm return value is not awaitable; chatlytics
  also didn't accept the `metadata` kwarg). The fix rewrites the method
  as a plain coroutine matching the base signature
  `(self, chat_id, interval=30.0, metadata=None, stop_event=None)` and
  extracts a new `_typing_scope` async-cm helper for the in-plugin tool
  handler ergonomics. Hidden in v2.0 because `tests/test_inbound.py`
  replaced `handle_message` with a recorder and never exercised the
  base path.
- **HI-01 (HIGH) fixed.** Tool surface exposed an arbitrary local-file
  read primitive: the 5 media tools accepted `filePath` with zero
  validation, so a prompt-injected `chatlytics_send_file(filePath="/etc/passwd")`
  would have exfiltrated arbitrary host files to Chatlytics. v2.1.0
  introduces a new env-configured allowlist
  `CHATLYTICS_UPLOAD_ALLOWED_ROOTS` (OS-pathsep-separated absolute
  paths). When unset, local-file uploads are default-deny; every
  `filePath` value must resolve under a configured allowed root or the
  tool returns `{"success": False, "error": "..."}` without opening or
  uploading the file. URL-based uploads via `mediaUrl` are unaffected.
- **HI-03 (HIGH) fixed.** Two of six media overrides
  (`send_image`, `send_animation`) dropped `**kwargs` in v2.0, making
  the plugin brittle to upstream `BasePlatformAdapter` signature
  evolution (subsequent base-class kwargs would have been silently
  unsupported). v2.1.0 brings all six media overrides to a consistent
  shape with `**kwargs: Any` swallowed for forward-compat.

### Added

- `CHATLYTICS_UPLOAD_ALLOWED_ROOTS` env var -- OS-pathsep-separated
  absolute paths under which media tools may read local files (default
  deny when unset). See README "Security: filePath upload allowlist".
- `scripts/smoke.sh --fast` flag -- host-venv pytest only, no docker.
  ~10-20s vs ~60-90s for the full dockerized smoke. Opt-in; default
  behavior unchanged for release-gate use.
- `pip install --retries 3` on the dockerized smoke step -- transient
  GitHub outages no longer look like plugin bugs.
- `tests/test_live_loader.py` -- gateway-loader integration smoke
  asserting `register(ctx)` runs against a real `PluginContext`-shaped
  registry and all 21 tools land. Closes the test-harness gap that hid
  BL-01.
- `tests/test_concurrency.py` -- regression guard for the v2.0
  `_resolve_media_url` `asyncio.to_thread` fix; verifies concurrent
  media-tool calls don't serialize on file I/O.
- `tests/test_observability.py` -- caplog-based assertions on log
  levels, dropped-metadata WARNINGs, and api-key/Bearer-token absence
  from log records.
- `webhook_path` validation in `ChatlyticsAdapter.__init__` -- rejects
  empty, missing leading slash, contains `?` / `#`, or collides with
  the reserved `/health` route. Raises `ValueError` at construction
  (fail-fast, matches Hermes conventions).
- Conftest session-autouse `platform_registry` fixture is now
  teardown-clean: snapshots the registry at session start, restores at
  session end. Two pytest runs in succession produce identical results.
- `tests/_fixtures.py::FakePlatformConfig` -- single source of truth
  for the test fake; previously duplicated across 7 test files.

### Changed

- `send_typing` transport-error log level: WARNING -> DEBUG (log
  hygiene; users were seeing routine gateway flakiness at WARN). The
  WARNING level is reserved for truly unexpected states.
- `chatlytics_login` semantics: when the upstream API returns
  `{"success": True, "webhook_registered": False}`, the tool now
  returns `{"success": False, "error": "webhook_registered=false"}`
  -- aligns with the Chatlytics Claude Code MCP bundle's behavior so
  agents on either surface see consistent results.
- Success-shape coercion is now a single canonical helper used by
  `_make_send_result`, `_standalone_send`, and `tools._ok` (MD-01
  cross-phase consistency dedup). Identical observable behavior across
  all three call sites.

### Fixed

- Silent `ctx.get_platform("chatlytics")` failures inside
  `_make_tool_handler` now emit a DEBUG log so operators can diagnose
  toolset misconfiguration without attaching a debugger.
- `send()` reserved-name metadata keys (e.g. caller passing `chatId`
  or `text` in `**extras`) now emit a WARNING per dropped key instead
  of silently discarding.
- `plugin.yaml` `optional_env` descriptions no longer leak internal
  phase identifiers (`(HERMES-03)`, `(HERMES-04)` stripped). Closes
  PR-review **MED-04** -- end users in `hermes config` UI now see
  feature-oriented descriptions instead of milestone metadata.
- Conftest cross-test pollution: re-running the suite twice in a row
  no longer leaves a dirty platform registry between runs.

### Docs

- README has a `## What's new in v2.1` section near the top calling
  out the security fixes and the upgrade recommendation.
- README "Tool catalog" clarifies the `chatlytics_actions` (GET
  gateway action catalog) vs `chatlytics_dispatch` (POST generic
  action invocation) semantic split (closes 05-MED-01 docs).
- README has a `## Known issues` section documenting that
  `filename` for URL-path documents may or may not be honored by the
  Chatlytics gateway (closes 04-LOW-02 docs; tracks upstream).

### Test infra

- Conftest teardown contract added (closes 02-MED-02).
- `_FakePlatformConfig` consolidated into `tests/_fixtures.py`
  (closes PR-review cross-cutting fixture-duplication nit).
- 88 tests total (was 65 in v2.0): +12 live-loader, +5 path-traversal
  negatives, +3 concurrency, +7 observability, -4 retired duplicates.

### Internal

- Log hygiene sweep across `adapter.py`, `client.py`, `tools.py`,
  `inbound.py` -- no api_key or full phone numbers surface in any log
  record (verified by `tests/test_observability.py::test_no_api_key_in_any_log_record`).
- Documented loader contract findings in
  `src/chatlytics_hermes/__init__.py` docstring (Phase 7).
- `_typing_scope` async-cm extracted so in-plugin tool handlers keep
  `async with self._typing_scope(chat_id):` ergonomics while the
  base-callable `_keep_typing` matches the upstream coroutine
  contract.

**Recommended for all users.** v2.0.0 has known BLOCKER + HIGH
security issues fixed in this release.

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
