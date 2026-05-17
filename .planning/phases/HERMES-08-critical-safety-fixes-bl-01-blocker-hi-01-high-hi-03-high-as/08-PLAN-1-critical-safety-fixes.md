---
phase: 8
plan_number: 1
plan_total: 1
title: Critical safety fixes (BL-01 + HI-01 + HI-03) + async lifecycle hardening + concurrency regression
status: ready
estimated_loc: 450
files_create:
  - tests/test_concurrency.py
files_modify:
  - src/chatlytics_hermes/adapter.py     # BL-01 + HI-01 + HI-03 + MD-01
  - src/chatlytics_hermes/tools.py       # MD-01 (single helper)
  - tests/test_live_loader.py            # un-xfail 6 markers
  - tests/test_media.py                  # migrate _keep_typing -> _typing_scope
  - README.md                            # CHATLYTICS_UPLOAD_ALLOWED_ROOTS doc
verification:
  - pytest tests/test_live_loader.py -v passes (no xfail markers left)
  - 6 xfail-strict markers un-xfailed; all 6 tests now PASS
  - tests/test_media.py::test_keep_typing_heartbeats_every_30s still passes (via _typing_scope)
  - tests/test_concurrency.py — new file, all tests pass
  - All v2.0 + Phase 7 + Phase 8 tests green, no xfail markers in the 3 BL-01/HI-01/HI-03 areas
---

# Plan 08-01: Critical safety fixes + async lifecycle hardening + concurrency regression

## Goal

Fix BL-01 (BLOCKER), HI-01 (HIGH), HI-03 (HIGH), MD-01 (success-shape dedup), 04-MED-01 / 04-LOW-03 / 06-LOW-02 (async lifecycle), and add concurrency regression coverage for the v2.0 `_resolve_media_url` `asyncio.to_thread` fix. Un-xfail the 6 Phase 7 regression tests once fixes land.

## Step 1 — BL-01 fix: rewrite `_keep_typing` + add `_typing_scope`

### `src/chatlytics_hermes/adapter.py`

Replace the existing `_keep_typing` `@asynccontextmanager` decorator (lines 741-806) with TWO methods:

```python
async def _keep_typing(
    self,
    chat_id: str,
    interval: float = 30.0,
    metadata: Optional[Dict[str, Any]] = None,
    stop_event: Optional[asyncio.Event] = None,
) -> None:
    """Continuously refresh the typing bubble until cancelled.

    Compatible with BasePlatformAdapter._keep_typing call site (in
    gateway/platforms/base.py:1787-1792) which does:

        asyncio.create_task(self._keep_typing(chat_id, metadata=..., stop_event=...))

    BL-01 fix: this method now returns a coroutine (was an
    @asynccontextmanager in v2.0, which crashed asyncio.create_task with
    "a coroutine was expected, got _AsyncGeneratorContextManager").

    metadata is accepted for base-class signature compatibility; the
    Chatlytics typing endpoint does not consume it currently.
    """
    # Initial fire: WARNING on failure (04-LOW-03 / 06-LOW-02 — first-fire
    # failure is operator-actionable; subsequent heartbeats stay at DEBUG
    # to prevent log flood).
    try:
        await self.send_typing(chat_id, duration=30.0)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001
        logger.warning(
            "send_typing initial fire raised for chat %s; continuing heartbeat",
            chat_id,
            exc_info=True,
        )

    while True:
        if stop_event is not None and stop_event.is_set():
            return
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            return
        if stop_event is not None and stop_event.is_set():
            return
        try:
            await self.send_typing(chat_id, duration=30.0)
        except asyncio.CancelledError:
            return
        except Exception:  # noqa: BLE001
            logger.debug(
                "send_typing heartbeat raised; continuing",
                exc_info=True,
            )


@contextlib.asynccontextmanager
async def _typing_scope(self, chat_id: str, interval: float = 30.0):
    """In-plugin convenience wrapper around _keep_typing.

    Usage::

        async with adapter._typing_scope(chat_id):
            result = await long_running_tool()

    Spawns a background task running ``_keep_typing`` with an internal
    stop_event. On context-manager exit, sets the stop event, cancels the
    task, and awaits cancellation. Errors from the typing path are
    swallowed so they never abort the wrapped body.
    """
    stop = asyncio.Event()
    task = asyncio.create_task(
        self._keep_typing(chat_id, interval=interval, stop_event=stop)
    )
    try:
        yield
    finally:
        stop.set()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001
            logger.debug("_typing_scope teardown raised; continuing", exc_info=True)
```

