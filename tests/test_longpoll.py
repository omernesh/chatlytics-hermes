"""v4.1 longpoll inbound-consumer tests.

Drives ``ChatlyticsAdapter._poll_loop`` against a fake ``ChatlyticsClient``
so we can assert the GET/ack sequencing + InboundEnvelope -> MessageEvent
translation without a live chatlytics gateway.

Test strategy:
- Replace ``adapter._client`` with a ``FakeClient`` that serves a scripted
  sequence of GET responses (one non-empty batch, then empty batches) and
  records every ``get``/``post`` call.
- Replace ``adapter.handle_message`` + ``adapter.register_chat_session``
  with recorders.
- Stop the loop deterministically: the FakeClient flips ``adapter._running``
  to False once the scripted batches are exhausted, so the ``while
  self._running`` guard exits cleanly.
"""

from __future__ import annotations

from typing import Any, Dict, List

import httpx
import pytest

from chatlytics_hermes.adapter import ChatlyticsAdapter
from tests._fixtures import FakePlatformConfig

BASE_URL = "https://gateway.test.chatlytics.ai"
BOT_TOKEN = "sk_bot_" + "A" * 43
CHAT_ID = "120363100000000000@g.us"
SESSION_ID = "3cf11776_logan"
CURSOR_1 = "Y3Vyc29yLTE="  # opaque base64url-ish cursor returned by batch 1


def _make_adapter() -> ChatlyticsAdapter:
    extra: Dict[str, Any] = {
        "base_url": BASE_URL,
        "bot_token": BOT_TOKEN,
        "inbound_mode": "longpoll",
    }
    return ChatlyticsAdapter(FakePlatformConfig(extra=extra))


class FakeClient:
    """Scripted stand-in for ChatlyticsClient.

    ``get_responses`` is a list of (status_code, json_body) tuples returned
    by successive GET calls. Once exhausted, it flips ``adapter._running``
    to False (after returning a final empty batch) so the poll loop exits.
    """

    def __init__(self, adapter: ChatlyticsAdapter, get_responses: List[Any]) -> None:
        self._adapter = adapter
        self._get_responses = list(get_responses)
        self.get_calls: List[Dict[str, Any]] = []
        self.post_calls: List[Dict[str, Any]] = []
        self.base_url = BASE_URL

    async def get(self, path: str, *, params: Dict[str, Any] | None = None) -> httpx.Response:
        self.get_calls.append({"path": path, "params": params or {}})
        if self._get_responses:
            status, body = self._get_responses.pop(0)
        else:
            # Nothing scripted left: return an empty batch and stop the loop.
            status, body = 200, {"envelopes": [], "cursor": params.get("cursor", "")}
        if not self._get_responses:
            # Last scripted response just consumed (or none left): stop the
            # loop after this iteration completes.
            self._adapter._running = False
        return httpx.Response(status, json=body, request=httpx.Request("GET", BASE_URL + path))

    async def post(self, path: str, *, json: Dict[str, Any] | None = None) -> httpx.Response:
        self.post_calls.append({"path": path, "json": json or {}})
        return httpx.Response(
            200,
            json={"acked": 1, "cursor": (json or {}).get("cursor", "")},
            request=httpx.Request("POST", BASE_URL + path),
        )

    async def aclose(self) -> None:  # pragma: no cover - parity with real client
        return None


def _install_recorders(adapter: ChatlyticsAdapter):
    events: List[Any] = []
    sessions: List[tuple] = []

    async def _recorder(event: Any) -> None:
        events.append(event)

    orig_register = adapter.register_chat_session

    def _reg(chat_id: str, session: str) -> None:
        sessions.append((chat_id, session))
        orig_register(chat_id, session)  # keep real map population

    adapter.handle_message = _recorder  # type: ignore[assignment]
    adapter.register_chat_session = _reg  # type: ignore[assignment]
    return events, sessions


