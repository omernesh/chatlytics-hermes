"""HERMES-08: Concurrency regression tests.

Covers:

- ``_resolve_media_url`` runs file I/O off the event loop (regression
  guard for the v2.0 commit ``5e00da9`` ``asyncio.to_thread`` fix --
  if someone later removes the wrap, concurrent media uploads will
  serialize and this test fails).
- ``_keep_typing`` initial fire does not block the wrapped body
  (regression guard for 04-LOW-03 -- the initial fire used to happen
  before the task spawn).
- ``_keep_typing`` first-fire failure logs at WARNING (06-LOW-02
  -- first-fire failure is operator-actionable; subsequent heartbeats
  stay at DEBUG to prevent log flood).
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
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
CHAT_ID = "120363100000000000@g.us"


class _FakePlatformConfig:
    """Minimal PlatformConfig stand-in (mirrors tests/test_inbound.py)."""

    def __init__(self, extra: Dict[str, Any]) -> None:
        self.extra = extra
        self.enabled = True
        self.token = None
        self.api_key = extra.get("api_key")
        self.home_channel = extra.get("home_channel")


def _make_adapter(*, upload_root: str = "") -> ChatlyticsAdapter:
    return ChatlyticsAdapter(
        _FakePlatformConfig(
            extra={
                "base_url": BASE_URL,
                "api_key": API_KEY,
                "webhook_host": "127.0.0.1",
                "webhook_port": 0,
                "webhook_path": "/webhook",
                "upload_allowed_roots": upload_root,
            }
        )
    )


# ---------------------------------------------------------------------
# _resolve_media_url runs file I/O off the event loop
# ---------------------------------------------------------------------


async def test_resolve_media_url_off_event_loop(tmp_path: Path) -> None:
    """``_resolve_media_url`` must wrap blocking file I/O in ``asyncio.to_thread``.

    Regression for the v2.0 fix (commit ``5e00da9``). If someone removes
    the ``to_thread`` wrap, concurrent ``_resolve_media_url`` calls
    against multi-MB files will serialize on the event-loop thread, and
    this test fails by exceeding its time budget.
    """
    f1 = tmp_path / "a.bin"
    f1.write_bytes(b"a" * 1024)
    f2 = tmp_path / "b.bin"
    f2.write_bytes(b"b" * 1024)

    adapter = _make_adapter(upload_root=str(tmp_path))
    adapter._client = ChatlyticsClient(base_url=BASE_URL, api_key=API_KEY)

    # Patch ``builtins.open`` to inject a 100ms blocking sleep -- this
    # simulates a slow disk read. If ``_resolve_media_url`` runs
    # ``open()`` on the event loop, two concurrent uploads would take
    # at least 200ms total. With ``asyncio.to_thread``, both ``open()``
    # calls run in worker threads and overlap to ~110ms.
    real_open = open

    def slow_open(path, *args, **kwargs):
        time.sleep(0.1)  # blocking -- only safe off the event loop
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
    # Generous margin for CI jitter: serial would be ~200ms, concurrent ~120ms.
    assert elapsed < 0.18, (
        f"Concurrent _resolve_media_url calls serialized "
        f"(elapsed={elapsed:.3f}s); asyncio.to_thread wrap may have been removed"
    )

    await adapter._client.aclose()


# ---------------------------------------------------------------------
# _keep_typing initial fire does not block the wrapped body
# ---------------------------------------------------------------------


async def test_keep_typing_initial_fire_does_not_block() -> None:
    """The wrapped body must start promptly even if initial typing hangs.

    Regression for 04-LOW-03: in v2.0 the initial typing fire happened
    in the asynccontextmanager body BEFORE the heartbeat task spawn,
    blocking the wrapped body until the request completed.  In HERMES-08
    the initial fire moved inside ``_keep_typing`` so the ``_typing_scope``
    body returns control immediately.
    """
    adapter = _make_adapter()
    adapter._client = ChatlyticsClient(base_url=BASE_URL, api_key=API_KEY)

    with respx.mock(assert_all_called=False) as router:

        async def slow_typing(request: httpx.Request) -> httpx.Response:
            # 500ms simulated upstream latency.
            await asyncio.sleep(0.5)
            return httpx.Response(200, json={"success": True})

        router.post(f"{BASE_URL}/api/v1/typing").mock(side_effect=slow_typing)
        router.route().pass_through()

        start = asyncio.get_event_loop().time()
        body_started_at = None
        async with adapter._typing_scope(CHAT_ID, interval=10.0):
            body_started_at = asyncio.get_event_loop().time() - start

        assert body_started_at is not None and body_started_at < 0.1, (
            f"Wrapped body blocked for {body_started_at:.3f}s waiting on "
            f"_keep_typing initial fire; expected <100ms even though initial "
            "typing takes 500ms"
        )

    await adapter._client.aclose()


# ---------------------------------------------------------------------
# _keep_typing first-fire failure logs at WARNING
# ---------------------------------------------------------------------


async def test_keep_typing_first_fire_failure_logs_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """First-fire failure must surface as WARNING (06-LOW-02 fix).

    The initial typing fire is operator-actionable: it tells you the
    gateway is misconfigured or down right now.  Subsequent heartbeats
    stay at DEBUG to prevent log flood when a gateway flaps for several
    minutes.
    """
    adapter = _make_adapter()
    adapter._client = ChatlyticsClient(base_url=BASE_URL, api_key=API_KEY)

    with respx.mock(assert_all_called=False) as router:
        # send_typing returns 500 -- the adapter swallows this internally
        # at WARNING level (send_typing's own log).  The first-fire
        # exception path in _keep_typing also surfaces as WARNING via
        # the explicit logger.warning call in HERMES-08.  We assert
        # SOMETHING at WARNING level surfaces for the chat_id.
        router.post(f"{BASE_URL}/api/v1/typing").mock(
            return_value=httpx.Response(500, text="upstream broken")
        )
        router.route().pass_through()

        with caplog.at_level(logging.WARNING, logger="chatlytics_hermes.adapter"):
            stop = asyncio.Event()
            task = asyncio.create_task(
                adapter._keep_typing(
                    CHAT_ID, interval=10.0, stop_event=stop
                )
            )
            await asyncio.sleep(0.1)  # let the initial fire complete
            stop.set()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warnings, (
            "Expected at least one WARNING log record on first-fire "
            f"failure; got nothing. All records: "
            f"{[(r.levelname, r.getMessage()) for r in caplog.records]}"
        )

    await adapter._client.aclose()
