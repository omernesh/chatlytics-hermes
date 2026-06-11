"""v4.3.0 control-envelope tests (chatlytics v5.4 P6, gateway half).

Covers the longpoll control-envelope wire contract:

- ``caps=control`` advertised on EVERY longpoll GET (capability negotiation).
- Message envelopes (absent ``kind`` / ``kind=="message"``) dispatch exactly
  as before and are acked.
- ``kind=="control"`` routing: new_conversation (gateway runner path +
  session-store fallback + no-API ignore), stop (gateway cancellation +
  adapter-side fallback), retry_last (memoized re-dispatch + no-memo ignore).
- Forward-compat: unknown ``kind`` and unknown control ``action`` are
  IGNORED — never dispatched as message text — and the batch still acks.
- Last-message memo: recorded per normal dispatch, LRU-bounded at 128.

Test strategy mirrors tests/test_longpoll.py: a scripted FakeClient drives
``ChatlyticsAdapter._poll_loop``; recorders replace ``handle_message`` /
``register_chat_session``. Gateway-runner-dependent paths install a
FakeGateway whose BOUND method is set as ``adapter._message_handler`` so
``_gateway_runner()`` resolves it via ``__self__`` exactly like production
(gateway/run.py ``adapter.set_message_handler(self._handle_message)``).
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from chatlytics_hermes.adapter import (
    _LAST_MESSAGE_MEMO_MAX,
    ChatlyticsAdapter,
)
from tests.test_longpoll import (
    BOT_TOKEN,
    CHAT_ID,
    CURSOR_1,
    SESSION_ID,
    FakeClient,
    _install_recorders,
    _make_adapter,
)

SENDER = "972544329000@c.us"


def _msg_envelope(text: str = "hello", **overrides: Any) -> Dict[str, Any]:
    env = {
        "bot_token": BOT_TOKEN,
        "session_id": SESSION_ID,
        "chat_type": "group",
        "entity_jid": CHAT_ID,
        "sender_jid": SENDER,
        "text": text,
        "dispatch": {"reason": "mention", "god_mode": False},
        "ts": 1700000000,
    }
    env.update(overrides)
    return env


def _control_envelope(action: str, **overrides: Any) -> Dict[str, Any]:
    env = {
        "kind": "control",
        "action": action,
        "bot_token": BOT_TOKEN,
        "session_id": SESSION_ID,
        "chat_type": "group",
        "entity_jid": CHAT_ID,
        "sender_jid": SENDER,
        "ts": 1700000001,
    }
    env.update(overrides)
    return env


def _expected_session_key(adapter: ChatlyticsAdapter, text: str = "x") -> str:
    """Compute the session key the harness derives for CHAT_ID inbound.

    Uses the same normalize_payload + build_session_key pipeline the
    adapter uses, so the assertion tracks the harness's key format
    instead of hardcoding it.
    """
    from gateway.session import build_session_key

    from chatlytics_hermes.inbound import normalize_payload

    body = {
        "chatId": CHAT_ID,
        "text": text,
        "senderId": SENDER,
        "chatType": "group",
        "session": SESSION_ID,
    }
    source = normalize_payload(body, adapter.platform).source
    return build_session_key(source)


class FakeGateway:
    """Stands in for the HermesGateway runner.

    ``adapter._message_handler`` is set to the BOUND ``_handle_message``
    so ``_gateway_runner()`` resolves this instance via ``__self__``.
    """

    def __init__(self) -> None:
        self.interrupts: List[Dict[str, Any]] = []
        self.resets: List[Any] = []
        self.handled: List[Any] = []

    async def _handle_message(self, event: Any) -> None:
        self.handled.append(event)

    async def _interrupt_and_clear_session(
        self,
        session_key: str,
        source: Any,
        *,
        interrupt_reason: str,
        invalidation_reason: str,
        release_running_state: bool = True,
    ) -> None:
        self.interrupts.append(
            {
                "session_key": session_key,
                "source": source,
                "interrupt_reason": interrupt_reason,
                "invalidation_reason": invalidation_reason,
            }
        )

    async def _handle_reset_command(self, event: Any) -> str:
        self.resets.append(event)
        return "new conversation started"


class FakeSessionStore:
    def __init__(self) -> None:
        self.resets: List[str] = []

    def reset_session(self, session_key: str, display_name: Any = None) -> Any:
        self.resets.append(session_key)
        return object()


def _wire_gateway(adapter: ChatlyticsAdapter) -> FakeGateway:
    gw = FakeGateway()
    adapter._message_handler = gw._handle_message  # bound → __self__ == gw
    return gw


# --- caps negotiation -------------------------------------------------------


async def test_every_longpoll_get_advertises_caps_control() -> None:
    adapter = _make_adapter()
    _install_recorders(adapter)

    fake = FakeClient(
        adapter,
        get_responses=[
            (200, {"envelopes": [], "cursor": "c1"}),
            (200, {"envelopes": [_msg_envelope()], "cursor": CURSOR_1}),
        ],
    )
    adapter._client = fake  # type: ignore[assignment]
    adapter._running = True

    await adapter._poll_loop()

    assert len(fake.get_calls) >= 2
    for call in fake.get_calls:
        assert call["params"].get("caps") == "control", (
            "caps=control must be advertised on EVERY longpoll GET"
        )
    # Existing params preserved alongside caps.
    assert fake.get_calls[0]["params"].get("cursor") == ""
    assert fake.get_calls[0]["params"].get("timeout_ms") == 25000


# --- message envelopes unchanged --------------------------------------------


async def test_message_envelope_without_kind_dispatches_and_acks() -> None:
    adapter = _make_adapter()
    events, sessions = _install_recorders(adapter)

    fake = FakeClient(
        adapter,
        get_responses=[(200, {"envelopes": [_msg_envelope("no kind")], "cursor": CURSOR_1})],
    )
    adapter._client = fake  # type: ignore[assignment]
    adapter._running = True

    await adapter._poll_loop()

    assert len(events) == 1
    assert events[0].text == "no kind"
    assert (CHAT_ID, SESSION_ID) in sessions
    assert fake.post_calls and fake.post_calls[0]["json"]["cursor"] == CURSOR_1
    # Memo recorded for retry_last.
    assert adapter._last_message_memo[CHAT_ID]["text"] == "no kind"


async def test_message_envelope_with_explicit_kind_message_dispatches() -> None:
    adapter = _make_adapter()
    events, _ = _install_recorders(adapter)

    fake = FakeClient(
        adapter,
        get_responses=[
            (200, {"envelopes": [_msg_envelope("explicit", kind="message")], "cursor": CURSOR_1})
        ],
    )
    adapter._client = fake  # type: ignore[assignment]
    adapter._running = True

    await adapter._poll_loop()

    assert len(events) == 1
    assert events[0].text == "explicit"


# --- new_conversation --------------------------------------------------------


async def test_control_new_conversation_resets_via_gateway_runner() -> None:
    adapter = _make_adapter()
    events, _ = _install_recorders(adapter)
    gw = _wire_gateway(adapter)

    await adapter._handle_control_envelope(_control_envelope("new_conversation"))

    expected_key = _expected_session_key(adapter)
    # In-flight turn interrupted + queued work cleared FIRST...
    assert len(gw.interrupts) == 1
    assert gw.interrupts[0]["session_key"] == expected_key
    assert gw.interrupts[0]["invalidation_reason"] == "control_new_conversation"
    # ...then the runner's own /new machinery performs the full reset.
    assert len(gw.resets) == 1
    assert gw.resets[0].source.chat_id == CHAT_ID
    # Control envelopes are NEVER dispatched to the agent as messages.
    assert events == []


async def test_control_new_conversation_falls_back_to_session_store() -> None:
    adapter = _make_adapter()
    events, _ = _install_recorders(adapter)
    # No gateway runner wired (adapter._message_handler is None).
    store = FakeSessionStore()
    adapter._session_store = store

    await adapter._handle_control_envelope(_control_envelope("new_conversation"))

    assert store.resets == [_expected_session_key(adapter)]
    assert events == []


async def test_control_new_conversation_without_any_reset_api_is_logged_noop(
    caplog: pytest.LogCaptureFixture,
) -> None:
    adapter = _make_adapter()
    events, _ = _install_recorders(adapter)

    with caplog.at_level("WARNING", logger="chatlytics_hermes.adapter"):
        await adapter._handle_control_envelope(_control_envelope("new_conversation"))

    assert events == []
    assert any(
        "no session-reset API available" in rec.getMessage() for rec in caplog.records
    )


async def test_control_new_conversation_records_waha_session() -> None:
    """Control envelopes keep the chat→WAHA-session map fresh too."""
    adapter = _make_adapter()
    _install_recorders(adapter)
    adapter._session_store = FakeSessionStore()

    await adapter._handle_control_envelope(_control_envelope("new_conversation"))

    assert adapter._resolve_session_for_chat(CHAT_ID) == SESSION_ID


# --- stop ---------------------------------------------------------------------


async def test_control_stop_cancels_via_gateway_runner() -> None:
    adapter = _make_adapter()
    events, _ = _install_recorders(adapter)
    gw = _wire_gateway(adapter)

    await adapter._handle_control_envelope(_control_envelope("stop"))

    assert len(gw.interrupts) == 1
    assert gw.interrupts[0]["session_key"] == _expected_session_key(adapter)
    assert gw.interrupts[0]["invalidation_reason"] == "control_stop"
    # stop must NOT reset the conversation.
    assert gw.resets == []
    assert events == []


async def test_control_stop_without_gateway_is_logged_best_effort(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """No runner reachable → adapter-side interrupt + pending drop, logged."""
    adapter = _make_adapter()
    events, _ = _install_recorders(adapter)
    session_key = _expected_session_key(adapter)
    # Simulate a queued follow-up for the chat's session.
    adapter._pending_messages[session_key] = object()

    with caplog.at_level("WARNING", logger="chatlytics_hermes.adapter"):
        await adapter._handle_control_envelope(_control_envelope("stop"))

    assert session_key not in adapter._pending_messages, (
        "stop fallback must drop the pending queued message for the chat"
    )
    assert any(
        "gateway cancellation API unavailable" in rec.getMessage()
        for rec in caplog.records
    )
    assert events == []


# --- retry_last ----------------------------------------------------------------


async def test_control_retry_last_redispatches_memoized_message() -> None:
    adapter = _make_adapter()
    events, sessions = _install_recorders(adapter)

    # Batch 1: a normal message (populates the memo). Batch 2: retry_last.
    fake = FakeClient(
        adapter,
        get_responses=[
            (200, {"envelopes": [_msg_envelope("retry me")], "cursor": "c1"}),
            (200, {"envelopes": [_control_envelope("retry_last")], "cursor": "c2"}),
        ],
    )
    adapter._client = fake  # type: ignore[assignment]
    adapter._running = True

    await adapter._poll_loop()

    # Original dispatch + the retry re-dispatch, identical text.
    assert [ev.text for ev in events] == ["retry me", "retry me"]
    assert [ev.source.chat_id for ev in events] == [CHAT_ID, CHAT_ID]
    # Session threading happened on both dispatches.
    assert sessions.count((CHAT_ID, SESSION_ID)) >= 2
    # Both batches acked (control envelopes ride the same seq space).
    assert [p["json"]["cursor"] for p in fake.post_calls] == ["c1", "c2"]


async def test_control_retry_last_without_memo_is_ignored(
    caplog: pytest.LogCaptureFixture,
) -> None:
    adapter = _make_adapter()
    events, _ = _install_recorders(adapter)

    with caplog.at_level("INFO", logger="chatlytics_hermes.adapter"):
        await adapter._handle_control_envelope(_control_envelope("retry_last"))

    assert events == [], "retry_last with no memo must not dispatch anything"
    assert any(
        "no memoized last message" in rec.getMessage() for rec in caplog.records
    )


async def test_last_message_memo_is_lru_bounded() -> None:
    adapter = _make_adapter()

    for i in range(_LAST_MESSAGE_MEMO_MAX + 10):
        adapter._remember_last_message(
            _msg_envelope(f"m{i}", entity_jid=f"12036310000000{i:04d}@g.us")
        )

    assert len(adapter._last_message_memo) == _LAST_MESSAGE_MEMO_MAX
    # Oldest entries evicted first (LRU).
    assert "120363100000000000@g.us" not in adapter._last_message_memo
    assert (
        f"12036310000000{_LAST_MESSAGE_MEMO_MAX + 9:04d}@g.us"
        in adapter._last_message_memo
    )


async def test_memo_keeps_only_last_message_per_chat() -> None:
    adapter = _make_adapter()
    adapter._remember_last_message(_msg_envelope("first"))
    adapter._remember_last_message(_msg_envelope("second"))

    assert len(adapter._last_message_memo) == 1
    assert adapter._last_message_memo[CHAT_ID]["text"] == "second"


# --- forward-compat ignores ----------------------------------------------------


async def test_unknown_kind_is_ignored_not_dispatched_and_still_acks(
    caplog: pytest.LogCaptureFixture,
) -> None:
    adapter = _make_adapter()
    events, _ = _install_recorders(adapter)

    fake = FakeClient(
        adapter,
        get_responses=[
            (
                200,
                {
                    "envelopes": [
                        _msg_envelope("real", kind="message"),
                        {**_msg_envelope("phantom"), "kind": "telemetry_v9"},
                    ],
                    "cursor": CURSOR_1,
                },
            )
        ],
    )
    adapter._client = fake  # type: ignore[assignment]
    adapter._running = True

    with caplog.at_level("WARNING", logger="chatlytics_hermes.adapter"):
        await adapter._poll_loop()

    # Only the real message dispatched — the unknown kind NEVER reaches the
    # agent as message text.
    assert [ev.text for ev in events] == ["real"]
    assert any("unknown kind" in rec.getMessage() for rec in caplog.records)
    # Cursor still advances + acks (unknown envelopes ride the seq space).
    assert fake.post_calls and fake.post_calls[0]["json"]["cursor"] == CURSOR_1


async def test_unknown_control_action_is_ignored(
    caplog: pytest.LogCaptureFixture,
) -> None:
    adapter = _make_adapter()
    events, _ = _install_recorders(adapter)
    gw = _wire_gateway(adapter)
    adapter._session_store = FakeSessionStore()

    with caplog.at_level("WARNING", logger="chatlytics_hermes.adapter"):
        await adapter._handle_control_envelope(
            _control_envelope("self_destruct_v2")
        )

    assert events == []
    assert gw.interrupts == [] and gw.resets == []
    assert adapter._session_store.resets == []
    assert any("unknown action" in rec.getMessage() for rec in caplog.records)


async def test_unknown_warnings_dedupe_per_value(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Repeated unknown values warn once (then DEBUG) — no per-envelope spam."""
    adapter = _make_adapter()
    _install_recorders(adapter)

    with caplog.at_level("WARNING", logger="chatlytics_hermes.adapter"):
        for _ in range(5):
            await adapter._dispatch_envelope(
                {**_msg_envelope("x"), "kind": "telemetry_v9"}
            )

    warnings = [
        rec
        for rec in caplog.records
        if rec.levelname == "WARNING" and "unknown kind" in rec.getMessage()
    ]
    assert len(warnings) == 1


async def test_control_envelope_missing_entity_jid_is_ignored(
    caplog: pytest.LogCaptureFixture,
) -> None:
    adapter = _make_adapter()
    events, _ = _install_recorders(adapter)

    with caplog.at_level("WARNING", logger="chatlytics_hermes.adapter"):
        await adapter._handle_control_envelope(
            {"kind": "control", "action": "stop"}
        )

    assert events == []
    assert any(
        "missing entity_jid" in rec.getMessage() for rec in caplog.records
    )
