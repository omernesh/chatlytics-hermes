"""HERMES-09: Observability + log hygiene regression tests.

Covers ROADMAP Phase 9 acceptance criteria and closes the carry-forward
v2.0 audit lows:

- 02-LOW-02 / LO-11: ``send_typing`` transport + non-200 paths log at
  DEBUG, not WARNING (UX-hint endpoint should not flood logs on a
  flapping gateway).
- 02-LOW-01: silent error paths now emit diagnostic logs --
  ``_make_tool_handler`` ctx.get_platform swallow, ``send()`` reserved
  metadata drop, ``tools.py`` JSON-decode swallows.
- 05-LOW-01: ``_make_tool_handler`` get_platform failure debugging.
- Generic guard: no ``api_key`` / ``Bearer `` substring ends up in any
  log record across a full smoke flow (defensive regression for the
  v2.0 invariant that secrets do not leak via the logger).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict

import httpx
import pytest
import respx

from chatlytics_hermes.adapter import ChatlyticsAdapter, _make_tool_handler
from chatlytics_hermes.client import ChatlyticsClient
from chatlytics_hermes.tools import chatlytics_send
from tests._fixtures import FakePlatformConfig


BASE_URL = "https://gateway.test.chatlytics.ai"
API_KEY = "SECRET_API_KEY_TEST_42"  # nosec: synthetic test value
CHAT_ID = "120363100000000000@g.us"


def _make_adapter() -> ChatlyticsAdapter:
    return ChatlyticsAdapter(
        FakePlatformConfig(
            extra={
                "base_url": BASE_URL,
                "api_key": API_KEY,
                "webhook_host": "127.0.0.1",
                "webhook_port": 0,
            }
        )
    )


@pytest.fixture
def adapter() -> ChatlyticsAdapter:
    return _make_adapter()


@pytest.fixture
async def connected_adapter(adapter: ChatlyticsAdapter):
    """Adapter with an attached client but no aiohttp server."""
    adapter._client = ChatlyticsClient(base_url=BASE_URL, api_key=API_KEY)
    try:
        yield adapter
    finally:
        await adapter._client.aclose()


@pytest.fixture
async def tools_client():
    c = ChatlyticsClient(base_url=BASE_URL, api_key=API_KEY)
    try:
        yield c
    finally:
        await c.aclose()


# ---------------------------------------------------------------------------
# AC-1: send_typing non-200 logs at DEBUG (not WARNING)
# ---------------------------------------------------------------------------


async def test_send_typing_non_200_logs_at_debug_not_warning(
    connected_adapter: ChatlyticsAdapter,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """02-LOW-02 / LO-11 fix: non-200 typing responses must not flood WARNING."""
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        router.post("/api/v1/typing").mock(
            return_value=httpx.Response(503, json={"error": "gateway flap"})
        )

        with caplog.at_level(logging.DEBUG, logger="chatlytics_hermes.adapter"):
            await connected_adapter.send_typing(CHAT_ID, duration=1.0)

    warnings = [
        r for r in caplog.records
        if r.levelno == logging.WARNING and "send_typing" in r.getMessage()
    ]
    debugs = [
        r for r in caplog.records
        if r.levelno == logging.DEBUG and "send_typing" in r.getMessage()
    ]
    assert not warnings, (
        f"send_typing non-200 must NOT log at WARNING; got: "
        f"{[r.getMessage() for r in warnings]}"
    )
    assert debugs, (
        "send_typing non-200 must emit a DEBUG record; "
        f"all records: {[(r.levelname, r.getMessage()) for r in caplog.records]}"
    )


# ---------------------------------------------------------------------------
# AC-2: send_typing transport error logs at DEBUG (not WARNING)
# ---------------------------------------------------------------------------


async def test_send_typing_transport_error_logs_at_debug(
    connected_adapter: ChatlyticsAdapter,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Transport-level RequestError must also stay at DEBUG."""
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        router.post("/api/v1/typing").mock(
            side_effect=httpx.ConnectError("boom")
        )

        with caplog.at_level(logging.DEBUG, logger="chatlytics_hermes.adapter"):
            await connected_adapter.send_typing(CHAT_ID, duration=1.0)

    warnings = [
        r for r in caplog.records
        if r.levelno == logging.WARNING and "send_typing" in r.getMessage()
    ]
    debugs = [
        r for r in caplog.records
        if r.levelno == logging.DEBUG and "transport error" in r.getMessage()
    ]
    assert not warnings, (
        f"send_typing transport error must NOT log at WARNING; got: "
        f"{[r.getMessage() for r in warnings]}"
    )
    assert debugs, (
        "send_typing transport error must emit a DEBUG record; "
        f"all records: {[(r.levelname, r.getMessage()) for r in caplog.records]}"
    )


