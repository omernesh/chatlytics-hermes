"""v4.5.3 — host-injected kwarg filtering tests.

Regression: hermes ``tools/registry.py:403`` dispatches
``entry.handler(args, **kwargs)`` with HOST-injected bookkeeping kwargs
(observed: ``task_id``). ``_bound`` forwarded kwargs raw into bare handlers
like ``chatlytics_health(client)`` →
``TypeError: chatlytics_health() got an unexpected keyword argument
'task_id'`` on EVERY tool call. Masked before v4.5.2 by the
adapter-not-connected failure; unmasked live on all 5 gateways
(botdaddy errors.log 2026-06-11 19:25+).

Fix under test: ``_make_tool_handler`` inspects the handler signature ONCE
at bind time and ``_bound`` drops kwargs the handler cannot accept — unless
the handler declares ``**kwargs`` (explicit opt-in to everything).
"""

from __future__ import annotations

import json
from typing import Any, Dict

import httpx
import pytest
import respx

from chatlytics_hermes.adapter import (
    ChatlyticsAdapter,
    _make_tool_handler,
    _register_live_adapter,
)
from chatlytics_hermes.tools import chatlytics_health
from tests._fixtures import FakePlatformConfig

pytestmark = pytest.mark.asyncio

BASE_URL = "https://gateway.test.chatlytics.ai"
BOT_TOKEN = "sk_bot_" + "A" * 43


class _BareCtx:
    """hermes_cli PluginContext stand-in: no get_platform, no platforms."""


def _live_adapter_with_client(client: Any) -> ChatlyticsAdapter:
    adapter = ChatlyticsAdapter(
        FakePlatformConfig(
            extra={"base_url": BASE_URL, "bot_token": BOT_TOKEN, "inbound_mode": "longpoll"}
        )
    )
    adapter._client = client  # type: ignore[assignment]
    _register_live_adapter(adapter)
    return adapter


async def test_bare_handler_does_not_receive_task_id() -> None:
    """A handler WITHOUT **kwargs must not see host-injected keys — and must
    not raise TypeError (the production regression)."""
    seen: Dict[str, Any] = {"called": False}

    async def _probe(client):  # bare: client only, like chatlytics_health
        seen["called"] = True
        return {"success": True}

    _live_adapter_with_client(object())
    bound = _make_tool_handler(_BareCtx(), "probe_tool", _probe)

    # Exact registry.py:403 dispatch shape: handler(args, **host_kwargs).
    raw = await bound(None, task_id="20260611_192325_d3a84678")
    payload = json.loads(raw)
    assert payload["success"] is True
    assert seen["called"] is True


async def test_chatlytics_health_with_task_id_executes_end_to_end() -> None:
    """The literal production repro: real chatlytics_health handler + injected
    task_id → real /health answer, no TypeError."""
    adapter = ChatlyticsAdapter(
        FakePlatformConfig(
            extra={"base_url": BASE_URL, "bot_token": BOT_TOKEN, "inbound_mode": "longpoll"}
        )
    )
    from chatlytics_hermes.client import ChatlyticsClient

    adapter._client = ChatlyticsClient(base_url=BASE_URL, api_key=BOT_TOKEN)
    _register_live_adapter(adapter)
    bound = _make_tool_handler(_BareCtx(), "chatlytics_health", chatlytics_health)

    with respx.mock(base_url=BASE_URL) as router:
        router.get("/health").mock(
            return_value=httpx.Response(
                200, json={"webhook_registered": True, "sessions": []}
            )
        )
        raw = await bound(None, task_id="t-123")

    payload = json.loads(raw)
    assert payload["success"] is True
    assert payload["webhook_registered"] is True
    await adapter._client.aclose()


async def test_handler_with_var_kwargs_still_receives_everything() -> None:
    """A handler that declares **kwargs opted in: host keys pass through."""
    seen: Dict[str, Any] = {}

    async def _probe_kw(client, **kwargs):
        seen.update(kwargs)
        return {"success": True}

    _live_adapter_with_client(object())
    bound = _make_tool_handler(_BareCtx(), "probe_kw_tool", _probe_kw)

    raw = await bound({"chatId": "123@c.us"}, task_id="t-456")
    assert json.loads(raw)["success"] is True
    assert seen == {"chatId": "123@c.us", "task_id": "t-456"}


async def test_json_args_path_unchanged_for_named_params() -> None:
    """Legit JSON tool args still reach a keyword-only handler; only the
    host-injected key is dropped."""
    seen: Dict[str, Any] = {}

    async def _probe_named(client, *, chatId: str, text: str = ""):
        seen["chatId"] = chatId
        seen["text"] = text
        return {"success": True}

    _live_adapter_with_client(object())
    bound = _make_tool_handler(_BareCtx(), "probe_named_tool", _probe_named)

    raw = await bound({"chatId": "123@c.us", "text": "hello"}, task_id="t-789")
    assert json.loads(raw)["success"] is True
    assert seen == {"chatId": "123@c.us", "text": "hello"}


async def test_adapter_taking_handler_keeps_adapter_injection() -> None:
    """``needs_adapter`` handlers still receive the adapter kwarg from _bound
    (the filter excludes 'adapter'/'client' from inbound kwargs only)."""
    seen: Dict[str, Any] = {}

    async def _probe_adapter(client, adapter=None, *, chatId: str):
        seen["adapter"] = adapter
        seen["chatId"] = chatId
        return {"success": True}

    live = _live_adapter_with_client(object())
    bound = _make_tool_handler(_BareCtx(), "probe_adapter_tool", _probe_adapter)

    # A malicious/buggy inbound 'adapter' kwarg must be dropped, not collide.
    raw = await bound({"chatId": "123@c.us", "adapter": "evil"}, task_id="t")
    assert json.loads(raw)["success"] is True
    assert seen["adapter"] is live
    assert seen["chatId"] == "123@c.us"
