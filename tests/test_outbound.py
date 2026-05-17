"""HERMES-02 acceptance tests for the chatlytics-hermes outbound surface.

Eight ``respx``-mocked tests cover ROADMAP Phase 2 acceptance criteria
1-8.  Tests instantiate ``ChatlyticsAdapter`` directly (the
``_HERMES_AVAILABLE`` shim from HERMES-01 is exercised here for the
first time -- the test runs require hermes-agent installed, which the
dockerized verification harness provides).
"""

from __future__ import annotations

import json as _json
from typing import Any, Dict

import httpx
import pytest
import respx

from chatlytics_hermes.adapter import ChatlyticsAdapter, ChatlyticsConnectError

BASE_URL = "https://gateway.test.chatlytics.ai"
API_KEY = "test-api-key-abc123"
CHAT_ID = "chat-001"

# Bearer header assertion helper -- AC-8 says "every request carries
# Authorization: Bearer {api_key}".  Asserted in every test.
EXPECTED_AUTH = f"Bearer {API_KEY}"


class _FakePlatformConfig:
    """Minimal PlatformConfig stand-in for tests.

    We do not import the real PlatformConfig because the adapter only
    touches ``getattr(config, "extra", {})``.  A namespace object is
    sufficient and keeps tests insulated from upstream PlatformConfig
    field churn.
    """

    def __init__(self, extra: Dict[str, Any]) -> None:
        self.extra = extra
        # Fields the base BasePlatformAdapter.__init__ accesses.
        self.enabled = True
        self.token = None
        self.api_key = extra.get("api_key")
        self.home_channel = extra.get("home_channel")


@pytest.fixture
def adapter() -> ChatlyticsAdapter:
    return ChatlyticsAdapter(
        _FakePlatformConfig(
            extra={
                "base_url": BASE_URL,
                "api_key": API_KEY,
                "account_id": "acct-1",
            }
        )
    )


@pytest.fixture
def mock_router():
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        yield router


# --- AC-1: connect succeeds on 200 health -----------------------------

async def test_connect_succeeds_on_200_health(
    adapter: ChatlyticsAdapter, mock_router: respx.MockRouter
) -> None:
    route = mock_router.get("/health").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    result = await adapter.connect()
    assert result is True
    assert adapter.is_connected is True
    assert route.called
    # AC-8: every request carries Authorization: Bearer {api_key}.
    assert route.calls.last.request.headers.get("authorization") == EXPECTED_AUTH
    await adapter.disconnect()


# --- AC-2: connect raises on non-200 health ---------------------------

async def test_connect_raises_on_non_200_health(
    adapter: ChatlyticsAdapter, mock_router: respx.MockRouter
) -> None:
    route = mock_router.get("/health").mock(
        return_value=httpx.Response(503, text="upstream busy")
    )
    with pytest.raises(ChatlyticsConnectError):
        await adapter.connect()
    # AC-8: even the failing health request must carry Bearer.
    assert route.calls.last.request.headers.get("authorization") == EXPECTED_AUTH
    # Adapter should clear its client so a retry from a clean slate is possible.
    assert adapter._client is None


# --- AC-3: send returns success=True on 200 + {success: true} ---------

async def test_send_returns_ok_true_on_200(
    adapter: ChatlyticsAdapter, mock_router: respx.MockRouter
) -> None:
    mock_router.get("/health").mock(return_value=httpx.Response(200, json={}))
    send_route = mock_router.post("/api/v1/send").mock(
        return_value=httpx.Response(
            200, json={"success": True, "messageId": "msg-42"}
        )
    )
    await adapter.connect()
    result = await adapter.send(CHAT_ID, "hello world")
    assert result.success is True
    assert result.message_id == "msg-42"
    # AC-8.
    assert send_route.calls.last.request.headers.get("authorization") == EXPECTED_AUTH
    # Request body shape.
    body = _json.loads(send_route.calls.last.request.content)
    assert body["chatId"] == CHAT_ID
    assert body["text"] == "hello world"
    assert body["accountId"] == "acct-1"
    await adapter.disconnect()


# --- AC-4: send returns success=False on 400 --------------------------

