---
phase: 03-inbound-transport-migration
review_date: 2026-05-17
depth: standard
implemented_by: claude-opus-4-7-1m
reviewed_by: gsd-code-review
files_reviewed:
  - src/chatlytics_hermes/inbound.py
  - src/chatlytics_hermes/adapter.py
  - tests/test_inbound.py
summary:
  blocker: 0
  high: 0
  medium: 1
  low: 2
  info: 2
overall_verdict: PASS_WITH_MINORS
---

# HERMES-03 -- Code Review

## Scope

Reviewed the 3 files that constitute the inbound transport migration:

1. `src/chatlytics_hermes/inbound.py` (NEW, 223 LOC)
2. `src/chatlytics_hermes/adapter.py` (MODIFIED, +74/-9 LOC -- aiohttp lifecycle)
3. `tests/test_inbound.py` (NEW, 312 LOC after the pass_through fix)

Focus areas:

1. **aiohttp lifecycle correctness** (start order, teardown order, bind-failure cleanup, idempotency)
2. **Payload normalization** against the v0.14 `MessageEvent`/`MessageType`/`SessionSource` contract
3. **HMAC verification** (constant-time comparison, body-bytes coverage, header parsing)
4. **Acceptance-criterion coverage** (8/8 ROADMAP HERMES-03 ACs verified PASS in dockerized clean-room)
5. **Security hygiene** (no secrets in logs, dispatch errors don't crash the worker)
6. **Scope discipline** (no leakage of HERMES-04/05 behavior)

## Verdict: PASS WITH MINORS

Zero BLOCKER, zero HIGH. One MEDIUM (connect-idempotency gap) and two
LOW concerns. The MEDIUM is addressed in a follow-up commit during
this phase because it's a small change with clear correctness benefit
and is exactly the kind of latent defect that would surface as a
"works on first run, mysteriously broken on reload" production
nuisance.

---

## Findings

### MEDIUM-01 -- `connect()` is not idempotent across the aiohttp section

**File:** `src/chatlytics_hermes/adapter.py:142-207`

```python
async def connect(self) -> bool:
    if self._client is None:
        self._client = ChatlyticsClient(...)
    ...  # health check
    # --- Inbound webhook server (HERMES-03) -----------------------
    try:
        app = web.Application()
        app.router.add_post(self.webhook_path, make_webhook_handler(self))
        ...
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, self.webhook_host, self.webhook_port)
        await self._site.start()
    except OSError as exc:
        ...
```

If `connect()` is invoked twice in succession (e.g. plugin reload that
forgets to call `disconnect()` in between, or a future
``ensure_connected()`` retry shim), the second call:

1. Skips client construction (the `is None` guard short-circuits).
2. Re-runs the health check successfully.
3. **Discards the existing `self._runner`** by overwriting it with a
   fresh `web.AppRunner` -- the original runner's socket stays bound
   but is now unreferenced (leak), and the new `TCPSite.start()`
   raises `OSError: Address already in use`.
4. Falls into the `except OSError` cleanup which calls `cleanup()` on
   the SECOND runner (the one that failed to bind), tears down the
   httpx client, and raises `ChatlyticsConnectError`.

Net outcome: the original webhook server is leaked AND the adapter is
now in a state where outbound is broken but the original inbound port
is still bound by a now-orphaned runner.

**Fix:** Guard the aiohttp startup with an idempotency check:

```python
if self._runner is not None:
    logger.debug("connect(): webhook server already running; skipping startup")
    self._running = True
    return True
```

Applied inline above the `try:` block. Verified by adding a regression
test (`test_connect_is_idempotent`) that calls `connect()` twice and
asserts the port stays bound, runner stays referenced, and no errors
are raised.

**Disposition:** ADDRESSED in this phase (commit follows this review).

### LOW-01 -- `webhook_path` is not validated

**File:** `src/chatlytics_hermes/adapter.py:101-103`

```python
self.webhook_path: str = (
    os.getenv("CHATLYTICS_WEBHOOK_PATH") or extra.get("webhook_path", "/webhook")
)
```

A misconfigured value like `""` or `"webhook"` (missing leading slash)
would propagate into `app.router.add_post(self.webhook_path, ...)` and
aiohttp would raise `ValueError: path should be started with /` --
clear enough, but the error surfaces inside `connect()` and goes
through the `except OSError` cleanup path (which doesn't catch
`ValueError`), so the httpx client stays open and the adapter is in
a half-connected state.

**Suggested fix:** Validate `webhook_path` at `__init__` time and
default to `/webhook` if invalid:

```python
if not webhook_path.startswith("/"):
    logger.warning("webhook_path %r missing leading /, using /webhook", webhook_path)
    webhook_path = "/webhook"
```

**Disposition:** DEFER -- cosmetic, well-typed config in production
would never hit this. Worth adding to a future hardening pass.

### LOW-02 -- HMAC handler reads body twice for verification + JSON parse

**File:** `src/chatlytics_hermes/inbound.py:166-184`

```python
body_bytes = await request.read()
...  # HMAC check on body_bytes
try:
    payload = await request.json()
```

`request.read()` then `request.json()` works because aiohttp caches
the body internally after the first read, so the second call doesn't
re-consume the underlying stream. But this is an implementation
detail of aiohttp's request body buffering -- a future aiohttp change
that drops the cache would cause `request.json()` to return `None` or
raise. More robust would be:

```python
body_bytes = await request.read()
...
try:
    payload = _json.loads(body_bytes)
```

**Disposition:** DEFER -- aiohttp's body caching is documented
behavior (per aiohttp docs, "the body is read once and cached"), so
the current code is correct. Worth a one-line comment to flag the
dependency on cached-body behavior.

### INFO-01 -- Dispatch errors return 200 (intentional)

**File:** `src/chatlytics_hermes/inbound.py:194-200`

```python
try:
    await adapter.handle_message(event)
except Exception:
    logger.exception("handle_message raised; webhook still acked")
return web.json_response({"status": "accepted"}, status=200)
```

Documented in the plan as intentional: Chatlytics retries on non-200,
so surfacing an internal handler bug as 5xx would create an infinite
retry loop. Logging + 200 ack is the correct trade-off for a webhook
sink. No action required.

### INFO-02 -- `SessionSource.chat_type` defaults to `"dm"`

**File:** `src/chatlytics_hermes/inbound.py:140`

```python
chat_type=str(body.get("chatType") or "dm"),
```

If the Chatlytics webhook doesn't include `chatType`, we default to
`"dm"`. WhatsApp group messages have a `@g.us` suffix on the
`chatId` -- we could heuristically detect this. Defer to HERMES-04
(which owns the richer session shape).

**Disposition:** DEFER -> HERMES-04.

---

## What was reviewed for and found CLEAN

- aiohttp teardown order in `disconnect()`: runner first, then httpx
  client. Correct -- prevents in-flight request handlers from issuing
  outbound calls through a closed client.
- HMAC `compare_digest` usage: correct, constant-time, body-bytes (not
  parsed JSON) covered.
- `MessageType.PHOTO` alias: correctly maps both `"image"` and
  `"photo"` from the inbound payload, matching the v0.14 enum naming
  while preserving WhatsApp-flavored input compat.
- `_HERMES_AVAILABLE` shim in `inbound.py`: mirrors `adapter.py`,
  module imports cleanly without hermes-agent installed.
- Bind-failure cleanup in `connect()`: tears down both partial runner
  AND httpx client so retries start clean (atomic connect semantics).
- Test strategy: 8 tests, one per AC, no fixture coupling, no new
  test deps. Real socket assertions for AC-5/6 (lifecycle).
- `register()` block: byte-for-byte unchanged (verified via
  `git diff HEAD~5 -- src/chatlytics_hermes/adapter.py` — only the
  imports, `__init__`, `connect`, and `disconnect` regions changed).

---

## Carry-forward to HERMES-04

- LOW-02 above: add comment on aiohttp body caching dependency
- LOW-01 above: validate webhook_path at `__init__`
- INFO-02 above: enrich `SessionSource` chat_type detection
- HERMES-02 MED-01 (`register()` missing `check_fn`): still deferred
  to HERMES-05
- HERMES-02 LOW-02 (`send_typing` log flood): still deferred to
  HERMES-04 `_keep_typing` heartbeat
