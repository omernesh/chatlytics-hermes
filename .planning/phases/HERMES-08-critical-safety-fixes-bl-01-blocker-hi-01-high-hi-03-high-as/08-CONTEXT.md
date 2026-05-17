# Phase 8: Critical safety fixes (BL-01 + HI-01 + HI-03) + async lifecycle hardening - Context

**Gathered:** 2026-05-17
**Status:** Ready for planning
**Mode:** Auto-generated (fix-locked phase — discuss skipped)

<domain>
## Phase Boundary

Fix BL-01 (BLOCKER: `_keep_typing` crash), HI-01 (HIGH: path traversal via `filePath`), HI-03 (HIGH: brittle `**kwargs`), MD-01 (success-shape coercion dedup) from the GSD v2.0 milestone review. Also closes 04-MED-01 / 04-LOW-03 / 06-LOW-02 (`_keep_typing` async lifecycle) and adds a concurrency regression test for the v2.0 `_resolve_media_url` `asyncio.to_thread` fix. Un-xfail the Phase 7 regression tests once fixes land.

Phase 7 wrote 6 xfail-strict regression tests in `tests/test_live_loader.py` covering BL-01 (3 tests), HI-01 (1 test), HI-03 (2 tests). Phase 8 must un-xfail all 6 after fixes land — `strict=True` forces this since xpass with strict=True is a test failure.
</domain>

<decisions>
## Implementation Decisions

### BL-01 Fix Shape (matches GSD review Option A + Phase 7 xfail tests)
- `_keep_typing` becomes a plain `async def` coroutine matching the upstream base signature: `(self, chat_id, interval=30.0, metadata=None, stop_event=None)`
- Initial fire at top, then loop with `await asyncio.sleep(interval)` + `send_typing(...)`
- Respects `stop_event.is_set()` between sleeps; respects `asyncio.CancelledError`
- `_typing_scope` async-cm wrapper preserves the in-plugin tool-handler ergonomics: spawns a task that runs `_keep_typing(...)` with an internal `stop_event`, cancels-and-awaits on exit
- Existing in-plugin test sites in `tests/test_media.py` migrate from `async with adapter._keep_typing(...)` to `async with adapter._typing_scope(...)`

### HI-01 Fix Shape
- New env var `CHATLYTICS_UPLOAD_ALLOWED_ROOTS` (OS-pathsep-separated absolute paths)
- `ChatlyticsAdapter.__init__` resolves the env value into `self.upload_allowed_roots: list[Path]`
- `_resolve_media_url` local-file branch: resolve path, check `Path.resolve().is_relative_to(root)` (or 3.8-safe fallback via `str().startswith()`) for each allowed root; raise `PermissionError` on miss
- Default-deny: when env var is unset, `upload_allowed_roots == []` and EVERY local-path upload is rejected
- The 5 media tools in `tools.py` (`chatlytics_send_image`, `_send_voice`, `_send_video`, `_send_file`, `_send_animation`) catch `PermissionError` from `_send_media_payload`/`_resolve_media_url` (already caught via `OSError`/`RuntimeError`) and return `{"success": False, "error": "..."}`. Since `PermissionError` is a subclass of `OSError`, the existing `except OSError` catch in `_send_media_payload` covers it.
- README adds a security note documenting the env var

### HI-03 Fix Shape
- Add `**kwargs: Any` to `send_image` (adapter.py:605-624) and `send_animation` (adapter.py:626-642). Document as "swallowed for forward-compat with upstream base signature evolution"
- Brings all 6 media overrides to a consistent shape (other 4 already have `**kwargs`)
- The Phase 7 xfail tests `test_hi03_send_image_accepts_unknown_kwargs` and `test_hi03_send_animation_accepts_unknown_kwargs` flip to pass

### MD-01 Fix Shape (success-shape coercion dedup)
- New module-level helper `_coerce_success_payload(status_code, payload)` returning `(success: bool, error_msg: Optional[str])` — single source of truth
- Used by `adapter._make_send_result`, `adapter._standalone_send` success-derivation, and `tools._ok` (via `_post`/`_get` 2xx path) — ALL three current call sites
- The Chatlytics-contract bug surfaced in the review (`200 + success:false` returning truthy) is fixed by this helper since it propagates `payload.get("success") is False` as failure

### `_keep_typing` lifecycle hardening (closes 04-MED-01, 04-LOW-03, 06-LOW-02)
- Initial-fire is moved into the coroutine body (NOT fired before task spawn) so wrapped tool bodies start within ms even if first typing request hangs (04-LOW-03)
- First-fire failure logs at WARNING; subsequent heartbeats stay at DEBUG (06-LOW-02)
- `try/finally` cleanup; bare `except` only swallow `asyncio.CancelledError` correctly (re-raise where needed)