# ---------------------------------------------------------------------------
# AC-3: send() WARNS on dropped reserved metadata key
# ---------------------------------------------------------------------------


async def test_send_warns_on_dropped_reserved_metadata(
    connected_adapter: ChatlyticsAdapter,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """02-LOW-01: caller learns that a reserved key was silently dropped."""
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        router.post("/api/v1/send").mock(
            return_value=httpx.Response(
                200, json={"success": True, "messageId": "m-1"}
            )
        )

        with caplog.at_level(logging.DEBUG, logger="chatlytics_hermes.adapter"):
            await connected_adapter.send(
                CHAT_ID,
                "hello",
                metadata={
                    "chatId": "OTHER",  # reserved -- must WARN
                    "replyTo": "msg-other",  # reserved -- must WARN
                    "extra": "ok",  # non-reserved -- must NOT warn
                },
            )

    warnings = [
        r for r in caplog.records if r.levelno == logging.WARNING
    ]
    reserved_warnings = [
        r for r in warnings if "reserved metadata key" in r.getMessage()
    ]
    assert len(reserved_warnings) == 2, (
        "Expected exactly two WARNING records for dropped reserved keys; "
        f"got {[r.getMessage() for r in reserved_warnings]}"
    )
    joined = " | ".join(r.getMessage() for r in reserved_warnings)
    assert "chatId" in joined
    assert "replyTo" in joined
    assert "extra" not in joined, (
        "Non-reserved key 'extra' must not appear in a reserved-key warning"
    )


# ---------------------------------------------------------------------------
# AC-4: _make_tool_handler logs ctx.get_platform failure at DEBUG
# ---------------------------------------------------------------------------


async def test_make_tool_handler_logs_get_platform_failure_at_debug(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """05-LOW-01: silent get_platform failure now emits a DEBUG record."""

    class _BadCtx:
        def get_platform(self, name: str):
            raise RuntimeError("ctx broken")

        platforms: Dict[str, Any] = {}

    async def _fake_handler(client, **kwargs):
        return {"success": True}

    wrapped = _make_tool_handler(_BadCtx(), "fake_tool", _fake_handler)

    with caplog.at_level(logging.DEBUG, logger="chatlytics_hermes.adapter"):
        result = await wrapped()

    # The handler should fall through to the "not connected" branch
    # because no adapter is resolvable.
    assert result["success"] is False

    debugs = [
        r for r in caplog.records
        if r.levelno == logging.DEBUG
        and "get_platform" in r.getMessage()
    ]
    assert debugs, (
        "Expected a DEBUG record mentioning get_platform; "
        f"all records: {[(r.levelname, r.getMessage()) for r in caplog.records]}"
    )


# ---------------------------------------------------------------------------
# AC-5: tools._post logs JSON-decode fallback at DEBUG
# ---------------------------------------------------------------------------


async def test_tools_post_json_decode_failure_logs_at_debug(
    tools_client: ChatlyticsClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """02-LOW-01: malformed gateway body no longer silent in tools._post."""
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        router.post("/api/v1/send").mock(
            return_value=httpx.Response(
                200, text="not json at all", headers={"content-type": "text/plain"}
            )
        )

        with caplog.at_level(logging.DEBUG, logger="chatlytics_hermes.tools"):
            result = await chatlytics_send(
                tools_client, chatId=CHAT_ID, text="hello"
            )

    # Tool still returns success (raw_text fallback is the canonical
    # behavior); we only assert the DEBUG record now surfaces it.
    assert result["success"] is True
    assert "raw_text" in result

    debugs = [
        r for r in caplog.records
        if r.levelno == logging.DEBUG
        and "JSON decode failed" in r.getMessage()
    ]
    assert debugs, (
        "Expected DEBUG record for JSON decode fallback; "
        f"all records: {[(r.levelname, r.getMessage()) for r in caplog.records]}"
    )


# ---------------------------------------------------------------------------
# AC-6: no api_key or Bearer token leaks into any log record
# ---------------------------------------------------------------------------


async def test_keep_typing_first_fire_emits_exactly_one_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """WARNING-01 regression (HERMES-09 fix-pass).

    The pre-fix code logged TWO WARNINGs on the exception path because
    the ``except Exception`` block set ``initial_ok = False`` and the
    fall-through ``if not initial_ok`` block fired again.  Post-fix the
    flow uses ``try/except/else`` so exactly one WARNING surfaces per
    first-fire-failure event.
    """
    adapter = _make_adapter()
    adapter._client = ChatlyticsClient(base_url=BASE_URL, api_key=API_KEY)

    # Force the degraded-status (non-exception) path -- the typing
    # endpoint returns 503, which _send_typing_once turns into a
    # ``False`` return without raising.  The else-branch fires exactly
    # one WARNING.
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        router.post("/api/v1/typing").mock(
            return_value=httpx.Response(503, text="upstream broken")
        )

        with caplog.at_level(logging.WARNING, logger="chatlytics_hermes.adapter"):
            stop = asyncio.Event()
            task = asyncio.create_task(
                adapter._keep_typing(
                    CHAT_ID, interval=10.0, stop_event=stop
                )
            )
            await asyncio.sleep(0.1)
            stop.set()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    warnings = [
        r for r in caplog.records
        if r.levelno == logging.WARNING
        and "send_typing initial fire" in r.getMessage()
    ]
    assert len(warnings) == 1, (
        f"Expected exactly ONE first-fire WARNING (WARNING-01 fix); "
        f"got {len(warnings)}: {[r.getMessage() for r in warnings]}"
    )

    await adapter._client.aclose()


async def test_no_api_key_in_any_log_record(
    adapter: ChatlyticsAdapter,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Defensive regression: secrets do not leak via the logger."""
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        router.get("/health").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        router.post("/api/v1/send").mock(
            return_value=httpx.Response(
                200, json={"success": True, "messageId": "m-1"}
            )
        )
        router.post("/api/v1/typing").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )

        with caplog.at_level(logging.DEBUG):
            # The adapter starts an aiohttp webhook server in connect();
            # we skip that by stubbing _start_inbound_server to a no-op
            # so the test stays unit-scoped (port 0 binds in CI but the
            # cleanup is racy under pytest-asyncio).
            async def _noop():
                return None

            adapter._start_inbound_server = _noop  # type: ignore[assignment]
            await adapter.connect()
            await adapter.send(CHAT_ID, "hello there")
            await adapter.send_typing(CHAT_ID, duration=1.0)
            await adapter.disconnect()

    leaks: list[str] = []
    for record in caplog.records:
        msg = record.getMessage()
        if API_KEY in msg:
            leaks.append(f"api_key in: {record.levelname} {msg!r}")
        if "Bearer " in msg:
            leaks.append(f"Bearer in: {record.levelname} {msg!r}")

    assert not leaks, (
        "Secrets leaked into log records:\n  " + "\n  ".join(leaks)
    )
