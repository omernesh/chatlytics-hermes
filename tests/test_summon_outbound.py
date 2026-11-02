"""chatlytics#956: which account a send leaves from, and the other fixes of the same review.

``_Hub`` answers the way the hub's ``resolveBotSendSession`` does (src/from-me-bot.ts), so these
tests read the account a message LEFT from, not only the body. Fake ids only.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

import pytest

from chatlytics_hermes import read_scope
from chatlytics_hermes.adapter import _BotConn, _make_tool_handler
from chatlytics_hermes.read_scope import pre_tool_call
from chatlytics_hermes.tools import chatlytics_react, chatlytics_send
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
    assert "scope lost" in caplog.text


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


async def test_summons_kept_per_chat_are_bounded() -> None:
    adapter, _, conn = _adapter()
    for _i in range(40):
        await _deliver(adapter, ENVELOPES["from_me_dm"], conn)
    assert len(adapter._summon_turns) == 16


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
    ("mcp__chatlytics__send_message", {"chatId": PEER, "text": "see sessions.json"}),
    ("mcp__memorygraph__search", {"query": "lextrove"}),
    ("mcp_memory_graph_search", {"query": "state.db"}),
    ("mcp__slack__post", {"channel": "c1", "text": "Lextrove is our archive"}),
    ("mcp__mail__send", {"to": "a@example.test", "subject": "lextrove", "body": "about lextrove"}),
    ("memory", {"action": "search", "query": "lextrove"}),
    ("send_message", {"target": "x", "message": "Lextrove"}),
])
def test_mentioning_the_word_is_not_refused(tool: str, args: Any) -> None:
    assert _summon_pre(tool, args) is None


@pytest.mark.parametrize("tool,args", [
    ("mcp__fetch__fetch", {"url": "https://lextrove.example/api?session=all"}),
    ("mcp_fetch_fetch", {"url": "https://lextrove.example/api?session=all"}),
    ("mcp__sql__query", {"query": "select * from lextrove.messages"}),
    ("mcp__fs__read_file", {"path": "/home/x/.hermes/state.db"}),
    ("mcp__ssh__exec", {"host": "h", "command": "cat /x/.hermes/sessions/a.jsonl"}),
    ("mcp__http__post", {"request": {"target": "https://lextrove.example"}}),
    ("mcp__x__y", "lextrove as text that isn't json"),
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


def test_a_single_underscore_dispatch_that_only_sends_passes() -> None:
    assert _summon_pre("mcp_chatlytics_chatlytics_dispatch", {"action": "send"}) is None


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
