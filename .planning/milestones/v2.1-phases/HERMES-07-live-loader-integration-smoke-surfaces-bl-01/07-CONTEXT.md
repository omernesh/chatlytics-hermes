# Phase 7: Live-loader integration smoke (surfaces BL-01) - Context

**Gathered:** 2026-05-17
**Status:** Ready for planning
**Mode:** Auto-generated (infrastructure phase — discuss skipped)

<domain>
## Phase Boundary

Wire Hermes v0.14's plugin loader (`hermes_cli.plugins.PluginManager` —
verified against `/tmp/hermes-ref-v0.14.0/hermes_cli/plugins.py`; the
entry point group is `hermes_agent.plugins`, the load method is
`PluginManager._load_plugin(manifest)`, and `PluginContext` is
constructed by the manager at `plugins.py:1157`) against a respx-mocked
Chatlytics backend; prove the plugin loads, `register(ctx)` is called,
the chatlytics platform is registered, AND 21 tools land on the in-memory
PluginContext registry (via `ctx.register_tool(name=, toolset=, schema=,
handler=, ...)`).

Reproduce BL-01 (`_keep_typing` crash — `metadata` kwarg + asynccontextmanager
return shape both incompatible with `asyncio.create_task(self._keep_typing(...))`
at `base.py:3045-3057`), HI-01 (path traversal via `filePath` in 5 media
tools — `chatlytics_send_image|voice|video|file|animation` accept
arbitrary local paths with zero validation), and HI-03 (`**kwargs` gap on
`send_image` and `send_animation` overrides) under xfail-marked regression
tests that exercise the BASE `handle_message` pipeline (NOT the
recorder-replacement pattern from `test_inbound.py:98-106` that hid BL-01
in v2.0).
</domain>

<decisions>
## Implementation Decisions

### Loader Entry Point (confirmed against `/tmp/hermes-ref-v0.14.0/`)
- Group: `hermes_agent.plugins` (constant `ENTRY_POINTS_GROUP` at `plugins.py:170`)
- Loader: `hermes_cli.plugins.PluginManager` — its `_scan_entry_points()`
  enumerates entry points; `_load_plugin(manifest)` imports the module
  and calls `register(ctx)` with a fresh `PluginContext` (`plugins.py:1135-1200`)
- For Phase 7 we instantiate `PluginManager` directly (or, if importing
  the full manager pulls in `agent.plugin_llm` heavy deps, we instantiate
  `PluginContext` with a minimal manager stub and call `register(ctx)`
  ourselves through the real entry-point dispatch path — `importlib.metadata.entry_points(group="hermes_agent.plugins")`)
- The plan phase will pick the lightest-weight harness that still
  exercises the real loader code path; defer to Claude in plan.

### Test Harness
- New test file: `tests/test_live_loader.py` (per ROADMAP — alongside
  existing `test_inbound.py`, `test_adapter.py`, etc.)
- Use respx for any in-loader HTTP probes (Chatlytics health, etc.)
- Build a real (or as-real-as-possible) `PluginContext` per the v0.14
  contract; do NOT mock the registry surface itself — that's what hides
  bugs
- Tests exercise the BASE `handle_message` pipeline directly — install an
  `AsyncMock` as the message handler so the base path runs but returns
  immediately. No recorder/replacement adapter pattern.
- xfail markers use `strict=True` so the test FAILS LOUDLY if Phase 8
  accidentally fixes BL-01/HI-01/HI-03 without un-xfailing

### Regression Test Naming
- `test_base_handle_message_invokes_keep_typing` — xfail(strict=True, reason="BL-01: fixed in Phase 8")
- `test_keep_typing_is_a_coroutine` — xfail(strict=True, reason="BL-01: fixed in Phase 8")
- `test_bl01_metadata_kwarg_accepted` — xfail(strict=True, reason="BL-01: fixed in Phase 8")
- `test_hi01_send_file_rejects_etc_passwd` (or Windows: `C:/Windows/win.ini`) — xfail(strict=True, reason="HI-01: fixed in Phase 8")
- `test_hi03_send_image_accepts_unknown_kwargs` — xfail(strict=True, reason="HI-03: fixed in Phase 8")
- `test_hi03_send_animation_accepts_unknown_kwargs` — xfail(strict=True, reason="HI-03: fixed in Phase 8")

### Loader Assertions
- Assert exactly 21 tools register (count must match `src/chatlytics_hermes/tools.py` `TOOLS` tuple — `assert len(TOOLS) == 21` already exists at module-load time)
- Assert each tool by NAME (whitelist of 21 names from `tools.TOOLS`) so reorderings don't silently change the surface
- Assert the chatlytics platform registers with the canonical `register_platform` kwargs (name="chatlytics", toolset="chatlytics", check_fn callable)

### Smoke Script Integration
- Add a `--live-loader` step (or unconditional new step) to `scripts/smoke.sh` that runs `pytest tests/test_live_loader.py -q` so CI/release verification includes it
- Emit one line `"live-loader: chatlytics platform + 21 tools registered"` on success

### Docstring Update
- `src/chatlytics_hermes/__init__.py` — module docstring documenting the
  loader contract: entry point group `hermes_agent.plugins`, the `register`
  callable signature `(ctx: PluginContext) -> None`, and the in-process
  test harness that proves it

