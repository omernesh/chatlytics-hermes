"""v4.5.2 — live-adapter registry fallback tests.

Root cause (BotDaddy "adapter is not connected" on longpoll-only gateways):
hermes-agent's real ``hermes_cli.plugins.PluginContext`` (0.16.0) exposes
NEITHER ``get_platform`` NOR a ``platforms`` mapping, so the register-time
ctx captured by ``_make_tool_handler`` could never resolve the live adapter
and EVERY chatlytics_* tool failed with "adapter is not connected" even
while the longpoll loop was alive.

Fix under test: ``connect()`` registers the adapter in the module-level
``_LIVE_ADAPTERS`` registry; ``_lookup_adapter`` falls back to it after the
ctx probes; ``disconnect()`` unregisters (identity-guarded).
"""

from __future__ import annotations

import json
from typing import Any, Dict

import httpx
import pytest
import respx

from chatlytics_hermes.adapter import (
    ChatlyticsAdapter,
    _LIVE_ADAPTERS,
    _make_tool_handler,
    _register_live_adapter,
    _unregister_live_adapter,
)
from chatlytics_hermes.tools import chatlytics_health, chatlytics_login
from tests._fixtures import FakePlatformConfig

pytestmark = pytest.mark.asyncio

BASE_URL = "https://gateway.test.chatlytics.ai"
BOT_TOKEN = "sk_bot_" + "A" * 43


class _BareCtx:
    """Faithful stand-in for hermes_cli.plugins.PluginContext (0.16.0):
    NO ``get_platform`` method, NO ``platforms`` attribute."""


def _make_adapter(inbound_mode: str = "longpoll") -> ChatlyticsAdapter:
    extra: Dict[str, Any] = {
        "base_url": BASE_URL,
        "bot_token": BOT_TOKEN,
        "inbound_mode": inbound_mode,
    }
    return ChatlyticsAdapter(FakePlatformConfig(extra=extra))


def _mock_gateway(router: respx.Router) -> None:
    router.get("/health").mock(
        return_value=httpx.Response(
            200, json={"webhook_registered": True, "sessions": ["3cf11776_logan"]}
        )
    )
    router.get("/api/v1/bot/me").mock(
        return_value=httpx.Response(200, json={"bot": {"name": "TestBot"}})
    )
    # Longpoll GET: empty batches keep the loop harmlessly idle for the
    # short life of the test.
    router.get("/api/v1/bot/updates").mock(
        return_value=httpx.Response(200, json={"envelopes": [], "cursor": "c0"})
    )
    router.post("/api/v1/bot/updates/ack").mock(
        return_value=httpx.Response(200, json={"acked": 0, "cursor": "c0"})
    )


# --- E2E: longpoll-only connect() makes tools resolvable on a bare ctx -----


async def test_health_and_login_work_on_longpoll_only_gateway() -> None:
    """The BotDaddy repro: bare PluginContext + longpoll inbound. After
    connect(), chatlytics_health / chatlytics_login resolve the live
    adapter via the registry fallback and return real /health truth."""
    adapter = _make_adapter("longpoll")
    health_bound = _make_tool_handler(_BareCtx(), "chatlytics_health", chatlytics_health)
    login_bound = _make_tool_handler(_BareCtx(), "chatlytics_login", chatlytics_login)

    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        _mock_gateway(router)

        assert await adapter.connect() is True
        assert _LIVE_ADAPTERS.get("chatlytics") is adapter

        health_payload = json.loads(await health_bound())
        assert health_payload["success"] is True
        assert health_payload["webhook_registered"] is True

        login_payload = json.loads(await login_bound())
        assert login_payload["success"] is True

        await adapter.disconnect()

    assert "chatlytics" not in _LIVE_ADAPTERS

    # After disconnect the tools report not-connected again (no stale entry).
    stale = json.loads(await health_bound())
    assert stale["success"] is False
    assert "not connected" in stale["error"]


async def test_bare_ctx_without_live_adapter_still_reports_not_connected() -> None:
    """No connect() ever happened: the registry fallback returns None and
    the canonical not-connected failure shape is preserved."""
    bound = _make_tool_handler(_BareCtx(), "chatlytics_health", chatlytics_health)
    payload = json.loads(await bound())
    assert payload["success"] is False
    assert "adapter is not connected" in payload["error"]


# --- Registry unit semantics ------------------------------------------------


async def test_disconnect_is_identity_guarded() -> None:
    """A stale adapter's disconnect() must not clobber a newer live one
    (overlapping reconnect)."""
    old = _make_adapter()
    new = _make_adapter()
    _register_live_adapter(old)
    _register_live_adapter(new)  # reconnect superseded `old`

    await old.disconnect()  # no client/runner/poll task — pure teardown
    assert _LIVE_ADAPTERS.get("chatlytics") is new

    _unregister_live_adapter(new)
    assert "chatlytics" not in _LIVE_ADAPTERS


async def test_ctx_accessors_still_win_over_registry() -> None:
    """Regression: a harness ctx that DOES expose get_platform keeps its
    adapter — the registry is a fallback, not an override."""

    class _Entry:
        def __init__(self, adapter: Any) -> None:
            self.adapter = adapter

    ctx_adapter = _make_adapter()
    registry_adapter = _make_adapter()
    _register_live_adapter(registry_adapter)

    sentinel_client = object()
    ctx_adapter._client = sentinel_client  # type: ignore[assignment]

    seen: Dict[str, Any] = {}

    async def _probe(client, **kwargs):
        seen["client"] = client
        return {"success": True}

    class _Ctx:
        def get_platform(self, name: str):
            return _Entry(ctx_adapter) if name == "chatlytics" else None

    bound = _make_tool_handler(_Ctx(), "chatlytics_health", _probe)
    payload = json.loads(await bound())
    assert payload["success"] is True
    assert seen["client"] is sentinel_client


async def test_degraded_no_credential_connect_registers() -> None:
    """Degraded (no token) connect() still registers the adapter so the
    exempt status tools resolve it and report the degraded truth."""
    adapter = ChatlyticsAdapter(
        FakePlatformConfig(extra={"base_url": BASE_URL, "inbound_mode": "longpoll"})
    )
    assert await adapter.connect() is True
    assert _LIVE_ADAPTERS.get("chatlytics") is adapter
    # client is None in the degraded state — health reports not-connected
    # (pre-existing v4.1.5 contract, unchanged by the registry).
    bound = _make_tool_handler(_BareCtx(), "chatlytics_health", chatlytics_health)
    payload = json.loads(await bound())
    assert payload["success"] is False
    await adapter.disconnect()
    assert "chatlytics" not in _LIVE_ADAPTERS
