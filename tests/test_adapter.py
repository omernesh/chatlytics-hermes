"""Tests for ChatlyticsAdapter (Hermes adapter)."""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure the src package is importable without installing.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import httpx
import pytest
import respx

from chatlytics_adapter.adapter import ChatlyticsAdapter

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BASE_URL = "http://mock.local"
API_KEY = "test-key"


def _make_adapter(**overrides: object) -> ChatlyticsAdapter:
    defaults = {
        "base_url": BASE_URL,
        "api_key": API_KEY,
        "account_id": "test-acct",
        "webhook_port": 19090,
    }
    defaults.update(overrides)
    return ChatlyticsAdapter(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestConstructor:
    def test_defaults(self) -> None:
        a = ChatlyticsAdapter(base_url="http://x.local", api_key="k")
        assert a.webhook_port == 9090
        assert a.account_id is None

    def test_trailing_slash_stripped(self) -> None:
        a = ChatlyticsAdapter(base_url="http://x.local/", api_key="k")
        assert a.base_url == "http://x.local"


class TestConnect:
    @pytest.mark.asyncio
    @respx.mock
    async def test_connect_success(self) -> None:
        respx.get(f"{BASE_URL}/health").mock(
            return_value=httpx.Response(200, json={"status": "ok"})
        )
        a = _make_adapter()
        await a.connect()
        assert a._client is not None
        await a.disconnect()

    @pytest.mark.asyncio
    @respx.mock
    async def test_connect_failure_raises(self) -> None:
        respx.get(f"{BASE_URL}/health").mock(
            return_value=httpx.Response(503, text="unavailable")
        )
        a = _make_adapter()
        with pytest.raises(httpx.HTTPStatusError):
            await a.connect()


class TestSend:
    @pytest.mark.asyncio
    @respx.mock
    async def test_send_payload(self) -> None:
        respx.get(f"{BASE_URL}/health").mock(
            return_value=httpx.Response(200, json={"status": "ok"})
        )
        route = respx.post(f"{BASE_URL}/api/v1/send").mock(
            return_value=httpx.Response(200, json={"messageId": "m1"})
        )
        a = _make_adapter()
        await a.connect()

        result = await a.send("972544329000@c.us", "hi")

        assert result == {"messageId": "m1"}
        import json

        body = json.loads(route.calls.last.request.content)
        assert body["chatId"] == "972544329000@c.us"
        assert body["text"] == "hi"
        assert body["accountId"] == "test-acct"
        assert route.calls.last.request.headers["authorization"] == f"Bearer {API_KEY}"

        await a.disconnect()


class TestSendTyping:
    @pytest.mark.asyncio
    @respx.mock
    async def test_send_typing_payload(self) -> None:
        respx.get(f"{BASE_URL}/health").mock(
            return_value=httpx.Response(200, json={"status": "ok"})
        )
        route = respx.post(f"{BASE_URL}/api/v1/typing").mock(
            return_value=httpx.Response(200, json={})
        )
        a = _make_adapter()
        await a.connect()

        await a.send_typing("972544329000@c.us", duration=5.0)

        import json

        body = json.loads(route.calls.last.request.content)
        assert body["chatId"] == "972544329000@c.us"
        assert body["duration"] == 5.0
        assert body["accountId"] == "test-acct"

        await a.disconnect()


class TestGetChatInfo:
    @pytest.mark.asyncio
    @respx.mock
    async def test_get_chat_info_params(self) -> None:
        respx.get(f"{BASE_URL}/health").mock(
            return_value=httpx.Response(200, json={"status": "ok"})
        )
        route = respx.get(f"{BASE_URL}/api/v1/chat").mock(
            return_value=httpx.Response(
                200, json={"chatId": "972544329000@c.us", "name": "Test"}
            )
        )
        a = _make_adapter()
        await a.connect()

        info = await a.get_chat_info("972544329000@c.us")

        assert info["name"] == "Test"
        req_url = str(route.calls.last.request.url)
        assert "chatId=972544329000%40c.us" in req_url

        await a.disconnect()


class TestEnsureConnected:
    @pytest.mark.asyncio
    async def test_raises_when_not_connected(self) -> None:
        a = _make_adapter()
        with pytest.raises(RuntimeError, match="Not connected"):
            await a.send("x@c.us", "hi")
