"""v4.6.0 (MULTIBOT) — one platform instance serving N bots.

Two things are under test, and the second is the one that matters:

1. ``extra_bot_tokens`` config parsing (list / string / env, dedup, shape
   validation) — cheap to get wrong, cheap to pin.

2. **Reply-bearer routing.** Chatlytics resolves the WhatsApp session
   server-side FROM the bot bearer, so a reply sent with the wrong bot's
   token egresses on the wrong WhatsApp account. Every routing test below
   therefore asserts BOTH sides: the right client got the call AND the
   other client got NOTHING. A test that only asserted "the extra client
   was used" would still pass if the code broadcast to every bot, and a
   test that only asserted "a send happened" would pass with the routing
   ripped out entirely.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

import httpx
import pytest

from chatlytics_hermes.adapter import ChatlyticsAdapter, _BotConn
from tests._fixtures import FakePlatformConfig

BASE_URL = "https://gateway.test.chatlytics.ai"
PRIMARY_TOKEN = "sk_bot_" + "A" * 43
EXTRA_TOKEN = "sk_bot_" + "B" * 43
THIRD_TOKEN = "sk_bot_" + "C" * 43

PRIMARY_CHAT = "120363421825201386@g.us"   # served by the primary bot
EXTRA_CHAT = "120363412367458428@g.us"     # served by the extra (proxy) bot
SESSION_PRIMARY = "3cf11776_logan"
SESSION_EXTRA = "3cf11776_omer"


def _make_adapter(**extra_over: Any) -> ChatlyticsAdapter:
    extra: Dict[str, Any] = {
        "base_url": BASE_URL,
        "bot_token": PRIMARY_TOKEN,
        "inbound_mode": "longpoll",
    }
    extra.update(extra_over)
    return ChatlyticsAdapter(FakePlatformConfig(extra=extra))


class RecordingClient:
    """Stand-in for ChatlyticsClient that records every call it receives.

    Every method awaits ``asyncio.sleep(0)`` before doing anything. That is
    NOT decoration: an ``async def`` whose body contains no ``await`` never
    suspends, so a poll loop driving such a fake spins without yielding and
    starves the event loop — which means ``asyncio.wait_for`` timers never
    fire and a routing regression HANGS the suite instead of failing it.
    Measured: a mutation that pointed the poll loop at the shared client was
    caught only by the 180s outer process timeout until these were added.

    ``label`` identifies WHICH bot's client this is, so a routing assertion
    can name the account a call would have egressed on.
    """

    def __init__(self, label: str) -> None:
        self.label = label
        self.base_url = BASE_URL
        self.posts: List[Dict[str, Any]] = []
        self.gets: List[Dict[str, Any]] = []
        self.closed = False

    async def post(self, path: str, *, json: Any = None, timeout: Any = None) -> httpx.Response:
        await asyncio.sleep(0)  # see _YIELD note: fakes must actually suspend
        self.posts.append({"path": path, "json": json or {}})
        return httpx.Response(
            200,
            json={"success": True, "messageId": f"{self.label}-msg-1"},
            request=httpx.Request("POST", BASE_URL + path),
        )

    async def get(self, path: str, *, params: Any = None, timeout: Any = None) -> httpx.Response:
        await asyncio.sleep(0)  # see _YIELD note: fakes must actually suspend
        self.gets.append({"path": path, "params": params or {}})
        return httpx.Response(
            200,
            json={"name": self.label},
            request=httpx.Request("GET", BASE_URL + path),
        )

    async def aclose(self) -> None:
        self.closed = True


def _wire_two_bots(adapter: ChatlyticsAdapter):
    """Put the adapter in the post-connect two-bot state, without I/O.

    Returns ``(primary_client, extra_client, extra_conn)``.
    """
    primary = RecordingClient("primary")
    extra = RecordingClient("extra")

    adapter._client = primary  # type: ignore[assignment]
    adapter._no_credential = False

    pconn = _BotConn(PRIMARY_TOKEN, index=0)
    pconn.client = primary  # type: ignore[assignment]
    pconn.label = "Sammie"
    econn = _BotConn(EXTRA_TOKEN, index=1)
    econn.client = extra  # type: ignore[assignment]
    econn.label = "Sammie (via Omer)"
    adapter._conns = [pconn, econn]
    return primary, extra, econn


# --------------------------------------------------------------------------
# 1. Config parsing
# --------------------------------------------------------------------------


def test_no_extra_tokens_by_default() -> None:
    """A config that never mentions extra bots stays single-bot."""
    adapter = _make_adapter()
    assert adapter.extra_bot_tokens == []
    assert adapter._chat_bot == {}


def test_extra_tokens_from_yaml_list() -> None:
    adapter = _make_adapter(extra_bot_tokens=[EXTRA_TOKEN, THIRD_TOKEN])
    assert adapter.extra_bot_tokens == [EXTRA_TOKEN, THIRD_TOKEN]


def test_extra_tokens_from_single_string() -> None:
    """A YAML scalar (not a list) is accepted — an easy config mistake."""
    adapter = _make_adapter(extra_bot_tokens=EXTRA_TOKEN)
    assert adapter.extra_bot_tokens == [EXTRA_TOKEN]


def test_extra_tokens_from_env_overrides_config(monkeypatch) -> None:
    """Env WINS over config, matching every other setting's precedence."""
    monkeypatch.setenv("CHATLYTICS_EXTRA_BOT_TOKENS", f"{THIRD_TOKEN}, {EXTRA_TOKEN}")
    adapter = _make_adapter(extra_bot_tokens=["sk_bot_" + "Z" * 43])
    assert adapter.extra_bot_tokens == [THIRD_TOKEN, EXTRA_TOKEN]


