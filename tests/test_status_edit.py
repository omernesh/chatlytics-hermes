"""v4.4.0 (chatlytics v5.4 P7) — progress-bubble edit-in-place tests.

Covers the gateway half of the P7 feature:

- fast turn (stop_event before threshold) ⇒ NO bubble POST and the final
  ``send()`` body carries NO ``edit_message_id`` (byte-identical fast path);
- slow turn ⇒ exactly ONE bubble POST with ``progress: true``, then the
  final ``send()`` consumes the memoized id as ``edit_message_id``;
- message-id parsing fallbacks (``message_id`` / ``messageId`` /
  ``waha.id._serialized``);
- bubble POST failure ⇒ DEBUG-only, final send is a plain send;
- edit-tagged send transport failure ⇒ ONE retry without
  ``edit_message_id``;
- ``status_edit_in_place: False`` ⇒ no bubble, no edit (v4.3.0 behavior);
- reserved-key rejection for ``edit_message_id`` / ``progress`` metadata;
- bounded LRU eviction + staleness TTL of the per-chat bubble memo.

respx-mocked like tests/test_outbound.py; asyncio_mode = auto.
"""

from __future__ import annotations

import asyncio
import json as _json
import logging
from typing import Any, Dict

import httpx
import pytest
import respx

from chatlytics_hermes import adapter as adapter_mod
from chatlytics_hermes.adapter import ChatlyticsAdapter
from tests._fixtures import FakePlatformConfig

BASE_URL = "https://gateway.test.chatlytics.ai"
API_KEY = "test-api-key-abc123"
CHAT_ID = "120363100000000000@g.us"

# Small threshold so "slow turn" tests don't sleep for real seconds.
FAST_THRESHOLD = 0.05


def make_adapter(**extra_overrides: Any) -> ChatlyticsAdapter:
    extra: Dict[str, Any] = {
        "base_url": BASE_URL,
        "api_key": API_KEY,
        "account_id": "acct-1",
        "session": "3cf11776_logan",
        "status_bubble_after_s": FAST_THRESHOLD,
    }
    extra.update(extra_overrides)
    return ChatlyticsAdapter(FakePlatformConfig(extra=extra))


@pytest.fixture
def mock_router():
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        yield router


async def run_turn(
    adapter: ChatlyticsAdapter,
    *,
    turn_duration: float,
) -> None:
    """Simulate one agent turn the way the gateway base class drives it.

    Spawns ``_keep_typing(chat_id, stop_event=...)`` as a task, waits
    ``turn_duration``, sets the stop event (turn finished), and reaps the
    task — the same lifecycle ``BasePlatformAdapter`` applies per inbound
    message.
    """
    stop = asyncio.Event()
    task = asyncio.create_task(
        adapter._keep_typing(CHAT_ID, interval=60.0, stop_event=stop)
    )
    await asyncio.sleep(turn_duration)
    stop.set()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


def send_bodies(send_route: respx.Route) -> list:
    return [_json.loads(call.request.content) for call in send_route.calls]


# --- Config knob resolution ---------------------------------------------


def test_flag_defaults_true_threshold_defaults_8() -> None:
    adapter = ChatlyticsAdapter(
        FakePlatformConfig(extra={"base_url": BASE_URL, "api_key": API_KEY})
    )
    assert adapter.status_edit_in_place is True
    assert adapter.status_bubble_after_s == 8.0
    assert adapter.status_bubble_text == "⏳ working…"


def test_env_overrides_and_defensive_parsing(monkeypatch) -> None:
    monkeypatch.setenv("CHATLYTICS_STATUS_EDIT_IN_PLACE", "false")
    monkeypatch.setenv("CHATLYTICS_STATUS_BUBBLE_AFTER_S", "not-a-number")
    monkeypatch.setenv("CHATLYTICS_STATUS_BUBBLE_TEXT", "hold on")
    adapter = ChatlyticsAdapter(
        FakePlatformConfig(
            extra={
                "base_url": BASE_URL,
                "api_key": API_KEY,
                # env must win over extra
                "status_edit_in_place": True,
            }
        )
    )
    assert adapter.status_edit_in_place is False
    assert adapter.status_bubble_after_s == 8.0  # bad value -> default
    assert adapter.status_bubble_text == "hold on"


# --- Fast turn: byte-identical v4.3.0 path -------------------------------