async def test_poll_loop_dispatches_envelope_and_acks() -> None:
    from gateway.platforms.base import MessageType

    adapter = _make_adapter()
    events, sessions = _install_recorders(adapter)

    envelope = {
        "bot_token": BOT_TOKEN,
        "session_id": SESSION_ID,
        "chat_type": "group",
        "entity_jid": CHAT_ID,
        "sender_jid": "972544329000@c.us",
        "text": "hello via longpoll",
        "dispatch": {"reason": "mention", "god_mode": False},
        "ts": 1700000000,
    }
    # Batch 1: one envelope + a fresh cursor. (FakeClient stops the loop
    # right after this single scripted response is consumed.)
    fake = FakeClient(
        adapter,
        get_responses=[
            (200, {"envelopes": [envelope], "cursor": CURSOR_1}),
        ],
    )
    adapter._client = fake  # type: ignore[assignment]
    adapter._running = True

    await adapter._poll_loop()

    # (a) GET called with the cursor param (first poll uses empty cursor).
    assert fake.get_calls, "poll loop never issued a GET"
    first = fake.get_calls[0]
    assert first["path"] == "/api/v1/bot/updates"
    assert first["params"].get("cursor") == ""
    assert first["params"].get("timeout_ms") == 25000

    # (b) handle_message called once with a MessageEvent translated correctly.
    assert len(events) == 1, f"expected 1 dispatched event, got {len(events)}"
    ev = events[0]
    assert ev.source.chat_id == CHAT_ID
    assert ev.text == "hello via longpoll"
    assert ev.message_type == MessageType.TEXT
    assert ev.source.chat_type == "group"

    # (c) ack POSTed with the cursor returned by the GET we just processed.
    assert fake.post_calls, "poll loop never acked"
    ack = fake.post_calls[0]
    assert ack["path"] == "/api/v1/bot/updates/ack"
    assert ack["json"].get("cursor") == CURSOR_1

    # (d) register_chat_session(entity_jid, session_id) was called + the map
    #     was populated so the outbound reply resolves the right session.
    assert (CHAT_ID, SESSION_ID) in sessions
    assert adapter._resolve_session_for_chat(CHAT_ID) == SESSION_ID


async def test_newsletter_chat_type_maps_to_channel() -> None:
    adapter = _make_adapter()
    events, _ = _install_recorders(adapter)

    envelope = {
        "session_id": SESSION_ID,
        "chat_type": "newsletter",
        "entity_jid": "120363111111111111@newsletter",
        "sender_jid": None,
        "text": "channel post",
        "ts": 1700000001,
    }
    fake = FakeClient(
        adapter, get_responses=[(200, {"envelopes": [envelope], "cursor": CURSOR_1})]
    )
    adapter._client = fake  # type: ignore[assignment]
    adapter._running = True

    await adapter._poll_loop()

    assert len(events) == 1
    assert events[0].source.chat_type == "channel"


async def test_invalid_cursor_resets_and_continues() -> None:
    adapter = _make_adapter()
    events, _ = _install_recorders(adapter)

    envelope = {
        "session_id": SESSION_ID,
        "chat_type": "dm",
        "entity_jid": CHAT_ID,
        "sender_jid": "972544329000@c.us",
        "text": "after reset",
        "ts": 1700000002,
    }
    # First GET 400s (invalid_cursor) -> loop resets cursor to "" and
    # re-polls; second GET serves the envelope.
    fake = FakeClient(
        adapter,
        get_responses=[
            (400, {"error": "invalid_cursor"}),
            (200, {"envelopes": [envelope], "cursor": CURSOR_1}),
        ],
    )
    adapter._client = fake  # type: ignore[assignment]
    adapter._running = True

    await adapter._poll_loop()

    # Two GETs: the rejected one, then the recovery poll with cursor reset.
    assert len(fake.get_calls) == 2
    assert fake.get_calls[1]["params"].get("cursor") == ""
    assert len(events) == 1
    assert events[0].text == "after reset"


async def test_empty_batch_does_not_ack() -> None:
    adapter = _make_adapter()
    events, _ = _install_recorders(adapter)

    fake = FakeClient(
        adapter, get_responses=[(200, {"envelopes": [], "cursor": "unchanged"})]
    )
    adapter._client = fake  # type: ignore[assignment]
    adapter._running = True

    await adapter._poll_loop()

    assert events == []
    assert fake.post_calls == [], "empty batch must not ack"