def test_primary_token_repeated_in_extras_is_dropped() -> None:
    """Two long-polls on ONE token would contend for the same server cursor."""
    adapter = _make_adapter(extra_bot_tokens=[PRIMARY_TOKEN, EXTRA_TOKEN])
    assert adapter.extra_bot_tokens == [EXTRA_TOKEN]


def test_duplicate_extra_token_is_dropped() -> None:
    adapter = _make_adapter(extra_bot_tokens=[EXTRA_TOKEN, EXTRA_TOKEN])
    assert adapter.extra_bot_tokens == [EXTRA_TOKEN]


def test_non_bot_shaped_token_is_rejected(caplog) -> None:
    """A legacy operator api_key has no bot identity to pin a session with."""
    with caplog.at_level(logging.WARNING):
        adapter = _make_adapter(extra_bot_tokens=["not-a-bot-token", EXTRA_TOKEN])
    assert adapter.extra_bot_tokens == [EXTRA_TOKEN]
    assert "does not look like a bot token" in caplog.text
    # The rejection must NEVER echo the token itself.
    assert "not-a-bot-token" not in caplog.text


def test_malformed_extra_tokens_value_degrades_quietly(caplog) -> None:
    """A bad type must not fail gateway boot over an optional feature."""
    with caplog.at_level(logging.WARNING):
        adapter = _make_adapter(extra_bot_tokens={"bad": "shape"})
    assert adapter.extra_bot_tokens == []


def test_extra_token_plaintext_never_logged(caplog) -> None:
    with caplog.at_level(logging.DEBUG):
        adapter = _make_adapter(extra_bot_tokens=[EXTRA_TOKEN, EXTRA_TOKEN])
    assert EXTRA_TOKEN not in caplog.text


# --------------------------------------------------------------------------
# 2. Reply-bearer routing — the correctness heart
# --------------------------------------------------------------------------


async def test_reply_uses_the_bearer_the_message_arrived_on() -> None:
    """A reply to a proxy-bot chat MUST go out on the proxy bot's client.

    Asserts both directions: the extra client sent it AND the primary
    client was not touched. Cross-using the bearer here would egress the
    reply on the wrong WhatsApp account.
    """
    adapter = _make_adapter(extra_bot_tokens=[EXTRA_TOKEN])
    primary, extra, econn = _wire_two_bots(adapter)

    # Inbound arrives on the EXTRA bot's poll loop.
    await adapter._dispatch_envelope(
        {
            "session_id": SESSION_EXTRA,
            "chat_type": "group",
            "entity_jid": EXTRA_CHAT,
            "sender_jid": "972544329000@c.us",
            "text": "!sammie ping multiplex-test",
            "dispatch": {"reason": "trigger-single", "god_mode": False},
            "ts": 1700000000,
        },
        econn,
    )

    result = await adapter.send(EXTRA_CHAT, "pong from the proxy connection")

    assert result.success is True
    assert [p["path"] for p in extra.posts] == ["/api/v1/send"]
    assert extra.posts[0]["json"]["chatId"] == EXTRA_CHAT
    # NEGATIVE CONTROL: the primary bot's connection must be untouched.
    assert primary.posts == [], (
        "reply leaked onto the PRIMARY bot's bearer — it would have egressed "
        "on the wrong WhatsApp account"
    )