async def test_fast_turn_no_bubble_and_plain_send(
    mock_router: respx.MockRouter,
) -> None:
    """Flag default True but the turn finishes before the threshold:
    NO bubble POST happens and the final send body has NO edit_message_id
    and NO progress key — byte-identical to v4.3.0."""
    adapter = make_adapter()
    mock_router.get("/health").mock(return_value=httpx.Response(200, json={}))
    mock_router.post("/api/v1/typing").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    send_route = mock_router.post("/api/v1/send").mock(
        return_value=httpx.Response(
            200, json={"success": True, "messageId": "final-1"}
        )
    )

    await adapter.connect()
    # Turn finishes well before the 0.05 s threshold.
    await run_turn(adapter, turn_duration=0.0)
    # Give any (incorrectly) lingering timer a chance to misfire.
    await asyncio.sleep(FAST_THRESHOLD * 3)

    assert not send_route.called  # no bubble POST at all

    result = await adapter.send(CHAT_ID, "the reply")
    assert result.success is True
    body = send_bodies(send_route)[-1]
    assert "edit_message_id" not in body
    assert "progress" not in body
    assert body["chatId"] == CHAT_ID
    assert body["text"] == "the reply"
    assert body["session"] == "3cf11776_logan"
    assert body["accountId"] == "acct-1"
    await adapter.disconnect()


# --- Slow turn: one bubble, then edit-in-place ----------------------------


async def test_slow_turn_sends_one_bubble_then_edits_reply(
    mock_router: respx.MockRouter,
) -> None:
    adapter = make_adapter()
    mock_router.get("/health").mock(return_value=httpx.Response(200, json={}))
    mock_router.post("/api/v1/typing").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    send_route = mock_router.post("/api/v1/send").mock(
        return_value=httpx.Response(
            200,
            json={"success": True, "message_id": "true_chat_bubble-1"},
        )
    )

    await adapter.connect()
    # Turn outlives the 0.05 s threshold -> exactly one bubble.
    await run_turn(adapter, turn_duration=FAST_THRESHOLD * 6)

    bodies = send_bodies(send_route)
    assert len(bodies) == 1, "exactly ONE bubble POST per turn"
    bubble = bodies[0]
    assert bubble["progress"] is True
    assert bubble["text"] == "⏳ working…"
    assert bubble["chatId"] == CHAT_ID
    assert bubble["session"] == "3cf11776_logan"
    assert "edit_message_id" not in bubble

    # Final reply consumes the pending bubble as edit_message_id.
    send_route.mock(
        return_value=httpx.Response(
            200,
            json={"success": True, "message_id": "true_chat_bubble-1", "edited": True},
        )
    )
    result = await adapter.send(CHAT_ID, "final answer")
    assert result.success is True
    final_body = send_bodies(send_route)[-1]
    assert final_body["edit_message_id"] == "true_chat_bubble-1"
    assert "progress" not in final_body

    # Pending entry was POPPED — a second send is plain.
    assert CHAT_ID not in adapter._progress_bubbles
    await adapter.send(CHAT_ID, "follow-up")
    assert "edit_message_id" not in send_bodies(send_route)[-1]
    await adapter.disconnect()


async def test_no_second_bubble_when_one_is_pending(
    mock_router: respx.MockRouter,
) -> None:
    """One-bubble-per-turn guard: an un-consumed pending bubble suppresses
    further bubble sends for the same chat."""
    adapter = make_adapter()
    mock_router.get("/health").mock(return_value=httpx.Response(200, json={}))
    send_route = mock_router.post("/api/v1/send").mock(
        return_value=httpx.Response(
            200, json={"success": True, "message_id": "bub-1"}
        )
    )
    await adapter.connect()
    await adapter._send_progress_bubble(CHAT_ID)
    assert len(send_route.calls) == 1
    await adapter._send_progress_bubble(CHAT_ID)
    assert len(send_route.calls) == 1, "second bubble suppressed"
    await adapter.disconnect()


# --- Message-id parsing fallbacks -----------------------------------------


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"success": True, "message_id": "mid-new"}, "mid-new"),
        ({"success": True, "messageId": "mid-legacy"}, "mid-legacy"),
        (
            {"success": True, "waha": {"id": {"_serialized": "true_x_y"}}},
            "true_x_y",
        ),
        ({"success": True, "waha": {"id": {"id": "inner-id"}}}, "inner-id"),
        ({"success": True, "waha": {"id": "plain-id"}}, "plain-id"),
        ({"success": True, "waha": {"key": {"id": "key-id"}}}, "key-id"),
        ({"success": True}, None),
        ("not-a-dict", None),
        (None, None),
    ],
)
def test_extract_message_id_fallbacks(payload: Any, expected: Any) -> None:
    assert ChatlyticsAdapter._extract_message_id(payload) == expected


