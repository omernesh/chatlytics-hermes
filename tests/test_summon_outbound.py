"""chatlytics#956: which account a send leaves from, and the other fixes of the same review.

``_Hub`` answers the way the hub's ``resolveBotSendSession`` does (src/from-me-bot.ts), so these
tests read the account a message LEFT from, not only the body. Fake ids only.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import re
import sys
import time
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

import pytest

from chatlytics_hermes import read_scope
from chatlytics_hermes.adapter import _BotConn, _make_tool_handler
from chatlytics_hermes.read_scope import pre_tool_call
from chatlytics_hermes.tools import (
    chatlytics_delete,
    chatlytics_dispatch,
    chatlytics_edit,
    chatlytics_pin,
    chatlytics_poll,
    chatlytics_react,
    chatlytics_send,
    chatlytics_unpin,
    chatlytics_unsend,
)
from tests.test_owner_tagging import BOT_TOKEN, _install_recorders, _lp_adapter
from tests.test_trigger_context import ENVELOPES, OWNER, OWNER_SESSION, PEER

BOT_SESSION = ENVELOPES["third_party_dm"]["session_id"]
GROUP = ENVELOPES["from_me_group"]["entity_jid"]
LM = "mcp__lextrove__list_messages"


@pytest.fixture(autouse=True)
def _no_leftover_scopes() -> Any:
    yield
    read_scope._bound.clear()
    read_scope._scopes.clear()


class _Resp:
    def __init__(self, status: int, payload: Dict[str, Any]) -> None:
        self.status_code = status
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self) -> Dict[str, Any]:
        return self._payload


class _Hub:
    """resolveBotSendSession for one bot, bound to one owner's session."""

    def __init__(self, own: str = BOT_SESSION, bound: str = OWNER_SESSION) -> None:
        self.own, self.bound = own, bound
        self.routes: Dict[str, str] = {}
        self.bodies: List[Tuple[str, Dict[str, Any]]] = []
        self.left: List[Tuple[str, str, str]] = []  # (session it left from, chat, text)
        self.refused: List[Tuple[str, str]] = []

    def _verdict(self, requested: Optional[str], chat: str) -> Optional[str]:
        if not requested:
            routed = self.routes.get(chat)
            return routed if routed and routed != self.own else self.own
        if requested == self.own:
            return self.own
        if self.routes.get(chat) == requested:
            return requested
        return None if requested == self.bound else self.own

    async def post(self, path: str, json: Dict[str, Any], timeout: Any = None) -> _Resp:  # noqa: A002
        self.bodies.append((path, dict(json)))
        chat = json.get("chatId") or (json.get("params") or {}).get("chatId") or ""
        session = self._verdict(json.get("session"), chat)
        if session is None:
            self.refused.append((path, chat))
            return _Resp(409, {"success": False, "error": "from_me_route_expired", "code": "from_me_route_expired",
                               "message": "Nothing was sent."})
        if path == "/api/v1/send":
            self.left.append((session, chat, json.get("text") or json.get("type") or ""))
        return _Resp(200, {"success": True, "message_id": "m1", "session_used": session})

    async def get(self, _path: str, **_: Any) -> _Resp:
        return _Resp(200, {"session_id": self.own, "display_name": "bot"})


def _adapter(hub: Optional[_Hub] = None, own: Optional[str] = BOT_SESSION) -> Tuple[Any, _Hub, _BotConn]:
    hub = hub or _Hub()
    adapter = _lp_adapter()
    conn = _BotConn(BOT_TOKEN, index=0)
    conn.client, conn.own_session = hub, own
    conn.own_session_confirmed = own is not None  # what GET /api/v1/bot/me leaves at boot
    adapter._conns, adapter._client = [conn], hub
    adapter._no_credential = False
    return adapter, hub, conn


async def _deliver(adapter: Any, env: Dict[str, Any], conn: Optional[_BotConn] = None, **kw: Any) -> Any:
    events, _ = _install_recorders(adapter)
    await adapter._dispatch_envelope(dict(env), conn, **kw)
    return events[0] if events else None


def _turn(event: Any, *, final: bool = True) -> Optional[Dict[str, Any]]:
    """What the host's _thread_metadata_for_event and _mark_notify_metadata make of an event."""
    meta: Dict[str, Any] = {}
    if event.source.thread_id is not None:
        meta["thread_id"] = event.source.thread_id
    if final:
        meta["notify"] = True
    return meta or None


# --- finding 1: wrong account --------------------------------------------------------------------


async def test_scenario_a_an_ordinary_reply_during_a_summon_leaves_from_the_bot() -> None:
    adapter, hub, conn = _adapter()
    ordinary = await _deliver(adapter, ENVELOPES["third_party_dm"], conn)
    summon = await _deliver(adapter, ENVELOPES["from_me_dm"], conn)
    hub.routes[PEER] = OWNER_SESSION  # delivered, so the hub opened the route

    assert (await adapter.send(PEER, "ordinary", metadata=_turn(ordinary))).success
    assert (await adapter.send(PEER, "summon", metadata=_turn(summon))).success
    assert hub.left == [(BOT_SESSION, PEER, "ordinary"), (OWNER_SESSION, PEER, "summon")]


async def test_scenario_b_a_summon_reply_after_the_route_closed_never_leaves_from_the_bot(caplog: Any) -> None:
    adapter, hub, conn = _adapter()
    summon = await _deliver(adapter, ENVELOPES["from_me_dm"], conn)
    hub.routes[PEER] = OWNER_SESSION
    ordinary = await _deliver(adapter, ENVELOPES["third_party_dm"], conn)
    hub.routes.pop(PEER)  # a native envelope closes the route

    with caplog.at_level(logging.ERROR, logger="chatlytics_hermes"):
        result = await adapter.send(PEER, "the answer", metadata=_turn(summon))
    # Nothing reached the chat, from either number, and the owner has the text.
    assert [left for left in hub.left if left[1] == PEER] == []
    assert hub.refused == [("/api/v1/send", PEER)]
    assert [(s, c) for s, c, _ in hub.left] == [(BOT_SESSION, OWNER)]
    assert "the answer" in hub.left[0][2]
    assert result.success
    assert "summon reply not posted" in caplog.text

    assert (await adapter.send(PEER, "ordinary", metadata=_turn(ordinary))).success
    assert hub.left[-1] == (BOT_SESSION, PEER, "ordinary")


async def test_a_closed_route_through_the_hosts_retry_loop_tells_the_owner_once() -> None:
    adapter, hub, conn = _adapter()
    if not hasattr(adapter, "_send_with_retry"):
        pytest.skip("host without the retry loop")
    summon = await _deliver(adapter, ENVELOPES["from_me_dm"], conn)
    result = await adapter._send_with_retry(PEER, "the answer", metadata=_turn(summon))
    assert result.success
    assert len(hub.refused) == 1 and [c for _, c, _ in hub.left] == [OWNER]


async def test_a_refused_owner_dm_stays_a_final_failure_and_retries_nothing() -> None:
    adapter, hub, conn = _adapter()
    if not hasattr(adapter, "_send_with_retry"):
        pytest.skip("host without the retry loop")
    summon = await _deliver(adapter, ENVELOPES["from_me_dm"], conn)
    real_post = hub.post

    async def refuse_owner(path: str, json: Dict[str, Any], timeout: Any = None) -> _Resp:  # noqa: A002
        if json.get("chatId") == OWNER:
            hub.bodies.append((path, dict(json)))
            return _Resp(403, {"success": False, "error": "not paired"})
        return await real_post(path, json, timeout)

    hub.post = refuse_owner  # type: ignore[method-assign]
    result = await adapter._send_with_retry(PEER, "the answer", metadata=_turn(summon))
    assert not result.success and adapter._send_retry_is_final(result)
    assert len(hub.refused) == 1 and hub.left == []


async def test_progress_and_notices_of_a_summon_do_not_go_to_the_owner() -> None:
    adapter, hub, conn = _adapter()
    summon = await _deliver(adapter, ENVELOPES["from_me_dm"], conn)
    result = await adapter.send(PEER, "working", metadata=_turn(summon, final=False))
    assert not result.success and hub.left == []


@pytest.mark.parametrize("sender", [PEER, "271000000000000@lid"])
async def test_a_dm_whose_sender_may_be_the_peer_never_gets_the_owners_reply(sender: str) -> None:
    adapter, hub, conn = _adapter()
    summon = await _deliver(adapter, {**ENVELOPES["from_me_dm"], "sender_jid": sender}, conn)
    result = await adapter.send(PEER, "the answer", metadata=_turn(summon))
    assert not result.success and hub.left == []


async def test_a_group_summon_after_a_member_triggered_the_bot_goes_to_the_owner() -> None:
    adapter, hub, conn = _adapter()
    summon = await _deliver(adapter, ENVELOPES["from_me_group"], conn)
    await adapter.send(GROUP, "the answer", metadata=_turn(summon))
    assert hub.refused == [("/api/v1/send", GROUP)]
    assert [(s, c) for s, c, _ in hub.left] == [(BOT_SESSION, ENVELOPES["from_me_group"]["sender_jid"])]


async def test_typing_and_the_bubble_name_the_turns_account() -> None:
    adapter, hub, conn = _adapter()
    ordinary = await _deliver(adapter, ENVELOPES["third_party_dm"], conn)
    summon = await _deliver(adapter, ENVELOPES["from_me_dm"], conn)
    hub.routes[PEER] = OWNER_SESSION

    await adapter.send_typing(PEER, metadata=_turn(summon, final=False))
    await adapter.send_typing(PEER, metadata=_turn(ordinary, final=False))
    assert [b["session"] for p, b in hub.bodies if p == "/api/v1/typing"] == [OWNER_SESSION, BOT_SESSION]

    await adapter._send_progress_bubble(PEER, "t1", _turn(summon, final=False))
    assert hub.left[-1][0] == OWNER_SESSION
    adapter._progress_bubbles.clear()
    await adapter._send_progress_bubble(PEER, "t2", _turn(ordinary, final=False))
    assert hub.left[-1][0] == BOT_SESSION


async def test_the_typing_loop_names_the_turns_account() -> None:
    adapter, hub, conn = _adapter()
    summon = await _deliver(adapter, ENVELOPES["from_me_dm"], conn)
    hub.routes[PEER] = OWNER_SESSION
    stop = asyncio.Event()
    stop.set()
    await adapter._keep_typing(PEER, interval=0.001, metadata=_turn(summon, final=False), stop_event=stop)
    typing = [b for p, b in hub.bodies if p == "/api/v1/typing"]
    assert typing and all(b["session"] == OWNER_SESSION for b in typing)


