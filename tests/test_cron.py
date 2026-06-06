"""HERMES-04 acceptance test for the out-of-process cron sender.

The ``_standalone_send`` coroutine is the hook Hermes invokes when
``hermes cron`` runs in a separate process from ``hermes gateway`` --
without this hook, ``deliver=chatlytics`` cron jobs fail with "No live
adapter for platform chatlytics".

This test does not require an adapter instance; the coroutine is a
top-level module function that takes ``text`` and a free-form
``**kwargs`` for forward-compat with the Hermes cron call shape.
"""

from __future__ import annotations

import json as _json

import httpx
import pytest
import respx

from chatlytics_hermes.adapter import _standalone_send

BASE_URL = "https://gateway.test.chatlytics.ai"
API_KEY = "test-api-key-abc123"
BOT_TOKEN = "sk_bot_test_cron_token_0000000000000000000000"
HOME = "120363100000000000@g.us"


async def test_cron_deliver_env_var_routes_to_standalone_sender(monkeypatch) -> None:
    """AC-8: standalone_sender_fn posts to /api/v1/send with HOME_CHANNEL chatId."""
    monkeypatch.delenv("CHATLYTICS_BOT_TOKEN", raising=False)
    monkeypatch.setenv("CHATLYTICS_BASE_URL", BASE_URL)
    monkeypatch.setenv("CHATLYTICS_API_KEY", API_KEY)
    monkeypatch.setenv("CHATLYTICS_HOME_CHANNEL", HOME)

    with respx.mock(base_url=BASE_URL, assert_all_called=True) as router:
        send_route = router.post("/api/v1/send").mock(
            return_value=httpx.Response(
                200, json={"success": True, "messageId": "m-cron"}
            )
        )
        result = await _standalone_send("cron payload")

    assert result["success"] is True
    assert result.get("messageId") == "m-cron"

    body = _json.loads(send_route.calls.last.request.content)
    assert body == {"chatId": HOME, "text": "cron payload"}

    # Bearer auth was injected by the fresh httpx client.
    assert send_route.calls.last.request.headers["authorization"] == f"Bearer {API_KEY}"


async def test_standalone_send_prefers_bot_token_over_api_key(monkeypatch) -> None:
    """v4.1.3 MD-01: CHATLYTICS_BOT_TOKEN wins over legacy CHATLYTICS_API_KEY.

    A token-only cron deployment (sk_bot_* set, api_key absent) must send;
    when both are present the per-bot token is the Bearer carried to the
    gateway's gated /api/v1/send.
    """
    monkeypatch.setenv("CHATLYTICS_BASE_URL", BASE_URL)
    monkeypatch.setenv("CHATLYTICS_BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setenv("CHATLYTICS_API_KEY", API_KEY)
    monkeypatch.setenv("CHATLYTICS_HOME_CHANNEL", HOME)

    with respx.mock(base_url=BASE_URL, assert_all_called=True) as router:
        send_route = router.post("/api/v1/send").mock(
            return_value=httpx.Response(200, json={"success": True, "messageId": "m-bot"})
        )
        result = await _standalone_send("cron payload")

    assert result["success"] is True
    assert send_route.calls.last.request.headers["authorization"] == f"Bearer {BOT_TOKEN}"


async def test_standalone_send_bot_token_only(monkeypatch) -> None:
    """v4.1.3 MD-01: api_key absent + bot_token present still sends (no guard trip)."""
    monkeypatch.setenv("CHATLYTICS_BASE_URL", BASE_URL)
    monkeypatch.setenv("CHATLYTICS_BOT_TOKEN", BOT_TOKEN)
    monkeypatch.delenv("CHATLYTICS_API_KEY", raising=False)
    monkeypatch.setenv("CHATLYTICS_HOME_CHANNEL", HOME)

    with respx.mock(base_url=BASE_URL, assert_all_called=True) as router:
        send_route = router.post("/api/v1/send").mock(
            return_value=httpx.Response(200, json={"success": True})
        )
        result = await _standalone_send("hi")

    assert result["success"] is True
    assert send_route.calls.last.request.headers["authorization"] == f"Bearer {BOT_TOKEN}"


async def test_standalone_send_returns_error_when_env_unset(monkeypatch) -> None:
    """Forward-compat: missing env vars yield {error:...} -- never raise.

    Hermes's cron pipeline interprets the return dict directly; raising
    would crash the whole cron tick.
    """
    monkeypatch.delenv("CHATLYTICS_BASE_URL", raising=False)
    monkeypatch.delenv("CHATLYTICS_BOT_TOKEN", raising=False)
    monkeypatch.delenv("CHATLYTICS_API_KEY", raising=False)
    monkeypatch.delenv("CHATLYTICS_HOME_CHANNEL", raising=False)
    result = await _standalone_send("anything")
    assert "error" in result
    assert "CHATLYTICS_HOME_CHANNEL" in result["error"] or "BASE_URL" in result["error"]


async def test_standalone_send_accepts_extra_kwargs(monkeypatch) -> None:
    """Hermes may call with thread_id / media_files; signature must accept."""
    monkeypatch.delenv("CHATLYTICS_BOT_TOKEN", raising=False)
    monkeypatch.setenv("CHATLYTICS_BASE_URL", BASE_URL)
    monkeypatch.setenv("CHATLYTICS_API_KEY", API_KEY)
    monkeypatch.setenv("CHATLYTICS_HOME_CHANNEL", HOME)
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        router.post("/api/v1/send").mock(
            return_value=httpx.Response(200, json={"success": True})
        )
        # Should not raise on unknown kwargs.
        result = await _standalone_send(
            "hi",
            thread_id="t-1",
            media_files=["/tmp/x.png"],
            force_document=True,
        )
    assert result["success"] is True