async def test_reply_to_primary_chat_still_uses_the_primary_bearer() -> None:
    """Regression leg: adding an extra bot must not steal the primary's chats."""
    adapter = _make_adapter(extra_bot_tokens=[EXTRA_TOKEN])
    primary, extra, econn = _wire_two_bots(adapter)
    pconn = adapter._conns[0]

    await adapter._dispatch_envelope(
        {
            "session_id": SESSION_PRIMARY,
            "chat_type": "group",
            "entity_jid": PRIMARY_CHAT,
            "sender_jid": "972544329000@c.us",
            "text": "!sammie ping regression",
            "dispatch": {"reason": "trigger-single", "god_mode": False},
            "ts": 1700000000,
        },
        pconn,
    )

    await adapter.send(PRIMARY_CHAT, "pong from Sammie's own connection")

    assert [p["path"] for p in primary.posts] == ["/api/v1/send"]
    assert extra.posts == []


async def test_two_chats_route_to_their_own_bots_concurrently() -> None:
    """Both bindings coexist — this is the whole point of the feature."""
    adapter = _make_adapter(extra_bot_tokens=[EXTRA_TOKEN])
    primary, extra, econn = _wire_two_bots(adapter)
    pconn = adapter._conns[0]

    for chat, session, conn in (
        (PRIMARY_CHAT, SESSION_PRIMARY, pconn),
        (EXTRA_CHAT, SESSION_EXTRA, econn),
    ):
        await adapter._dispatch_envelope(
            {
                "session_id": session,
                "chat_type": "group",
                "entity_jid": chat,
                "sender_jid": "972544329000@c.us",
                "text": "hi",
                "dispatch": {"reason": "trigger-single", "god_mode": False},
                "ts": 1700000000,
            },
            conn,
        )

    await adapter.send(PRIMARY_CHAT, "a")
    await adapter.send(EXTRA_CHAT, "b")

    assert [p["json"]["chatId"] for p in primary.posts] == [PRIMARY_CHAT]
    assert [p["json"]["chatId"] for p in extra.posts] == [EXTRA_CHAT]


async def test_unbound_chat_falls_back_to_primary() -> None:
    """Tool-initiated / cron sends to a never-seen chat keep old behavior."""
    adapter = _make_adapter(extra_bot_tokens=[EXTRA_TOKEN])
    primary, extra, _ = _wire_two_bots(adapter)

    await adapter.send("972500000000@c.us", "cron notification")

    assert len(primary.posts) == 1
    assert extra.posts == []


async def test_typing_and_media_follow_the_same_binding() -> None:
    """Presence and media are chat-scoped too — they must not split accounts."""
    adapter = _make_adapter(extra_bot_tokens=[EXTRA_TOKEN])
    primary, extra, econn = _wire_two_bots(adapter)
    adapter._bind_chat_bot(EXTRA_CHAT, econn)

    await adapter._send_typing_once(EXTRA_CHAT)
    await adapter.send_image(EXTRA_CHAT, "https://example.test/cat.png")

    paths = [p["path"] for p in extra.posts]
    assert "/api/v1/typing" in paths
    assert "/api/v1/send" in paths
    assert primary.posts == []


async def test_control_envelope_also_binds_the_chat() -> None:
    """/new, /stop, /retry reply in-chat — they must bind like messages do."""
    adapter = _make_adapter(extra_bot_tokens=[EXTRA_TOKEN])
    primary, extra, econn = _wire_two_bots(adapter)

    # An unknown control action is ignored for dispatch, but the BINDING
    # happens before the kind branch — that is what this pins.
    await adapter._dispatch_envelope(
        {
            "kind": "control",
            "action": "some-future-action",
            "session_id": SESSION_EXTRA,
            "chat_type": "group",
            "entity_jid": EXTRA_CHAT,
            "sender_jid": "972544329000@c.us",
            "text": "/future",
            "ts": 1700000000,
        },
        econn,
    )

    assert adapter._chat_bot.get(EXTRA_CHAT) is econn
    await adapter.send(EXTRA_CHAT, "control reply")
    assert len(extra.posts) == 1
    assert primary.posts == []


async def test_rebinding_a_chat_moves_the_reply_bearer() -> None:
    """A chat both bots can see follows the MOST RECENT inbound."""
    adapter = _make_adapter(extra_bot_tokens=[EXTRA_TOKEN])
    primary, extra, econn = _wire_two_bots(adapter)
    pconn = adapter._conns[0]

    adapter._bind_chat_bot(EXTRA_CHAT, econn)
    adapter._bind_chat_bot(EXTRA_CHAT, pconn)

    await adapter.send(EXTRA_CHAT, "now on the primary")
    assert len(primary.posts) == 1
    assert extra.posts == []


