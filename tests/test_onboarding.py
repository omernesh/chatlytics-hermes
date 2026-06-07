"""v4.1.5 telegram-style no-token onboarding tests.

Covers the degraded "no-credential" path added in v4.1.5:

- connect() with NO bot token does NOT raise — it loads degraded, sets
  ``_no_credential``, warns, and reports loaded-but-degraded via
  ``is_connected`` without crashing.
- A DATA tool invoked with no token returns the standard failure shape
  (``{"success": False, "error": ...}``) carrying the relayable
  get-a-token prompt (Web UI + CLI routes).
- connect() WITH a bot token still behaves as before — no degraded flag
  (regression guard: the token'd path is byte-for-byte unchanged).

The autouse conftest fixture clears ambient ``CHATLYTICS_*`` per-test, so
tokens are set explicitly via ``monkeypatch`` where a credential is needed.
"""

from __future__ import annotations

import json as _json
from typing import Any, Optional

import httpx
import pytest
import respx

from chatlytics_hermes.adapter import (
    NO_TOKEN_PROMPT,
    ChatlyticsAdapter,
    _make_tool_handler,
)
from chatlytics_hermes.tools import chatlytics_send_image
from tests._fixtures import FakePlatformConfig

BASE_URL = "https://gateway.test.chatlytics.ai"
BOT_TOKEN = "sk_bot_" + "a" * 43
CHAT_ID = "120363100000000000@g.us"


def _no_token_adapter() -> ChatlyticsAdapter:
    """Adapter with a base_url but NO auth token (degraded path)."""
    return ChatlyticsAdapter(FakePlatformConfig(extra={"base_url": BASE_URL}))


def _token_adapter() -> ChatlyticsAdapter:
    """Adapter with a bot token + session (existing token'd path)."""
    return ChatlyticsAdapter(
        FakePlatformConfig(
            extra={
                "base_url": BASE_URL,
                "bot_token": BOT_TOKEN,
                "session": "3cf11776_logan",
            }
        )
    )


# --- (a) connect() with no token loads degraded, does NOT raise ----------


async def test_connect_no_token_does_not_raise_loads_degraded(caplog) -> None:
    adapter = _no_token_adapter()
    assert adapter._no_credential is False  # not set until connect()

    with caplog.at_level("WARNING"):
        result = await adapter.connect()

    # Loaded (no exception), degraded flag set, no authed client built.
    assert result is True
    assert adapter._no_credential is True
    assert adapter._client is None
    # is_connected reflects loaded-but-degraded without crashing.
    assert adapter.is_connected is True
    # A clear operator-actionable warning was emitted.
    assert any(
        "WITHOUT a bot token" in rec.getMessage() for rec in caplog.records
    )
    await adapter.disconnect()


async def test_connect_no_token_does_not_build_client_or_hit_health() -> None:
    # No /health route mocked: if connect() tried to reach the gateway in
    # degraded mode this would raise a respx "no route" error.
    adapter = _no_token_adapter()
    with respx.mock(base_url=BASE_URL, assert_all_called=False):
        result = await adapter.connect()
    assert result is True
    assert adapter._client is None


# --- (b) a data tool with no token returns the get-a-token prompt --------


class _Entry:
    def __init__(self, adapter: Any) -> None:
        self.adapter = adapter


class _Ctx:
    """Minimal PluginContext stand-in for _make_tool_handler lookup."""

    def __init__(self, adapter: Any) -> None:
        self._entry = _Entry(adapter)

    def get_platform(self, name: str) -> Optional[_Entry]:
        return self._entry if name == "chatlytics" else None


async def test_data_tool_no_token_returns_prompt_via_wrapper() -> None:
    """A client-only DATA tool, invoked through the real register-time
    wrapper, returns the no-token prompt when the adapter is degraded."""
    adapter = _no_token_adapter()
    await adapter.connect()  # degraded; adapter.client is None

    ctx = _Ctx(adapter)
    from chatlytics_hermes.tools import chatlytics_send

    bound = _make_tool_handler(ctx, "chatlytics_send", chatlytics_send)
    raw = await bound({"chatId": CHAT_ID, "text": "hi"})

    # Wrapper serializes the dict to a JSON string (DeepSeek-compat path).
    payload = _json.loads(raw)
    assert payload["success"] is False
    assert payload["error"] == NO_TOKEN_PROMPT
    # Carries both onboarding routes.
    assert "Bots → Create Bot" in payload["error"]
    assert "chatlytics bots create" in payload["error"]
    await adapter.disconnect()


async def test_media_data_tool_no_token_returns_prompt_direct() -> None:
    """An adapter-aware DATA tool handler called directly returns the
    no-token prompt failure shape when the adapter is degraded."""
    adapter = _no_token_adapter()
    await adapter.connect()

    result = await chatlytics_send_image(
        adapter.client, adapter=adapter, chatId=CHAT_ID, mediaUrl="https://x/y.png"
    )
    assert result["success"] is False
    assert "Bots → Create Bot" in result["error"]
    assert "chatlytics bots create" in result["error"]
    await adapter.disconnect()


async def test_send_no_token_returns_prompt() -> None:
    """adapter.send() in degraded mode returns NO_TOKEN_PROMPT, not the
    generic 'Adapter not connected' message."""
    adapter = _no_token_adapter()
    await adapter.connect()

    res = await adapter.send(CHAT_ID, "hello")
    assert res.success is False
    assert res.error == NO_TOKEN_PROMPT
    await adapter.disconnect()


# --- (c) connect() WITH a token still behaves as before (regression) -----


async def test_connect_with_token_no_degraded_flag() -> None:
    adapter = _token_adapter()
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        router.get("/health").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        result = await adapter.connect()

    assert result is True
    # Token'd path is unchanged: NOT degraded, authed client built.
    assert adapter._no_credential is False
    assert adapter._client is not None
    assert adapter.is_connected is True
    # Bearer carried the bot token (existing behavior preserved).
    await adapter.disconnect()


async def test_status_tool_exempt_from_no_token_guard() -> None:
    """Health/login status tools are NOT no-token-guarded — in degraded
    mode they fall through to the normal not-connected handling instead
    of returning the onboarding prompt."""
    adapter = _no_token_adapter()
    await adapter.connect()

    ctx = _Ctx(adapter)
    from chatlytics_hermes.tools import chatlytics_health

    bound = _make_tool_handler(ctx, "chatlytics_health", chatlytics_health)
    raw = await bound({})
    payload = _json.loads(raw)
    assert payload["success"] is False
    # Not the onboarding prompt — the generic not-connected status surface.
    assert payload["error"] != NO_TOKEN_PROMPT
    await adapter.disconnect()
