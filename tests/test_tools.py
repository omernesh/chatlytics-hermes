"""HERMES-05 acceptance tests for the Chatlytics tool handlers.

Covers ROADMAP Phase 5 acceptance criteria:

- AC-3: ``chatlytics_send`` POSTs to ``/api/v1/send`` and returns
        ``{"success": True, "messageId": ...}``
- AC-4: ``chatlytics_react`` POSTs to ``/api/v1/actions`` with
        ``{"action": "react", "messageId", "emoji"}``
- AC-5: ``chatlytics_search`` POSTs to ``/api/v1/actions`` with
        ``{"action": "search", "params": {"query": ...}}`` and returns
        a ``results`` list
- AC-6: gateway 400 yields ``{"success": False, "error": ...}``
- AC-7: ``len(TOOLS) >= 13`` (duplicated from test_tool_schemas so
        the count guard fires in either file)
"""

from __future__ import annotations

import json as _json
from typing import Any, Dict

import httpx
import pytest
import respx

from chatlytics_hermes.client import ChatlyticsClient
from chatlytics_hermes.tools import (
    TOOLS,
    chatlytics_react,
    chatlytics_search,
    chatlytics_send,
)


BASE_URL = "https://gateway.test.chatlytics.ai"
API_KEY = "test-api-key-abc123"
CHAT_ID = "120363100000000000@g.us"
EXPECTED_AUTH = f"Bearer {API_KEY}"


@pytest.fixture
async def client() -> ChatlyticsClient:
    """Real ChatlyticsClient bound to a respx-mocked base URL."""
    c = ChatlyticsClient(base_url=BASE_URL, api_key=API_KEY)
    try:
        yield c
    finally:
        await c.aclose()


@pytest.fixture
def mock_router():
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        yield router


# --- AC-3: chatlytics_send calls /api/v1/send -------------------------


async def test_chatlytics_send_calls_send_endpoint(
    client: ChatlyticsClient, mock_router: respx.MockRouter
) -> None:
    route = mock_router.post("/api/v1/send").mock(
        return_value=httpx.Response(
            200, json={"success": True, "messageId": "m-001"}
        )
    )
    result = await chatlytics_send(client, chatId=CHAT_ID, text="hello")
    assert result["success"] is True
    assert result["messageId"] == "m-001"

    body = _json.loads(route.calls.last.request.content)
    assert body == {"chatId": CHAT_ID, "text": "hello"}
    assert (
        route.calls.last.request.headers.get("authorization") == EXPECTED_AUTH
    )


async def test_chatlytics_send_passes_optional_fields(
    client: ChatlyticsClient, mock_router: respx.MockRouter
) -> None:
    """Optional replyTo/accountId land in the body iff provided."""
    route = mock_router.post("/api/v1/send").mock(
        return_value=httpx.Response(
            200, json={"success": True, "messageId": "m-002"}
        )
    )
    await chatlytics_send(
        client,
        chatId=CHAT_ID,
        text="threaded reply",
        replyTo="msg-orig",
        accountId="acct-7",
    )
    body = _json.loads(route.calls.last.request.content)
    assert body["replyTo"] == "msg-orig"
    assert body["accountId"] == "acct-7"


# --- AC-4: chatlytics_react calls /api/v1/actions with action=react ---


async def test_chatlytics_react_calls_react_action(
    client: ChatlyticsClient, mock_router: respx.MockRouter
) -> None:
    route = mock_router.post("/api/v1/actions").mock(
        return_value=httpx.Response(200, json={"success": True})
    )
    result = await chatlytics_react(client, messageId="m-001", emoji="\U0001f44d")
    assert result["success"] is True

    body = _json.loads(route.calls.last.request.content)
    assert body["action"] == "react"
    assert body["messageId"] == "m-001"
    assert body["emoji"] == "\U0001f44d"


# --- AC-5: chatlytics_search returns results list ---------------------


async def test_chatlytics_search_returns_results_list(
    client: ChatlyticsClient, mock_router: respx.MockRouter
) -> None:
    route = mock_router.post("/api/v1/actions").mock(
        return_value=httpx.Response(
            200,
            json={
                "success": True,
                "results": [
                    {"jid": "9725001@c.us", "name": "Alice"},
                    {"jid": "9725002@c.us", "name": "Alicia"},
                ],
            },
        )
    )
    result = await chatlytics_search(client, query="ali")
    assert result["success"] is True
    assert isinstance(result["results"], list)
    assert result["results"][0]["name"] == "Alice"

    body = _json.loads(route.calls.last.request.content)
    assert body == {"action": "search", "params": {"query": "ali"}}


# --- AC-6: tool returns {success: False, error} on 4xx ----------------


async def test_tool_returns_success_false_on_400(
    client: ChatlyticsClient, mock_router: respx.MockRouter
) -> None:
    mock_router.post("/api/v1/send").mock(
        return_value=httpx.Response(400, json={"error": "invalid chatId"})
    )
    # v3.0 schema tightening (HERMES-14): in production, "not-a-jid"
    # would be rejected at the Hermes framework's schema-validation
    # layer BEFORE reaching this handler. This test calls the handler
    # directly to exercise its 4xx error-shape contract, simulating a
    # gateway-side rejection (which can still happen even with valid
    # JIDs -- e.g. unknown chat).
    result = await chatlytics_send(client, chatId="not-a-jid", text="hi")
    assert result["success"] is False
    assert result["error"] == "invalid chatId"
    assert result["status_code"] == 400
    # raw_response is preserved for caller-side debugging.
    assert result["raw_response"] == {"error": "invalid chatId"}


# --- AC-7: tool count guard (duplicated in test_tool_schemas) ---------


def test_tool_count_matches_claude_code_plugin_baseline() -> None:
    """``len(TOOLS) >= 13`` and exactly ``21`` for HERMES-05."""
    n = len(TOOLS)
    assert n >= 13, f"Expected at least 13 tools; got {n}"
    assert n == 21, f"HERMES-05 locks the count at 21; got {n}"
