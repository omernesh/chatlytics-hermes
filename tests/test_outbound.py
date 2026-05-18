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
from tests._fixtures import FakePlatformConfig

BASE_URL = "https://gateway.test.chatlytics.ai"
API_KEY = "test-api-key-abc123"
CHAT_ID = "chat-001"

# Bearer header assertion helper -- AC-8 says "every request carries
# Authorization: Bearer {api_key}".  Asserted in every test.
EXPECTED_AUTH = f"Bearer {API_KEY}"


@pytest.fixture
def adapter() -> ChatlyticsAdapter:
    return ChatlyticsAdapter(
        FakePlatformConfig(
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


# --- AC-6: get_chat_info returns dict (HERMES-13 contract preserved) ---
#
# v3.0 HERMES-13 (BREAKING — see CHANGELOG entry "BREAKING —
# get_chat_info return shape"): the chat-found branch still returns
# a dict, but the error / empty branches changed (chat-not-found is
# now None; errors raise ChatlyticsLookupError). This test exercises
# only the success branch; the new branches are covered in the
# test_get_chat_info_* / test_tool_wrapper_* tests below.

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


# --- HERMES-13: get_chat_info three-way contract -----------------------
#
# v3.0 BREAKING — see CHANGELOG entry "BREAKING — get_chat_info return
# shape". The adapter now returns ``dict | None`` and raises
# ``ChatlyticsLookupError`` on error paths with a machine-readable
# ``.code``. The tool-layer wrapper translates this into
# ``{"success": bool, ...}`` with error responses additionally
# including ``_error: "<code>"``.
#
# Branches covered:
#   1. 200 + dict payload                       -> dict (AC-6 above)
#   2. 200 + falsy/empty payload                -> None (legitimate empty)
#   3. httpx.RequestError                       -> code='transport_error'
#   4. 401                                      -> code='auth_error'
#   5. 403                                      -> code='auth_error'
#   6. 500                                      -> code='server_error'
#   7. 404                                      -> code='validation_error'
#      (404 from gateway is malformed/unknown JID, NOT empty)
#   8. wrapper: chat found                      -> {success: True, chat: {...}}
#   9. wrapper: legitimate empty                -> {success: True, chat: None}
#  10. wrapper: 5xx                             -> {success: False, _error: 'server_error'}
#  11. wrapper: 404                             -> {success: False, _error: 'validation_error'}


async def test_get_chat_info_returns_none_on_legitimate_empty(
    adapter: ChatlyticsAdapter, mock_router: respx.MockRouter
) -> None:
    """200 + falsy payload (None, {}, []) -> adapter returns None (chat-not-found)."""
    mock_router.get("/health").mock(return_value=httpx.Response(200, json={}))
    # NB: ``httpx.Response(200, json=None)`` sends an EMPTY body (httpx
    # treats ``json=None`` as "no JSON kwarg"), which would surface as
    # ``unknown_error`` via the malformed-JSON path. Use ``content=b"null"``
    # with an explicit JSON content-type to deliver a literal JSON ``null``
    # — the legitimate-empty contract is "2xx + falsy JSON body".
    mock_router.get("/api/v1/chat").mock(
        return_value=httpx.Response(
            200, content=b"null", headers={"content-type": "application/json"}
        )
    )
    await adapter.connect()
    result = await adapter.get_chat_info(CHAT_ID)
    assert result is None
    await adapter.disconnect()


async def test_get_chat_info_raises_transport_error(
    adapter: ChatlyticsAdapter, mock_router: respx.MockRouter
) -> None:
    """httpx.RequestError on the chat call -> ChatlyticsLookupError('transport_error')."""
    from chatlytics_hermes.adapter import ChatlyticsLookupError

    mock_router.get("/health").mock(return_value=httpx.Response(200, json={}))
    mock_router.get("/api/v1/chat").mock(
        side_effect=httpx.ConnectError("connection refused")
    )
    await adapter.connect()
    with pytest.raises(ChatlyticsLookupError) as excinfo:
        await adapter.get_chat_info(CHAT_ID)
    assert excinfo.value.code == "transport_error"
    await adapter.disconnect()


async def test_get_chat_info_raises_auth_error_on_401(
    adapter: ChatlyticsAdapter, mock_router: respx.MockRouter
) -> None:
    from chatlytics_hermes.adapter import ChatlyticsLookupError

    mock_router.get("/health").mock(return_value=httpx.Response(200, json={}))
    mock_router.get("/api/v1/chat").mock(
        return_value=httpx.Response(401, json={"error": "unauthorized"})
    )
    await adapter.connect()
    with pytest.raises(ChatlyticsLookupError) as excinfo:
        await adapter.get_chat_info(CHAT_ID)
    assert excinfo.value.code == "auth_error"
    await adapter.disconnect()


async def test_get_chat_info_raises_auth_error_on_403(
    adapter: ChatlyticsAdapter, mock_router: respx.MockRouter
) -> None:
    from chatlytics_hermes.adapter import ChatlyticsLookupError

    mock_router.get("/health").mock(return_value=httpx.Response(200, json={}))
    mock_router.get("/api/v1/chat").mock(
        return_value=httpx.Response(403, json={"error": "forbidden"})
    )
    await adapter.connect()
    with pytest.raises(ChatlyticsLookupError) as excinfo:
        await adapter.get_chat_info(CHAT_ID)
    assert excinfo.value.code == "auth_error"
    await adapter.disconnect()


async def test_get_chat_info_raises_server_error_on_500(
    adapter: ChatlyticsAdapter, mock_router: respx.MockRouter
) -> None:
    from chatlytics_hermes.adapter import ChatlyticsLookupError

    mock_router.get("/health").mock(return_value=httpx.Response(200, json={}))
    mock_router.get("/api/v1/chat").mock(
        return_value=httpx.Response(500, json={"error": "internal"})
    )
    await adapter.connect()
    with pytest.raises(ChatlyticsLookupError) as excinfo:
        await adapter.get_chat_info(CHAT_ID)
    assert excinfo.value.code == "server_error"
    await adapter.disconnect()


async def test_get_chat_info_raises_validation_error_on_404(
    adapter: ChatlyticsAdapter, mock_router: respx.MockRouter
) -> None:
    """404 from gateway -> validation_error (unknown JID), NOT legitimate empty."""
    from chatlytics_hermes.adapter import ChatlyticsLookupError

    mock_router.get("/health").mock(return_value=httpx.Response(200, json={}))
    mock_router.get("/api/v1/chat").mock(
        return_value=httpx.Response(404, json={"error": "not found"})
    )
    await adapter.connect()
    with pytest.raises(ChatlyticsLookupError) as excinfo:
        await adapter.get_chat_info(CHAT_ID)
    assert excinfo.value.code == "validation_error"
    await adapter.disconnect()


async def test_tool_wrapper_returns_success_true_with_chat_on_found(
    adapter: ChatlyticsAdapter, mock_router: respx.MockRouter
) -> None:
    """Tool-layer wrapper: chat-found -> {success: True, chat: {...}}."""
    from chatlytics_hermes.tools import chatlytics_get_chat_info

    mock_router.get("/health").mock(return_value=httpx.Response(200, json={}))
    mock_router.get("/api/v1/chat").mock(
        return_value=httpx.Response(
            200, json={"name": "Alice", "isGroup": False}
        )
    )
    await adapter.connect()
    result = await chatlytics_get_chat_info(
        adapter.client, adapter=adapter, chatId=CHAT_ID
    )
    assert result == {
        "success": True,
        "chat": {"name": "Alice", "isGroup": False},
    }
    await adapter.disconnect()


async def test_tool_wrapper_returns_success_true_with_null_on_empty(
    adapter: ChatlyticsAdapter, mock_router: respx.MockRouter
) -> None:
    """Tool-layer wrapper: legitimate empty -> {success: True, chat: None}.

    HERMES-13 NEW ASSERTION #1 (per phase brief): explicit null branch.
    """
    from chatlytics_hermes.tools import chatlytics_get_chat_info

    mock_router.get("/health").mock(return_value=httpx.Response(200, json={}))
    # NB: ``httpx.Response(200, json=None)`` sends an EMPTY body (httpx
    # treats ``json=None`` as "no JSON kwarg"), which would surface as
    # ``unknown_error`` via the malformed-JSON path. Use ``content=b"null"``
    # with an explicit JSON content-type to deliver a literal JSON ``null``
    # — the legitimate-empty contract is "2xx + falsy JSON body".
    mock_router.get("/api/v1/chat").mock(
        return_value=httpx.Response(
            200, content=b"null", headers={"content-type": "application/json"}
        )
    )
    await adapter.connect()
    result = await chatlytics_get_chat_info(
        adapter.client, adapter=adapter, chatId=CHAT_ID
    )
    assert result == {"success": True, "chat": None}
    await adapter.disconnect()


async def test_tool_wrapper_returns_error_with_underscore_error_on_500(
    adapter: ChatlyticsAdapter, mock_router: respx.MockRouter
) -> None:
    """Tool-layer wrapper: 5xx -> {success: False, _error: 'server_error'}.

    HERMES-13 NEW ASSERTION #2 (per phase brief): explicit _error sentinel
    on the error branch.
    """
    from chatlytics_hermes.tools import chatlytics_get_chat_info

    mock_router.get("/health").mock(return_value=httpx.Response(200, json={}))
    mock_router.get("/api/v1/chat").mock(
        return_value=httpx.Response(500, json={"error": "boom"})
    )
    await adapter.connect()
    result = await chatlytics_get_chat_info(
        adapter.client, adapter=adapter, chatId=CHAT_ID
    )
    assert result["success"] is False
    assert result["_error"] == "server_error"
    assert "error" in result and isinstance(result["error"], str)
    await adapter.disconnect()


async def test_tool_wrapper_returns_validation_error_on_404(
    adapter: ChatlyticsAdapter, mock_router: respx.MockRouter
) -> None:
    """Tool-layer wrapper: 404 -> {success: False, _error: 'validation_error'}.

    Explicit coverage of the 404-disambiguation rule (404 from gateway is
    a malformed/unknown JID, NOT a chat-not-found legitimate empty).
    """
    from chatlytics_hermes.tools import chatlytics_get_chat_info

    mock_router.get("/health").mock(return_value=httpx.Response(200, json={}))
    mock_router.get("/api/v1/chat").mock(
        return_value=httpx.Response(404, json={"error": "not found"})
    )
    await adapter.connect()
    result = await chatlytics_get_chat_info(
        adapter.client, adapter=adapter, chatId=CHAT_ID
    )
    assert result["success"] is False
    assert result["_error"] == "validation_error"
    await adapter.disconnect()