async def test_bubble_memoizes_id_from_waha_serialized(
    mock_router: respx.MockRouter,
) -> None:
    adapter = make_adapter()
    mock_router.get("/health").mock(return_value=httpx.Response(200, json={}))
    mock_router.post("/api/v1/send").mock(
        return_value=httpx.Response(
            200,
            json={"success": True, "waha": {"id": {"_serialized": "true_c_77"}}},
        )
    )
    await adapter.connect()
    await adapter._send_progress_bubble(CHAT_ID)
    assert adapter._progress_bubbles[CHAT_ID][0] == "true_c_77"
    await adapter.disconnect()


# --- Bubble failure isolation ----------------------------------------------


async def test_bubble_failure_is_debug_only_and_send_is_plain(
    mock_router: respx.MockRouter, caplog: pytest.LogCaptureFixture
) -> None:
    adapter = make_adapter()
    mock_router.get("/health").mock(return_value=httpx.Response(200, json={}))
    send_route = mock_router.post("/api/v1/send").mock(
        side_effect=httpx.ConnectError("bubble pipe burst")
    )
    await adapter.connect()
    with caplog.at_level(logging.DEBUG, logger="chatlytics_hermes.adapter"):
        await adapter._send_progress_bubble(CHAT_ID)
    bubble_records = [
        r for r in caplog.records if "progress bubble" in r.getMessage()
    ]
    assert bubble_records, "expected a DEBUG record for the bubble failure"
    assert all(r.levelno == logging.DEBUG for r in bubble_records)
    assert CHAT_ID not in adapter._progress_bubbles

    # Final send proceeds as a plain send (no edit_message_id).
    send_route.mock(
        return_value=httpx.Response(
            200, json={"success": True, "messageId": "ok-1"}
        )
    )
    result = await adapter.send(CHAT_ID, "reply anyway")
    assert result.success is True
    assert "edit_message_id" not in send_bodies(send_route)[-1]
    await adapter.disconnect()


async def test_bubble_non_200_is_swallowed(
    mock_router: respx.MockRouter,
) -> None:
    adapter = make_adapter()
    mock_router.get("/health").mock(return_value=httpx.Response(200, json={}))
    mock_router.post("/api/v1/send").mock(
        return_value=httpx.Response(500, json={"error": "boom"})
    )
    await adapter.connect()
    await adapter._send_progress_bubble(CHAT_ID)  # must not raise
    assert CHAT_ID not in adapter._progress_bubbles
    await adapter.disconnect()


# --- Edit-tagged transport failure: one plain retry -------------------------


async def test_edit_tagged_transport_failure_retries_once_plain(
    mock_router: respx.MockRouter,
) -> None:
    adapter = make_adapter()
    mock_router.get("/health").mock(return_value=httpx.Response(200, json={}))
    send_route = mock_router.post("/api/v1/send").mock(
        side_effect=[
            httpx.ConnectError("edit request died"),
            httpx.Response(200, json={"success": True, "messageId": "retry-ok"}),
        ]
    )
    await adapter.connect()
    adapter._store_progress_bubble(CHAT_ID, "bubble-9")

    result = await adapter.send(CHAT_ID, "important reply")
    assert result.success is True
    assert result.message_id == "retry-ok"

    bodies = send_bodies(send_route)
    assert len(bodies) == 2
    assert bodies[0]["edit_message_id"] == "bubble-9"
    assert "edit_message_id" not in bodies[1], "retry must be a plain send"
    assert bodies[1]["text"] == "important reply"
    await adapter.disconnect()


async def test_edit_tagged_double_transport_failure_returns_failure(
    mock_router: respx.MockRouter,
) -> None:
    adapter = make_adapter()
    mock_router.get("/health").mock(return_value=httpx.Response(200, json={}))
    send_route = mock_router.post("/api/v1/send").mock(
        side_effect=httpx.ConnectError("gateway gone")
    )
    await adapter.connect()
    adapter._store_progress_bubble(CHAT_ID, "bubble-10")

    result = await adapter.send(CHAT_ID, "doomed reply")
    assert result.success is False
    assert result.retryable is True
    assert "Transport error" in (result.error or "")
    assert len(send_route.calls) == 2, "exactly one retry"
    await adapter.disconnect()


async def test_plain_send_transport_failure_does_not_retry(
    mock_router: respx.MockRouter,
) -> None:
    """v4.3.0 semantics preserved: with no pending bubble there is no retry."""
    adapter = make_adapter()
    mock_router.get("/health").mock(return_value=httpx.Response(200, json={}))
    send_route = mock_router.post("/api/v1/send").mock(
        side_effect=httpx.ConnectError("down")
    )
    await adapter.connect()
    result = await adapter.send(CHAT_ID, "plain")
    assert result.success is False
    assert result.retryable is True
    assert len(send_route.calls) == 1
    await adapter.disconnect()