### Claude's Discretion
All other implementation choices are at Claude's discretion — infrastructure
phase, no user-facing behavior changes (only new tests + smoke step + one
docstring).
</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `tests/conftest.py` already seeds platform_registry (closes 02-MED-02 in Phase 11 — DO NOT FIX HERE)
- respx is already a test dep (used pervasively across `test_outbound.py`, `test_media.py`, `test_cron.py`)
- `src/chatlytics_hermes/__init__.py` re-exports `register` from `adapter.py`; actual `register(ctx)` lives at `adapter.py:982`
- `src/chatlytics_hermes/tools.py` defines the 21 tools in `TOOLS` tuple at line 796 with a module-level `assert len(TOOLS) == 21`
- `pyproject.toml` declares `[project.entry-points."hermes_agent.plugins"]` with `chatlytics = "chatlytics_hermes:register"`
- `_install_recorder` at `test_inbound.py:98-106` is the GSD-MD-04 anti-pattern; Phase 7 tests must NOT use it

### Established Patterns
- pytest + pytest-asyncio (see `pyproject.toml`)
- respx for httpx mocking
- aiohttp test client for inbound webhook tests
- `mock_health` respx fixture seeds `GET /health` returning 200 (used by every test that calls `adapter.connect()`)

### Integration Points
- Hermes v0.14 plugin loader: `hermes_cli.plugins.PluginManager._load_plugin(manifest)` (confirmed)
- Entry points group: `hermes_agent.plugins` (constant at `plugins.py:170`)
- `PluginContext` constructor: `PluginContext(manifest, manager)` (`plugins.py:290`)
- `PluginContext.register_tool` signature: `(name, toolset, schema, handler, check_fn=None, requires_env=None, is_async=False, description="", emoji="", override=False)` (`plugins.py:317`)
- Inbound adapter base: `gateway.platforms.base.BasePlatformAdapter`
- The BL-01 call site that crashes: `base.py:3045-3057` —
  `asyncio.create_task(self._keep_typing(event.source.chat_id, metadata=_thread_metadata, stop_event=interrupt_event))`
</code_context>

<specifics>
## Specific Ideas

- **BL-01 reproduction (the structural-cause test):** Instantiate `ChatlyticsAdapter` against a respx-mocked Chatlytics backend, install an `AsyncMock` as `adapter._message_handler` (so the base `handle_message` runs but the agent loop returns immediately), feed it a `MessageEvent` constructed via the real `MessageEvent` / `SessionSource` dataclasses, await `adapter.handle_message(event)`, and assert NO `TypeError` from `_keep_typing(chat_id, metadata=...)`. Currently this MUST raise `TypeError` (Phase 7 marks it xfail-strict; Phase 8's fix flips it to xpass and the strict marker forces un-xfailing).

- **BL-01 direct test:** `asyncio.create_task(adapter._keep_typing(chat_id, metadata={}, stop_event=asyncio.Event()))` — assert (a) the method IS a coroutine (`asyncio.iscoroutine(...)` of the return), (b) accepts both kwargs, (c) respects `stop_event.set()`. Currently the asynccontextmanager flavor returns an `_AsyncGeneratorContextManager` which is NOT a coroutine → `asyncio.create_task` raises. Phase 7 xfails this; Phase 8 fixes.

- **HI-01 reproduction:** Call `chatlytics_send_file(chatId="120363xxx@g.us", filePath="/etc/passwd")` on POSIX (or `C:/Windows/win.ini` on Windows — detect via `sys.platform`) against a respx-mocked Chatlytics backend, and assert that under current v2.0 code it SUCCEEDS (reaches the upload step) — the assertion under xfail-strict is therefore "current behavior is broken; Phase 8 fix makes the call return `{success: False, error: ...}`". Concretely: xfail expects the test to currently pass (no rejection) but Phase 8 will make it fail (rejection added) → un-xfailed and inverted.
  - Alternative formulation (cleaner): write the test as the DESIRED post-fix assertion (`assert result["success"] is False and "outside allowed" in result["error"].lower()`) and xfail-strict it now. Currently FAILS (no validation exists), Phase 8 fix makes it PASS, un-xfail.
  - We'll go with the alternative — tests are documentation of intended behavior, not snapshots of broken state.

- **HI-03 reproduction:** Pass an unexpected kwarg (e.g. `priority="high"`) to `adapter.send_image(...)` and `adapter.send_animation(...)`; currently raises `TypeError` because neither override accepts `**kwargs`. Write the test as the DESIRED post-fix assertion (`await adapter.send_image(chat_id, url, priority="high")` succeeds and just swallows the kwarg). xfail-strict; Phase 8 adds `**kwargs: Any` and the test passes.

- The `mock_health` fixture pattern from `tests/test_outbound.py:71` is the canonical respx setup — replicate it for the live-loader tests.

- The 21 tool names whitelist comes directly from `tools.TOOLS` (import it at test time and assert the registered tools' names match the set).
</specifics>

<deferred>
## Deferred Ideas

- Live Chatlytics gateway integration test (out of scope — verification ceiling per v2.1 STATE.md is respx-mocked only)
- PyPI publish smoke (out of scope — operator lock)
- Pre-baked docker image for hermes-agent install (Phase 11 — 06-LOW-01 / PR-review MED-03)
- conftest teardown for platform_registry (Phase 11 — 02-MED-02)
- Actual BL-01 / HI-01 / HI-03 fixes (Phase 8)
</deferred>
