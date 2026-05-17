---
phase: 04-media-ux-polish-cron
review_date: 2026-05-17
depth: standard
implemented_by: claude-opus-4-7-1m
reviewed_by: gsd-code-review
files_reviewed:
  - src/chatlytics_hermes/client.py
  - src/chatlytics_hermes/adapter.py
  - tests/test_media.py
  - tests/test_cron.py
  - tests/test_register.py
summary:
  blocker: 0
  high: 0
  medium: 2
  low: 4
  info: 2
overall_verdict: PASS_WITH_MINORS
---

# HERMES-04 -- Code Review

## Scope

Reviewed the 5 files touched by HERMES-04:

1. `src/chatlytics_hermes/client.py` (MODIFIED, +46 LOC) -- `send_media`, `upload_file`, `post_multipart`
2. `src/chatlytics_hermes/adapter.py` (MODIFIED, +501/-2 LOC, +36/-10 LOC for register block) -- 6 media handlers, `_keep_typing`, `_env_enablement`, `_standalone_send`, register-block extensions
3. `tests/test_media.py` (NEW, 300 LOC)
4. `tests/test_cron.py` (NEW, 82 LOC)
5. `tests/test_register.py` (MODIFIED, polarity flip on the deferred-hooks scope-discipline test)

Focus areas:

1. **Media handler correctness** -- payload shape, mediaType mapping, URL-vs-bytes-vs-path branching
2. **`_keep_typing` lifecycle** -- background-task creation, cancellation, exception swallowing
3. **Cron sender** -- env-var sourcing, missing-config behavior, kwarg compatibility
4. **Register-block extensions** -- `check_fn` (02-MED-01 fix), `cron_deliver_env_var`, `standalone_sender_fn`, `env_enablement_fn`
5. **Acceptance-criterion coverage** (8/8 verified PASS in dockerized clean-room -- see `04-VERIFICATION.md`)
6. **Scope discipline** (no leakage of HERMES-05/06 behavior)

## Verdict: PASS WITH MINORS

Zero BLOCKER, zero HIGH. Two MEDIUM and four LOW concerns documented
below -- none affect acceptance criteria or block HERMES-05 from
starting. The MEDIUMs are either documented-by-design trade-offs
(`_keep_typing` shape divergence) or known-future-work items (blocking
file I/O in async path); both are tracked as forward action items for
HERMES-05/06.

---

## Findings

### MEDIUM-01 -- `_keep_typing` overrides base coroutine with asynccontextmanager (shape divergence)

**File:** `src/chatlytics_hermes/adapter.py:721-786`

The upstream `BasePlatformAdapter._keep_typing` (see
`/tmp/hermes-ref-v0.14.0/gateway/platforms/base.py:2215`) is a plain
async coroutine intended to be `await`ed from a long-running handler
that wants typing bubbles for the duration of its own execution. The
HERMES-04 override is a `@contextlib.asynccontextmanager` -- callers
must use `async with adapter._keep_typing(chat_id): ...` and CANNOT
`await adapter._keep_typing(chat_id)` (the latter returns a
`_AsyncGeneratorContextManager` object without firing any typing call).