def test_chat_binding_map_is_bounded() -> None:
    """A long-lived gateway must not grow this map without limit."""
    from chatlytics_hermes.adapter import _CHAT_BOT_BINDING_MAX

    adapter = _make_adapter()
    conn = _BotConn(EXTRA_TOKEN, index=1)
    for i in range(_CHAT_BOT_BINDING_MAX + 50):
        adapter._bind_chat_bot(f"chat-{i}@g.us", conn)
    assert len(adapter._chat_bot) == _CHAT_BOT_BINDING_MAX
    # Oldest evicted, newest retained.
    assert "chat-0@g.us" not in adapter._chat_bot
    assert f"chat-{_CHAT_BOT_BINDING_MAX + 49}@g.us" in adapter._chat_bot


async def test_evicted_binding_falls_back_to_primary_not_to_a_stale_bot() -> None:
    """LRU eviction must degrade to the primary, never to a wrong bot."""
    from chatlytics_hermes.adapter import _CHAT_BOT_BINDING_MAX

    adapter = _make_adapter(extra_bot_tokens=[EXTRA_TOKEN])
    primary, extra, econn = _wire_two_bots(adapter)
    adapter._bind_chat_bot(EXTRA_CHAT, econn)
    for i in range(_CHAT_BOT_BINDING_MAX + 5):
        adapter._bind_chat_bot(f"filler-{i}@g.us", econn)

    assert EXTRA_CHAT not in adapter._chat_bot
    await adapter.send(EXTRA_CHAT, "after eviction")
    assert len(primary.posts) == 1


# --------------------------------------------------------------------------
# 3. Failure isolation + lifecycle
# --------------------------------------------------------------------------


async def test_rejected_extra_token_is_not_served_and_primary_survives(caplog) -> None:
    """A 401 on /api/v1/bot/me means don't start that loop — and only that one."""
    adapter = _make_adapter(extra_bot_tokens=[EXTRA_TOKEN])
    primary, extra, econn = _wire_two_bots(adapter)
    adapter._conns = [adapter._conns[0]]  # _start_extra_bots rebuilds #1
    adapter._running = True

    class _RejectingClient(RecordingClient):
        async def get(self, path: str, *, params: Any = None, timeout: Any = None):
            self.gets.append({"path": path})
            return httpx.Response(
                401,
                json={"error": "bot_token_required"},
                request=httpx.Request("GET", BASE_URL + path),
            )

    import chatlytics_hermes.adapter as adapter_mod

    monkey = _RejectingClient("rejected")
    orig = adapter_mod.ChatlyticsClient
    adapter_mod.ChatlyticsClient = lambda **kw: monkey  # type: ignore[assignment]
    try:
        with caplog.at_level(logging.ERROR):
            await adapter._start_extra_bots()
    finally:
        adapter_mod.ChatlyticsClient = orig  # type: ignore[assignment]

    rejected = adapter._conns[1]
    assert rejected.task is None, "a rejected bot must not get a poll loop"
    assert "REJECTED" in caplog.text
    assert EXTRA_TOKEN not in caplog.text  # fingerprint only
    # The primary is untouched.
    assert adapter._conns[0].client is primary


async def test_extra_bot_start_failure_does_not_break_the_primary(caplog) -> None:
    adapter = _make_adapter(extra_bot_tokens=[EXTRA_TOKEN])
    primary, _extra, _ = _wire_two_bots(adapter)
    adapter._conns = [adapter._conns[0]]
    adapter._running = True

    import chatlytics_hermes.adapter as adapter_mod

    def _boom(**kw):
        raise RuntimeError("client construction exploded")

    orig = adapter_mod.ChatlyticsClient
    adapter_mod.ChatlyticsClient = _boom  # type: ignore[assignment]
    try:
        with caplog.at_level(logging.ERROR):
            await adapter._start_extra_bots()  # must NOT raise
    finally:
        adapter_mod.ChatlyticsClient = orig  # type: ignore[assignment]

    assert "failed to start" in caplog.text
    assert adapter._conns[0].client is primary
    # And the primary can still send.
    await adapter.send(PRIMARY_CHAT, "still alive")
    assert len(primary.posts) == 1