# --- v4.5.1 (review-d3 X16): edit-tagged NON-200 also retries once plain ----


async def test_edit_tagged_non_200_retries_once_plain(
    mock_router: respx.MockRouter, caplog
) -> None:
    """A server that 500s on the edit decoration must not lose the reply:
    ONE plain retry (same at-least-once tradeoff as the transport path)."""
    adapter = make_adapter()
    mock_router.get("/health").mock(return_value=httpx.Response(200, json={}))
    send_route = mock_router.post("/api/v1/send").mock(
        side_effect=[
            httpx.Response(500, json={"error": "edit blew up"}),
            httpx.Response(200, json={"success": True, "messageId": "plain-ok"}),
        ]
    )
    await adapter.connect()
    adapter._store_progress_bubble(CHAT_ID, "bubble-11")

    with caplog.at_level(logging.WARNING, logger="chatlytics_hermes.adapter"):
        result = await adapter.send(CHAT_ID, "important reply")

    assert result.success is True
    assert result.message_id == "plain-ok"
    bodies = send_bodies(send_route)
    assert len(bodies) == 2
    assert bodies[0]["edit_message_id"] == "bubble-11"
    assert "edit_message_id" not in bodies[1], "retry must be a plain send"
    assert any(
        "retrying once as plain send" in r.getMessage() for r in caplog.records
    )
    await adapter.disconnect()


async def test_edit_tagged_double_non_200_returns_failure(
    mock_router: respx.MockRouter,
) -> None:
    adapter = make_adapter()
    mock_router.get("/health").mock(return_value=httpx.Response(200, json={}))
    send_route = mock_router.post("/api/v1/send").mock(
        return_value=httpx.Response(500, json={"error": "still broken"})
    )
    await adapter.connect()
    adapter._store_progress_bubble(CHAT_ID, "bubble-12")

    result = await adapter.send(CHAT_ID, "doomed reply")
    assert result.success is False
    assert result.retryable is True
    assert len(send_route.calls) == 2, "exactly one retry"
    await adapter.disconnect()


async def test_plain_send_non_200_does_not_retry(
    mock_router: respx.MockRouter,
) -> None:
    """No pending bubble → a non-200 fails immediately (no retry — only the
    edit decoration earns the at-least-once retry)."""
    adapter = make_adapter()
    mock_router.get("/health").mock(return_value=httpx.Response(200, json={}))
    send_route = mock_router.post("/api/v1/send").mock(
        return_value=httpx.Response(500, json={"error": "down"})
    )
    await adapter.connect()
    result = await adapter.send(CHAT_ID, "plain")
    assert result.success is False
    assert len(send_route.calls) == 1
    await adapter.disconnect()


# --- v4.5.1 (review-d3 M6): 200 + non-JSON body is a failure ----------------


async def test_send_200_with_non_json_body_is_failure(
    mock_router: respx.MockRouter, caplog
) -> None:
    adapter = make_adapter()
    mock_router.get("/health").mock(return_value=httpx.Response(200, json={}))
    mock_router.post("/api/v1/send").mock(
        return_value=httpx.Response(
            200, text="<html>proxy error page</html>",
            headers={"content-type": "text/html"},
        )
    )
    await adapter.connect()

    with caplog.at_level(logging.WARNING, logger="chatlytics_hermes.adapter"):
        result = await adapter.send(CHAT_ID, "hello")

    assert result.success is False
    assert "non-JSON" in (result.error or "")
    assert result.retryable is False  # 200 — not a 5xx
    assert any(
        "non-JSON body" in r.getMessage() and "proxy error page" in r.getMessage()
        for r in caplog.records
    )
    await adapter.disconnect()


# --- Flag off: pure v4.3.0 behavior ----------------------------------------


async def test_flag_false_disables_bubble_and_edit(
    mock_router: respx.MockRouter,
) -> None:
    adapter = make_adapter(status_edit_in_place=False)
    assert adapter.status_edit_in_place is False
    mock_router.get("/health").mock(return_value=httpx.Response(200, json={}))
    mock_router.post("/api/v1/typing").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    send_route = mock_router.post("/api/v1/send").mock(
        return_value=httpx.Response(
            200, json={"success": True, "messageId": "m-1"}
        )
    )
    await adapter.connect()
    # Slow turn — would bubble if the flag were on.
    await run_turn(adapter, turn_duration=FAST_THRESHOLD * 6)
    assert not send_route.called, "flag off: no bubble even on slow turns"

    # Even with a (synthetically) pending bubble, the edit path is gated off.
    adapter._store_progress_bubble(CHAT_ID, "stray-bubble")
    result = await adapter.send(CHAT_ID, "reply")
    assert result.success is True
    assert "edit_message_id" not in send_bodies(send_route)[-1]
    await adapter.disconnect()