async def test_media_names_the_turns_account(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, hub, conn = _adapter()

    async def file_field(*_a: Any, **_k: Any) -> Dict[str, Any]:
        return {"url": "https://example.test/a.png"}

    monkeypatch.setattr(adapter, "_resolve_media_file_field", file_field)
    ordinary = await _deliver(adapter, ENVELOPES["third_party_dm"], conn)
    summon = await _deliver(adapter, ENVELOPES["from_me_dm"], conn)
    hub.routes[PEER] = OWNER_SESSION
    await adapter.send_image(PEER, "https://example.test/a.png", metadata=_turn(summon))
    await adapter.send_image(PEER, "https://example.test/a.png", metadata=_turn(ordinary))
    await adapter.send_document(PEER, "https://example.test/a.pdf", metadata=_turn(summon))
    assert [s for s, _, _ in hub.left] == [OWNER_SESSION, BOT_SESSION, OWNER_SESSION]


async def test_the_scope_key_does_not_reach_the_hub() -> None:
    adapter, hub, conn = _adapter()
    summon = await _deliver(adapter, ENVELOPES["from_me_dm"], conn)
    hub.routes[PEER] = OWNER_SESSION
    await adapter.send(PEER, "x", metadata=_turn(summon))
    assert "thread_id" not in hub.bodies[-1][1]
    assert hub.bodies[-1][1]["notify"] is True  # what ordinary sends always carried


async def test_a_thread_id_that_is_not_a_scope_key_still_passes_through() -> None:
    adapter, hub, _ = _adapter()
    await adapter.send(PEER, "x", metadata={"thread_id": "123"})
    assert hub.bodies[-1][1]["thread_id"] == "123"


async def test_a_summon_this_process_has_no_scope_for_sends_nothing(caplog: Any) -> None:
    adapter, hub, conn = _adapter()
    summon = await _deliver(adapter, ENVELOPES["from_me_dm"], conn)
    read_scope._scopes.clear()
    with caplog.at_level(logging.ERROR, logger="chatlytics_hermes"):
        result = await adapter.send(PEER, "x", metadata=_turn(summon))
    assert not result.success and hub.bodies == []
    assert adapter._send_retry_is_final(result)
    assert "summon_scope_lost" in caplog.text


# --- ordinary turns stay as they were ------------------------------------------------------------


async def test_an_ordinary_send_with_no_route_leaves_from_where_it_always_did() -> None:
    adapter, hub, conn = _adapter()
    ordinary = await _deliver(adapter, ENVELOPES["third_party_dm"], conn)
    await adapter.send(PEER, "x", metadata=_turn(ordinary))
    assert hub.left == [(BOT_SESSION, PEER, "x")]
    # Named or not, the hub's verdict with no route is the same one.
    assert hub._verdict(None, PEER) == hub._verdict(BOT_SESSION, PEER) == BOT_SESSION


async def test_with_no_known_own_session_an_ordinary_body_is_what_it_was() -> None:
    adapter, hub, _ = _adapter(own=None)
    await adapter.send(PEER, "x")
    assert "session" not in hub.bodies[0][1]
    await adapter.send_typing(PEER)
    assert hub.bodies[1] == ("/api/v1/typing", {"chatId": PEER, "duration": 3.0})


async def test_an_ordinary_envelope_teaches_the_connection_its_session() -> None:
    adapter, hub, conn = _adapter(own=None)
    await _deliver(adapter, ENVELOPES["third_party_dm"], conn)
    assert conn.own_session == BOT_SESSION
    # A summon's session is the owner's and teaches nothing.
    conn.own_session = None
    conn.client = SimpleNamespace(get=None)  # the probe below fails, and that doesn't lose the summon
    assert await _deliver(adapter, ENVELOPES["from_me_dm"], conn) is not None
    assert conn.own_session is None


async def test_a_summon_learns_the_bots_session_when_the_boot_probe_failed() -> None:
    adapter, hub, conn = _adapter(own=None)
    await _deliver(adapter, ENVELOPES["from_me_dm"], conn)
    assert conn.own_session == BOT_SESSION


async def test_a_proxy_turn_on_the_owners_number_stays_there() -> None:
    """A bot whose OWN session is the owner's: not a summon, no flag, its own number."""
    hub = _Hub(own=OWNER_SESSION, bound="unbound")
    adapter, hub, conn = _adapter(hub, own=None)
    event = await _deliver(adapter, {**ENVELOPES["third_party_dm"], "session_id": OWNER_SESSION}, conn)
    assert event.source.thread_id is None
    await adapter.send(PEER, "x", metadata=_turn(event))
    assert hub.left == [(OWNER_SESSION, PEER, "x")]


async def test_a_summon_does_not_move_the_chats_other_sends_to_its_bot() -> None:
    adapter, hub, conn = _adapter()
    other_hub = _Hub()
    other = _BotConn("sk_bot_" + "b" * 43, index=1)
    other.client, other.own_session = other_hub, "other-session"
    adapter._conns.append(other)

    ordinary = await _deliver(adapter, ENVELOPES["third_party_dm"], conn)
    summon = await _deliver(adapter, ENVELOPES["from_me_dm"], other)
    other_hub.routes[PEER] = OWNER_SESSION
    assert adapter._conn_for_chat(PEER) is conn
    await adapter.send(PEER, "ordinary", metadata=_turn(ordinary))
    await adapter.send(PEER, "summon", metadata=_turn(summon))
    assert hub.left == [(BOT_SESSION, PEER, "ordinary")]
    assert other_hub.left == [(OWNER_SESSION, PEER, "summon")]


async def test_the_owners_dm_uses_the_summons_bot_on_its_own_session() -> None:
    adapter, hub, conn = _adapter()
    other_hub = _Hub(own="other-session")
    other = _BotConn("sk_bot_" + "b" * 43, index=1)
    other.client, other.own_session = other_hub, "other-session"
    adapter._conns.append(other)
    await _deliver(adapter, ENVELOPES["third_party_dm"], conn)
    summon = await _deliver(adapter, ENVELOPES["from_me_dm"], other)
    assert (await adapter.send(PEER, "the answer", metadata=_turn(summon))).success
    assert [(s, c) for s, c, _ in other_hub.left] == [("other-session", OWNER)]
    assert other_hub.bodies[-1][1]["session"] == "other-session"
    assert hub.left == []


async def test_on_the_legacy_path_a_summon_does_not_change_the_ordinary_session() -> None:
    adapter = _lp_adapter(api_key="legacy-operator-key", bot_token="")
    hub = _Hub()
    adapter._auth_token, adapter._client, adapter._no_credential = "legacy-operator-key", hub, False
    ordinary = await _deliver(adapter, ENVELOPES["third_party_dm"])
    summon = await _deliver(adapter, ENVELOPES["from_me_dm"])
    await adapter.send(PEER, "ordinary", metadata=_turn(ordinary))
    await adapter.send(PEER, "summon", metadata=_turn(summon))
    assert [b["session"] for _, b in hub.bodies] == [BOT_SESSION, OWNER_SESSION]


# --- tools ---------------------------------------------------------------------------------------


def _bound_tool(adapter: Any, handler: Any, name: str) -> Any:
    return _make_tool_handler(SimpleNamespace(get_platform=lambda _n: SimpleNamespace(adapter=adapter)), name, handler)


def _in_turn(thread_id: str, coro_fn: Any) -> Any:
    from gateway import session_context as sc

    async def run() -> Any:
        sc.set_session_vars(platform="chatlytics", thread_id=thread_id)
        return await coro_fn()

    return contextvars.copy_context().run(lambda: run())


async def test_a_send_tool_names_the_turns_account() -> None:
    adapter, hub, conn = _adapter()
    summon = await _deliver(adapter, ENVELOPES["from_me_dm"], conn)
    hub.routes[PEER] = OWNER_SESSION
    tool = _bound_tool(adapter, chatlytics_send, "chatlytics_send")

    await _in_turn(summon.source.thread_id, lambda: tool({"chatId": PEER, "text": "summon"}))
    await _in_turn("", lambda: tool({"chatId": PEER, "text": "ordinary"}))
    await _in_turn(summon.source.thread_id, lambda: tool({"chatId": "999@c.us", "text": "elsewhere"}))
    assert hub.left == [(OWNER_SESSION, PEER, "summon"), (BOT_SESSION, PEER, "ordinary"),
                        (BOT_SESSION, "999@c.us", "elsewhere")]


async def test_a_send_tool_finds_the_summon_by_task_id() -> None:
    adapter, hub, conn = _adapter()
    summon = await _deliver(adapter, ENVELOPES["from_me_dm"], conn)
    hub.routes[PEER] = OWNER_SESSION
    read_scope.note_request(True, f"x:{summon.source.thread_id}", "s1", "task-1")
    tool = _bound_tool(adapter, chatlytics_send, "chatlytics_send")
    await tool({"chatId": PEER, "text": "x"}, task_id="task-1")
    assert hub.left == [(OWNER_SESSION, PEER, "x")]


async def test_a_tool_of_a_summon_with_no_scope_sends_nothing() -> None:
    adapter, hub, conn = _adapter()
    tool = _bound_tool(adapter, chatlytics_send, "chatlytics_send")
    out = await _in_turn(read_scope.LOST_KEY, lambda: tool({"chatId": PEER, "text": "x"}))
    assert json.loads(out)["success"] is False and hub.bodies == []


async def test_a_reaction_names_the_account_and_other_actions_do_not() -> None:
    adapter, hub, conn = _adapter()
    summon = await _deliver(adapter, ENVELOPES["from_me_dm"], conn)
    hub.routes[PEER] = OWNER_SESSION
    tool = _bound_tool(adapter, chatlytics_react, "chatlytics_react")
    await _in_turn(summon.source.thread_id, lambda: tool({"messageId": "true_x_y", "emoji": "ok", "chatId": PEER}))
    assert hub.bodies[-1][1]["session"] == OWNER_SESSION
    await _in_turn(summon.source.thread_id, lambda: tool({"messageId": "true_x_y", "emoji": "ok"}))
    assert "session" not in hub.bodies[-1][1]  # With no chat the hub can only refuse a named session.


async def test_a_handler_called_directly_sends_what_it_always_sent() -> None:
    hub = _Hub()
    await chatlytics_send(hub, chatId=PEER, text="x")  # type: ignore[arg-type]
    assert hub.bodies == [("/api/v1/send", {"chatId": PEER, "text": "x"})]


async def test_the_hosts_send_message_path_finds_the_summon_by_context() -> None:
    """tools/send_message_tool calls adapter.send with no thread id, inside the turn's context."""
    adapter, hub, conn = _adapter()
    summon = await _deliver(adapter, ENVELOPES["from_me_dm"], conn)
    hub.routes[PEER] = OWNER_SESSION
    await _in_turn(summon.source.thread_id, lambda: adapter.send(chat_id=PEER, content="x", metadata=None))
    assert hub.left == [(OWNER_SESSION, PEER, "x")]


# --- finding 2: a key with no scope --------------------------------------------------------------


def _hook_in(thread_id: str, args: Any, ids: str = "resumed") -> Any:
    from gateway import session_context as sc

    def run() -> Any:
        sc.set_session_vars(platform="chatlytics", thread_id=thread_id)
        return pre_tool_call(tool_name=LM, args=args, session_id=ids, task_id=ids)

    return contextvars.copy_context().run(run)


def test_a_thread_id_in_the_key_format_with_no_scope_reads_nothing() -> None:
    verdict = _hook_in("summon-00000000000000ab", {"chat_id": "999@c.us", "session": "all"})
    assert verdict is not None and "no_scope_for_this_turn" in verdict["message"]


async def test_an_evicted_scope_reads_nothing() -> None:
    adapter, _, conn = _adapter()
    summon = await _deliver(adapter, ENVELOPES["from_me_dm"], conn)
    read_scope._scopes.pop(summon.source.thread_id)
    assert _hook_in(summon.source.thread_id, {"session": "all"}, "child") is not None


@pytest.mark.parametrize("thread_id", ["", "123", "summon-short", "summon-0000000000000000x"])
def test_any_other_thread_id_is_an_ordinary_turn(thread_id: str) -> None:
    assert _hook_in(thread_id, {"session": "all"}) is None


# --- finding 3: /stop and /new -------------------------------------------------------------------


def _control(action: str, session: str, chat: str = PEER) -> Dict[str, Any]:
    return {"kind": "control", "action": action, "session_id": session, "chat_type": "dm",
            "entity_jid": chat, "sender_jid": OWNER, "ts": 1}


class _Gateway:
    def __init__(self) -> None:
        self.stopped: List[str] = []

    async def _interrupt_and_clear_session(self, session_key: str, _source: Any, **_: Any) -> None:
        self.stopped.append(session_key)

    async def _handle_reset_command(self, _event: Any) -> str:
        return ""


@pytest.mark.parametrize("action", ["stop", "new_conversation"])
async def test_control_reaches_every_summon_of_the_chat(action: str, monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, _, conn = _adapter()
    gateway = _Gateway()
    monkeypatch.setattr(adapter, "_gateway_runner", lambda: gateway)
    first = await _deliver(adapter, ENVELOPES["from_me_dm"], conn)
    second = await _deliver(adapter, ENVELOPES["from_me_dm"], conn)
    other = await _deliver(adapter, {**ENVELOPES["from_me_group"], "session_id": OWNER_SESSION}, conn)
    await adapter._dispatch_envelope(_control(action, OWNER_SESSION))
    for event in (first, second):
        assert adapter._control_session_key(event.source) in gateway.stopped
    assert adapter._control_session_key(other.source) not in gateway.stopped
    assert adapter._summon_turns[other.source.thread_id].stopped is False


async def test_a_second_stop_doesnt_try_a_stopped_summon_again(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, _, conn = _adapter()
    gateway = _Gateway()
    monkeypatch.setattr(adapter, "_gateway_runner", lambda: gateway)
    summon = await _deliver(adapter, ENVELOPES["from_me_dm"], conn)
    await adapter._dispatch_envelope(_control("stop", OWNER_SESSION))
    await adapter._dispatch_envelope(_control("stop", OWNER_SESSION))
    assert gateway.stopped.count(adapter._control_session_key(summon.source)) == 1


async def test_a_peers_stop_reaches_no_summon(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, _, conn = _adapter()
    gateway = _Gateway()
    monkeypatch.setattr(adapter, "_gateway_runner", lambda: gateway)
    first = await _deliver(adapter, ENVELOPES["from_me_dm"], conn)
    await adapter._dispatch_envelope(_control("stop", BOT_SESSION))
    assert adapter._control_session_key(first.source) not in gateway.stopped


# --- finding 4: the host's setup notice ----------------------------------------------------------


def _notice() -> str:
    from agent.i18n import t

    return t("gateway.notify.no_home_channel", platform="Chatlytics", sethome_cmd="/sethome")


async def test_the_no_home_notice_stays_out_of_a_summon_and_only_a_summon() -> None:
    pytest.importorskip("agent.i18n")
    adapter, hub, conn = _adapter()
    ordinary = await _deliver(adapter, ENVELOPES["third_party_dm"], conn)
    summon = await _deliver(adapter, ENVELOPES["from_me_dm"], conn)

    assert (await adapter.send(PEER, _notice(), metadata=_turn(summon, final=False))).success
    assert hub.bodies == []
    await adapter.send(PEER, _notice(), metadata=_turn(ordinary, final=False))
    assert hub.left == [(BOT_SESSION, PEER, _notice())]


async def test_the_real_host_path_of_the_notice_posts_nothing_for_a_summon(monkeypatch: pytest.MonkeyPatch) -> None:
    prepare = pytest.importorskip("gateway.run_turn_prepare")
    notifications = pytest.importorskip("gateway.run_notifications")
    adapter, hub, conn = _adapter()
    summon = await _deliver(adapter, ENVELOPES["from_me_dm"], conn)
    # hermes-agent 0.22.5 as released has no off switch. A patched copy has one: open it.
    if hasattr(prepare, "_home_channel_notice_enabled"):
        monkeypatch.setattr(prepare, "_home_channel_notice_enabled", lambda _c: True)

    class Runner(prepare.GatewayTurnPrepareMixin, notifications.GatewayNotificationsMixin):  # type: ignore[name-defined]
        def __init__(self) -> None:
            self.config = SimpleNamespace(get_home_channel=lambda _p: None, get_notice_delivery=lambda _p: "public")
            self.async_session_store = SimpleNamespace(has_any_sessions=_has_sessions)

        def _delivery_adapter_for(self, _source: Any) -> Any:
            return adapter

        def _thread_metadata_for_source(self, source: Any) -> Dict[str, Any]:
            return {"thread_id": source.thread_id}

    sent: List[str] = []
    real_send = adapter.send

    async def spy(*a: Any, **k: Any) -> Any:
        sent.append(k.get("content") or a[1])
        return await real_send(*a, **k)

    monkeypatch.setattr(adapter, "send", spy)
    await Runner()._hmwa_first_contact_notes(summon.source, [], [])
    assert sent, "the host did not fire the notice, so this test proves nothing"
    assert hub.bodies == []


async def _has_sessions() -> bool:
    return True


# --- finding 5: a word is not a route ------------------------------------------------------------


def _summon_pre(tool: str, args: Any) -> Any:
    env = ENVELOPES["from_me_dm"]
    scope = read_scope.open_for_event(env, SimpleNamespace(source=SimpleNamespace(thread_id=None)))
    read_scope.note_request(True, f"x:{scope.key}", "s", "s")
    return pre_tool_call(tool_name=tool, args=args, session_id="s", task_id="s")


@pytest.mark.parametrize("tool,args", [
    ("mcp__chatlytics__chatlytics_send", {"chatId": PEER, "text": "Lextrove is our archive"}),
    ("mcp_chatlytics_chatlytics_send", {"chatId": PEER, "text": "Lextrove is our archive"}),
    ("mcp__memorygraph__search", {"query": "lextrove"}),
    ("mcp_memory_graph_search", {"query": "state.db"}),
    ("mcp__slack__post", {"channel": "c1", "text": "Lextrove is our archive"}),
    ("mcp__mail__send", {"to": "a@example.test", "subject": "lextrove", "body": "about lextrove"}),
    ("mcp__mail__send", {"to": "sales@lextrove.com", "body": "hi"}),
    ("mcp__telegram__sendMessage", {"chat": "1", "text": "Lextrove is our archive"}),
    ("mcp__zep__search", {"query": "lextrove"}),
    ("mcp__qmd__query", {"query": "lextrove"}),
    ("mcp__memory__search", {"query": "messages from lextrove"}),
    ("mcp__slack__post_message", {"channel": "lextrove-dev", "text": "hi"}),
    ("mcp__notes__create", {"title": "Lextrove roadmap", "name": "state.db notes"}),
    ("mcp_other_agent_ask", {"message": "what is lextrove"}),
    ("mcp__http__post", {"url": "https://relay.example/", "body": '{"u": "https://lextrove.example"}'}),
    ("mcp__x__y", {"lextrove.messages": True}),
    ("mcp__x__y", "lextrove as text that isn't json"),
    ("memory", {"action": "search", "query": "lextrove"}),
    ("send_message", {"target": "x", "message": "Lextrove"}),
])
def test_mentioning_the_word_is_not_refused(tool: str, args: Any) -> None:
    assert _summon_pre(tool, args) is None


@pytest.mark.parametrize("tool,args", [
    ("mcp__fetch__fetch", {"url": "https://lextrove.example/api?session=all"}),
    ("mcp_fetch_fetch", {"url": "https://lextrove.example/api?session=all"}),
    ("mcp__sql__query", {"query": "select * from lextrove.messages"}),
    ("mcp__sql__query", {"query": 'SELECT id\nFROM "lextrove".messages'}),
    ("mcp__fs__read_file", {"path": "/home/x/.hermes/state.db"}),
    ("mcp__ssh__exec", {"host": "h", "command": "cat /x/.hermes/sessions/a.jsonl"}),
    ("mcp__http__post", {"request": {"target": "https://lextrove.example"}}),
    ("terminal", {"command": "curl https://lextrove.example/x"}),
    ("execute_code", {"code": "import lextrove"}),
    ("cronjob", {"prompt": "read lextrove"}),
])
def test_a_real_route_to_the_data_is_still_refused(tool: str, args: Any) -> None:
    verdict = _summon_pre(tool, args)
    assert verdict is not None and "names_the_archive_or_host_store" in verdict["message"]


# --- finding 8: names ----------------------------------------------------------------------------


@pytest.mark.parametrize("tool,args,reason", [
    ("mcp_chatlytics_read_messages", {"chatId": PEER}, "chatlytics_reads_another_account"),
    ("mcp_chatlytics_chatlytics_read", {"chatId": PEER}, "chatlytics_reads_another_account"),
    ("mcp_chatlytics_sean_chatlytics_read", {"chatId": PEER}, "chatlytics_reads_another_account"),
    ("mcp-chatlytics-read-messages", {"chatId": PEER}, "chatlytics_reads_another_account"),
    ("mcp_chatlytics_chatlytics_dispatch", {"action": "getChatMessages"}, "chatlytics_read_action"),
    ("mcp_chatlytics_chatlytics_dispatch_action", {"action": "listChats"}, "chatlytics_read_action"),
    ("chatlytics_dispatch", {"action": " getMessage"}, "chatlytics_read_action"),
])
def test_single_underscore_names_mean_the_same_tool(tool: str, args: Any, reason: str) -> None:
    verdict = _summon_pre(tool, args)
    assert verdict is not None and reason in verdict["message"]


def test_the_plugins_dispatch_that_only_sends_passes_and_the_hubs_does_not() -> None:
    assert _summon_pre("chatlytics_dispatch", {"action": "send"}) is None
    verdict = _summon_pre("mcp_chatlytics_chatlytics_dispatch", {"action": "send"})
    assert verdict is not None and "hub_tool_skips_summon_checks" in verdict["message"]


def test_another_servers_tool_of_a_similar_name_is_left_alone() -> None:
    assert _summon_pre("mcp_notes_read_messages", {"id": "1"}) is None


# --- finding 6: redelivery -----------------------------------------------------------------------


def _with_id(name: str, message_id: str = "true_x_y") -> Dict[str, Any]:
    return {**ENVELOPES[name], "message_id": message_id}


async def test_a_redelivered_summon_runs_once(caplog: Any) -> None:
    adapter, _, conn = _adapter()
    first = await _deliver(adapter, _with_id("from_me_dm"), conn)
    with caplog.at_level(logging.WARNING, logger="chatlytics_hermes"):
        again = await _deliver(adapter, _with_id("from_me_dm"), conn)
    assert first is not None and again is None
    assert "dropped a redelivered summon" in caplog.text
    assert len(read_scope._scopes) == 1


async def test_a_redelivered_summon_runs_once_across_a_restart() -> None:
    adapter, _, conn = _adapter()
    assert await _deliver(adapter, _with_id("from_me_dm"), conn) is not None
    read_scope._seen.clear()
    read_scope._scopes.clear()  # the new process
    adapter, _, conn = _adapter()
    assert await _deliver(adapter, _with_id("from_me_dm"), conn) is None


async def test_the_file_holds_no_chat_id_or_session() -> None:
    adapter, _, conn = _adapter()
    await _deliver(adapter, _with_id("from_me_dm"), conn)
    text = read_scope._seen_path().read_text("utf-8")
    assert PEER not in text and OWNER_SESSION not in text


async def test_another_message_in_the_chat_is_a_new_summon() -> None:
    adapter, _, conn = _adapter()
    assert await _deliver(adapter, _with_id("from_me_dm", "a"), conn) is not None
    assert await _deliver(adapter, _with_id("from_me_dm", "b"), conn) is not None


async def test_the_key_stays_random_for_the_same_message() -> None:
    adapter, _, conn = _adapter()
    first = await _deliver(adapter, _with_id("from_me_dm"), conn)
    read_scope._seen.clear()
    read_scope._seen_path().unlink()
    second = await _deliver(adapter, _with_id("from_me_dm"), conn)
    assert first.source.thread_id != second.source.thread_id


async def test_retry_is_not_a_redelivery_and_starts_clean() -> None:
    adapter, _, conn = _adapter()
    first = await _deliver(adapter, _with_id("from_me_dm"), conn)
    events, _ = _install_recorders(adapter)
    await adapter._dispatch_envelope(_control("retry_last", OWNER_SESSION))
    assert len(events) == 1 and events[0].source.thread_id != first.source.thread_id


async def test_an_ordinary_redelivery_is_left_to_the_host() -> None:
    adapter, _, conn = _adapter()
    assert await _deliver(adapter, _with_id("third_party_dm"), conn) is not None
    assert await _deliver(adapter, _with_id("third_party_dm"), conn) is not None
    assert not read_scope._seen or not any(read_scope._seen.values())


async def test_a_broken_file_does_not_lose_a_summon(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    blocked = tmp_path / "file-not-dir"
    blocked.write_text("x")
    monkeypatch.setattr(read_scope, "_seen_path", lambda: blocked / "seen.json")
    adapter, _, conn = _adapter()
    assert await _deliver(adapter, _with_id("from_me_dm"), conn) is not None
    assert await _deliver(adapter, _with_id("from_me_dm"), conn) is None  # still once in this process


async def test_a_summon_with_no_message_id_is_not_deduped() -> None:
    adapter, _, conn = _adapter()
    assert await _deliver(adapter, ENVELOPES["from_me_dm"], conn) is not None
    assert await _deliver(adapter, ENVELOPES["from_me_dm"], conn) is not None


# --- second review: a send to this chat under another spelling (F1) ------------------------------


LID_DM = ENVELOPES["from_me_dm_lid_unmapped"]
LID_CHAT = LID_DM["entity_jid"]
PEER_DIGITS = PEER.split("@")[0]


async def _summon_on_route(adapter: Any, hub: _Hub, env: Dict[str, Any], conn: _BotConn) -> Any:
    summon = await _deliver(adapter, env, conn)
    hub.routes[env["entity_jid"]] = OWNER_SESSION  # the envelope's id: the only one a test can rely on
    return summon


@pytest.mark.parametrize("spelling", [
    PEER_DIGITS, "+" + PEER_DIGITS, f"{PEER_DIGITS}@s.whatsapp.net", f"{PEER_DIGITS}:26@s.whatsapp.net",
    f" {PEER.upper()} ", f"chat:{PEER}", f"whatsapp:{PEER}",
])
@pytest.mark.parametrize("via", ["adapter", "tool"])
async def test_another_spelling_of_this_chat_leaves_from_the_owner(spelling: str, via: str) -> None:
    """The hub reads the route by a JID, so the body has to carry the envelope's own id."""
    adapter, hub, conn = _adapter()
    summon = await _summon_on_route(adapter, hub, ENVELOPES["from_me_dm"], conn)
    key = summon.source.thread_id
    if via == "adapter":
        assert (await _in_turn(key, lambda: adapter.send(spelling, "x"))).success
    else:
        tool = _bound_tool(adapter, chatlytics_send, "chatlytics_send")
        assert json.loads(await _in_turn(key, lambda: tool({"chatId": spelling, "text": "x"})))["success"]
    assert hub.left == [(OWNER_SESSION, PEER, "x")]


async def test_a_lid_chat_with_a_phone_takes_the_phone_in_any_spelling() -> None:
    adapter, hub, conn = _adapter()
    env = ENVELOPES["from_me_dm_lid"]
    summon = await _summon_on_route(adapter, hub, env, conn)
    phone = env["chat_phone"]
    for spelling in (phone, f"+{phone}", f"{phone}@c.us"):
        assert (await _in_turn(summon.source.thread_id, lambda: adapter.send(spelling, "x"))).success
    assert hub.left == [(OWNER_SESSION, env["entity_jid"], "x")] * 3


@pytest.mark.parametrize("target", [PEER, PEER_DIGITS, "+" + PEER_DIGITS, f"{PEER_DIGITS}@s.whatsapp.net", "Dana Levi"])
async def test_a_lid_summon_with_no_phone_refuses_what_may_be_its_person(target: str, caplog: Any) -> None:
    adapter, hub, conn = _adapter()
    summon = await _summon_on_route(adapter, hub, LID_DM, conn)
    key = summon.source.thread_id
    hub.bodies.clear()

    with caplog.at_level(logging.ERROR, logger="chatlytics_hermes"):
        result = await _in_turn(key, lambda: adapter.send(target, "the answer"))
    assert not result.success and adapter._send_retry_is_final(result)
    assert "summon_target_unclear" in caplog.text

    tool = _bound_tool(adapter, chatlytics_send, "chatlytics_send")
    out = json.loads(await _in_turn(key, lambda: tool({"chatId": target, "text": "x"})))
    assert out["success"] is False and "Nothing was sent" in out["error"]

    assert await _in_turn(key, lambda: adapter.send_typing(target)) is None
    assert hub.bodies == []


async def test_a_refused_target_takes_media_with_it(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, hub, conn = _adapter()

    async def file_field(*_a: Any, **_k: Any) -> Dict[str, Any]:
        return {"url": "https://example.test/a.png"}

    monkeypatch.setattr(adapter, "_resolve_media_file_field", file_field)
    summon = await _summon_on_route(adapter, hub, LID_DM, conn)
    result = await _in_turn(summon.source.thread_id, lambda: adapter.send_image(PEER, "https://example.test/a.png"))
    assert not result.success and adapter._send_retry_is_final(result) and hub.left == []


@pytest.mark.parametrize("env,target", [
    (LID_DM, "137000000000000@lid"),  # another LID, and this chat's is known
    (LID_DM, "120363000000000000@g.us"),
    (LID_DM, "120363000000000000@newsletter"),
    (ENVELOPES["from_me_dm"], "999@c.us"),  # another phone, and this chat's is known
    (ENVELOPES["from_me_dm"], "+999"),
    (ENVELOPES["from_me_dm"], "120363000000000000@g.us"),
    (ENVELOPES["from_me_group"], PEER),
    (ENVELOPES["from_me_group"], "137000000000000@lid"),
    (ENVELOPES["from_me_group"], "Dana Levi"),
])
async def test_a_target_known_to_be_another_chat_leaves_from_the_bot(env: Dict[str, Any], target: str) -> None:
    adapter, hub, conn = _adapter()
    summon = await _summon_on_route(adapter, hub, env, conn)
    assert (await _in_turn(summon.source.thread_id, lambda: adapter.send(target, "x"))).success
    assert hub.left == [(BOT_SESSION, target, "x")]


async def test_a_phone_summon_refuses_a_lid_the_envelope_did_not_give() -> None:
    adapter, hub, conn = _adapter()
    summon = await _summon_on_route(adapter, hub, ENVELOPES["from_me_dm"], conn)
    result = await _in_turn(summon.source.thread_id, lambda: adapter.send("137000000000000@lid", "x"))
    assert not result.success and hub.left == []


async def test_a_group_summon_takes_its_id_without_the_suffix() -> None:
    adapter, hub, conn = _adapter()
    summon = await _summon_on_route(adapter, hub, ENVELOPES["from_me_group"], conn)
    bare = GROUP.split("@")[0]
    assert (await _in_turn(summon.source.thread_id, lambda: adapter.send(bare, "x"))).success
    assert hub.left == [(OWNER_SESSION, GROUP, "x")]


@pytest.mark.parametrize("target", [PEER, PEER_DIGITS, "Dana Levi", "137000000000000@lid"])
async def test_an_ordinary_turn_refuses_no_target_during_a_summon(target: str) -> None:
    adapter, hub, conn = _adapter()
    await _summon_on_route(adapter, hub, LID_DM, conn)
    assert (await adapter.send(target, "x")).success
    assert hub.left == [(BOT_SESSION, target, "x")]


async def test_typing_and_a_reaction_carry_the_envelopes_id() -> None:
    adapter, hub, conn = _adapter()
    summon = await _summon_on_route(adapter, hub, ENVELOPES["from_me_dm"], conn)
    key = summon.source.thread_id
    await _in_turn(key, lambda: adapter.send_typing("+" + PEER_DIGITS))
    assert hub.bodies[-1][1] == {"chatId": PEER, "duration": 3.0, "session": OWNER_SESSION}
    tool = _bound_tool(adapter, chatlytics_react, "chatlytics_react")
    await _in_turn(key, lambda: tool({"messageId": "true_x_y", "emoji": "ok", "chatId": PEER_DIGITS}))
    assert hub.bodies[-1][1]["session"] == OWNER_SESSION and hub.bodies[-1][1]["params"]["chatId"] == PEER


async def test_a_phone_sender_of_a_lid_dm_with_no_phone_never_gets_the_reply() -> None:
    """The hub leaves the sender as the peer with no owner on record, and here that can't be seen."""
    adapter, hub, conn = _adapter()
    summon = await _deliver(adapter, {**LID_DM, "sender_jid": PEER}, conn)
    result = await adapter.send(LID_CHAT, "private answer", metadata=_turn(summon))
    assert not result.success and hub.left == []


# --- second review: an argument that says where the tool goes (F2) ---------------------------------


@pytest.mark.parametrize("tool,args", [
    # Any server: a memory or chatlytics name buys nothing, and no substring of one can buy it.
    ("mcp__memory_fs__read", {"file_path": "/h/.hermes/state.db"}),
    ("mcp__memorial_db__read", {"path": "/x/state.db"}),
    ("mcp__chatlytics_evil__x", {"command": "cat ~/.hermes/state.db"}),
    ("mcp_chatlytics_evil_x", {"cmd": "cat state.db"}),
    ("mcp__mem0__add", {"text": "x", "metadata": {"source_url": "https://lextrove.example"}}),
    ("mcp__chatlytics__chatlytics_send_image", {"chatId": "1", "url": "https://lextrove.example/a.png"}),
    ("mcp_chatlytics_send_media", {"chatId": PEER, "file": "https://lextrove.example/media/x"}),
    # Any spelling of the key.
    ("mcp__x__read", {"filePath": "/h/.hermes/state.db"}),
    ("mcp__x__read", {"file-path": "/h/.hermes/state.db"}),
    ("mcp__x__read", {"FILE_PATH": "/h/.hermes/state.db"}),
    ("mcp__x__read", {"file": "/h/.hermes/state.db"}),
    ("mcp__x__read", {"filename": "/h/.hermes/state.db"}),
    ("mcp__x__fetch", {"baseUrl": "https://lextrove.example/api"}),
    ("mcp__x__fetch", {"baseURL": "https://lextrove.example/api"}),
    ("mcp__x__fetch", {"endpoint": "https://lextrove.example/api"}),
    ("mcp__x__fetch", {"href": "https://lextrove.example/api"}),
    ("mcp__x__db", {"sqlQuery": "select * from lextrove.messages"}),
    ("mcp__x__db", {"statement": "select * from lextrove.messages"}),
    # Nested under it, and JSON held in it.
    ("mcp__x__read", {"path": {"value": "/x/state.db"}}),
    ("mcp__x__read", {"paths": ["/x/state.db"]}),
    ("mcp__x__read", {"url": '{"href": "https://lextrove.example"}'}),
    ("mcp__x__read", {"sql": '{"q": "select * from lextrove.messages"}'}),
    ("mcp__x__read", {"resource": {"uri": "file:///x/state.db"}}),
    ("mcp__memory__read", {"options": {"file_path": "/home/u/.hermes/state.db"}}),
    ("mcp__x__y", {"urls": ["https://example.test", "https://lextrove.example"]}),
    ("mcp__x__y", {"uri": "lextrove://messages/all"}),
    ("mcp__x__y", {"url": "lextrove.example/api"}),
    ("mcp__x__y", {"dsn": "postgres://u:p@lextrove-db:5432/x"}),
    ("mcp__x__y", {"url": "https://example.test/?next=https://lextrove.example"}),
    ("mcp__mail__send", {"to": "x", "body": "hi", "attachment": "/home/u/.hermes/state.db"}),
    ("tool_call", {"calls": [{"name": "mcp_files_open", "arguments": json.dumps({"path": "state.db"})}]}),
])
def test_a_reach_argument_pointing_at_the_archive_or_a_store_is_refused(tool: str, args: Any) -> None:
    verdict = _summon_pre(tool, args)
    assert verdict is not None and "names_the_archive_or_host_store" in verdict["message"]


@pytest.mark.parametrize("tool,args", [
    # A place that holds the word and is not the archive: memory reads must work.
    ("mcp__memory__read_note", {"path": "projects/lextrove.md"}),
    ("mcp__memory__read_note", {"path": "lextrove.md"}),
    ("mcp__memory__read_note", {"path": "notes/lextrove/roadmap.md"}),
    ("mcp__mem0__add", {"source_url": "https://notes.example/lextrove/roadmap"}),
    ("mcp__x__fetch", {"url": "https://example.test/fine"}),
    # A query that isn't a query language.
    ("mcp__x__db", {"sql": "select * from notes where topic = 'lextrove'"}),
    ("mcp__x__db", {"query": "state.db"}),
    # Prose under any key, on any server.
    ("mcp__chatlytics__chatlytics_send", {"chatId": PEER, "text": "Lextrove is our archive"}),
    ("mcp_chatlytics_search", {"query": "lextrove"}),
    ("mcp_cognee_recall", {"query": "lextrove"}),
    ("mcp__plugin_x_memory__search", {"query": "state.db"}),
    ("mcp__honcho__search", {"query": "state.db"}),
    ("mcp__mem0__add", {"content": "lextrove notes", "name": "lextrove"}),
    ("mcp__slack__post_message", {"request": {"text": "Lextrove is our archive", "title": "lextrove"}}),
    ("mcp__mail__send", {"to": "a@example.test", "body": json.dumps({"note": "about lextrove"})}),
    ("mcp__x__y", {"q": "fine"}),
    ("mcp__x__y", {"curl_flags": "state.db"}),
])
def test_a_word_is_not_a_place(tool: str, args: Any) -> None:
    assert _summon_pre(tool, args) is None


@pytest.mark.parametrize("key,expected", [
    ("url", "place"), ("baseUrl", "place"), ("base-url", "place"), ("SOURCE_URL", "place"), ("filePath", "place"),
    ("command", "run"), ("runCmd", "run"), ("sqlQuery", "query"), ("query", "query"), ("query_url", "place"),
    ("text", None), ("message", None), ("content", None), ("body", None), ("name", None), ("channel", None),
    ("curlFlags", None), ("profile", None), ("urlish", None),
])
def test_a_key_is_read_in_any_spelling_and_by_whole_words(key: str, expected: Optional[str]) -> None:
    marks = {"place": read_scope._PLACE_MARKS, "run": read_scope._TEXT_ROUTE_MARKS, "query": read_scope._QUERY_MARKS}
    assert read_scope._marks_for_key(key) is (marks[expected] if expected else None)


def test_json_too_deep_to_open_is_just_text() -> None:
    assert _summon_pre("mcp__mail__send", {"body": "[" * 200000}) is None
    assert _summon_pre("mcp__x__y", {"url": "[" * 200000}) is None


# --- second review: several bots (F3) --------------------------------------------------------------


def _two_bots() -> Tuple[Any, _Hub, _BotConn, _Hub, _BotConn]:
    adapter, hub, conn = _adapter()
    other_hub = _Hub(own="other-session")
    other = _BotConn("sk_bot_" + "b" * 43, index=1)
    other.client, other.own_session = other_hub, "other-session"
    adapter._conns.append(other)
    return adapter, hub, conn, other_hub, other


async def test_a_tool_on_a_summon_of_an_extra_bot_uses_that_bot() -> None:
    adapter, hub, conn, other_hub, other = _two_bots()
    summon = await _deliver(adapter, ENVELOPES["from_me_dm"], other)
    other_hub.routes[PEER] = OWNER_SESSION
    key = summon.source.thread_id
    tool = _bound_tool(adapter, chatlytics_send, "chatlytics_send")

    await _in_turn(key, lambda: tool({"chatId": PEER, "text": "here"}))
    await _in_turn(key, lambda: tool({"chatId": "999@c.us", "text": "elsewhere"}))
    assert hub.bodies == []
    assert other_hub.left == [(OWNER_SESSION, PEER, "here"), ("other-session", "999@c.us", "elsewhere")]
    assert other_hub.bodies[-1][1]["session"] == "other-session"


async def test_a_tool_on_a_summon_of_an_extra_bot_found_by_task_id_uses_that_bot() -> None:
    adapter, hub, conn, other_hub, other = _two_bots()
    summon = await _deliver(adapter, ENVELOPES["from_me_dm"], other)
    other_hub.routes[PEER] = OWNER_SESSION
    read_scope.note_request(True, f"x:{summon.source.thread_id}", "s1", "task-1")
    tool = _bound_tool(adapter, chatlytics_send, "chatlytics_send")
    await tool({"chatId": PEER, "text": "x"}, task_id="task-1")
    assert hub.bodies == [] and other_hub.left == [(OWNER_SESSION, PEER, "x")]


async def test_an_ordinary_tool_stays_on_the_primary_during_an_extra_bots_summon() -> None:
    adapter, hub, conn, other_hub, other = _two_bots()
    await _deliver(adapter, ENVELOPES["from_me_dm"], other)
    tool = _bound_tool(adapter, chatlytics_send, "chatlytics_send")
    await _in_turn("", lambda: tool({"chatId": PEER, "text": "x"}))
    assert hub.left == [(BOT_SESSION, PEER, "x")] and other_hub.bodies == []


async def test_a_send_to_another_chat_names_the_session_of_the_bearer_it_uses() -> None:
    adapter, hub, conn, other_hub, other = _two_bots()
    await _deliver(adapter, {**ENVELOPES["third_party_dm"], "entity_jid": "999@c.us"}, conn)
    summon = await _deliver(adapter, ENVELOPES["from_me_dm"], other)
    await _in_turn(summon.source.thread_id, lambda: adapter.send("999@c.us", "x"))
    assert other_hub.bodies[-1][1]["session"] == "other-session" and hub.left == []


async def test_a_retry_of_an_extra_bots_summon_stays_on_that_bot() -> None:
    adapter, hub, conn, other_hub, other = _two_bots()
    await _deliver(adapter, ENVELOPES["third_party_dm"], conn)
    await _deliver(adapter, ENVELOPES["from_me_dm"], other)
    events, _ = _install_recorders(adapter)
    await adapter._dispatch_envelope(_control("retry_last", OWNER_SESSION))
    assert len(events) == 1
    other_hub.routes[PEER] = OWNER_SESSION
    await adapter.send(PEER, "again", metadata=_turn(events[0]))
    assert hub.left == [] and other_hub.left == [(OWNER_SESSION, PEER, "again")]


async def test_a_retry_with_no_known_bearer_among_several_bots_is_refused() -> None:
    adapter, hub, conn, other_hub, other = _two_bots()
    await _deliver(adapter, ENVELOPES["from_me_dm"], other)
    adapter._summon_turns.clear()
    events, _ = _install_recorders(adapter)
    await adapter._dispatch_envelope(_control("retry_last", OWNER_SESSION))
    assert events == []


async def test_a_retry_with_one_bot_needs_no_earlier_summon() -> None:
    adapter, _, conn = _adapter()
    await _deliver(adapter, ENVELOPES["from_me_dm"], conn)
    adapter._summon_turns.clear()
    events, _ = _install_recorders(adapter)
    await adapter._dispatch_envelope(_control("retry_last", OWNER_SESSION))
    assert len(events) == 1


async def test_a_summon_keeps_its_bearer_after_many_later_summons() -> None:
    adapter, hub, conn, other_hub, other = _two_bots()
    await _deliver(adapter, ENVELOPES["third_party_dm"], conn)
    first = await _deliver(adapter, ENVELOPES["from_me_dm"], other)
    for _i in range(20):
        await _deliver(adapter, ENVELOPES["from_me_dm"], other)
    other_hub.routes[PEER] = OWNER_SESSION
    await adapter.send(PEER, "late", metadata=_turn(first))
    assert hub.left == [] and other_hub.left == [(OWNER_SESSION, PEER, "late")]


async def test_turns_are_bounded_by_the_scopes_and_stop_by_the_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, _, conn = _adapter()
    gateway = _Gateway()
    monkeypatch.setattr(adapter, "_gateway_runner", lambda: gateway)
    summons = [await _deliver(adapter, ENVELOPES["from_me_dm"], conn) for _i in range(20)]
    await adapter._dispatch_envelope(_control("stop", OWNER_SESSION))
    assert len(gateway.stopped) == 17  # 16 summons, and the ordinary session key of the chat
    assert adapter._control_session_key(summons[-1].source) in gateway.stopped
    assert adapter._control_session_key(summons[0].source) not in gateway.stopped

    monkeypatch.setattr(read_scope, "_SCOPES_MAX", 5)
    await _deliver(adapter, ENVELOPES["from_me_dm"], conn)
    assert len(adapter._summon_turns) == 5


# --- second review: the group fallback, and a 409 on an ordinary turn (F4) -------------------------


async def test_a_group_owner_dm_is_addressed_to_the_participant_as_the_envelope_has_it() -> None:
    """What the hub then does with this is exact-match: docs/summoned-turns.md, live test row G1."""
    adapter, hub, conn = _adapter()
    lid = "137000000000000@lid"
    summon = await _deliver(adapter, {**ENVELOPES["from_me_group"], "sender_jid": lid}, conn)
    assert (await adapter.send(GROUP, "the answer", metadata=_turn(summon))).success
    assert [(s, c) for s, c, _ in hub.left] == [(BOT_SESSION, lid)]


async def test_a_403_on_the_group_owner_dm_loses_the_reply_with_an_error(caplog: Any) -> None:
    adapter, hub, conn = _adapter()
    real_post = hub.post

    async def bot_unpaired(path: str, json: Dict[str, Any], timeout: Any = None) -> _Resp:  # noqa: A002
        if json.get("chatId") != GROUP:
            return _Resp(403, {"error": "bot_unpaired", "error_kind": "bot-unpaired"})
        return await real_post(path, json, timeout)

    hub.post = bot_unpaired  # type: ignore[method-assign]
    lid = "137000000000000@lid"
    summon = await _deliver(adapter, {**ENVELOPES["from_me_group"], "sender_jid": lid}, conn)
    with caplog.at_level(logging.ERROR, logger="chatlytics_hermes"):
        result = await adapter.send(GROUP, "the answer", metadata=_turn(summon))
    assert not result.success and adapter._send_retry_is_final(result)
    assert hub.left == [] and "could not tell the owner" in caplog.text


async def test_a_409_on_an_ordinary_turn_is_not_sent_anywhere_else(caplog: Any) -> None:
    adapter, hub, conn = _adapter()
    summon = await _deliver(adapter, ENVELOPES["from_me_dm"], conn)
    hub.own = "moved"  # the session this turn names is now foreign, and the bound one
    hub.bound = BOT_SESSION
    with caplog.at_level(logging.ERROR, logger="chatlytics_hermes"):
        result = await adapter.send(PEER, "ordinary")
    assert not result.success and hub.left == []
    assert "summon reply not posted" not in caplog.text
    assert read_scope.scope_for_key(summon.source.thread_id).route_closed is False


# --- second review: a stale envelope (F5) ----------------------------------------------------------


def _unmarked(env: str = "from_me_dm") -> Dict[str, Any]:
    return {k: v for k, v in ENVELOPES[env].items() if k != "trigger_source"}


async def test_an_unmarked_owner_envelope_does_not_move_ordinary_sends_to_the_owner() -> None:
    adapter, hub, conn = _adapter()
    await _deliver(adapter, _unmarked(), conn)
    await conn.own_session_probe
    assert conn.own_session == BOT_SESSION
    assert (await adapter.send("999@c.us", "x")).success
    assert hub.left == [(BOT_SESSION, "999@c.us", "x")]


async def test_a_bot_really_moved_is_named_once_bot_me_confirms_it() -> None:
    adapter, hub, conn = _adapter()
    hub.own = "moved-session"
    await _deliver(adapter, {**ENVELOPES["third_party_dm"], "session_id": "moved-session"}, conn)
    await conn.own_session_probe
    assert conn.own_session == "moved-session"


async def test_a_failed_confirmation_keeps_what_bot_me_said() -> None:
    adapter, hub, conn = _adapter()

    async def down(_path: str, **_: Any) -> _Resp:
        raise RuntimeError("hub down")

    hub.get = down  # type: ignore[method-assign]
    await _deliver(adapter, _unmarked(), conn)
    await conn.own_session_probe
    assert conn.own_session == BOT_SESSION


async def test_an_ordinary_turn_does_not_wait_for_the_confirmation_and_asks_once() -> None:
    adapter, hub, conn = _adapter()
    gate = asyncio.Event()
    calls: List[str] = []

    async def slow(path: str, **_: Any) -> _Resp:
        calls.append(path)
        await gate.wait()
        return _Resp(200, {"session_id": BOT_SESSION})

    hub.get = slow  # type: ignore[method-assign]
    assert await _deliver(adapter, _unmarked(), conn) is not None
    await _deliver(adapter, _unmarked(), conn)
    await asyncio.sleep(0)
    assert calls == ["/api/v1/bot/me"]
    gate.set()
    await conn.own_session_probe


async def test_a_matching_envelope_asks_nothing() -> None:
    adapter, hub, conn = _adapter()
    await _deliver(adapter, ENVELOPES["third_party_dm"], conn)
    assert conn.own_session_probe is None


async def test_the_boot_probe_records_the_primarys_session() -> None:
    adapter, hub, conn = _adapter(own=None)
    await adapter._log_boot_identity()
    assert conn.own_session == BOT_SESSION and conn.own_session_confirmed


async def test_the_extra_bot_probe_records_its_session() -> None:
    adapter, _, _ = _adapter()
    other = _BotConn("sk_bot_" + "b" * 43, index=1)
    other.client = _Hub(own="other-session")
    assert await adapter._verify_bot_identity(other)
    assert other.own_session == "other-session" and other.own_session_confirmed


# --- second review: a late retry into a reopened route (F7) ----------------------------------------


async def test_a_refused_summon_stays_out_of_a_route_a_later_summon_reopened(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, hub, conn = _adapter()

    async def file_field(*_a: Any, **_k: Any) -> Dict[str, Any]:
        return {"url": "https://example.test/a.png"}

    monkeypatch.setattr(adapter, "_resolve_media_file_field", file_field)
    old = await _deliver(adapter, ENVELOPES["from_me_dm"], conn)
    assert (await adapter.send(PEER, "old answer", metadata=_turn(old))).success  # 409, then the owner's DM
    assert hub.refused == [("/api/v1/send", PEER)]

    new = await _deliver(adapter, ENVELOPES["from_me_dm"], conn)
    hub.routes[PEER] = OWNER_SESSION  # the owner summoned again

    # What the host's ledger does 30 and 120 seconds later: the row's thread id, and nothing else.
    row = {"thread_id": old.source.thread_id}
    assert not (await adapter.send(chat_id=PEER, content="old answer", metadata=row)).success
    assert hub.refused == [("/api/v1/send", PEER)]  # it never got as far as the hub
    assert (await adapter.send(PEER, "old answer", metadata=_turn(old))).success
    assert not (await adapter.send_image(PEER, "https://example.test/a.png", metadata=_turn(old))).success
    await adapter.send_typing(PEER, metadata=_turn(old, final=False))
    await adapter._send_progress_bubble(PEER, "t", _turn(old, final=False))
    assert [left for left in hub.left if left[1] == PEER] == []
    assert not [b for p, b in hub.bodies if p == "/api/v1/typing"]
    assert [c for _, c, _ in hub.left] == [OWNER, OWNER]

    assert (await adapter.send(PEER, "new answer", metadata=_turn(new))).success
    assert hub.left[-1] == (OWNER_SESSION, PEER, "new answer")


async def test_a_closed_route_holds_for_another_spelling_of_the_chat() -> None:
    adapter, hub, conn = _adapter()
    old = await _deliver(adapter, ENVELOPES["from_me_dm"], conn)
    await adapter.send(PEER, "old answer", metadata={**_turn(old), "notify": False})
    await _deliver(adapter, ENVELOPES["from_me_dm"], conn)
    hub.routes[PEER] = OWNER_SESSION
    assert not (await adapter.send(PEER_DIGITS, "old answer", metadata={"thread_id": old.source.thread_id})).success
    assert hub.left == []


async def test_a_refused_file_closes_the_route_too(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, hub, conn = _adapter()

    async def file_field(*_a: Any, **_k: Any) -> Dict[str, Any]:
        return {"url": "https://example.test/a.png"}

    monkeypatch.setattr(adapter, "_resolve_media_file_field", file_field)
    summon = await _deliver(adapter, ENVELOPES["from_me_dm"], conn)
    assert not (await adapter.send_image(PEER, "https://example.test/a.png", metadata=_turn(summon))).success
    assert read_scope.scope_for_key(summon.source.thread_id).route_closed


async def test_a_closed_route_doesnt_stop_the_summon_writing_elsewhere() -> None:
    adapter, hub, conn = _adapter()
    summon = await _deliver(adapter, ENVELOPES["from_me_dm"], conn)
    await adapter.send(PEER, "x", metadata=_turn(summon))
    assert (await _in_turn(summon.source.thread_id, lambda: adapter.send("999@c.us", "y"))).success
    assert hub.left[-1] == (BOT_SESSION, "999@c.us", "y")


# --- second review: what the mutation run showed no test held --------------------------------------


@pytest.mark.parametrize("sender", ["send_image", "send_voice", "send_video", "send_animation", "send_document"])
async def test_every_media_kind_names_the_turns_account(sender: str, monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, hub, conn = _adapter()

    async def file_field(*_a: Any, **_k: Any) -> Dict[str, Any]:
        return {"url": "https://example.test/a.bin"}

    monkeypatch.setattr(adapter, "_resolve_media_file_field", file_field)
    ordinary = await _deliver(adapter, ENVELOPES["third_party_dm"], conn)
    summon = await _deliver(adapter, ENVELOPES["from_me_dm"], conn)
    hub.routes[PEER] = OWNER_SESSION
    send = getattr(adapter, sender)
    await send(PEER, "https://example.test/a.bin", metadata=_turn(summon))
    await send(PEER, "https://example.test/a.bin", metadata=_turn(ordinary))
    assert [s for s, _, _ in hub.left] == [OWNER_SESSION, BOT_SESSION]


async def _wait_for(check: Any) -> None:
    for _i in range(500):
        if check():
            return
        await asyncio.sleep(0.002)
    raise AssertionError("never happened")


async def test_the_typing_loop_keeps_naming_the_turns_account() -> None:
    adapter, hub, conn = _adapter()
    adapter.status_bubble_after_s = 0  # no bubble in this one
    summon = await _deliver(adapter, ENVELOPES["from_me_dm"], conn)
    hub.routes[PEER] = OWNER_SESSION
    stop = asyncio.Event()
    typing = lambda: [b for p, b in hub.bodies if p == "/api/v1/typing"]  # noqa: E731
    loop = asyncio.ensure_future(
        adapter._keep_typing(PEER, interval=0.001, metadata=_turn(summon, final=False), stop_event=stop)
    )
    await _wait_for(lambda: len(typing()) >= 3)
    stop.set()
    await loop
    assert all(b["session"] == OWNER_SESSION for b in typing())


async def test_the_bubble_timer_names_the_turns_account_and_its_delete_names_none() -> None:
    adapter, hub, conn = _adapter()
    adapter.status_edit_in_place, adapter.status_bubble_after_s = True, 0.001
    summon = await _deliver(adapter, ENVELOPES["from_me_dm"], conn)
    hub.routes[PEER] = OWNER_SESSION
    stop = asyncio.Event()
    loop = asyncio.ensure_future(
        adapter._keep_typing(PEER, interval=0.01, metadata=_turn(summon, final=False), stop_event=stop)
    )
    bubble = lambda: [b for p, b in hub.bodies if p == "/api/v1/send" and b.get("progress")]  # noqa: E731
    await _wait_for(lambda: bubble())
    stop.set()
    await loop
    assert bubble()[0]["session"] == OWNER_SESSION
    # No route covers this verb, so the hub pins it to the bot's number and ignores a session.
    actions = [b for p, b in hub.bodies if p == "/api/v1/actions"]
    assert [b["action"] for b in actions] == ["unsend"] and "session" not in actions[0]


async def test_the_webhook_path_opens_a_summon_like_the_long_poll() -> None:
    from tests.test_owner_tagging import _post, _wh_adapter, _wh_payload

    adapter = _wh_adapter(secret=True, allow_admin_from=[OWNER])
    adapter.client_for_test = None
    payload = {**_wh_payload("!sammie hi", PEER), "senderId": OWNER, **ENVELOPES["from_me_dm"], "message_id": "m-1"}
    first = await _post(adapter, payload, sign=True)
    assert read_scope.is_scope_key(first[0].source.thread_id)
    assert adapter._summon_turns[first[0].source.thread_id].session == OWNER_SESSION
    assert await _post(adapter, payload, sign=True) == []  # the ledger, on this path too


async def test_a_webhook_summon_keeps_the_chats_session_for_its_other_turns() -> None:
    from tests.test_owner_tagging import _post, _wh_adapter, _wh_payload

    adapter = _wh_adapter(secret=True, allow_admin_from=[OWNER])
    adapter.register_chat_session(PEER, BOT_SESSION)
    payload = {**_wh_payload("!sammie hi", PEER), "senderId": OWNER, **ENVELOPES["from_me_dm"], "session": OWNER_SESSION}
    await _post(adapter, payload, sign=True)
    assert adapter._chat_session_map[PEER] == BOT_SESSION


async def test_the_route_verbs_are_the_hubs_and_no_others() -> None:
    from chatlytics_hermes.tools import _ROUTE_ACTIONS, _name_outbound_session

    assert _ROUTE_ACTIONS == {"react", "startTyping", "stopTyping", "sendSeen"}
    token = read_scope.OUTBOUND_RESOLVER.set(lambda _chat: ("s", None))
    try:
        for action, named in (("react", True), ("unsend", False), ("edit", False), ("getChatMessages", False)):
            body = {"action": action, "params": {"chatId": PEER}}
            _name_outbound_session("/api/v1/actions", body)
            assert ("session" in body) is named, action
    finally:
        read_scope.OUTBOUND_RESOLVER.reset(token)


def test_a_request_with_the_line_and_no_scope_gives_its_task_the_lost_key() -> None:
    read_scope.note_request(True, "the line with no key", "s1", "task-lost")
    assert read_scope.turn_key_for_tool("task-lost") == read_scope.LOST_KEY
    assert read_scope.turn_key_for_tool("task-other") is None


async def test_a_tool_of_a_task_with_no_scope_sends_nothing() -> None:
    adapter, hub, _ = _adapter()
    read_scope.note_request(True, "the line with no key", "s1", "task-lost")
    tool = _bound_tool(adapter, chatlytics_send, "chatlytics_send")
    out = json.loads(await tool({"chatId": PEER, "text": "x"}, task_id="task-lost"))
    assert out["success"] is False and hub.bodies == []


# --- second review: the delivered summons file (F6) ------------------------------------------------


@pytest.mark.parametrize(
    "content",
    [b"[" * 200000, b"\xff\xfe\x00garbage", b"[1,2,3]", b"", b'{"a":"x","b":null}'],
    ids=["too_deep", "binary", "a_list", "empty", "bad_values"],
)
def test_a_bad_file_never_loses_a_summon(content: bytes, tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "seen.json"
    path.write_bytes(content)
    monkeypatch.setattr(read_scope, "_seen_path", lambda: path)
    env = {"session_id": "s", "entity_jid": "c", "message_id": "m"}
    assert read_scope.first_delivery(env) is True
    assert read_scope.first_delivery(env) is False
    read_scope._seen.clear()
    assert read_scope.first_delivery(env) is False  # and the file is good again


def _delivered(message: str, chat: str = "c") -> Dict[str, Any]:
    return {"session_id": "s", "entity_jid": chat, "message_id": message}


def test_a_delivery_older_than_the_ttl_counts_as_new(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [1000.0]
    monkeypatch.setattr(read_scope.time, "time", lambda: now[0])
    assert read_scope.first_delivery(_delivered("m")) is True
    now[0] += read_scope._SEEN_TTL_S
    assert read_scope.first_delivery(_delivered("m")) is False
    now[0] += 1
    assert read_scope.first_delivery(_delivered("m")) is True


def test_the_ttl_clears_old_entries_from_the_file(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [1000.0]
    monkeypatch.setattr(read_scope.time, "time", lambda: now[0])
    read_scope.first_delivery(_delivered("old"))
    now[0] += read_scope._SEEN_TTL_S + 1
    read_scope.first_delivery(_delivered("new"))
    assert len(json.loads(read_scope._seen_path().read_text("utf-8"))) == 1


def test_the_file_is_capped_and_drops_the_oldest(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(read_scope, "_SEEN_MAX", 3)
    for message in "abcd":
        assert read_scope.first_delivery(_delivered(message)) is True
    assert len(json.loads(read_scope._seen_path().read_text("utf-8"))) == 3
    assert read_scope.first_delivery(_delivered("d")) is False
    assert read_scope.first_delivery(_delivered("a")) is True


def test_a_loaded_file_drops_the_oldest_by_time_not_by_position(monkeypatch: pytest.MonkeyPatch) -> None:
    now = time.time()
    newer, older = read_scope._delivery_id(_delivered("newer")), read_scope._delivery_id(_delivered("older"))
    read_scope._seen_path().write_text(json.dumps({newer: now - 10, older: now - 1000}), "utf-8")
    monkeypatch.setattr(read_scope, "_SEEN_MAX", 2)
    assert read_scope.first_delivery(_delivered("third")) is True
    assert read_scope.first_delivery(_delivered("newer")) is False


def test_the_same_message_id_in_another_chat_or_session_is_new() -> None:
    assert read_scope.first_delivery(_delivered("m", "a@c.us")) is True
    assert read_scope.first_delivery(_delivered("m", "b@c.us")) is True
    assert read_scope.first_delivery({**_delivered("m", "a@c.us"), "session_id": "other"}) is True
    assert read_scope.first_delivery(_delivered("m", "a@c.us")) is False


# --- third review ----------------------------------------------------------------------------------


class _RefusingHub(_Hub):
    """Answers 409 with ``code`` for one chat, as proxy-send-handler's reach check does."""

    def __init__(self, chat: str, code: str = "SESSION_CANNOT_REACH_TARGET") -> None:
        super().__init__()
        self.refuse_chat, self.refuse_code = chat, code

    async def post(self, path: str, json: Dict[str, Any], timeout: Any = None) -> _Resp:  # noqa: A002
        if json.get("chatId") == self.refuse_chat or (json.get("params") or {}).get("chatId") == self.refuse_chat:
            self.bodies.append((path, dict(json)))
            return _Resp(409, {"success": False, "error": "x", "code": self.refuse_code})
        return await super().post(path, json, timeout)


OTHER_GROUP = "120363999999999999@g.us"


def _media_ok(adapter: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    async def file_field(*_a: Any, **_k: Any) -> Dict[str, Any]:
        return {"url": "https://example.test/a.png"}

    monkeypatch.setattr(adapter, "_resolve_media_file_field", file_field)


async def test_a_file_refused_for_another_group_leaves_the_turns_reply_in_its_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, hub, conn = _adapter(_RefusingHub(OTHER_GROUP))
    _media_ok(adapter, monkeypatch)
    summon = await _summon_on_route(adapter, hub, ENVELOPES["from_me_dm"], conn)
    key = summon.source.thread_id
    assert not (await _in_turn(key, lambda: adapter.send_image(OTHER_GROUP, "https://example.test/a.png"))).success
    assert not read_scope.scope_for_key(key).route_closed
    assert (await adapter.send(PEER, "the answer", metadata=_turn(summon))).success
    assert hub.left == [(OWNER_SESSION, PEER, "the answer")]


async def test_a_file_refused_in_its_chat_for_another_reason_closes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, hub, conn = _adapter(_RefusingHub(PEER))
    _media_ok(adapter, monkeypatch)
    summon = await _summon_on_route(adapter, hub, ENVELOPES["from_me_dm"], conn)
    assert not (await adapter.send_image(PEER, "https://example.test/a.png", metadata=_turn(summon))).success
    assert not read_scope.scope_for_key(summon.source.thread_id).route_closed


async def test_a_file_refused_with_a_body_that_is_not_json_closes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Html(_Hub):
        async def post(self, path: str, json: Dict[str, Any], timeout: Any = None) -> Any:  # noqa: A002
            self.bodies.append((path, dict(json)))
            resp = _Resp(409, {})

            def bad() -> Any:
                raise ValueError("not json")

            resp.json = bad  # type: ignore[method-assign]
            return resp

    adapter, hub, conn = _adapter(_Html())
    _media_ok(adapter, monkeypatch)
    summon = await _summon_on_route(adapter, hub, ENVELOPES["from_me_dm"], conn)
    assert not (await adapter.send_image(PEER, "https://example.test/a.png", metadata=_turn(summon))).success
    assert not read_scope.scope_for_key(summon.source.thread_id).route_closed


@pytest.mark.parametrize("via", ["text", "file"])
async def test_a_route_refusal_for_another_chat_closes_nothing_and_tells_nobody(via: str, monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, hub, conn = _adapter(_RefusingHub(OTHER_GROUP, "from_me_route_expired"))
    _media_ok(adapter, monkeypatch)
    summon = await _summon_on_route(adapter, hub, ENVELOPES["from_me_dm"], conn)
    key = summon.source.thread_id
    if via == "text":
        result = await adapter.send(OTHER_GROUP, "x", metadata=_turn(summon))
    else:
        result = await adapter.send_image(OTHER_GROUP, "https://example.test/a.png", metadata=_turn(summon))
    assert not result.success and not read_scope.scope_for_key(key).route_closed
    assert hub.left == []  # and nothing went to the owner's DM


async def test_a_closed_route_refuses_a_tool_of_the_old_summon_after_a_new_summon_reopened_it(caplog: Any) -> None:
    adapter, hub, conn = _adapter()
    old = await _summon_on_route(adapter, hub, ENVELOPES["from_me_dm"], conn)
    hub.routes.pop(PEER)  # a person's message closes the route
    assert not (await adapter.send(PEER, "interim", metadata={"thread_id": old.source.thread_id})).success
    new = await _summon_on_route(adapter, hub, dict(ENVELOPES["from_me_dm"], message_id="other-msg"), conn)
    key = old.source.thread_id
    bodies = len(hub.bodies)

    send = _bound_tool(adapter, chatlytics_send, "chatlytics_send")
    react = _bound_tool(adapter, chatlytics_react, "chatlytics_react")
    caplog.set_level(logging.ERROR)
    sent = json.loads(await _in_turn(key, lambda: send({"chatId": PEER, "text": "old answer"})))
    reacted = json.loads(await _in_turn(key, lambda: react({"messageId": f"false_{PEER}_ABC", "emoji": "x"})))
    assert not sent["success"] and not reacted["success"]
    assert sent["error"] == read_scope.SummonRouteClosed.error
    assert len(hub.bodies) == bodies and hub.left == []
    assert "summon send refused, summon_route_closed" in caplog.text

    assert (await adapter.send(PEER, "new answer", metadata=_turn(new))).success
    await _in_turn(key, lambda: send({"chatId": "999@c.us", "text": "elsewhere"}))  # another chat is not its route
    assert hub.left == [(OWNER_SESSION, PEER, "new answer"), (BOT_SESSION, "999@c.us", "elsewhere")]


async def test_a_tools_own_409_closes_the_route_for_every_later_send() -> None:
    adapter, hub, conn = _adapter()
    old = await _summon_on_route(adapter, hub, ENVELOPES["from_me_dm"], conn)
    key = old.source.thread_id
    hub.routes.pop(PEER)
    send = _bound_tool(adapter, chatlytics_send, "chatlytics_send")
    out = json.loads(await _in_turn(key, lambda: send({"chatId": PEER, "text": "x"})))
    assert out["status_code"] == 409 and read_scope.scope_for_key(key).route_closed
    hub.routes[PEER] = OWNER_SESSION
    assert not json.loads(await _in_turn(key, lambda: send({"chatId": PEER, "text": "y"})))["success"]
    assert not (await adapter.send(PEER, "z", metadata={"thread_id": key})).success
    assert hub.left == []


@pytest.mark.parametrize("chat,code", [(OTHER_GROUP, "from_me_route_expired"), (PEER, "SESSION_CANNOT_REACH_TARGET")])
async def test_a_tools_other_409_closes_nothing(chat: str, code: str) -> None:
    adapter, hub, conn = _adapter(_RefusingHub(chat, code))
    summon = await _summon_on_route(adapter, hub, ENVELOPES["from_me_dm"], conn)
    send = _bound_tool(adapter, chatlytics_send, "chatlytics_send")
    await _in_turn(summon.source.thread_id, lambda: send({"chatId": chat, "text": "x"}))
    assert not read_scope.scope_for_key(summon.source.thread_id).route_closed


async def test_an_ordinary_tools_409_closes_no_summon() -> None:
    adapter, hub, conn = _adapter(_RefusingHub(PEER, "from_me_route_expired"))
    summon = await _summon_on_route(adapter, hub, ENVELOPES["from_me_dm"], conn)
    send = _bound_tool(adapter, chatlytics_send, "chatlytics_send")
    await _in_turn("", lambda: send({"chatId": PEER, "text": "x"}))
    assert not read_scope.scope_for_key(summon.source.thread_id).route_closed


@pytest.mark.parametrize("via", ["tool", "adapter"])
async def test_a_refusal_logs_the_same_line_on_every_path(via: str, caplog: Any) -> None:
    adapter, hub, conn = _adapter()
    summon = await _summon_on_route(adapter, hub, LID_DM, conn)
    key = summon.source.thread_id
    caplog.set_level(logging.DEBUG)
    if via == "tool":
        tool = _bound_tool(adapter, chatlytics_send, "chatlytics_send")
        await _in_turn(key, lambda: tool({"chatId": "15550002222@c.us", "text": "x"}))
    else:
        await _in_turn(key, lambda: adapter.send("15550002222@c.us", "x"))
    lines = [r for r in caplog.records if "summon send refused" in r.getMessage()]
    assert [r.levelname for r in lines] == ["ERROR"]
    assert lines[0].getMessage() == (
        f"chatlytics#956: summon send refused, summon_target_unclear chat=15550002222@c.us scope={key}"
    )
    assert hub.bodies == []


async def test_a_lost_summons_tool_logs_its_refusal(caplog: Any) -> None:
    adapter, hub, _ = _adapter()
    tool = _bound_tool(adapter, chatlytics_send, "chatlytics_send")
    caplog.set_level(logging.ERROR)
    await _in_turn(read_scope.LOST_KEY, lambda: tool({"chatId": PEER, "text": "x"}))
    assert "summon send refused, summon_scope_lost" in caplog.text


async def test_a_refused_target_is_cut_down_for_the_log(caplog: Any) -> None:
    adapter, hub, conn = _adapter()
    summon = await _summon_on_route(adapter, hub, LID_DM, conn)
    tool = _bound_tool(adapter, chatlytics_send, "chatlytics_send")
    caplog.set_level(logging.ERROR)
    await _in_turn(summon.source.thread_id, lambda: tool({"chatId": "Dana\nFAKE LINE " + "x" * 200, "text": "x"}))
    line = next(r.getMessage() for r in caplog.records if "summon send refused" in r.getMessage())
    assert "\n" not in line and "x" * 65 not in line


class _ActionHub(_Hub):
    """The hub's /api/v1/actions for a bot: api-v1.ts with bot-action-authz and fromMeRouteActionTargetsChat.

    The route is kept under every alias of the chat, as recordFromMeRoute does.
    """

    SEND_CLASS = {"poll", "send", "sendPoll", "forwardMessage"}
    ROUTE = {"react", "startTyping", "stopTyping", "sendSeen"}

    def __init__(self) -> None:
        super().__init__()
        self.ran: List[Tuple[str, str, Dict[str, Any]]] = []  # (session it ran on, action, params)

    async def post(self, path: str, json: Dict[str, Any], timeout: Any = None) -> _Resp:  # noqa: A002
        if path != "/api/v1/actions":
            return await super().post(path, json, timeout)
        self.bodies.append((path, dict(json)))
        action, params = json["action"], json.get("params") or {}
        if action in self.SEND_CLASS:
            return _Resp(403, {"success": False, "error": "bot_send_via_dispatch_denied"})
        session = self.own
        if action in self.ROUTE:
            chat = params.get("chatId")
            verdict = self._verdict(json.get("session"), chat or "") if chat else self.own
            if verdict is None:
                return _Resp(409, {"success": False, "code": "from_me_route_expired"})
            if verdict != self.own:
                ids = [params.get("messageId"), *(params.get("messageIds") or [])]
                for i in filter(None, ids):
                    named = i.split("_")[1] if i.count("_") >= 2 else ""
                    if named.replace("@s.whatsapp.net", "@c.us") != chat:
                        return _Resp(403, {"success": False, "code": "from_me_route_target_mismatch"})
            session = verdict
        self.ran.append((session, action, dict(params)))
        return _Resp(200, {"success": True, "session_used": session})


LID_WITH_PHONE = {**LID_DM, "chat_phone": "15550001111"}
PHONE_JID = "15550001111@c.us"


async def _lid_summon(adapter: Any, hub: _Hub, conn: _BotConn) -> str:
    summon = await _summon_on_route(adapter, hub, LID_WITH_PHONE, conn)
    hub.routes[PHONE_JID] = OWNER_SESSION
    return summon.source.thread_id


@pytest.mark.parametrize("given,mid,sent", [
    (PHONE_JID, f"false_{PHONE_JID}_ABC", PHONE_JID),
    ("15550001111", f"false_{PHONE_JID}_ABC", PHONE_JID),
    (None, f"false_{PHONE_JID}_ABC", PHONE_JID),
    (PHONE_JID, f"false_{LID_CHAT}_ABC", LID_CHAT),
    (LID_CHAT, f"false_{LID_CHAT}_ABC", LID_CHAT),
    ("999@c.us", f"false_{LID_CHAT}_ABC", LID_CHAT),
])
async def test_a_reaction_keeps_the_chat_its_message_id_names(given: Optional[str], mid: str, sent: str) -> None:
    adapter, hub, conn = _adapter(_ActionHub())
    key = await _lid_summon(adapter, hub, conn)
    tool = _bound_tool(adapter, chatlytics_react, "chatlytics_react")
    args = {"messageId": mid, "emoji": "x", **({"chatId": given} if given else {})}
    assert json.loads(await _in_turn(key, lambda: tool(args)))["success"]
    assert hub.ran == [(OWNER_SESSION, "react", {"messageId": mid, "emoji": "x", "chatId": sent})]


async def test_a_reaction_with_an_id_that_names_no_chat_still_takes_the_envelopes_spelling() -> None:
    adapter, hub, conn = _adapter(_ActionHub())
    key = await _lid_summon(adapter, hub, conn)
    tool = _bound_tool(adapter, chatlytics_react, "chatlytics_react")
    await _in_turn(key, lambda: tool({"messageId": "ABC", "emoji": "x", "chatId": "15550001111"}))
    assert hub.bodies[-1][1]["params"]["chatId"] == LID_CHAT and hub.bodies[-1][1]["session"] == OWNER_SESSION


async def test_a_reaction_to_another_chats_message_stays_on_the_bot() -> None:
    adapter, hub, conn = _adapter(_ActionHub())
    key = await _lid_summon(adapter, hub, conn)
    tool = _bound_tool(adapter, chatlytics_react, "chatlytics_react")
    other = "888@lid"
    await _in_turn(key, lambda: tool({"messageId": f"false_{other}_ABC", "emoji": "x", "chatId": other}))
    assert hub.ran[-1][0] == BOT_SESSION and "session" in hub.bodies[-1][1]


async def test_a_message_id_this_summon_cant_place_is_refused() -> None:
    adapter, hub, conn = _adapter(_ActionHub())
    summon = await _summon_on_route(adapter, hub, LID_DM, conn)  # no chat_phone
    tool = _bound_tool(adapter, chatlytics_react, "chatlytics_react")
    out = json.loads(await _in_turn(summon.source.thread_id, lambda: tool(
        {"messageId": f"false_{PHONE_JID}_ABC", "emoji": "x", "chatId": LID_CHAT})))
    assert not out["success"] and hub.bodies == []


async def test_seen_by_message_ids_names_the_owner_through_dispatch() -> None:
    adapter, hub, conn = _adapter(_ActionHub())
    key = await _lid_summon(adapter, hub, conn)
    tool = _bound_tool(adapter, chatlytics_dispatch, "chatlytics_dispatch")
    ids = [f"false_{PHONE_JID}_A", f"false_{PHONE_JID}_B"]
    await _in_turn(key, lambda: tool({"action": "sendSeen", "parameters": {"messageIds": ids}}))
    assert hub.ran == [(OWNER_SESSION, "sendSeen", {"messageIds": ids, "chatId": PHONE_JID})]


async def test_a_session_a_model_names_doesnt_move_a_summons_reaction() -> None:
    adapter, hub, conn = _adapter(_ActionHub())
    summon = await _summon_on_route(adapter, hub, ENVELOPES["from_me_dm"], conn)
    tool = _bound_tool(adapter, chatlytics_dispatch, "chatlytics_dispatch")
    await _in_turn(summon.source.thread_id, lambda: tool(
        {"action": "react", "target": PEER, "session": BOT_SESSION, "parameters": {"messageId": f"false_{PEER}_A", "emoji": "x"}}))
    assert hub.ran[-1][0] == OWNER_SESSION


async def test_on_an_ordinary_turn_an_action_is_exactly_what_it_was() -> None:
    adapter, hub, conn = _adapter(_ActionHub())
    await _lid_summon(adapter, hub, conn)
    tool = _bound_tool(adapter, chatlytics_dispatch, "chatlytics_dispatch")
    mid = f"false_{PHONE_JID}_ABC"
    await _in_turn("", lambda: tool({"action": "react", "target": LID_CHAT, "parameters": {"messageId": mid, "emoji": "x"}}))
    assert hub.bodies[-1][1] == {
        "action": "react", "params": {"chatId": LID_CHAT, "messageId": mid, "emoji": "x"}, "session": BOT_SESSION,
    }
    await _in_turn("", lambda: tool({"action": "react", "target": LID_CHAT, "session": "theirs", "parameters": {"messageId": mid, "emoji": "x"}}))
    assert hub.bodies[-1][1]["session"] == "theirs"


_OFF_ROUTE_TOOLS = [
    (chatlytics_poll, "chatlytics_poll", {"chatId": PEER, "question": "q", "options": ["a", "b"]}),
    (chatlytics_edit, "chatlytics_edit", {"chatId": PEER, "messageId": "ABC", "text": "t"}),
    (chatlytics_unsend, "chatlytics_unsend", {"chatId": PEER, "messageId": "ABC"}),
    (chatlytics_delete, "chatlytics_delete", {"chatId": PEER, "messageId": "ABC"}),
    (chatlytics_pin, "chatlytics_pin", {"chatId": PEER, "messageId": "ABC"}),
    (chatlytics_unpin, "chatlytics_unpin", {"chatId": PEER, "messageId": "ABC"}),
    (chatlytics_pin, "chatlytics_pin", {"messageId": f"false_{PEER}_ABC"}),
    (chatlytics_edit, "chatlytics_edit", {"messageId": f"true_{PEER}_ABC", "text": "t"}),
    (chatlytics_poll, "chatlytics_poll", {"chatId": PEER_DIGITS, "question": "q", "options": ["a", "b"]}),
    (chatlytics_dispatch, "chatlytics_dispatch", {"action": "send", "target": PEER, "parameters": {"text": "x"}}),
    (chatlytics_dispatch, "chatlytics_dispatch", {"action": "sendPoll", "parameters": {"chatId": PEER}}),
    (chatlytics_dispatch, "chatlytics_dispatch", {"action": "sendImage", "parameters": {"to": PEER}}),
    (chatlytics_dispatch, "chatlytics_dispatch", {"action": "forwardMessage", "target": f"whatsapp:{PEER}"}),
    (chatlytics_dispatch, "chatlytics_dispatch", {"action": "pin", "target": PEER, "session": OWNER_SESSION}),
    (chatlytics_dispatch, "chatlytics_dispatch", {"action": "someNewVerb", "parameters": {"target": PEER}}),
]


@pytest.mark.parametrize("handler,name,args", _OFF_ROUTE_TOOLS)
async def test_an_action_the_hub_cant_route_is_refused_in_the_summoned_chat(handler: Any, name: str, args: Dict[str, Any], caplog: Any) -> None:
    adapter, hub, conn = _adapter(_ActionHub())
    summon = await _summon_on_route(adapter, hub, ENVELOPES["from_me_dm"], conn)
    tool = _bound_tool(adapter, handler, name)
    caplog.set_level(logging.ERROR)
    out = json.loads(await _in_turn(summon.source.thread_id, lambda: tool(args)))
    assert out == {"success": False, "error": read_scope.SummonActionOffRoute.error}
    assert hub.bodies == [] and hub.ran == []
    assert "summon send refused, summon_action_off_route" in caplog.text


@pytest.mark.parametrize("handler,name,args", [
    (chatlytics_pin, "chatlytics_pin", {"chatId": "999@c.us", "messageId": "ABC"}),
    (chatlytics_edit, "chatlytics_edit", {"messageId": "false_999@c.us_ABC", "text": "t"}),
    (chatlytics_dispatch, "chatlytics_dispatch", {"action": "muteChat", "target": OTHER_GROUP}),
    (chatlytics_dispatch, "chatlytics_dispatch", {"action": "search", "parameters": {"query": "Dana"}}),
])
async def test_an_action_elsewhere_or_on_no_chat_stays_as_designed(handler: Any, name: str, args: Dict[str, Any]) -> None:
    adapter, hub, conn = _adapter(_ActionHub())
    summon = await _summon_on_route(adapter, hub, ENVELOPES["from_me_dm"], conn)
    tool = _bound_tool(adapter, handler, name)
    assert json.loads(await _in_turn(summon.source.thread_id, lambda: tool(args)))["success"]
    assert hub.ran[-1][0] == BOT_SESSION and "session" not in hub.bodies[-1][1]


@pytest.mark.parametrize("handler,name,args", [
    entry for entry in _OFF_ROUTE_TOOLS if entry[1] != "chatlytics_dispatch"
])
async def test_on_an_ordinary_turn_those_actions_reach_the_hub_as_before(handler: Any, name: str, args: Dict[str, Any]) -> None:
    adapter, hub, conn = _adapter(_ActionHub())
    await _summon_on_route(adapter, hub, ENVELOPES["from_me_dm"], conn)
    tool = _bound_tool(adapter, handler, name)
    await _in_turn("", lambda: tool(args))
    assert len(hub.bodies) == 1 and "session" not in hub.bodies[0][1]


async def test_an_action_on_what_may_be_this_person_is_refused() -> None:
    adapter, hub, conn = _adapter(_ActionHub())
    summon = await _summon_on_route(adapter, hub, LID_DM, conn)  # no chat_phone
    tool = _bound_tool(adapter, chatlytics_pin, "chatlytics_pin")
    out = json.loads(await _in_turn(summon.source.thread_id, lambda: tool({"chatId": PHONE_JID, "messageId": "ABC"})))
    assert not out["success"] and hub.bodies == []


async def test_an_action_of_a_lost_summon_is_refused() -> None:
    adapter, hub, _ = _adapter(_ActionHub())
    tool = _bound_tool(adapter, chatlytics_pin, "chatlytics_pin")
    out = json.loads(await _in_turn(read_scope.LOST_KEY, lambda: tool({"chatId": PEER, "messageId": "ABC"})))
    assert out == {"success": False, "error": read_scope.SummonScopeLost.error} and hub.bodies == []


async def test_a_reactions_own_409_closes_the_route_too() -> None:
    adapter, hub, conn = _adapter(_ActionHub())
    summon = await _summon_on_route(adapter, hub, ENVELOPES["from_me_dm"], conn)
    key = summon.source.thread_id
    hub.routes.pop(PEER)
    tool = _bound_tool(adapter, chatlytics_react, "chatlytics_react")
    out = json.loads(await _in_turn(key, lambda: tool({"messageId": f"false_{PEER}_ABC", "emoji": "x", "chatId": PEER})))
    assert out["status_code"] == 409 and read_scope.scope_for_key(key).route_closed


async def test_a_tools_409_with_a_body_that_is_not_json_is_a_failure_and_closes_nothing() -> None:
    class _Html(_Hub):
        async def post(self, path: str, json: Dict[str, Any], timeout: Any = None) -> Any:  # noqa: A002
            self.bodies.append((path, dict(json)))
            resp = _Resp(409, {})

            def bad() -> Any:
                raise ValueError("not json")

            resp.json = bad  # type: ignore[method-assign]
            return resp

    adapter, hub, conn = _adapter(_Html())
    summon = await _summon_on_route(adapter, hub, ENVELOPES["from_me_dm"], conn)
    tool = _bound_tool(adapter, chatlytics_send, "chatlytics_send")
    out = json.loads(await _in_turn(summon.source.thread_id, lambda: tool({"chatId": PEER, "text": "x"})))
    assert out["success"] is False and out["status_code"] == 409
    assert not read_scope.scope_for_key(summon.source.thread_id).route_closed


# --- the hub's own MCP tools that send or act --------------------------------------------------------

# Copied from src/mcp-server.ts on the hub: what the tool is called and what it says it does.
_HUB_TOOLS = {
    "send_message": "Send a WhatsApp text message. Routes through the mimicry gate (time gate + hourly cap).",
    "send_media": "Send a WhatsApp media message (image, video, file, or voice). Routes through the mimicry gate.",
    "send_poll": "Send a WhatsApp poll message. Routes through the mimicry gate.",
    "send_reaction": "Send an emoji reaction to a WhatsApp message.",
    "manage_group": "Manage a WhatsApp group: info/get_invite_code are reads.",
    "update_settings": "Update a Chatlytics config value. Path must be under 'channels.waha'.",
    "session_update_filters": "Update event type filters for a WAHA session.",
    "chatlytics_dispatch_action": "Execute an action by name.",
    "chatlytics_access": "Give THIS bot access to a chat, or take it away.",
    "chatlytics_gates": "Read or change THIS bot's own session send gates.",
    "chatlytics_short_links_create": "Create a tracked short link with the URL Shortener add-on.",
    "chatlytics_update_config": "Update a config value.",
    "chatlytics_module_config": "Get or update configuration for a specific module.",
    "chatlytics_toggle_module": "Enable or disable a module.",
    "chatlytics_bots_create": "Operator only. Create a bot.",
    "chatlytics_bots_set_default": "Operator only. Set the default bot.",
    "chatlytics_pairings": "Operator only. List, add or remove a bot's pairings.",
}
_HUB_ARGS = {"action": "send", "chatId": PEER, "text": "hi"}
_REFUSED = "hub_tool_skips_summon_checks"
_LONG_SERVER = "a_very_long_operator_chosen_server_name_nothing_more"


@pytest.fixture
def mcp_tools() -> Any:
    """Register MCP tools in the host's real registry, under the name and toolset the host gives them."""
    registry = pytest.importorskip("tools.registry").registry
    schema_module = pytest.importorskip("tools.mcp_tool_schema")
    names: List[str] = []

    def register(server: str, tool: str, description: str) -> str:
        name = schema_module.mcp_prefixed_tool_name(server, tool)
        registry.register(name=name, toolset=f"mcp-{server}", handler=lambda *_a, **_k: "{}", schema={
            "name": name, "description": description, "parameters": {"type": "object", "properties": {}},
        })
        names.append(name)
        return name

    yield register
    for name in names:
        registry.deregister(name)


def _refused_by_hub_check(verdict: Any) -> bool:
    return verdict is not None and verdict["action"] == "block" and f"Refused ({_REFUSED})" in verdict["message"]


@pytest.mark.parametrize("tool", sorted(_HUB_TOOLS))
def test_every_write_of_the_hub_is_refused_under_a_name_the_operator_chose(tool: str, mcp_tools: Any) -> None:
    name = mcp_tools("acme_hub", tool, _HUB_TOOLS[tool])
    assert name == f"mcp__acme_hub__{tool}"
    verdict = _summon_pre(name, _HUB_ARGS)
    assert _refused_by_hub_check(verdict)
    assert "chatlytics_send" in verdict["message"]


@pytest.mark.parametrize("tool", ["send_message", "manage_group", "chatlytics_dispatch_action"])
def test_a_hub_write_is_refused_inside_the_wrapper(tool: str, mcp_tools: Any) -> None:
    name = mcp_tools("acme_hub", tool, _HUB_TOOLS[tool])
    for args in (
        {"name": name, "arguments": _HUB_ARGS},
        {"calls": [{"name": name, "arguments": json.dumps(_HUB_ARGS)}]},
        {"calls": json.dumps([{"name": "chatlytics_send", "arguments": {}}, {"name": name, "arguments": _HUB_ARGS}])},
    ):
        assert _refused_by_hub_check(_summon_pre("tool_call", args))


@pytest.mark.parametrize("spelled", [
    "mcp__hub__send_message", "mcp_hub_send_message", "mcp-hub-send-message", "mcp__hub.x__send_poll",
    "MCP__HUB__SEND_REACTION", "mcp_my_server_chatlytics_dispatch_action",
])
def test_a_spelling_the_host_has_no_record_of_is_refused(spelled: str) -> None:
    assert _refused_by_hub_check(_summon_pre(spelled, _HUB_ARGS))


def test_with_no_registry_a_hub_write_is_refused(monkeypatch: pytest.MonkeyPatch, mcp_tools: Any) -> None:
    name = mcp_tools("slack", "send_message", "Post to a Slack channel.")
    assert _summon_pre(name, {"text": "hi"}) is None
    monkeypatch.setitem(sys.modules, "tools.registry", None)
    assert _refused_by_hub_check(_summon_pre(name, {"text": "hi"}))


@pytest.mark.parametrize("server,tool,description", [
    ("chatlytics_cloud", "send_message", "Send a message."),  # by the server's name
    ("acme", "send_message", "Send a WhatsApp text message."),  # by what it says, with a filtered catalog
    ("acme", "session_update_filters", "Update event type filters for a WAHA session."),
])
def test_each_sign_of_the_hub_is_enough_on_its_own(server: str, tool: str, description: str, mcp_tools: Any) -> None:
    assert _refused_by_hub_check(_summon_pre(mcp_tools(server, tool, description), {"chatId": PEER}))


def test_a_sibling_only_the_hub_has_is_a_sign_of_the_hub(mcp_tools: Any) -> None:
    name = mcp_tools("acme", "send_message", "Send a message.")
    assert _summon_pre(name, {"text": "hi"}) is None
    mcp_tools("acme", "chatlytics_resolve_entity", "Find a contact.")
    assert _refused_by_hub_check(_summon_pre(name, {"text": "hi"}))


@pytest.mark.parametrize("tool", ["send_message", "chatlytics_dispatch_action", "manage_group"])
def test_a_hub_write_is_refused_when_the_host_cut_its_name(tool: str, mcp_tools: Any) -> None:
    name = mcp_tools(_LONG_SERVER, tool, "Send a WhatsApp thing.")
    assert len(name) == 64 and not name.endswith(tool)
    assert _refused_by_hub_check(_summon_pre(name, {"chatId": PEER}))


def test_another_servers_tool_is_left_alone_when_the_hubs_is_refused(mcp_tools: Any) -> None:
    hub = mcp_tools("acme_hub", "send_message", _HUB_TOOLS["send_message"])
    slack = mcp_tools("slack", "send_message", "Post a message to a Slack channel.")
    mail = mcp_tools("mail", "send_media", "Attach a file to a mail.")
    cut = mcp_tools(_LONG_SERVER, "send_message", "Post to a pager.")
    cut_other = mcp_tools(_LONG_SERVER, "fetch_message", "Send a WhatsApp thing, by its description.")
    assert _refused_by_hub_check(_summon_pre(hub, {"chatId": PEER}))
    assert _summon_pre(slack, {"channel": "c", "text": "hi"}) is None
    assert _summon_pre(mail, {"to": "a@example.test"}) is None
    assert _summon_pre(cut, {"text": "hi"}) is None
    assert _summon_pre(cut_other, {"text": "hi"}) is None
    assert _summon_pre("tool_call", {"calls": [{"name": slack, "arguments": {"text": "hi"}}]}) is None
    assert _refused_by_hub_check(_summon_pre("tool_call", {"calls": [{"name": hub, "arguments": {}}]}))


def test_the_plugins_own_tools_pass_when_the_hubs_are_refused(mcp_tools: Any) -> None:
    hub = mcp_tools("acme_hub", "send_message", _HUB_TOOLS["send_message"])
    assert _refused_by_hub_check(_summon_pre(hub, {"chatId": PEER}))
    for tool, args in (
        ("chatlytics_send", {"chatId": PEER, "text": "hi"}),
        ("chatlytics_react", {"messageId": "x", "emoji": "y"}),
        ("chatlytics_access", {"action": "list_grants"}),
        ("send_message", {"target": "x", "message": "hi"}),  # the host's own
    ):
        assert _summon_pre(tool, args) is None


def test_the_hubs_tools_that_only_read_names_stay_allowed(mcp_tools: Any) -> None:
    hub = mcp_tools("acme_hub", "send_message", _HUB_TOOLS["send_message"])
    assert _refused_by_hub_check(_summon_pre(hub, {"chatId": PEER}))
    for tool in ("search", "get_directory", "get_status", "chatlytics_list_actions"):
        assert _summon_pre(mcp_tools("acme_hub", tool, f"Hub {tool}."), {"query": "x"}) is None


@pytest.mark.parametrize("tool", sorted(_HUB_TOOLS))
def test_an_ordinary_turn_is_unchanged(tool: str, mcp_tools: Any) -> None:
    name = mcp_tools("acme_hub", tool, _HUB_TOOLS[tool])
    wrapped = {"calls": [{"name": name, "arguments": _HUB_ARGS}]}
    # The same calls: refused while a summon is bound to these ids, untouched once a request released them.
    assert _refused_by_hub_check(_summon_pre(name, _HUB_ARGS))
    read_scope.note_request(False, "", "s", "s")
    assert pre_tool_call(tool_name=name, args=_HUB_ARGS, session_id="s", task_id="s") is None
    assert pre_tool_call(tool_name="tool_call", args=wrapped, session_id="s", task_id="s") is None
    assert pre_tool_call(tool_name=name, args=_HUB_ARGS, session_id="never", task_id="seen") is None


def test_the_refusal_keeps_the_log_line_and_names_the_way_to_send(
    mcp_tools: Any, caplog: pytest.LogCaptureFixture
) -> None:
    name = mcp_tools("acme_hub", "send_message", _HUB_TOOLS["send_message"])
    with caplog.at_level(logging.WARNING, logger=read_scope.__name__):
        verdict = _summon_pre(name, {"chatId": PEER, "text": "private words"})
    assert "Use chatlytics_send" in verdict["message"] and "read outside" not in verdict["message"]
    lines = [r.getMessage() for r in caplog.records if "summon read refused" in r.getMessage()]
    assert len(lines) == 1
    assert re.fullmatch(
        rf"chatlytics#956: summon read refused tool={name} reason={_REFUSED} scope=summon-[0-9a-f]{{16}} turn=",
        lines[0],
    )
    assert "private words" not in caplog.text


async def test_a_summons_reply_and_chatlytics_send_post_while_the_hubs_tools_are_refused(mcp_tools: Any) -> None:
    hub_tool = mcp_tools("acme_hub", "send_message", _HUB_TOOLS["send_message"])
    adapter, hub, conn = _adapter()
    summon = await _deliver(adapter, ENVELOPES["from_me_dm"], conn)
    key = summon.source.thread_id
    hub.routes[PEER] = OWNER_SESSION
    read_scope.note_request(True, f"x:{key}", "s", "s")

    refused = pre_tool_call(tool_name=hub_tool, args={"chatId": PEER, "text": "x"}, session_id="s", task_id="s")
    assert _refused_by_hub_check(refused)
    assert pre_tool_call(
        tool_name="chatlytics_send", args={"chatId": PEER, "text": "tool"}, session_id="s", task_id="s"
    ) is None

    tool = _bound_tool(adapter, chatlytics_send, "chatlytics_send")
    await _in_turn(key, lambda: tool({"chatId": PEER, "text": "tool"}))
    assert (await adapter.send(PEER, "the answer", metadata=_turn(summon))).success
    assert hub.left == [(OWNER_SESSION, PEER, "tool"), (OWNER_SESSION, PEER, "the answer")]