### Concurrency regression test for `_resolve_media_url`
- New file `tests/test_concurrency.py`
- `test_resolve_media_url_off_event_loop` — launches several concurrent `_resolve_media_url` calls against a slow `open()` fixture (monkeypatched), asserts they overlap in time (would serialize if `to_thread` was removed)
- `test_keep_typing_initial_fire_does_not_block` — body starts within 10ms even if first typing request blocks
- `test_keep_typing_first_fire_failure_logs_warning` — caplog captures WARNING

### Test un-xfail
- Remove all 6 `@pytest.mark.xfail(strict=True, ...)` markers in `tests/test_live_loader.py`
- After Phase 8 fixes land, all 6 must PASS (strict=True converts xpass to failure if left in place)

### Claude's Discretion
- Path allowlist storage representation (`list[Path]` vs `tuple[Path, ...]`)
- Exact logging shape on PermissionError (single-line message vs structured)
- Whether to add a small `_security.py` helper module or keep validation inline in `_resolve_media_url`
- Helper function name (`_coerce_success_payload` vs `_derive_success` etc.)
</decisions>

<code_context>
## Existing Code Insights

### Touched files
- `src/chatlytics_hermes/adapter.py` — `_keep_typing` rewrite + `_typing_scope` extraction + `send_image`/`send_animation` `**kwargs` + `_resolve_media_url` allowlist + `_make_send_result`/`_standalone_send` dedup + `__init__` env var
- `src/chatlytics_hermes/tools.py` — `_ok` uses the canonical helper
- `src/chatlytics_hermes/inbound.py` — unchanged in this phase (lifecycle is in adapter.py)
- `tests/test_live_loader.py` — un-xfail 6 markers
- `tests/test_media.py` — migrate `_keep_typing` sites to `_typing_scope`
- `tests/test_concurrency.py` — NEW
- `README.md` — security note about `CHATLYTICS_UPLOAD_ALLOWED_ROOTS`

### Established Patterns
- pytest + pytest-asyncio (`pytestmark = pytest.mark.asyncio`)
- respx for httpx mocking
- aiohttp test client for inbound
- Tool handlers return `{"success": bool, ...}` shape (locked invariant)
- 21 tools (`tools.TOOLS` assertion locked)

### Integration Points
- Hermes v0.14 plugin loader: `BasePlatformAdapter._process_message_background` calls `asyncio.create_task(self._keep_typing(chat_id, metadata=...))` at `gateway/platforms/base.py:1787-1792`
- README documents env var

### v2.0 invariants (must preserve)
- Hermes pin `>=0.14,<0.15`
- 21 tools exactly
- httpx for outbound, aiohttp for embedded inbound only
- `{"success": bool, ...}` tool response shape
- 45/45 v2.0 tests + new Phase 7 tests still passing (regression tests now PASS not xfail)
- `chatlytics-hermes` package name
- MIT license
</code_context>

<specifics>
## Specific Ideas

- After fixing BL-01: verify Phase 7's `test_keep_typing_is_a_coroutine`, `test_bl01_keep_typing_accepts_metadata_kwarg`, and `test_base_process_message_invokes_keep_typing` flip from xfailed-strict to passed.
- After fixing HI-01: same flip for `test_hi01_send_file_rejects_path_outside_allowed_roots`.
- After fixing HI-03: same flip for `test_hi03_send_image_accepts_unknown_kwargs` and `test_hi03_send_animation_accepts_unknown_kwargs`.
- Test in HI-01 hits `/api/v1/upload` mock with `assert_all_called=False` and asserts the route was NOT called when filePath rejected — this is a path-rejection test, not a path-acceptance test, so allowlist must run BEFORE any upload.
- The existing `tests/test_media.py::test_keep_typing_heartbeats_every_30s` uses `async with adapter._keep_typing(...)`. After the rename, this must become `async with adapter._typing_scope(...)` — but only AFTER the test file is updated. Path order matters.
- README updates: add a "Security" or "Configuration" section mentioning `CHATLYTICS_UPLOAD_ALLOWED_ROOTS` (Phase 12 will polish the release doc; Phase 8 should leave a working baseline).
</specifics>

<deferred>
## Deferred Ideas

- Other security hardening (CSP, rate limiting) — out of scope for v2.1
- Live Chatlytics gateway testing — still operator-locked at verification-ceiling level
- `client.py` LO-03 (`_client` vs `is_closed` API hygiene) — defer to Phase 9 or beyond
- Observability sweep (LO-11 send_typing log level) — Phase 9
</deferred>