async def test_threshold_zero_disables_bubble(
    mock_router: respx.MockRouter,
) -> None:
    adapter = make_adapter(status_bubble_after_s=0)
    mock_router.get("/health").mock(return_value=httpx.Response(200, json={}))
    mock_router.post("/api/v1/typing").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    send_route = mock_router.post("/api/v1/send").mock(
        return_value=httpx.Response(
            200, json={"success": True, "messageId": "m-1"}
        )
    )
    await adapter.connect()
    await run_turn(adapter, turn_duration=0.2)
    assert not send_route.called
    await adapter.disconnect()


# --- Reserved-key rejection --------------------------------------------------


async def test_metadata_cannot_inject_edit_or_progress_keys(
    mock_router: respx.MockRouter, caplog: pytest.LogCaptureFixture
) -> None:
    adapter = make_adapter()
    mock_router.get("/health").mock(return_value=httpx.Response(200, json={}))
    send_route = mock_router.post("/api/v1/send").mock(
        return_value=httpx.Response(
            200, json={"success": True, "messageId": "m-1"}
        )
    )
    await adapter.connect()
    with caplog.at_level(logging.WARNING, logger="chatlytics_hermes.adapter"):
        result = await adapter.send(
            CHAT_ID,
            "hello",
            metadata={
                "edit_message_id": "attacker-controlled",
                "progress": True,
                "linkPreview": False,  # non-reserved key still merges
            },
        )
    assert result.success is True
    body = send_bodies(send_route)[-1]
    assert "edit_message_id" not in body
    assert "progress" not in body
    assert body["linkPreview"] is False
    warned = " ".join(r.getMessage() for r in caplog.records)
    assert "edit_message_id" in warned
    assert "progress" in warned
    await adapter.disconnect()


def test_reserved_body_keys_include_new_fields() -> None:
    assert "edit_message_id" in adapter_mod._RESERVED_BODY_KEYS
    assert "progress" in adapter_mod._RESERVED_BODY_KEYS


# --- Bounded memo: LRU eviction + staleness ---------------------------------


def test_progress_bubble_memo_lru_eviction() -> None:
    adapter = make_adapter()
    cap = adapter_mod._PROGRESS_BUBBLE_MAX
    for i in range(cap + 10):
        adapter._store_progress_bubble(f"chat-{i}@c.us", f"mid-{i}")
    assert len(adapter._progress_bubbles) == cap
    # Oldest entries evicted first.
    assert "chat-0@c.us" not in adapter._progress_bubbles
    assert "chat-9@c.us" not in adapter._progress_bubbles
    assert f"chat-{cap + 9}@c.us" in adapter._progress_bubbles


def test_pop_progress_bubble_staleness(monkeypatch) -> None:
    adapter = make_adapter()
    adapter._store_progress_bubble(CHAT_ID, "old-mid")
    # Age the entry past the TTL.
    mid, ts = adapter._progress_bubbles[CHAT_ID]
    adapter._progress_bubbles[CHAT_ID] = (
        mid,
        ts - (adapter_mod._PROGRESS_BUBBLE_TTL_S + 1.0),
    )
    assert adapter._pop_progress_bubble(CHAT_ID) is None
    # Stale entry was dropped, not retained.
    assert CHAT_ID not in adapter._progress_bubbles


def test_pop_progress_bubble_fresh_and_consumed_once() -> None:
    adapter = make_adapter()
    adapter._store_progress_bubble(CHAT_ID, "fresh-mid")
    assert adapter._pop_progress_bubble(CHAT_ID) == "fresh-mid"
    assert adapter._pop_progress_bubble(CHAT_ID) is None


def test_has_pending_progress_bubble_evicts_stale() -> None:
    adapter = make_adapter()
    adapter._store_progress_bubble(CHAT_ID, "mid")
    assert adapter._has_pending_progress_bubble(CHAT_ID) is True
    mid, ts = adapter._progress_bubbles[CHAT_ID]
    adapter._progress_bubbles[CHAT_ID] = (
        mid,
        ts - (adapter_mod._PROGRESS_BUBBLE_TTL_S + 1.0),
    )
    assert adapter._has_pending_progress_bubble(CHAT_ID) is False
    assert CHAT_ID not in adapter._progress_bubbles