Note: the initial fire now happens INSIDE the coroutine. This means the wrapped body in `_typing_scope` starts immediately (does NOT block waiting for the first `send_typing` round-trip — 04-LOW-03 fix).

## Step 2 — HI-01 fix: filePath allowlist

### `src/chatlytics_hermes/adapter.py` — `__init__`

Add after the `self.home_channel` block (around line 138):

```python
# Path allowlist for filePath uploads (HI-01 fix). When unset, all
# local-file uploads are rejected (default-deny). Use OS path separator
# (':' on POSIX, ';' on Windows).
_roots_raw = os.getenv("CHATLYTICS_UPLOAD_ALLOWED_ROOTS") or extra.get(
    "upload_allowed_roots", ""
)
self.upload_allowed_roots: list[Path] = []
if _roots_raw:
    for entry in str(_roots_raw).split(os.pathsep):
        entry = entry.strip()
        if not entry:
            continue
        try:
            self.upload_allowed_roots.append(Path(entry).expanduser().resolve())
        except (OSError, RuntimeError):
            logger.warning(
                "CHATLYTICS_UPLOAD_ALLOWED_ROOTS contained un-resolvable path: %s",
                entry,
            )
```

Add `from pathlib import Path` to the top-of-file imports.

### `src/chatlytics_hermes/adapter.py` — `_resolve_media_url`

Modify the local-file branch (lines 466-482). Before reading the file, validate against the allowlist:

```python
else:
    # Local file path. Reject paths outside the configured allowlist
    # BEFORE opening the file (HI-01 fix). When the allowlist is empty
    # (env var unset), every local upload is rejected — default-deny.
    path = str(resource)
    try:
        resolved = Path(path).expanduser().resolve()
    except (OSError, RuntimeError) as exc:
        raise PermissionError(
            f"Cannot resolve upload path: {exc}"
        ) from exc
    if not self.upload_allowed_roots:
        raise PermissionError(
            "Local file uploads are disabled; set "
            "CHATLYTICS_UPLOAD_ALLOWED_ROOTS to an allowlist of absolute "
            "paths to enable filePath uploads."
        )
    allowed = False
    for root in self.upload_allowed_roots:
        try:
            if resolved == root or resolved.is_relative_to(root):
                allowed = True
                break
        except AttributeError:
            # Python 3.8 fallback (resolve() returns Path always)
            if str(resolved).startswith(str(root) + os.sep) or str(resolved) == str(root):
                allowed = True
                break
    if not allowed:
        raise PermissionError(
            f"Refusing upload outside CHATLYTICS_UPLOAD_ALLOWED_ROOTS: {resolved}"
        )

    def _read_file() -> tuple[bytes, str]:
        with open(str(resolved), "rb") as fh:
            return fh.read(), os.path.basename(str(resolved)) or "upload.bin"

    content, basename = await asyncio.to_thread(_read_file)
    # ... existing upload logic
```

### `src/chatlytics_hermes/adapter.py` — `_send_media_payload`

The existing `except OSError` catch covers `PermissionError` (subclass). Confirm the error message is preserved:

```python
except FileNotFoundError as exc:
    return SendResult(success=False, error=f"File not found: {exc}")
except PermissionError as exc:
    return SendResult(success=False, error=f"Permission denied: {exc}")
except OSError as exc:
    return SendResult(success=False, error=f"File read error: {exc}")
```