This is intentional per the phase brief ("async context manager,
yields, runs background heartbeat task..."), but it diverges from the
base contract. Any upstream code that calls `await
adapter._keep_typing(...)` against this adapter will silently no-op.
Concretely, `gateway/platforms/base.py:3053` reads:

```python
self._keep_typing(
    chat_id,
    **_keep_typing_kwargs,
)
```

This call is then wrapped in `asyncio.create_task(...)` somewhere -- a
type-checker would flag the type mismatch (Coroutine vs
AsyncContextManager), but at runtime Python just silently schedules the
context-manager object as a task that completes immediately without
firing typing calls.

**Suggested fix:** Either (a) keep the base shape (plain coroutine) and
expose a separate `async def keep_typing_block(...)` for the context-
manager flavor, OR (b) document the override loudly in the docstring
AND in the HERMES-05/06 docs.

**Disposition:** ACCEPT with documentation -- the phase brief is
explicit about the context-manager shape, and HERMES-05's tool handlers
will be the only callers (all in this plugin). The base-class path is
not exercised by any code in this repo. Track as a forward action item
for HERMES-06 release docs.

### MEDIUM-02 -- `_resolve_media_url` does blocking file I/O inside the async event loop

**File:** `src/chatlytics_hermes/adapter.py:455-457`

```python
path = str(resource)
with open(path, "rb") as fh:
    content = fh.read()
```

`open()` + `fh.read()` are synchronous; for a multi-MB document or
video file, the full read blocks the event loop. WhatsApp's max
attachment size is 100 MB, which at typical disk speeds is ~100-500 ms
of blocked event loop -- enough to delay other webhook handlers and
typing heartbeats running in the same loop.

**Suggested fix:** Use `asyncio.to_thread(fh.read)` or `aiofiles` for
the file read. Cheapest path is `asyncio.to_thread`:

```python
def _read_bytes(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()
content = await asyncio.to_thread(_read_bytes, path)
```

**Disposition:** DEFER to HERMES-05 or HERMES-06 -- in the current
plugin call path, media-handler invocations come from tool handlers
that are themselves awaiting in their own task, so the local impact is
bounded. The cost shows up only when the gateway is dispatching dozens
of concurrent media sends on the same event loop. Worth a hardening
pass before production rollout but not blocking.

### LOW-01 -- `send_image` accepts bytes but has no dedicated test

**File:** `src/chatlytics_hermes/adapter.py:585-602`, `tests/test_media.py:64-87`

`send_image(chat_id, image_url, ...)` widens the base signature's
`image_url: str` parameter to `Union[str, bytes, bytearray]` and the
implementation routes bytes through `/api/v1/upload` first. But the
test suite only exercises the URL branch for `send_image`; the
bytes-direct branch is exercised transitively via
`send_image_file_uploads_local_bytes` but never directly for
`send_image`.

**Suggested fix:** Add `test_send_image_with_bytes_uploads_first` to
`test_media.py` to lock the bytes-direct branch. Three-line test:

```python
async def test_send_image_with_bytes_uploads_first(adapter, mock_router):
    mock_router.get("/health").mock(return_value=httpx.Response(200, json={}))
    mock_router.post("/api/v1/upload").mock(
        return_value=httpx.Response(200, json={"url": "https://cdn.test/u.png"})
    )
    media = mock_router.post("/api/v1/send-media").mock(
        return_value=httpx.Response(200, json={"success": True, "messageId": "m"})
    )
    await adapter.connect()
    result = await adapter.send_image(CHAT_ID, b"\x89PNG\r\n\x1a\n raw bytes")
    assert result.success is True
    body = _json.loads(media.calls.last.request.content)
    assert body["mediaUrl"] == "https://cdn.test/u.png"
    await adapter.disconnect()
```

**Disposition:** DEFER -- the code path is exercised in test 6 and the
shared `_send_media_payload` helper means a regression would surface
in multiple tests. Acceptable coverage gap for v2.0.

### LOW-02 -- `_send_media_payload` includes `filename` only for `document` and `image_file`, but `send_document` also includes it for URL-path inputs

**File:** `src/chatlytics_hermes/adapter.py:565-566`

```python
if filename and media_kind in {"document", "image_file"}:
    body["filename"] = filename
```

For a URL-path document send (`send_document(CHAT_ID, "https://cdn.test/d.pdf", file_name="report.pdf")`),
the body includes both `mediaUrl=https://cdn.test/d.pdf` AND
`filename=report.pdf`. The Chatlytics gateway is presumed to honor
this and override the URL's basename in the WhatsApp display, but the
HERMES-04 phase brief and the Chatlytics API contract (as documented
in `04-CONTEXT.md`) do not explicitly confirm this.

**Suggested fix:** Confirm with the Chatlytics gateway team in
HERMES-06 whether `filename` is honored for URL-path documents, or
only for upload-path documents. If only the latter, gate the
`filename` injection to bytes/local-path inputs.

**Disposition:** DEFER -> HERMES-06 -- requires gateway-side
confirmation, not adapter-side code change.

### LOW-03 -- `_keep_typing` initial fire is sequential (await before task creation)

**File:** `src/chatlytics_hermes/adapter.py:765-773`

```python
try:
    await self.send_typing(chat_id, duration=30.0)
except Exception:
    logger.debug(...)

task = asyncio.create_task(_beat())
try:
    yield
```

The initial typing call is awaited BEFORE the heartbeat task is
created and BEFORE the wrapped body runs. If `send_typing` is slow
(e.g. 5-30 s on a degraded gateway), the wrapped body is delayed by
the same amount -- which defeats the purpose of "fire immediately so
the bubble appears without waiting `interval` seconds".

**Suggested fix:** Wrap the initial fire in `asyncio.create_task` so
the body starts immediately AND the bubble appears as soon as the
gateway responds:

```python
asyncio.create_task(self.send_typing(chat_id, duration=30.0))
task = asyncio.create_task(_beat())
try:
    yield
```

`ChatlyticsClient`'s 30 s timeout caps the worst case, but the
fire-and-forget pattern is closer to the UX goal.

**Disposition:** DEFER -- acceptable today because `ChatlyticsClient`
caps at 30 s and tools that wrap long-running work usually run on the
order of seconds-to-minutes (the 30s ceiling is rare). Revisit in
HERMES-05 once the actual tool-handler latency profile is known.

### LOW-04 -- `_keep_typing` exception swallow in initial fire uses bare `Exception`

**File:** `src/chatlytics_hermes/adapter.py:766-771`

```python
try:
    await self.send_typing(chat_id, duration=30.0)
except Exception:  # noqa: BLE001
    logger.debug(...)
```

`asyncio.CancelledError` inherits from `BaseException` (Python 3.8+),
so the bare `Exception` correctly does NOT catch cancellation. Good.
BUT a `KeyboardInterrupt` or `SystemExit` would also escape correctly,
which is what we want.

The concern is that the `noqa: BLE001` is correct here (intentional
broad catch for a UX heartbeat), but the LOW-04 lesson learned in
HERMES-02 (send_typing log flood) is partly mitigated by using
`logger.debug` instead of `logger.warning`. The flood concern from
LOW-02 (HERMES-02) is therefore RESOLVED for the heartbeat call path;
the original `send_typing` call in HERMES-02 still uses
`logger.warning` on transport errors and remains a forward action
item.

**Disposition:** ACCEPT. The 02-LOW-02 flood concern still applies to
the original `send_typing` call path -- defer to HERMES-05 or HERMES-06.

### INFO-01 -- `mimetypes.guess_type` is best-effort and may return `application/octet-stream` for known MIME types

**File:** `src/chatlytics_hermes/adapter.py:39-43`, `_resolve_media_url:445-462`

The `_guess_content_type` helper falls back to
`application/octet-stream` when `mimetypes.guess_type` returns None.
For some uncommon extensions (`.opus`, `.heic`, `.webm` on some
platforms), the stdlib's MIME database may not include the mapping.

The Chatlytics upload endpoint likely doesn't enforce Content-Type
beyond size limits, so the octet-stream fallback is fine. Worth a
note that if a future Chatlytics revision starts validating
Content-Type, the upload could 415 on Opus voice messages.

**Disposition:** ACCEPT. No action.

### INFO-02 -- `_standalone_send` uses a new `httpx.AsyncClient` per invocation

**File:** `src/chatlytics_hermes/adapter.py:852-861`

```python
async with httpx.AsyncClient(...) as client:
    response = await client.post(...)
```

`hermes cron` invocations are typically infrequent (minute-granularity
or coarser), so the per-call client construction cost is negligible
compared to network round-trip. If a future Hermes revision batches
cron deliveries (multiple `_standalone_send` calls per tick), pooling
the client would be a meaningful optimization.

**Disposition:** ACCEPT for v2.0. No action.

---

## What was reviewed for and found CLEAN

- **mediaType mapping is correct**: `voice` (NOT `audio`), `file`
  (NOT `document`), `video` for animations. Documented inline in
  `_MEDIA_TYPE_MAP` and asserted in tests 2, 4, 5.
- **`_resolve_media_url` correctly distinguishes the three input
  shapes**: bytes / `http(s)://...` / local path. URL passthrough
  skips the upload (test 1). Local path reads bytes and uploads (test
  6). Bytes-direct path uploads (covered transitively).
- **Upload response validation**: 3-layer check on status code, JSON
  parseability, and presence of `url` field. Each path raises
  `RuntimeError` with a clear message that `_send_media_payload`
  catches and surfaces as `SendResult(success=False, error=...)`.
- **`SendResult` mapping is consistent with `send()`**: 200 +
  `success: True` (or absent) -> success; 5xx -> `retryable=True`;
  4xx -> `retryable=False`. The shared `_make_send_result` helper
  guarantees no drift between `send()` and the 6 media handlers.
- **`_keep_typing` background-task cancellation**: `task.cancel()` in
  `finally` block, awaited with `CancelledError` caught. The
  `_keep_typing_swallows_send_typing_errors` test confirms that a
  failing typing endpoint does not abort the wrapped body.
- **`_standalone_send` env-var ordering**: All three required env vars
  checked together; missing any returns `{error: ...}` without
  raising. Forward-compat kwargs accepted without strict signature
  enforcement.
- **`register()` block extensions are additive**: All HERMES-01..03
  parameters preserved; HERMES-04 adds `check_fn`, `env_enablement_fn`,
  `cron_deliver_env_var`, `standalone_sender_fn`. Verified by
  `test_register_declares_hermes_04_hooks` and the unchanged
  `test_register_adds_chatlytics_platform`.
- **Scope discipline**: No `ctx.register_tool(...)` calls. No README
  edits. No new runtime dependencies.

---

## Carry-forward to HERMES-05

- MED-01 above: document `_keep_typing` context-manager shape divergence
  in the tool-handler docs that HERMES-05 will write
- MED-02 above: blocking file I/O in `_resolve_media_url` -- wrap with
  `asyncio.to_thread` if any tool handler does multi-MB media sends
- LOW-01 above: add `test_send_image_with_bytes_uploads_first` if a
  third-party caller passes bytes to `send_image` (otherwise transitive
  coverage is fine)
- LOW-03 above: convert `_keep_typing` initial fire to
  fire-and-forget if tool-handler latency profile shows the 30s ceiling
  hits in practice

## Carry-forward to HERMES-06

- LOW-02 above: confirm with Chatlytics gateway whether `filename` is
  honored for URL-path documents
- HERMES-02 LOW-02 (send_typing log flood): still deferred -- the
  HERMES-04 heartbeat uses `logger.debug` (good); the original
  `send_typing` call path still uses `logger.warning` and may flood
  if the gateway is unhealthy
- INFO-01 above: monitor for Content-Type 415 errors on uncommon
  media extensions; expand `mimetypes` knowledge base or pass
  per-call content_type if needed

## Cross-references

- ROADMAP: `.planning/ROADMAP.md` (Phase 4 acceptance criteria 1-8)
- PLAN: `.planning/phases/HERMES-04-media-ux-polish-cron/04-01-PLAN.md`
- SUMMARY: `.planning/phases/HERMES-04-media-ux-polish-cron/04-01-SUMMARY.md`
- VERIFICATION: `.planning/phases/HERMES-04-media-ux-polish-cron/04-VERIFICATION.md`
- HERMES-02 review (forward action items disposition): `.planning/phases/HERMES-02-outbound-text-control-parity/02-REVIEW.md`
- HERMES-03 review (forward action items disposition): `.planning/phases/HERMES-03-inbound-transport-migration/03-REVIEW.md`
- Upstream `_keep_typing` base shape: `/tmp/hermes-ref-v0.14.0/gateway/platforms/base.py:2215`
- Upstream IRC `_standalone_send` reference: `/tmp/hermes-ref-v0.14.0/plugins/platforms/irc/adapter.py:717`
- Upstream IRC `_env_enablement` reference: `/tmp/hermes-ref-v0.14.0/plugins/platforms/irc/adapter.py:651`
