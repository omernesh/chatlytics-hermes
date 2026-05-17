"""HERMES-03 acceptance tests for the chatlytics-hermes inbound surface.

Eight tests covering ROADMAP Phase 3 acceptance criteria 1-8.

Test strategy:
- Bind the adapter's webhook server to an ephemeral port via
  ``aiohttp.test_utils.unused_port()``.
- Mock the httpx outbound /health endpoint via ``respx`` so
  ``adapter.connect()`` reaches the aiohttp startup block.
- Replace ``adapter.handle_message`` with a list-recorder so we can
  assert what the inbound handler dispatched.
- Drive real HTTP traffic against the started aiohttp server via
  ``httpx.AsyncClient`` (no ``TestClient`` -- AC-5/6 require a real
  socket).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json as _json
import socket
from typing import Any, Dict, List

import httpx
import pytest
import respx
from aiohttp.test_utils import unused_port

from chatlytics_hermes.adapter import ChatlyticsAdapter
from tests._fixtures import FakePlatformConfig

BASE_URL = "https://gateway.test.chatlytics.ai"
API_KEY = "test-api-key-abc123"
CHAT_ID = "chat-001"
WEBHOOK_SECRET = "shhh-very-secret"


def _make_adapter(
    *, with_secret: bool = False, port: int | None = None
) -> ChatlyticsAdapter:
    extra: Dict[str, Any] = {
        "base_url": BASE_URL,
        "api_key": API_KEY,
        "webhook_host": "127.0.0.1",
        "webhook_port": port if port is not None else unused_port(),
        "webhook_path": "/webhook",
    }
    if with_secret:
        extra["webhook_secret"] = WEBHOOK_SECRET
    return ChatlyticsAdapter(FakePlatformConfig(extra=extra))


@pytest.fixture
def mock_health():
    # Mock the outbound /health call to the fake Chatlytics gateway.
    # Any other httpx traffic in this test module is made against a
    # real local aiohttp server bound to 127.0.0.1 -- register an
    # explicit ``pass_through`` catch-all so respx does not intercept
    # those localhost requests with a stub response.
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{BASE_URL}/health").mock(
            return_value=httpx.Response(200, json={})
        )
        # Pass through any other URL (the real aiohttp server on
        # 127.0.0.1:{ephemeral} that the tests are exercising).
        router.route().pass_through()
        yield router


async def _post_webhook(
    adapter: ChatlyticsAdapter,
    payload: Dict[str, Any],
    *,
    signature: str | None = None,
    path: str = "/webhook",
) -> httpx.Response:
    body_bytes = _json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if signature is not None:
        headers["X-Chatlytics-Signature"] = signature
    url = f"http://{adapter.webhook_host}:{adapter.webhook_port}{path}"
    async with httpx.AsyncClient(timeout=5.0) as client:
        return await client.post(url, content=body_bytes, headers=headers)


def _install_recorder(adapter: ChatlyticsAdapter) -> List[Any]:
    """Replace ``adapter.handle_message`` with a list-recorder."""
    captured: List[Any] = []

    async def _recorder(event: Any) -> None:
        captured.append(event)

    adapter.handle_message = _recorder  # type: ignore[assignment]
    return captured


# --- AC-1: webhook text payload dispatches MessageType.TEXT -----------

async def test_webhook_text_payload_dispatches_text_message_event(
    mock_health: respx.MockRouter,
) -> None:
    from gateway.platforms.base import MessageType

    adapter = _make_adapter()
    captured = _install_recorder(adapter)
    await adapter.connect()
    try:
        resp = await _post_webhook(
            adapter,
            {
                "chatId": CHAT_ID,
                "text": "hello inbound",
                "senderId": "user-001",
                "messageId": "msg-1",
            },
        )
        assert resp.status_code == 200
        assert len(captured) == 1
        event = captured[0]
        assert event.message_type == MessageType.TEXT
        assert event.text == "hello inbound"
        assert event.source.chat_id == CHAT_ID
        assert event.source.user_id == "user-001"
    finally:
        await adapter.disconnect()


# --- AC-2: webhook image payload dispatches MessageType.PHOTO ---------

async def test_webhook_image_payload_dispatches_image_event(
    mock_health: respx.MockRouter,
) -> None:
    # Note: Hermes v0.14 calls images PHOTO -- the inbound normalizer
    # aliases the WhatsApp-flavored "image" mediaType to PHOTO.
    from gateway.platforms.base import MessageType

    adapter = _make_adapter()
    captured = _install_recorder(adapter)
    await adapter.connect()
    try:
        resp = await _post_webhook(
            adapter,
            {
                "chatId": CHAT_ID,
                "text": "look at this",
                "mediaType": "image",
                "mediaUrl": "https://cdn.chatlytics.ai/img/abc.jpg",
            },
        )
        assert resp.status_code == 200
        assert len(captured) == 1
        event = captured[0]
        assert event.message_type == MessageType.PHOTO
        assert event.media_urls == ["https://cdn.chatlytics.ai/img/abc.jpg"]
    finally:
        await adapter.disconnect()


# --- AC-3: webhook audio payload dispatches MessageType.AUDIO ---------

async def test_webhook_audio_payload_dispatches_audio_event(
    mock_health: respx.MockRouter,
) -> None:
    from gateway.platforms.base import MessageType

    adapter = _make_adapter()
    captured = _install_recorder(adapter)
    await adapter.connect()
    try:
        resp = await _post_webhook(
            adapter,
            {
                "chatId": CHAT_ID,
                "text": "",
                "mediaType": "audio",
                "mediaUrl": "https://cdn.chatlytics.ai/aud/xyz.ogg",
            },
        )
        assert resp.status_code == 200
        assert len(captured) == 1
        assert captured[0].message_type == MessageType.AUDIO
    finally:
        await adapter.disconnect()


# --- AC-4: GET /health returns 200 ------------------------------------

async def test_webhook_health_returns_200(
    mock_health: respx.MockRouter,
) -> None:
    adapter = _make_adapter()
    await adapter.connect()
    try:
        url = f"http://{adapter.webhook_host}:{adapter.webhook_port}/health"
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url)
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}
    finally:
        await adapter.disconnect()


# --- AC-5: connect() starts the aiohttp server (port is listening) ----

async def test_connect_starts_aiohttp_server(
    mock_health: respx.MockRouter,
) -> None:
    adapter = _make_adapter()
    await adapter.connect()
    try:
        # Open a raw TCP socket to prove the port is actually listening.
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2.0)
        try:
            sock.connect((adapter.webhook_host, adapter.webhook_port))
        finally:
            sock.close()
    finally:
        await adapter.disconnect()


# --- AC-6: disconnect() stops the aiohttp server (port not listening) -

async def test_disconnect_stops_aiohttp_server(
    mock_health: respx.MockRouter,
) -> None:
    adapter = _make_adapter()
    await adapter.connect()
    host, port = adapter.webhook_host, adapter.webhook_port
    await adapter.disconnect()

    # Give the kernel a moment to release the bind.
    await asyncio.sleep(0.05)

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(1.0)
    try:
        with pytest.raises((ConnectionRefusedError, OSError)):
            sock.connect((host, port))
    finally:
        sock.close()

    # Runner + site references should be cleared.
    assert adapter._runner is None
    assert adapter._site is None
    assert adapter._client is None


# --- 03-REVIEW MED-01 regression: connect() is idempotent -------------

async def test_connect_is_idempotent(
    mock_health: respx.MockRouter,
) -> None:
    """Two consecutive connect() calls without an intervening disconnect()
    must not leak the runner or rebind the port."""
    adapter = _make_adapter()
    await adapter.connect()
    first_runner = adapter._runner
    first_site = adapter._site
    try:
        # Second connect() should be a no-op for the aiohttp side.
        await adapter.connect()
        assert adapter._runner is first_runner, "second connect() rebuilt the runner"
        assert adapter._site is first_site, "second connect() rebuilt the site"

        # Port is still listening -- no rebind happened.
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2.0)
        try:
            sock.connect((adapter.webhook_host, adapter.webhook_port))
        finally:
            sock.close()
    finally:
        await adapter.disconnect()


# --- AC-7: HMAC verification rejects bad signature --------------------

async def test_hmac_verification_rejects_bad_signature(
    mock_health: respx.MockRouter,
) -> None:
    adapter = _make_adapter(with_secret=True)
    captured = _install_recorder(adapter)
    await adapter.connect()
    try:
        resp = await _post_webhook(
            adapter,
            {"chatId": CHAT_ID, "text": "should be rejected"},
            signature="deadbeef" * 8,  # 64 hex chars, but wrong digest
        )
        assert resp.status_code == 401
        assert captured == []  # no dispatch
    finally:
        await adapter.disconnect()


# --- AC-8: HMAC verification accepts good signature -------------------

async def test_hmac_verification_accepts_good_signature(
    mock_health: respx.MockRouter,
) -> None:
    adapter = _make_adapter(with_secret=True)
    captured = _install_recorder(adapter)
    await adapter.connect()
    try:
        payload = {"chatId": CHAT_ID, "text": "signed and sealed"}
        body_bytes = _json.dumps(payload).encode("utf-8")
        good_sig = hmac.new(
            WEBHOOK_SECRET.encode("utf-8"), body_bytes, hashlib.sha256
        ).hexdigest()

        url = f"http://{adapter.webhook_host}:{adapter.webhook_port}/webhook"
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                url,
                content=body_bytes,
                headers={
                    "Content-Type": "application/json",
                    "X-Chatlytics-Signature": good_sig,
                },
            )
        assert resp.status_code == 200
        assert len(captured) == 1
        assert captured[0].text == "signed and sealed"
    finally:
        await adapter.disconnect()