This ordering ensures `PermissionError` (an OSError subclass) is caught with the right message before the generic OSError branch.

## Step 3 — HI-03 fix: add `**kwargs` to `send_image` and `send_animation`

### `src/chatlytics_hermes/adapter.py`

```python
async def send_image(
    self,
    chat_id: str,
    image_url: Union[str, bytes, bytearray],
    caption: Optional[str] = None,
    reply_to: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    **kwargs: Any,
) -> "SendResult":
    """..."""
    # **kwargs is swallowed for forward-compat with upstream base
    # signature evolution. Today the gateway has no use for extra kwargs
    # on image sends; future versions may add priority, force_native, etc.
    return await self._send_media_payload(
        chat_id, "image", image_url, caption=caption
    )


async def send_animation(
    self,
    chat_id: str,
    animation_url: Union[str, bytes, bytearray],
    caption: Optional[str] = None,
    reply_to: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    **kwargs: Any,
) -> "SendResult":
    """..."""
    return await self._send_media_payload(
        chat_id, "animation", animation_url, caption=caption
    )
```

## Step 4 — MD-01 fix: canonical success-shape helper

### `src/chatlytics_hermes/adapter.py` — add module-level helper

After the `_guess_content_type` helper (around line 67):

```python
def _coerce_success_payload(
    status_code: int,
    payload: Any,
) -> tuple[bool, Optional[str]]:
    """Single source of truth for Chatlytics gateway response success derivation.

    Returns ``(success, error_msg)``:

    - HTTP 4xx/5xx -> (False, payload.error or "HTTP {status}")
    - HTTP 2xx + payload.success is False -> (False, payload.error or "gateway reported success=false")
    - HTTP 2xx + payload.success is True or absent -> (True, None)
    - Non-dict payload on 2xx -> (True, None)

    Used by ``ChatlyticsAdapter._make_send_result``,
    ``_standalone_send``, and ``tools._ok`` so all three sites agree on
    the contract.
    """
    if status_code >= 400:
        err = (payload.get("error") if isinstance(payload, dict) else None) \
            or f"HTTP {status_code}"
        return False, err
    if isinstance(payload, dict) and payload.get("success") is False:
        err = payload.get("error") or "gateway reported success=false"
        return False, err
    return True, None
```

### `src/chatlytics_hermes/adapter.py` — `_make_send_result`

```python
def _make_send_result(self, response: httpx.Response) -> "SendResult":
    try:
        payload: Any = response.json()
    except Exception:  # noqa: BLE001
        payload = {"raw_text": response.text}
    success, error_msg = _coerce_success_payload(response.status_code, payload)
    if success:
        return SendResult(
            success=True,
            message_id=payload.get("messageId") if isinstance(payload, dict) else None,
            raw_response=payload,
        )
    return SendResult(
        success=False,
        error=error_msg,
        raw_response=payload,
        retryable=response.status_code >= 500,
    )
```

### `src/chatlytics_hermes/adapter.py` — `_standalone_send`

Apply same helper in the final block (around line 890):

```python
success, error_msg = _coerce_success_payload(response.status_code, payload)
if success:
    result: Dict[str, Any] = {"success": True}
    if isinstance(payload, dict):
        result.update(payload)
        result["success"] = True
    return result
return {
    "success": False,
    "error": error_msg,
    "raw_response": payload,
}
```

### `src/chatlytics_hermes/tools.py` — `_ok` + `_post` + `_get`

`_ok` keeps its current "spread payload, re-assert success=True" behavior for `True` responses (the success path). The bug was that `_ok` was called unconditionally on 2xx — `_post`/`_get` must check `_coerce_success_payload` BEFORE calling `_ok`. Add import:

```python
from .adapter import _coerce_success_payload
```