async def test_send_returns_ok_false_on_400(
    adapter: ChatlyticsAdapter, mock_router: respx.MockRouter
) -> None:
    mock_router.get("/health").mock(return_value=httpx.Response(200, json={}))
    send_route = mock_router.post("/api/v1/send").mock(
        return_value=httpx.Response(
            400, json={"success": False, "error": "invalid chatId"}
        )
    )
    await adapter.connect()
    result = await adapter.send(CHAT_ID, "hello")
    assert result.success is False
    assert "invalid chatId" in (result.error or "")
    # Raw response carries diagnostic info.
    assert result.raw_response is not None
    # AC-8.
    assert send_route.calls.last.request.headers.get("authorization") == EXPECTED_AUTH
    await adapter.disconnect()


# --- AC-5: send_typing posts to /api/v1/typing ------------------------

async def test_send_typing_calls_typing_endpoint(
    adapter: ChatlyticsAdapter, mock_router: respx.MockRouter
) -> None:
    mock_router.get("/health").mock(return_value=httpx.Response(200, json={}))
    typing_route = mock_router.post("/api/v1/typing").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    await adapter.connect()
    await adapter.send_typing(CHAT_ID, duration=2.0)
    assert typing_route.called
    # AC-8.
    assert typing_route.calls.last.request.headers.get("authorization") == EXPECTED_AUTH
    # Body shape.
    body = _json.loads(typing_route.calls.last.request.content)
    assert body == {"chatId": CHAT_ID, "duration": 2.0}
    await adapter.disconnect()


# --- AC-6: get_chat_info returns dict ---------------------------------

async def test_get_chat_info_returns_dict(
    adapter: ChatlyticsAdapter, mock_router: respx.MockRouter
) -> None:
    mock_router.get("/health").mock(return_value=httpx.Response(200, json={}))
    chat_route = mock_router.get("/api/v1/chat").mock(
        return_value=httpx.Response(
            200, json={"name": "Alice", "phone": "+15551234", "isGroup": False}
        )
    )
    await adapter.connect()
    info = await adapter.get_chat_info(CHAT_ID)
    assert isinstance(info, dict)
    assert info["name"] == "Alice"
    assert info["phone"] == "+15551234"
    assert info["isGroup"] is False
    # AC-8.
    assert chat_route.calls.last.request.headers.get("authorization") == EXPECTED_AUTH
    # Query param.
    assert chat_route.calls.last.request.url.params["chatId"] == CHAT_ID
    await adapter.disconnect()


# --- AC-7: disconnect closes the httpx client -------------------------

async def test_disconnect_closes_client(
    adapter: ChatlyticsAdapter, mock_router: respx.MockRouter
) -> None:
    mock_router.get("/health").mock(return_value=httpx.Response(200, json={}))
    await adapter.connect()
    # Capture a strong reference so we can prove it was closed.
    client_ref = adapter._client
    assert client_ref is not None
    assert client_ref.is_closed is False
    await adapter.disconnect()
    assert client_ref.is_closed is True
    assert adapter._client is None
    # Idempotency -- a second disconnect must not raise.
    await adapter.disconnect()


# --- AC-8: every request carries Authorization: Bearer ----------------

async def test_all_requests_carry_bearer_auth(
    adapter: ChatlyticsAdapter, mock_router: respx.MockRouter
) -> None:
    """Cross-cutting assertion that every endpoint enforces Bearer auth.

    AC-8 is also asserted inline in each of the per-endpoint tests
    above; this test exercises all four endpoints in sequence and
    asserts each request's Authorization header explicitly.
    """
    mock_router.get("/health").mock(return_value=httpx.Response(200, json={}))
    mock_router.post("/api/v1/send").mock(
        return_value=httpx.Response(200, json={"success": True, "messageId": "m"})
    )
    mock_router.post("/api/v1/typing").mock(
        return_value=httpx.Response(200, json={})
    )
    mock_router.get("/api/v1/chat").mock(
        return_value=httpx.Response(200, json={"name": "X"})
    )

    await adapter.connect()
    await adapter.send(CHAT_ID, "ping")
    await adapter.send_typing(CHAT_ID, duration=1.0)
    await adapter.get_chat_info(CHAT_ID)

    request_count = 0
    for route in mock_router.routes:
        for call in route.calls:
            request_count += 1
            assert (
                call.request.headers.get("authorization") == EXPECTED_AUTH
            ), f"{call.request.method} {call.request.url} missing Bearer"
    # Sanity: connect + send + send_typing + get_chat_info = 4 requests.
    assert request_count == 4

    await adapter.disconnect()