async def test_extra_bots_refused_in_webhook_mode(caplog) -> None:
    """Webhook PUSH has one server-registered URL — there is no fan-in to mux."""
    adapter = _make_adapter(inbound_mode="webhook", extra_bot_tokens=[EXTRA_TOKEN])
    with caplog.at_level(logging.ERROR):
        await adapter._start_extra_bots()
    assert "served ONLY in longpoll mode" in caplog.text
    assert len(adapter._conns) == 0


async def test_poll_loops_have_independent_backoff_state() -> None:
    """One bot's degraded loop must not slow or silence another's.

    Drives the extra bot's loop against a client that only ever errors and
    asserts the primary's loop still processes a normal batch — the failure
    isolation the design promises, exercised rather than asserted.
    """
    adapter = _make_adapter(extra_bot_tokens=[EXTRA_TOKEN])
    _primary, _extra, econn = _wire_two_bots(adapter)
    pconn = adapter._conns[0]
    delivered: List[Any] = []

    async def _recorder(event: Any) -> None:
        delivered.append(event)

    adapter.handle_message = _recorder  # type: ignore[assignment]
    adapter._running = True

    class _AlwaysFailing:
        base_url = BASE_URL

        async def get(self, path: str, *, params: Any = None, timeout: Any = None):
            await asyncio.sleep(0)
            raise httpx.ConnectError("refused")

        async def post(self, path: str, *, json: Any = None, timeout: Any = None):
            raise httpx.ConnectError("refused")

        async def aclose(self) -> None:
            return None

    class _HealthyOnce:
        base_url = BASE_URL

        def __init__(self) -> None:
            self.served = False

        async def get(self, path: str, *, params: Any = None, timeout: Any = None):
            await asyncio.sleep(0)
            if not self.served:
                self.served = True
                body = {
                    "envelopes": [
                        {
                            "session_id": SESSION_PRIMARY,
                            "chat_type": "group",
                            "entity_jid": PRIMARY_CHAT,
                            "sender_jid": "972544329000@c.us",
                            "text": "primary still works",
                            "dispatch": {"reason": "trigger-single", "god_mode": False},
                            "ts": 1700000000,
                        }
                    ],
                    "cursor": "c1",
                }
            else:
                adapter._running = False
                body = {"envelopes": [], "cursor": "c1"}
            return httpx.Response(
                200, json=body, request=httpx.Request("GET", BASE_URL + path)
            )

        async def post(self, path: str, *, json: Any = None, timeout: Any = None):
            return httpx.Response(
                200, json={"acked": 1}, request=httpx.Request("POST", BASE_URL + path)
            )

        async def aclose(self) -> None:
            return None

    econn.client = _AlwaysFailing()  # type: ignore[assignment]
    pconn.client = _HealthyOnce()  # type: ignore[assignment]

    failing = asyncio.create_task(adapter._poll_loop(econn))
    try:
        # HARD BOUND, deliberately: the healthy loop terminates itself by
        # flipping _running once its scripted batches are consumed. If a
        # regression makes this loop read the SHARED client instead of its
        # own conn's, it never sees those batches, never stops, and the test
        # would HANG rather than fail. A hang is a useless detector — this
        # wait_for turns that regression into a fast, named failure.
        await asyncio.wait_for(adapter._poll_loop(pconn), timeout=5)
    except asyncio.TimeoutError:  # pragma: no cover - regression guard
        adapter._running = False
        pytest.fail(
            "the healthy bot's poll loop never consumed its own scripted "
            "batches — it is reading a client other than its own _BotConn's"
        )
    finally:
        failing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await failing

    assert len(delivered) == 1, "the healthy bot's inbound was blocked by the sick one"


async def test_disconnect_closes_every_extra_client_and_clears_bindings() -> None:
    adapter = _make_adapter(extra_bot_tokens=[EXTRA_TOKEN])
    primary, extra, econn = _wire_two_bots(adapter)
    adapter._bind_chat_bot(EXTRA_CHAT, econn)
    adapter._running = True

    await adapter.disconnect()

    assert extra.closed is True
    assert primary.closed is True
    assert adapter._chat_bot == {}
    assert adapter._conns[1].client is None
    assert adapter._conns[0].client is None


def test_bot_conn_repr_and_label_never_expose_the_token() -> None:
    conn = _BotConn(EXTRA_TOKEN, index=1)
    assert EXTRA_TOKEN not in repr(conn)
    assert EXTRA_TOKEN not in conn.label
    assert len(conn.fp) == 8
    assert conn.is_primary is False
    assert _BotConn(PRIMARY_TOKEN, index=0).is_primary is True