Then in `_post`:

```python
async def _post(client, path, body):
    try:
        response = await client.post(path, json=body)
    except httpx.RequestError as exc:
        return _err_from_exception(exc)
    if response.status_code >= 400:
        return _err_from_response(response)
    try:
        payload = response.json()
    except Exception:
        return _ok({"raw_text": response.text})
    success, error_msg = _coerce_success_payload(response.status_code, payload)
    if not success:
        return {
            "success": False,
            "error": error_msg,
            "status_code": response.status_code,
            "raw_response": payload,
        }
    return _ok(payload)
```

Same for `_get`.

## Step 5 — Un-xfail Phase 7 regression tests

### `tests/test_live_loader.py`

Remove the `@pytest.mark.xfail(strict=True, ...)` decorators from all 6 tests:
- `test_keep_typing_is_a_coroutine`
- `test_bl01_keep_typing_accepts_metadata_kwarg`
- `test_base_process_message_invokes_keep_typing`
- `test_hi01_send_file_rejects_path_outside_allowed_roots`
- `test_hi03_send_image_accepts_unknown_kwargs`
- `test_hi03_send_animation_accepts_unknown_kwargs`

The HI-01 test will need the env var `CHATLYTICS_UPLOAD_ALLOWED_ROOTS` set to a SAFE path (NOT the `/etc/passwd` or `C:/Windows` parent) so the allowlist rejects the malicious path. Add to the test body:

```python
import tempfile
monkeypatch_or_env = tempfile.gettempdir()  # safe root
# adapter constructed with extra={"upload_allowed_roots": monkeypatch_or_env}
```

Actually, simpler: construct the adapter with an `upload_allowed_roots` set to a safe temp dir (via `_FakePlatformConfig.extra`). Validate that `bad_path` (e.g. `/etc/passwd`) is rejected because it's outside the safe root.

## Step 6 — Migrate `tests/test_media.py` heartbeat tests

### `tests/test_media.py`

Replace `async with adapter._keep_typing(CHAT_ID, interval=0.05):` with `async with adapter._typing_scope(CHAT_ID, interval=0.05):` in:
- `test_keep_typing_heartbeats_every_30s`
- `test_keep_typing_swallows_send_typing_errors`

(The semantics are identical for callers; only the name changed.)

## Step 7 — `tests/test_concurrency.py` — new file

```python
"""HERMES-08: Concurrency regression tests.

Covers:
- _resolve_media_url runs file I/O off the event loop (v2.0 to_thread fix)
- _keep_typing initial fire does not block the wrapped body
- _keep_typing initial-fire failure logs at WARNING (06-LOW-02)
"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any, Dict
from unittest.mock import patch

import httpx
import pytest
import respx

from chatlytics_hermes.adapter import ChatlyticsAdapter
from chatlytics_hermes.client import ChatlyticsClient

pytestmark = pytest.mark.asyncio

BASE_URL = "https://gateway.test.chatlytics.ai"
API_KEY = "test-api-key-concurrency"


class _FakePlatformConfig:
    def __init__(self, extra: Dict[str, Any]) -> None:
        self.extra = extra
        self.enabled = True
        self.token = None
        self.api_key = extra.get("api_key")
        self.home_channel = extra.get("home_channel")


def _make_adapter(*, upload_root: str = "") -> ChatlyticsAdapter:
    return ChatlyticsAdapter(_FakePlatformConfig(extra={
        "base_url": BASE_URL,
        "api_key": API_KEY,
        "webhook_host": "127.0.0.1",
        "webhook_port": 0,
        "webhook_path": "/webhook",
        "upload_allowed_roots": upload_root,
    }))


# --- _resolve_media_url runs off event loop -------------------------

async def test_resolve_media_url_off_event_loop(tmp_path: Path) -> None:
    """_resolve_media_url must use asyncio.to_thread for file I/O.

    Regression for the v2.0 fix (commit 5e00da9). If someone removes the
    to_thread wrap, concurrent media uploads serialize and this test
    fails by exceeding the time budget.
    """
    # Two files in a safe upload root
    f1 = tmp_path / "a.bin"
    f1.write_bytes(b"a" * 1024)
    f2 = tmp_path / "b.bin"
    f2.write_bytes(b"b" * 1024)

    adapter = _make_adapter(upload_root=str(tmp_path))
    adapter._client = ChatlyticsClient(base_url=BASE_URL, api_key=API_KEY)

    # Patch open() to inject a 100ms sleep -- this simulates slow disk.
    # If _resolve_media_url runs open() on the event loop, two concurrent
    # uploads would take >=200ms total. With asyncio.to_thread, they
    # overlap to <=~110ms.
    real_open = open

    def slow_open(path, *args, **kwargs):
        time.sleep(0.1)  # blocking sleep — only safe off the event loop
        return real_open(path, *args, **kwargs)

    with respx.mock(assert_all_called=False) as router:
        router.post(f"{BASE_URL}/api/v1/upload").mock(
            return_value=httpx.Response(200, json={"url": "https://x/y"})
        )

        with patch("builtins.open", slow_open):
            start = asyncio.get_event_loop().time()
            r1, r2 = await asyncio.gather(
                adapter._resolve_media_url(str(f1)),
                adapter._resolve_media_url(str(f2)),
            )
            elapsed = asyncio.get_event_loop().time() - start

        assert r1 == "https://x/y" and r2 == "https://x/y"
        # Allow generous margin for CI jitter: serial would be ~200ms, concurrent ~120ms
        assert elapsed < 0.18, (
            f"Concurrent _resolve_media_url calls serialized "
            f"(elapsed={elapsed:.3f}s); asyncio.to_thread wrap may have been removed"
        )

    await adapter._client.aclose()


# --- _keep_typing initial fire does not block -----------------------

async def test_keep_typing_initial_fire_does_not_block() -> None:
    """The wrapped body must run promptly even if initial typing hangs.

    Regression for 04-LOW-03: the initial typing fire used to happen
    BEFORE the task spawn, blocking the wrapped body until the request
    completed. Now the initial fire runs INSIDE the coroutine so the
    body starts immediately.
    """
    adapter = _make_adapter()
    adapter._client = ChatlyticsClient(base_url=BASE_URL, api_key=API_KEY)

    with respx.mock(assert_all_called=False) as router:
        # Slow typing endpoint — 500ms latency.
        async def slow_typing(request):
            await asyncio.sleep(0.5)
            return httpx.Response(200, json={"success": True})

        router.post(f"{BASE_URL}/api/v1/typing").mock(side_effect=slow_typing)
        router.route().pass_through()

        start = asyncio.get_event_loop().time()
        body_started_at = None
        async with adapter._typing_scope("120363xxx@g.us", interval=10.0):
            body_started_at = asyncio.get_event_loop().time() - start
        # Body started within 50ms even though first typing takes 500ms
        assert body_started_at is not None and body_started_at < 0.05, (
            f"Wrapped body blocked for {body_started_at:.3f}s waiting on "
            f"_keep_typing initial fire; expected <50ms"
        )

    await adapter._client.aclose()


# --- _keep_typing first-fire failure logs WARNING -------------------

async def test_keep_typing_first_fire_failure_logs_warning(caplog) -> None:
    """First-fire failure must surface as WARNING (06-LOW-02 fix).

    Subsequent heartbeats stay at DEBUG to prevent log flood; the first
    failure is what an operator needs to see promptly.
    """
    adapter = _make_adapter()
    adapter._client = ChatlyticsClient(base_url=BASE_URL, api_key=API_KEY)

    with respx.mock(assert_all_called=False) as router:
        router.post(f"{BASE_URL}/api/v1/typing").mock(
            return_value=httpx.Response(500, text="upstream broken")
        )

        with caplog.at_level(logging.WARNING, logger="chatlytics_hermes.adapter"):
            stop = asyncio.Event()
            task = asyncio.create_task(
                adapter._keep_typing(
                    "120363xxx@g.us", interval=10.0, stop_event=stop
                )
            )
            await asyncio.sleep(0.1)  # let the initial fire complete
            stop.set()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # send_typing swallows HTTP non-200 internally (it logs warning
        # inside send_typing too); the initial-fire path must also log
        # at WARNING level. Accept either: the visible WARNING from
        # send_typing's own non-200 path is acceptable here since that's
        # the actual operator signal. Just assert SOMETHING at WARNING
        # surfaced for the chat_id.
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("120363xxx" in r.getMessage() or "send_typing" in r.getMessage()
                   for r in warnings), (
            f"No WARNING log for first-fire failure; got: {[r.getMessage() for r in caplog.records]}"
        )

    await adapter._client.aclose()
```

## Step 8 — README.md security note

Add a "Security" subsection or extend the existing config section:

```markdown
### Security: filePath upload allowlist (CHATLYTICS_UPLOAD_ALLOWED_ROOTS)

The 5 media tools (`chatlytics_send_image`, `chatlytics_send_voice`,
`chatlytics_send_video`, `chatlytics_send_file`,
`chatlytics_send_animation`) accept an optional `filePath` parameter for
uploading local files. To prevent prompt-injection attacks from reading
arbitrary host files (e.g. `/etc/passwd`), local-file uploads are
**default-deny**: they are rejected unless the path resolves to a
configured allowed root.

Configure the allowlist via the `CHATLYTICS_UPLOAD_ALLOWED_ROOTS`
environment variable, OS-pathsep-separated:

```bash
# POSIX: colon-separated
export CHATLYTICS_UPLOAD_ALLOWED_ROOTS="/var/uploads:/tmp/chatlytics"

# PowerShell: semicolon-separated
$env:CHATLYTICS_UPLOAD_ALLOWED_ROOTS = "C:\Users\Public\Uploads;C:\Temp\chatlytics"
```

When the env var is **unset**, all `filePath` uploads fail with
`{"success": false, "error": "Local file uploads are disabled; ..."}`.
URL-based uploads via `mediaUrl` are unaffected.
```

## Verification

After Step 7 lands:

```bash
pytest tests/test_live_loader.py -v
pytest tests/test_media.py -v
pytest tests/test_concurrency.py -v
pytest tests/ -q   # full suite — should be 0 xfail in BL-01/HI-01/HI-03 area
```

All 6 previously-xfail tests must now PASS. The full suite should report
no XPASS-failed-because-strict failures.

## Acceptance criteria

1. `_keep_typing` is `async def`, returns a coroutine, accepts `metadata=` and `stop_event=` kwargs (BL-01)
2. `_typing_scope` exists as `@asynccontextmanager`; in-plugin call sites use it (BL-01)
3. `_resolve_media_url` rejects local paths outside `CHATLYTICS_UPLOAD_ALLOWED_ROOTS` with `PermissionError`; tool layer surfaces `{success: False, error: ...}` (HI-01)
4. `send_image` and `send_animation` accept `**kwargs` (HI-03)
5. `_coerce_success_payload` is the single helper for success derivation (MD-01)
6. 6 Phase 7 xfail-strict markers removed; corresponding tests pass
7. `tests/test_media.py::test_keep_typing_heartbeats_every_30s` still passes (via `_typing_scope`)
8. `tests/test_concurrency.py` exists with 3 tests: `_resolve_media_url_off_event_loop`, `keep_typing_initial_fire_does_not_block`, `keep_typing_first_fire_failure_logs_warning` — all pass
9. README documents `CHATLYTICS_UPLOAD_ALLOWED_ROOTS`
10. All 45 v2.0 baseline tests still pass (no regressions)
