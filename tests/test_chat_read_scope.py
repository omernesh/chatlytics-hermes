"""chatlytics#956: the chat read scope. Reads only: outside the owner's own DM with the bot, a Chatlytics
turn reads only its chat on the bot's session, and nothing else about it changes.

Placeholders only: this repo is public.
"""

from __future__ import annotations

import contextvars
import json
import logging
import threading
import types
from typing import Any, Callable, Dict, List, Optional

import pytest

from chatlytics_hermes import memory_strip, read_scope
from chatlytics_hermes.memory_strip import is_summoned_request, llm_request_middleware
from chatlytics_hermes.owner import is_owner_dm
from chatlytics_hermes.read_scope import pre_tool_call, transform_tool_result
from chatlytics_hermes.tools import chatlytics_send
from chatlytics_hermes.trigger_context import CHAT_LIMIT_MARKER, CHAT_SCOPE_TOKEN, NO_IDS_RULE, TURN_CONTEXT_MARKER
from tests.test_owner_tagging import WEBHOOK_SECRET, _dispatch, _lp_adapter, _post, _wh_adapter  # noqa: F401
from tests.test_read_scope_real_agent import _call, _everyone, _text, _tool_rows, _tools, host, store  # noqa: F401
from tests.test_summon_outbound import _adapter as _hub_adapter, _bound_tool, _deliver
from tests.test_trigger_context import ENVELOPES, OWNER, OWNER_SESSION, PEER
from tests._fixtures import HOST_TURN_ACTOR

BOT = ENVELOPES["third_party_dm"]["session_id"]
GROUP = ENVELOPES["from_me_group"]["entity_jid"]
OWNER_LID = "200000000000001@lid"
MEMBER = "972544000007@c.us"
OTHER_CHAT = "972544000099@c.us"
SID = "20260101_000000_chat"
LM = "mcp__lextrove__list_messages"
SUMMON_LM = "mcp__lextrove_limited__list_messages"
EVERY_ACCOUNT = {"limit": 1, "session": "all"}


def _env(chat: str, sender: str, chat_type: str = "dm", session: str = BOT, **more: Any) -> Dict[str, Any]:
    return {"session_id": session, "chat_type": chat_type, "entity_jid": chat, "sender_jid": sender, "text": "hi", **more}


OWNER_DM = _env(OWNER, OWNER)
THIRD_DM = dict(ENVELOPES["third_party_dm"])
MEMBER_GROUP = _env(GROUP, MEMBER, "group")
# Measured live: the owner's trigger, delivered from the bot's own leg with no trigger_source.
OWNER_GROUP = _env(GROUP, OWNER, "group")
LIMITED = {"third party DM": (THIRD_DM, PEER), "group member": (MEMBER_GROUP, GROUP), "owner in a group": (OWNER_GROUP, GROUP)}


def _bot(**extra: Any) -> Any:
    return _lp_adapter(**(extra or {"allow_admin_from": [OWNER, OWNER_LID], "group_allow_admin_from": [OWNER]}))


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr(read_scope, "_not_ready_logged", False)
    yield
    for table in (read_scope._bound, read_scope._scopes, read_scope._chat_scopes, read_scope._chat_turns):
        table.clear()


def _request(event: Any, session_id: str = SID, task_id: str = SID, *, system: Optional[str] = None) -> Any:
    """A provider request of the turn, through the real middleware. Returns what it says to do."""
    prompt = system if system is not None else (getattr(event, "channel_prompt", None) or "the system prompt")
    request = {"messages": [{"role": "system", "content": prompt}, {"role": "user", "content": "q"}]}
    return llm_request_middleware(request=request, session_id=session_id, task_id=task_id, turn_id="t")


def _hook(tool: str, args: Any = None, session_id: str = SID, task_id: str = SID) -> Optional[Dict[str, str]]:
    return pre_tool_call(tool_name=tool, args=args if args is not None else {}, session_id=session_id, task_id=task_id, turn_id="t")


def _reason(verdict: Optional[Dict[str, str]]) -> Optional[str]:
    if verdict is None:
        return None
    assert verdict["action"] == "block"
    return verdict["message"].split("(", 1)[1].split(")", 1)[0]


def _in_context(fn: Callable[[], Any], **session_vars: Any) -> Any:
    """In a gateway turn's context variables, with nothing bound by id: what a child agent has."""
    from gateway import session_context as sc

    def run() -> Any:
        sc.set_session_vars(**session_vars)
        return fn()

    return contextvars.copy_context().run(run)


async def _limited(env: Dict[str, Any]) -> Any:
    event = await _dispatch(_bot(), dict(env))
    _request(event)
    return event


# --- which turns ---------------------------------------------------------------------------------


async def test_the_owners_own_dm_has_no_scope_and_reads_every_account() -> None:
    event = await _dispatch(_bot(), dict(OWNER_DM))
    assert _request(event) is None
    assert not event.channel_prompt
    assert _hook(LM, EVERY_ACCOUNT) is None
    # And by the context variables, where a subagent looks.
    assert _in_context(lambda: _hook(LM, EVERY_ACCOUNT, "child", "child"), platform="chatlytics", chat_id=OWNER) is None


@pytest.mark.parametrize("name", list(LIMITED))
async def test_every_other_turn_is_limited_to_its_chat_on_the_bots_session(name: str) -> None:
    env, chat = LIMITED[name]
    await _limited(env)
    assert _reason(_hook(LM, EVERY_ACCOUNT)) == "session_not_summon"
    assert _reason(_hook(LM, {"chat_id": chat, "session": "all"})) == "session_not_summon"
    assert _reason(_hook(LM, {"chat_id": chat, "session": OWNER_SESSION})) == "session_not_summon"
    assert _reason(_hook(LM, {"chat_id": chat})) == "session_missing"
    assert _reason(_hook(LM, {"chat_id": OTHER_CHAT, "session": BOT})) == "chat_not_summon"
    assert _reason(_hook("tool_call", {"calls": [{"name": LM, "arguments": EVERY_ACCOUNT}]})) == "session_not_summon"
    assert _reason(_hook("mcp__lextrove__list_groups", {})) == "tool_cannot_be_scoped"
    assert _hook(LM, {"chat_id": chat, "session": BOT, "limit": 5}) is None


@pytest.mark.parametrize("name", list(LIMITED))
async def test_the_same_limit_by_the_context_variables(name: str) -> None:
    env, chat = LIMITED[name]
    await _dispatch(_bot(), dict(env))  # No provider request yet: nothing bound by id.
    in_chat = {"platform": "chatlytics", "chat_id": chat}
    assert _reason(_in_context(lambda: _hook(LM, EVERY_ACCOUNT), **in_chat)) == "session_not_summon"
    assert _in_context(lambda: _hook(LM, {"chat_id": chat, "session": BOT}), **in_chat) is None


async def test_a_refusal_gives_the_model_the_ids_of_the_read_that_works() -> None:
    await _limited(THIRD_DM)
    message = _hook(LM, EVERY_ACCOUNT)["message"]
    assert f"chat_id {PEER} and session {BOT}" in message and "cannot read outside this chat" in message


async def test_the_result_check_holds_a_limited_turn_too() -> None:
    await _limited(MEMBER_GROUP)
    other = json.dumps({"data": {"items": [{"id": "m1", "chat_id": OTHER_CHAT, "body": "X"}]}})
    here = json.dumps({"data": {"items": [{"id": "m1", "chat_id": GROUP, "body": "X"}]}})
    replaced = transform_tool_result(tool_name=LM, result=other, session_id=SID, task_id=SID)
    assert "result_row_other_chat" in replaced and "body" not in replaced
    assert transform_tool_result(tool_name=LM, result=here, session_id=SID, task_id=SID) is None
    # An id from an in-scope read may be used, and another may not.
    assert _hook("mcp__lextrove__get_message", {"message_id": "m1"}) is None
    assert _reason(_hook("mcp__lextrove__get_message", {"message_id": "m2"})) == "id_not_from_this_chat"


@pytest.mark.parametrize("tool,args,reason", [
    ("session_search", {"query": "x"}, "transcripts_of_other_chats"),
    ("chatlytics_read", {"chatId": OTHER_CHAT}, "chat_not_summon"),
    ("mcp__hub__read_messages", {"chatId": OTHER_CHAT}, "chatlytics_reads_another_account"),
    ("mcp__hub__chatlytics_dispatch_action", {"action": "getMessages"}, "chatlytics_read_action"),
    ("execute_code", {"code": "print(open('/home/x/.hermes/.env').read())"}, "names_the_archive_or_host_store"),
    ("terminal", {"command": "cat ~/.hermes/state.db"}, "names_the_archive_or_host_store"),
    ("mcp__web__fetch", {"url": "https://mcp.lextrove.example/mcp"}, "names_the_archive_or_host_store"),
])
async def test_the_other_read_routes_are_refused(tool: str, args: Dict[str, Any], reason: str) -> None:
    await _limited(THIRD_DM)
    assert _reason(_hook(tool, args)) == reason


# --- the plugin's chatlytics_read of this chat ----------------------------------------------------


READ = "chatlytics_read"
PEER_PHONE = PEER.split("@")[0]
PEER_LID = "200000000000002@lid"
LID_DM_WITH_PHONE = _env(PEER_LID, PEER_LID, chat_phone=PEER_PHONE)
LID_DM_NO_PHONE = _env(PEER_LID, PEER_LID)


@pytest.mark.parametrize("env,chat_id", [
    pytest.param(THIRD_DM, PEER, id="DM by its JID"),
    pytest.param(THIRD_DM, PEER.upper(), id="DM, another case"),
    pytest.param(THIRD_DM, f"{PEER_PHONE}@s.whatsapp.net", id="DM, the other phone JID"),
    pytest.param(THIRD_DM, PEER_PHONE, id="DM, bare phone"),
    pytest.param(THIRD_DM, f"+{PEER_PHONE}", id="DM, + phone"),
    pytest.param(MEMBER_GROUP, GROUP, id="group by its JID"),
    pytest.param(MEMBER_GROUP, GROUP.upper(), id="group, another case"),
    pytest.param(OWNER_GROUP, GROUP, id="owner in a group"),
    pytest.param(LID_DM_WITH_PHONE, PEER_LID, id="lid DM by its lid"),
    pytest.param(LID_DM_WITH_PHONE, PEER, id="lid DM by the envelope's phone"),
])
async def test_a_limited_turn_reads_its_own_chat_with_chatlytics_read(env: Dict[str, Any], chat_id: str) -> None:
    await _limited(env)
    assert _hook(READ, {"chatId": chat_id}) is None
    assert _hook(READ, {"chatId": chat_id, "limit": 20}) is None
    # Null optional fields, as models send them, are not arguments.
    assert _hook(READ, {"chatId": chat_id, "limit": None}) is None


@pytest.mark.parametrize("env,args,reason", [
    pytest.param(THIRD_DM, {"chatId": OTHER_CHAT}, "chat_not_summon", id="DM, another phone"),
    pytest.param(THIRD_DM, {"chatId": GROUP}, "chat_not_summon", id="DM, a group"),
    pytest.param(THIRD_DM, {"chatId": OWNER_LID}, "chat_not_summon", id="DM, a lid the envelope didn't give"),
    pytest.param(MEMBER_GROUP, {"chatId": PEER}, "chat_not_summon", id="group, a person"),
    pytest.param(MEMBER_GROUP, {"chatId": "120363000000000099@g.us"}, "chat_not_summon", id="group, another group"),
    pytest.param(LID_DM_NO_PHONE, {"chatId": PEER}, "chat_not_summon", id="lid DM with no phone, a phone"),
    pytest.param(THIRD_DM, {"chatId": "Peer Name"}, "chat_not_summon", id="DM, a name"),
    pytest.param(MEMBER_GROUP, {"chatId": "Family group"}, "chat_not_summon", id="group, a name"),
    pytest.param(MEMBER_GROUP, {"chatId": GROUP.split("@")[0] + " x"}, "chat_not_summon", id="group, id-like name"),
    # send_target calls these this chat, and they aren't an id: the hub reads the string as written.
    pytest.param(THIRD_DM, {"chatId": f"+{PEER_PHONE[:3]} {PEER_PHONE[3:]}"}, "chat_not_summon", id="phone with a space"),
    pytest.param(THIRD_DM, {"chatId": f"{PEER_PHONE[:3]}-{PEER_PHONE[3:]}"}, "chat_not_summon", id="phone with a dash"),
    pytest.param(THIRD_DM, {"chatId": f"{PEER} "}, "chat_not_summon", id="a JID with a trailing space"),
    pytest.param(THIRD_DM, {}, "chat_missing", id="no chat"),
    pytest.param(THIRD_DM, {"limit": 5}, "chat_missing", id="only a limit"),
    pytest.param(THIRD_DM, {"chatId": None}, "chat_missing", id="a null chat"),
    pytest.param(THIRD_DM, {"chatId": ""}, "chat_not_summon", id="an empty chat"),
    pytest.param(THIRD_DM, {"chatId": [PEER]}, "chat_not_summon", id="a list"),
    pytest.param(THIRD_DM, {"chatId": PEER, "session": "all"}, "arg_not_allowed", id="an argument it doesn't take"),
    pytest.param(THIRD_DM, "{not json", "args_unreadable", id="unreadable"),
])
async def test_chatlytics_read_of_anything_else_is_refused(env: Dict[str, Any], args: Any, reason: str) -> None:
    await _limited(env)
    assert _reason(_hook(READ, args)) == reason


async def test_chatlytics_read_goes_through_tool_call_the_same() -> None:
    await _limited(MEMBER_GROUP)
    assert _hook("tool_call", {"calls": [{"name": READ, "arguments": {"chatId": GROUP}}]}) is None
    assert _hook("tool_call", {"calls": json.dumps([{"name": READ, "arguments": json.dumps({"chatId": GROUP})}])}) is None
    assert _hook("tool_call", {"name": READ, "arguments": {"chatId": GROUP}}) is None
    for arguments in ({"chatId": OTHER_CHAT}, {"chatId": "Family group"}):
        assert _reason(_hook("tool_call", {"calls": [{"name": READ, "arguments": arguments}]})) == "chat_not_summon"
    assert _reason(_hook("tool_call", {"calls": [{"name": READ, "arguments": {}}]})) == "chat_missing"
    # One entry out of scope refuses the whole call.
    both = [{"name": READ, "arguments": {"chatId": GROUP}}, {"name": READ, "arguments": {"chatId": OTHER_CHAT}}]
    assert _reason(_hook("tool_call", {"calls": both})) == "chat_not_summon"


async def test_chatlytics_read_by_the_context_variables() -> None:
    await _dispatch(_bot(), dict(MEMBER_GROUP))  # What a delegated subagent has: nothing bound by id.
    in_chat = {"platform": "chatlytics", "chat_id": GROUP}
    assert _in_context(lambda: _hook(READ, {"chatId": GROUP}), **in_chat) is None
    assert _reason(_in_context(lambda: _hook(READ, {"chatId": OTHER_CHAT}), **in_chat)) == "chat_not_summon"
    # A Chatlytics turn this process didn't dispatch knows no chat.
    lost = {"platform": "chatlytics", "chat_id": OTHER_CHAT}
    assert _reason(_in_context(lambda: _hook(READ, {"chatId": OTHER_CHAT}), **lost)) == "no_scope_for_this_turn"


@pytest.mark.parametrize("name,tool", [
    ("from_me_dm", READ),
    ("from_me_group", READ),
    ("from_me_dm", "mcp__hub__read_messages"),
])
async def test_chatlytics_read_stays_refused_on_a_summon_in_its_own_chat(name: str, tool: str) -> None:
    env = ENVELOPES[name]
    await _limited(env)
    args = {"chatId": env["entity_jid"]}
    assert _reason(_hook(tool, args)) == "chatlytics_reads_another_account"
    assert _reason(_hook("tool_call", {"calls": [{"name": tool, "arguments": args}]})) == "chatlytics_reads_another_account"


@pytest.mark.parametrize("tool", [
    "mcp__hub__read_messages",
    "mcp_chatlytics_read_messages",
    "mcp__hub__chatlytics_read",
    "mcp_chatlytics_chatlytics_read",
    "read_messages",
])
async def test_the_hubs_read_stays_refused_on_a_limited_turn_for_its_own_chat(tool: str) -> None:
    # An MCP server's bearer may be another bot's, so this isn't the bot's own number.
    await _limited(MEMBER_GROUP)
    assert _reason(_hook(tool, {"chatId": GROUP})) == "chatlytics_reads_another_account"
    assert _reason(_hook("tool_call", {"calls": [{"name": tool, "arguments": {"chatId": GROUP}}]})) == "chatlytics_reads_another_account"


async def test_chatlytics_read_is_refused_where_the_session_isnt_known() -> None:
    # An unsigned webhook can claim any chat, and this would read that chat on the bot's number.
    adapter = _wh_adapter(secret=False, allow_admin_from=[OWNER])
    _request((await _post(adapter, _hook_payload(PEER, PEER), sign=False))[0])
    assert _reason(_hook(READ, {"chatId": PEER})) == "no_scope_for_this_turn"
    # An envelope on a session that isn't this bearer's.
    adapter, _hub, conn = _hub_adapter()
    _request(await _deliver(adapter, _env(PEER, PEER, session=OWNER_SESSION), conn), "s2", "s2")
    assert _reason(_hook(READ, {"chatId": PEER}, "s2", "s2")) == "no_scope_for_this_turn"


async def test_chatlytics_read_in_the_owners_dm_is_as_before() -> None:
    _request(await _dispatch(_bot(), dict(OWNER_DM)))
    assert _hook(READ, {"chatId": OTHER_CHAT}) is None


async def test_chatlytics_read_is_logged_like_any_other_read(caplog: pytest.LogCaptureFixture) -> None:
    await _limited(MEMBER_GROUP)
    with caplog.at_level(logging.INFO, logger="chatlytics_hermes"):
        _hook(READ, {"chatId": GROUP})
        _hook(READ, {"chatId": OTHER_CHAT})
    assert "chat read allowed kind=chat tool=chatlytics_read" in caplog.text
    assert "chat read refused kind=chat tool=chatlytics_read reason=chat_not_summon" in caplog.text
    assert OTHER_CHAT not in caplog.text


async def test_the_result_of_chatlytics_read_is_not_replaced() -> None:
    # The hub's rows have no chat field, and they're this chat's by the id the check let through.
    await _limited(MEMBER_GROUP)
    rows = json.dumps({"success": True, "data": [{"id": "m1", "from": MEMBER, "body": "x"}]})
    assert transform_tool_result(tool_name=READ, result=rows, session_id=SID, task_id=SID) is None


@pytest.mark.parametrize("env", [THIRD_DM, MEMBER_GROUP, OWNER_GROUP])
async def test_the_line_and_a_refusal_name_chatlytics_read_for_a_bot_with_no_lextrove(env: Dict[str, Any]) -> None:
    how = f"chatlytics_read with chatId {env['entity_jid']}"
    event = await _limited(env)
    assert how in event.channel_prompt
    assert how in _hook(READ, {"chatId": OTHER_CHAT})["message"]


async def test_a_summons_line_and_refusal_dont_offer_chatlytics_read() -> None:
    env = ENVELOPES["from_me_dm"]
    event = await _limited(env)
    assert "use chatlytics_read" not in event.channel_prompt
    assert "use chatlytics_read" not in _hook(READ, {"chatId": env["entity_jid"]})["message"]


async def test_an_unreadable_turn_isnt_told_to_use_chatlytics_read() -> None:
    event = await _limited(_env(PEER, PEER, session=""))
    assert "use chatlytics_read" not in event.channel_prompt and "no readable chat" in event.channel_prompt


# --- who the owner is ----------------------------------------------------------------------------


@pytest.mark.parametrize("sender,chat", [
    (OWNER, OWNER),
    (OWNER_LID, OWNER_LID),
    ("972544000001:26@s.whatsapp.net", OWNER),
    (OWNER_LID, OWNER),  # Both forms are on the list.
])
async def test_the_owners_dm_under_any_listed_form(sender: str, chat: str) -> None:
    event = await _dispatch(_bot(), _env(chat, sender))
    _request(event)
    assert _hook(LM, EVERY_ACCOUNT) is None


@pytest.mark.parametrize("extra,sender,chat", [
    pytest.param({"bot_token": "sk_bot_x"}, OWNER, OWNER, id="no owner on record"),
    pytest.param({"group_allow_admin_from": [OWNER]}, OWNER, OWNER, id="owner of groups only"),
    pytest.param({"allow_admin_from": [OWNER]}, OWNER_LID, OWNER_LID, id="the unlisted form of the owner"),
    pytest.param({"allow_admin_from": [OWNER]}, OWNER, OWNER_LID, id="listed sender, chat under an unlisted id"),
    pytest.param({"allow_admin_from": [OWNER]}, OWNER, PEER, id="the owner's chat with someone, no marker"),
    pytest.param({"allow_admin_from": [OWNER]}, "", OWNER, id="no sender"),
])
async def test_a_turn_that_cant_be_shown_to_be_the_owners_dm_is_limited(extra: Dict[str, Any], sender: str, chat: str) -> None:
    event = await _dispatch(_bot(**extra), _env(chat, sender))
    _request(event)
    assert CHAT_LIMIT_MARKER in event.channel_prompt
    assert _reason(_hook(LM, EVERY_ACCOUNT)) == "session_not_summon"
    assert _hook(LM, {"chat_id": chat, "session": BOT}) is None


async def test_an_admin_in_dms_is_limited_in_a_group_and_a_group_admin_in_a_dm() -> None:
    await _limited(_env(GROUP, OWNER, "group"))
    assert _reason(_hook(LM, EVERY_ACCOUNT)) == "session_not_summon"
    event = await _dispatch(_bot(group_allow_admin_from=[OWNER]), dict(OWNER_DM))
    assert CHAT_LIMIT_MARKER in event.channel_prompt


async def test_tagging_switched_off_leaves_the_owner_the_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    # The opt-out is about the text prefix, as with the access pin.
    monkeypatch.setenv("CHATLYTICS_OWNER_TAGGING", "off")
    event = await _dispatch(_bot(), dict(OWNER_DM))
    assert not event.text.startswith("[owner reply]") and not event.channel_prompt
    _request(event)
    assert _hook(LM, EVERY_ACCOUNT) is None


def test_is_owner_dm_needs_a_dm_chat() -> None:
    from types import SimpleNamespace

    extra = {"allow_admin_from": [OWNER], "group_allow_admin_from": [OWNER]}
    assert is_owner_dm(SimpleNamespace(user_id=OWNER, chat_id=OWNER, chat_type="dm"), extra)
    assert not is_owner_dm(SimpleNamespace(user_id=OWNER, chat_id=GROUP, chat_type="group"), extra)
    assert not is_owner_dm(SimpleNamespace(user_id=OWNER, chat_id=GROUP, chat_type="dm"), extra)
    assert not is_owner_dm(SimpleNamespace(user_id=OWNER, chat_id=OWNER, chat_type="group"), extra)
    assert not is_owner_dm(SimpleNamespace(user_id=OWNER, chat_id=OWNER, chat_type="dm"), {})
    assert not is_owner_dm(None, extra)


# --- webhook -------------------------------------------------------------------------------------


def _hook_payload(chat: str, sender: str, **more: Any) -> Dict[str, Any]:
    return {"chatId": chat, "senderId": sender, "text": "hey", "chatType": "dm", "session": BOT, **more}


async def test_a_signed_webhook_treats_the_owner_and_others_as_longpoll_does() -> None:
    adapter = _wh_adapter(secret=True, allow_admin_from=[OWNER])
    owner = (await _post(adapter, _hook_payload(OWNER, OWNER), sign=True))[0]
    _request(owner)
    assert _hook(LM, EVERY_ACCOUNT) is None
    other = (await _post(adapter, _hook_payload(PEER, PEER), sign=True))[0]
    _request(other)
    assert _reason(_hook(LM, EVERY_ACCOUNT)) == "session_not_summon"
    assert _hook(LM, {"chat_id": PEER, "session": BOT}) is None


@pytest.mark.parametrize("payload", [
    _hook_payload(OWNER, OWNER),
    _hook_payload(OWNER, OWNER, trigger_source="from_me"),
], ids=["ordinary", "claims a summon"])
async def test_an_unsigned_webhook_reads_nothing_whoever_it_claims_to_be(payload: Dict[str, Any]) -> None:
    adapter = _wh_adapter(secret=False, allow_admin_from=[OWNER])
    event = (await _post(adapter, payload, sign=False))[0]
    _request(event)
    assert _reason(_hook(LM, {"chat_id": OWNER, "session": BOT})) == "no_scope_for_this_turn"
    assert "no readable chat" in _hook(LM, {"chat_id": OWNER, "session": BOT})["message"]


# --- the bot's own session, per bot --------------------------------------------------------------


async def test_a_session_that_isnt_the_bots_own_reads_nothing() -> None:
    adapter, _hub, conn = _hub_adapter()
    event = await _deliver(adapter, _env(PEER, PEER, session=OWNER_SESSION), conn)
    _request(event)
    assert _reason(_hook(LM, {"chat_id": PEER, "session": OWNER_SESSION})) == "no_scope_for_this_turn"


@pytest.mark.parametrize("session", [None, "", "bad session!"])
async def test_no_usable_session_on_the_envelope_reads_nothing(session: Any) -> None:
    event = await _dispatch(_bot(), {**THIRD_DM, "session_id": session})
    _request(event)
    assert _reason(_hook(LM, {"chat_id": PEER, "session": BOT})) == "no_scope_for_this_turn"


async def test_each_bot_of_a_gateway_reads_on_the_session_of_the_bearer_it_arrived_on() -> None:
    from chatlytics_hermes.adapter import _BotConn

    adapter, hub, first = _hub_adapter()
    second = _BotConn("sk_bot_second", index=1)
    second.client, second.own_session, second.own_session_confirmed = hub, "bot-two", True
    adapter._conns.append(second)

    event = await _deliver(adapter, _env(PEER, PEER, session="bot-two"), second)
    _request(event)
    assert _hook(LM, {"chat_id": PEER, "session": "bot-two"}) is None
    assert _reason(_hook(LM, {"chat_id": PEER, "session": BOT})) == "session_not_summon"

    event = await _deliver(adapter, _env(PEER, PEER), first)
    _request(event, "s2", "s2")
    assert _hook(LM, {"chat_id": PEER, "session": BOT}, "s2", "s2") is None
    assert _reason(_hook(LM, {"chat_id": PEER, "session": "bot-two"}, "s2", "s2")) == "session_not_summon"
    # And each line names its own scope: the first request is still bound to its own.
    assert _hook(LM, {"chat_id": PEER, "session": "bot-two"}) is None


async def test_two_bots_in_one_chat_each_keep_their_scope_when_the_requests_come_later() -> None:
    # The key names the session. Without that the second dispatch replaces the first's entry, and the
    # first turn's provider request, which comes later, reads on the other bot's session.
    from chatlytics_hermes.adapter import _BotConn

    adapter, hub, first = _hub_adapter()
    second = _BotConn("sk_bot_second", index=1)
    second.client, second.own_session, second.own_session_confirmed = hub, "bot-two", True
    adapter._conns.append(second)

    one = await _deliver(adapter, _env(PEER, PEER), first)
    two = await _deliver(adapter, _env(PEER, PEER, session="bot-two"), second)
    _request(one, "s1", "s1")
    _request(two, "s2", "s2")
    assert _hook(LM, {"chat_id": PEER, "session": BOT}, "s1", "s1") is None
    assert _reason(_hook(LM, {"chat_id": PEER, "session": "bot-two"}, "s1", "s1")) == "session_not_summon"
    assert _hook(LM, {"chat_id": PEER, "session": "bot-two"}, "s2", "s2") is None


# --- several profiles on one gateway (the module and its tables are shared, each adapter has its own list) ---


def _extra_profile(name: str = "other", **extra: Any) -> Any:
    adapter = _lp_adapter(**extra)
    adapter.set_owner_profile(name)  # What the host does for a secondary: _configure_profile_adapter.
    return adapter


def _child(chat: str, profile: str = "") -> Optional[str]:
    """A subagent of a turn of ``profile``: found by the context variables alone."""
    return _reason(_in_context(lambda: _hook(LM, EVERY_ACCOUNT, "a-child", "a-child"), platform="chatlytics", chat_id=chat, profile=profile))


async def test_another_profiles_turn_in_the_chat_doesnt_limit_the_owners_dm() -> None:
    # The owner DMs Sammie, then under the same chat id a bot on a profile that doesn't list them.
    assert (await _dispatch(_bot(), dict(OWNER_DM))).channel_prompt is None
    assert _child(OWNER) is None
    assert CHAT_LIMIT_MARKER in (await _dispatch(_extra_profile(), dict(OWNER_DM))).channel_prompt
    assert _child(OWNER) is None
    assert _child(OWNER, "other") == "session_not_summon"


async def test_the_owners_dm_on_another_profile_doesnt_lift_a_limit() -> None:
    await _dispatch(_extra_profile(), dict(OWNER_DM))
    assert _child(OWNER, "other") == "session_not_summon"
    await _dispatch(_bot(), dict(OWNER_DM))
    assert _child(OWNER) is None
    assert _child(OWNER, "other") == "session_not_summon"


async def test_two_extra_profiles_keep_their_own_verdicts_on_one_chat() -> None:
    await _dispatch(_extra_profile("listed", allow_admin_from=[OWNER]), dict(OWNER_DM))
    await _dispatch(_extra_profile("unlisted"), dict(OWNER_DM))
    assert _child(OWNER, "listed") is None
    assert _child(OWNER, "unlisted") == "session_not_summon"


async def test_a_host_that_doesnt_bind_the_profile_stays_limited() -> None:
    # The read side has no profile: this finds the primary's entry, or none. It never finds another's.
    await _dispatch(_extra_profile(allow_admin_from=[OWNER]), dict(OWNER_DM))
    assert _child(OWNER, "other") is None
    assert _child(OWNER) == "no_scope_for_this_turn"


async def test_a_signed_webhook_is_keyed_by_its_profile_too() -> None:
    adapter = _wh_adapter(secret=True, allow_admin_from=[OWNER])
    adapter.set_owner_profile("other")
    await _post(adapter, _hook_payload(OWNER, OWNER), sign=True)
    assert _child(OWNER, "other") is None
    assert _child(OWNER) == "no_scope_for_this_turn"


def _host_runner(adapter: Any, routed: Optional[str], tmp_path: Any) -> Any:
    """The runner as the real resolve_identity reads it, under gateway.multiplex_profiles."""

    def route(_source: Any, adapter_profile: Optional[str] = None) -> Optional[str]:
        return routed

    return types.SimpleNamespace(
        config=types.SimpleNamespace(multiplex_profiles=True),
        _primary_profile_name="default",
        _transport_owner=lambda _source: None,
        _owning_profile=lambda _adapter, _platform: (True, adapter._owner_profile),
        _profile_name_for_source=route,
        _resolve_profile_home_for_source=lambda _source: tmp_path,
    )


@pytest.mark.parametrize(
    "owner_profile,routed,bound",
    [(None, None, ""), ("other", None, "other"), (None, "routed", "routed"), ("other", "routed", "routed")],
    ids=["primary", "extra profile", "primary routed elsewhere", "extra profile routed elsewhere"],
)
async def test_the_entry_is_where_the_real_host_will_look(owner_profile: Optional[str], routed: Optional[str], bound: str, tmp_path: Any) -> None:
    # DO NOT replace with a stub of either side. The real _canonicalize stamps the profile after
    # dispatch would otherwise read it, and the real _set_session_env is what the hook reads.
    run = pytest.importorskip("gateway.run")
    adapter = _bot()
    adapter.set_owner_profile(owner_profile)
    adapter.gateway_runner = _host_runner(adapter, routed, tmp_path)
    event = await _dispatch(adapter, dict(OWNER_DM))
    assert event.channel_prompt is None
    assert (event.source.profile or "") == bound

    def turn() -> Any:
        host = types.SimpleNamespace(adapters={})
        context = types.SimpleNamespace(source=event.source, session_key="k", session_id="s", **HOST_TURN_ACTOR)
        run.GatewayRunner._set_session_env(host, context)
        return _hook(LM, EVERY_ACCOUNT, "a-child", "a-child")

    assert contextvars.copy_context().run(turn) is None
    assert list(read_scope._chat_turns) == [(bound, OWNER)]


# --- another message in the chat during a turn -----------------------------------------------------


def _read_rows(*ids: str) -> None:
    rows = [{"id": i, "chat_id": GROUP} for i in ids]
    assert transform_tool_result(tool_name=LM, result=json.dumps({"data": {"items": rows}}), session_id=SID, task_id=SID, turn_id="t") is None


GET = "mcp__lextrove__get_message"


async def test_another_message_mid_turn_keeps_the_ids_the_turn_read() -> None:
    adapter = _bot()
    _request(await _dispatch(adapter, dict(MEMBER_GROUP)))
    _read_rows("m1")
    assert _hook(GET, {"message_id": "m1"}) is None
    # Another member's trigger. The running turn's next provider request binds the same line again.
    next_turn = await _dispatch(adapter, _env(GROUP, "972544000008@c.us", "group"))
    _request(next_turn)
    assert _hook(GET, {"message_id": "m1"}) is None
    assert _reason(_hook(GET, {"message_id": "m-not-read"})) == "id_not_from_this_chat"


async def test_another_session_in_the_chat_gets_a_scope_of_its_own() -> None:
    adapter = _bot()
    _request(await _dispatch(adapter, dict(MEMBER_GROUP)))
    _read_rows("m1")
    # Reusing the scope must not keep this session, or the ids that were read on it.
    _request(await _dispatch(adapter, _env(GROUP, MEMBER, "group", session="another")), "s2", "s2")
    assert _reason(_hook(GET, {"message_id": "m1"}, "s2", "s2")) == "id_not_from_this_chat"
    assert _reason(_hook(LM, {"chat_id": GROUP, "session": BOT}, "s2", "s2")) == "session_not_summon"
    assert _hook(LM, {"chat_id": GROUP, "session": "another"}, "s2", "s2") is None


async def test_reusing_the_scope_doesnt_keep_the_owners_verdict() -> None:
    adapter = _bot()
    stranger = _env(OWNER, MEMBER)
    await _dispatch(adapter, dict(stranger))
    assert _child(OWNER) == "session_not_summon"
    await _dispatch(adapter, dict(OWNER_DM))
    assert _child(OWNER) is None
    await _dispatch(adapter, dict(stranger))
    assert _child(OWNER) == "session_not_summon"


async def test_the_chats_facts_are_this_envelopes_when_the_scope_is_reused() -> None:
    adapter = _bot()
    first = await _dispatch(adapter, _env(PEER, PEER, chat_phone="972544000001"))
    scope = next(iter(read_scope._chat_scopes.values()))
    assert scope.phone == "972544000001" and "972544000001@c.us" in scope.chat_forms
    await _dispatch(adapter, _env(PEER, PEER))
    assert read_scope._chat_scopes[scope.key] is scope
    assert scope.phone is None and "972544000001@c.us" not in scope.chat_forms
    assert first.channel_prompt is not None


async def test_a_long_lived_chat_scope_keeps_taking_new_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    # It lasts as long as the process. Stopping at the cap would refuse every later get_message there.
    monkeypatch.setattr(read_scope, "_SEEN_IDS_MAX", 3)
    _request(await _dispatch(_bot(), dict(MEMBER_GROUP)))
    _read_rows("m1", "m2")
    _read_rows("m3", "m4")
    assert _hook(GET, {"message_id": "m4"}) is None
    assert _reason(_hook(GET, {"message_id": "m1"})) == "id_not_from_this_chat"


def test_a_summons_ids_are_not_forgotten_at_the_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    # A summon is one turn: what it read stays readable, and past the cap it stops adding.
    monkeypatch.setattr(read_scope, "_SEEN_IDS_MAX", 2)
    scope = read_scope.SummonScope(key="summon-" + "1" * 16, session=BOT, chat_id=GROUP, chat_forms=frozenset({GROUP}))
    assert read_scope._result_problem("list_messages", {"data": {"items": [{"id": "m1", "chat_id": GROUP}]}}, scope) is None
    assert read_scope._result_problem("list_messages", {"data": {"items": [{"id": "m2", "chat_id": GROUP}, {"id": "m3", "chat_id": GROUP}]}}, scope) is None
    assert scope.seen_ids == {"m1", "m2"}


# --- sends and sessions are not touched ----------------------------------------------------------


@pytest.mark.parametrize("env,chat", [(THIRD_DM, PEER), (MEMBER_GROUP, GROUP), (OWNER_GROUP, GROUP)])
async def test_a_limited_turn_sends_from_the_bots_own_session_with_no_refusal(env: Dict[str, Any], chat: str) -> None:
    adapter, hub, conn = _hub_adapter()
    event = await _deliver(adapter, env, conn)
    assert event is not None
    _request(event)
    tool = _bound_tool(adapter, chatlytics_send, "chatlytics_send")

    assert read_scope.turn_key_for_tool(SID) is None
    assert json.loads(await tool({"chatId": chat, "text": "by task id"}, task_id=SID))["success"] is True
    assert (await adapter.send(chat, "the reply", metadata={"notify": True})).success
    assert hub.left == [(BOT, chat, "by task id"), (BOT, chat, "the reply")]
    assert hub.refused == []


@pytest.mark.parametrize("tool,args", [
    ("chatlytics_send", {"chatId": GROUP, "text": "x"}),
    ("mcp__hub__send_message", {"chatId": GROUP, "text": "x"}),
    ("mcp__hub__send_poll", {"chatId": GROUP}),
    ("mcp__hub__send_reaction", {"messageId": "x"}),
    ("mcp__hub__manage_group", {"action": "info"}),
    ("mcp__hub__chatlytics_dispatch_action", {"action": "react"}),
    ("chatlytics_dispatch", {"action": "react"}),
    ("delegate_task", {"goal": "x"}),
    ("memory", {"action": "add", "content": "x"}),
    ("chatlytics_directory", {"query": "x"}),
])
async def test_what_a_limited_turn_keeps(tool: str, args: Dict[str, Any], caplog: pytest.LogCaptureFixture) -> None:
    await _limited(MEMBER_GROUP)
    assert _hook(tool, args) is None
    assert "hub_tool_skips_summon_checks" not in caplog.text


async def test_a_limited_turn_keeps_its_session_key_and_history(store: Any) -> None:  # noqa: F811
    adapter = _bot()
    first = await _dispatch(adapter, dict(THIRD_DM))
    assert first.source.thread_id is None
    entry = store.get_or_create_session(first.source)
    store.append_to_transcript(entry.session_id, {"role": "assistant", "content": "OLD-ANSWER"})

    later = store.get_or_create_session((await _dispatch(adapter, dict(THIRD_DM))).source)
    assert later.session_id == entry.session_id
    assert "OLD-ANSWER" in json.dumps(store.load_transcript(later.session_id))


async def test_a_retry_opens_the_scope_again() -> None:
    adapter, _hub, conn = _hub_adapter()
    event_list: List[Any] = []

    async def record(ev: Any) -> None:
        event_list.append(ev)

    adapter.handle_message = record  # type: ignore[assignment]
    await adapter._dispatch_envelope(dict(THIRD_DM), conn, replay=True)
    assert CHAT_LIMIT_MARKER in event_list[0].channel_prompt


# --- the summon is not touched -------------------------------------------------------------------


@pytest.mark.parametrize("name", ["from_me_dm", "from_me_group"])
async def test_a_summon_gets_no_chat_scope_and_keeps_its_own_rules(name: str, caplog: pytest.LogCaptureFixture) -> None:
    env = ENVELOPES[name]
    with caplog.at_level(logging.INFO, logger="chatlytics_hermes"):
        event = await _dispatch(_bot(), dict(env))
        _request(event)
        hub_send = _hook("mcp__chatlytics__send_message", {"chatId": env["entity_jid"], "text": "x"})
        wide = _hook(SUMMON_LM, EVERY_ACCOUNT)

    assert read_scope.is_scope_key(event.source.thread_id)
    assert CHAT_LIMIT_MARKER not in event.channel_prompt and TURN_CONTEXT_MARKER in event.channel_prompt
    assert read_scope._chat_turns == {}
    assert _reason(hub_send) == "hub_tool_skips_summon_checks"
    assert _reason(wide) == "session_not_summon"
    # The stable lines, with no kind added to them.
    assert "summon scope opened" in caplog.text and "summon read refused tool=" in caplog.text
    assert "kind=chat" not in caplog.text


async def test_a_summon_in_a_limited_chat_stays_on_the_owners_session_and_the_chat_stays_on_the_bots() -> None:
    adapter = _bot()
    await _dispatch(adapter, dict(THIRD_DM))
    summon = await _dispatch(adapter, dict(ENVELOPES["from_me_dm"]))

    def in_summon() -> Any:
        return _hook(SUMMON_LM, {"chat_id": PEER, "session": OWNER_SESSION}, "x", "x")

    assert _in_context(in_summon, platform="chatlytics", chat_id=PEER, thread_id=summon.source.thread_id) is None
    in_chat = _in_context(lambda: _hook(LM, {"chat_id": PEER, "session": OWNER_SESSION}, "x", "x"), platform="chatlytics", chat_id=PEER)
    assert _reason(in_chat) == "session_not_summon"


def test_a_chat_scope_cant_be_read_as_a_summon_key() -> None:
    assert not read_scope.is_scope_key("chat-0123456789abcdef")
    assert read_scope.scope_for_key("chat-0123456789abcdef") is None


async def test_ordinary_turns_dont_evict_a_running_summon(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(read_scope, "_SCOPES_MAX", 2)
    adapter = _bot()
    summon = await _dispatch(adapter, dict(ENVELOPES["from_me_dm"]))
    for i in range(5):
        await _dispatch(adapter, _env(f"97254400010{i}@c.us", f"97254400010{i}@c.us"))
    assert read_scope.scope_for_key(summon.source.thread_id) is not None


# --- the prompt line -----------------------------------------------------------------------------


@pytest.mark.parametrize("env", [THIRD_DM, MEMBER_GROUP, OWNER_GROUP])
async def test_the_line_says_this_chat_and_not_to_repeat_ids(env: Dict[str, Any]) -> None:
    prompt = (await _dispatch(_bot(), dict(env))).channel_prompt
    assert CHAT_LIMIT_MARKER in prompt and "only from this chat" in prompt and NO_IDS_RULE in prompt
    assert f"session {BOT}" in prompt and f"chat_id {env['entity_jid']}" in prompt


async def test_the_summon_line_has_the_same_rule() -> None:
    assert NO_IDS_RULE in (await _dispatch(_bot(), dict(ENVELOPES["from_me_dm"]))).channel_prompt
    assert "call it this chat" in NO_IDS_RULE and "session ids or phone numbers" in NO_IDS_RULE


async def test_a_configured_channel_prompt_stays_first() -> None:
    event = await _dispatch(_bot(), {**THIRD_DM, "channel_prompt": "Be brief."})
    assert event.channel_prompt.startswith("Be brief.\n\n" + CHAT_LIMIT_MARKER)


async def test_the_line_is_the_same_on_every_turn_of_a_chat() -> None:
    # It is part of the system prompt of a session that keeps its history: a new one per turn would
    # cost the provider's prompt cache.
    adapter = _bot()
    first = (await _dispatch(adapter, dict(THIRD_DM))).channel_prompt
    assert (await _dispatch(adapter, dict(THIRD_DM))).channel_prompt == first
    assert (await _dispatch(adapter, _env(PEER, PEER, session="another"))).channel_prompt != first


async def test_a_limited_request_is_not_a_summoned_one_so_memory_stays() -> None:
    event = await _dispatch(_bot(), dict(THIRD_DM))
    system = f"<memory-context>a note</memory-context>\n\n{event.channel_prompt}"
    assert not is_summoned_request({"messages": [{"role": "system", "content": system}]})
    assert _request(event, system=system) is None  # The middleware changes nothing.
    assert _reason(_hook(LM, EVERY_ACCOUNT)) == "session_not_summon"  # And the ids were bound.


async def test_a_request_with_no_line_releases_the_ids() -> None:
    event = await _limited(THIRD_DM)
    assert _reason(_hook(LM, EVERY_ACCOUNT)) == "session_not_summon"
    _request(event, system="a prompt with no line")
    assert _hook(LM, EVERY_ACCOUNT) is None


async def test_the_token_outside_its_line_binds_nothing() -> None:
    # An entry the model wrote can hold the token. The line starts with the marker.
    event = await _dispatch(_bot(), dict(OWNER_DM))
    _request(event, system=f"memory entry: {CHAT_SCOPE_TOKEN}:chat-0123456789abcdef")
    assert _hook(LM, EVERY_ACCOUNT) is None


def test_a_line_with_no_entry_here_reads_nothing_and_keeps_its_sends() -> None:
    line = f"{CHAT_LIMIT_MARKER} x {CHAT_SCOPE_TOKEN}:chat-0123456789abcdef"
    _request(None, system=line)
    assert _reason(_hook(LM, {"chat_id": PEER, "session": BOT})) == "no_scope_for_this_turn"
    assert _hook("mcp__hub__send_message", {"chatId": PEER}) is None
    assert read_scope.turn_key_for_tool(SID) is None


# --- other turns ---------------------------------------------------------------------------------


@pytest.mark.parametrize("session_vars", [
    pytest.param({"platform": "telegram", "chat_id": PEER, "user_id": PEER}, id="telegram, same id as a limited chat"),
    pytest.param({"platform": "", "cron_session": "1"}, id="cron"),
    pytest.param({}, id="no platform"),
])
async def test_a_turn_with_no_chatlytics_context_has_no_scope(session_vars: Dict[str, Any]) -> None:
    await _dispatch(_bot(), dict(THIRD_DM))  # A limited chat exists in this process.
    assert _in_context(lambda: _hook(LM, EVERY_ACCOUNT, "tg", "tg"), **session_vars) is None
    assert _in_context(lambda: transform_tool_result(tool_name=LM, result="{}", session_id="tg", task_id="tg"), **session_vars) is None


def test_no_gateway_context_at_all_has_no_scope() -> None:
    assert contextvars.Context().run(lambda: _hook(LM, EVERY_ACCOUNT, "cli", "cli")) is None


def test_no_gateway_module_has_no_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    monkeypatch.setitem(sys.modules, "gateway.session_context", None)
    assert _hook(LM, EVERY_ACCOUNT, "cli", "cli") is None


def test_a_chatlytics_turn_this_process_didnt_dispatch_reads_nothing_and_keeps_its_sends() -> None:
    # A turn resumed after a restart: the host's, with no envelope.
    vars_ = {"platform": "chatlytics", "chat_id": PEER}
    assert _reason(_in_context(lambda: _hook(LM, {"chat_id": PEER, "session": BOT}), **vars_)) == "no_scope_for_this_turn"
    assert _in_context(lambda: _hook("mcp__hub__send_message", {"chatId": PEER}), **vars_) is None


# --- logs and a host that can't enforce ----------------------------------------------------------


async def test_the_log_tells_the_kinds_apart_and_holds_no_arguments(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="chatlytics_hermes"):
        event = await _dispatch(_bot(), dict(MEMBER_GROUP))
        _request(event)
        _hook(LM, {"chat_id": GROUP, "session": "all", "limit": 1})
        _hook(LM, {"chat_id": GROUP, "session": BOT})
        await _dispatch(_bot(), dict(OWNER_DM))
    text = caplog.text
    assert "chat scope opened kind=chat scope=chat-" in text and f"session={BOT} chat={GROUP} chat_type=group" in text
    assert "owner_known=True" in text
    assert f"chat read refused kind=chat tool={LM} reason=session_not_summon" in text
    assert f"chat read allowed kind=chat tool={LM}" in text
    assert "the owner's own DM kind=chat" in text
    assert "limit" not in text


async def test_a_bot_with_no_owner_says_so_in_the_log(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="chatlytics_hermes"):
        await _dispatch(_bot(bot_token="sk_bot_x"), dict(OWNER_DM))
    assert "owner_known=False" in caplog.text


async def test_a_host_that_cant_enforce_keeps_the_turn_and_says_so_once(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setattr(read_scope, "scope_ready", lambda: "no hooks")
    adapter = _bot()
    with caplog.at_level(logging.ERROR, logger="chatlytics_hermes"):
        assert await _dispatch(adapter, dict(THIRD_DM)) is not None
        assert await _dispatch(adapter, dict(THIRD_DM)) is not None
    assert caplog.text.count("reads are NOT limited: no hooks") == 1


async def test_a_failure_opening_the_scope_keeps_the_turn_and_stays_limited(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    adapter = _bot()
    await _dispatch(adapter, dict(OWNER_DM))  # This chat had no limit a moment ago.

    def boom(*_a: Any, **_k: Any) -> bool:
        raise RuntimeError("boom")

    monkeypatch.setattr(read_scope, "is_owner_dm", boom)
    with caplog.at_level(logging.ERROR, logger="chatlytics_hermes"):
        assert await _dispatch(adapter, dict(OWNER_DM)) is not None
    assert "could not open a chat scope" in caplog.text
    verdict = _in_context(lambda: _hook(LM, EVERY_ACCOUNT, "x", "x"), platform="chatlytics", chat_id=OWNER)
    assert _reason(verdict) == "no_scope_for_this_turn"


# --- through the real host -----------------------------------------------------------------------


def _limited_line() -> str:
    source = type("S", (), {"chat_id": GROUP, "user_id": MEMBER, "chat_type": "group"})()
    scope = read_scope.open_chat_turn(MEMBER_GROUP, type("E", (), {"source": source})(), {}, authenticated=True)
    assert scope is not None
    return read_scope.chat_limit_line(scope)


def test_a_limited_turn_through_the_real_tool_loop_and_wrapper(host: Any) -> None:  # noqa: F811
    host.tool(LM, _everyone)
    wrapped = _call("tool_call", {"calls": [{"name": LM, "arguments": EVERY_ACCOUNT}]})
    provider = host.provider(_tools(wrapped), _text("I can only read this chat."))
    result = host.agent(provider, prompt=_limited_line(), session_id="20260101_000000_c").run_conversation(
        "[owner reply] what was said elsewhere", conversation_history=[], task_id="20260101_000000_c"
    )
    assert host.served == []
    assert "session_not_summon" in _tool_rows(result)[0]
    assert CHAT_LIMIT_MARKER in provider.requests[0]["messages"][0]["content"]


def test_the_in_scope_read_runs_through_the_real_tool_loop(host: Any) -> None:  # noqa: F811
    host.tool(LM, lambda _a: {"data": {"items": [{"id": "m1", "chat_id": GROUP, "body": "THIS-CHAT"}]}})
    provider = host.provider(_tools(_call(LM, {"chat_id": GROUP, "session": BOT})), _text("done"))
    agent = host.agent(provider, prompt=_limited_line(), session_id="20260101_000000_d", visible=True)
    result = agent.run_conversation("q", conversation_history=[])
    assert [name for name, _ in host.served] == [LM]
    assert any("THIS-CHAT" in row for row in _tool_rows(result))


def test_the_owners_dm_through_the_real_tool_loop_reads_every_account(host: Any) -> None:  # noqa: F811
    # The control. No line: the same call runs.
    host.tool(LM, _everyone)
    provider = host.provider(_tools(_call(LM, EVERY_ACCOUNT)), _text("done"))
    host.agent(provider, prompt=None, session_id="20260101_000000_e", visible=True).run_conversation("q", conversation_history=[])
    assert [name for name, _ in host.served] == [LM]


def test_execute_code_tool_calls_inherit_the_limit_by_task_id(host: Any) -> None:  # noqa: F811
    # tools/code_execution_rpc.py: handle_function_call(name, args, task_id=task_id), on a thread with
    # none of the turn's context variables.
    model_tools = pytest.importorskip("model_tools")
    host.tool(LM, _everyone)
    _request(None, "the-session", "the-task", system=_limited_line())
    out: List[Any] = []
    worker = threading.Thread(target=lambda: contextvars.Context().run(
        lambda: out.append(model_tools.handle_function_call(LM, EVERY_ACCOUNT, task_id="the-task"))
    ))
    worker.start()
    worker.join()
    assert "session_not_summon" in out[0] and host.served == []


def test_execute_code_naming_the_env_file_is_refused_by_the_real_dispatch(host: Any) -> None:  # noqa: F811
    _request(None, "the-session", "the-task", system=_limited_line())
    directive, message = host.plugins.get_pre_tool_call_directive(
        "execute_code", {"code": "open('/home/x/.hermes/.env').read()"}, session_id="the-session", task_id="the-task",
    )
    assert directive == "block" and "names_the_archive_or_host_store" in message


def test_a_delegated_subagent_inherits_the_limit_by_the_context_variables(host: Any) -> None:  # noqa: F811
    # What tools/delegate_tool_dispatch.py does: a copied context on another thread, and a child's own ids.
    _limited_line()

    def child() -> Any:
        out: List[Any] = []
        ctx = contextvars.copy_context()
        worker = threading.Thread(target=lambda: ctx.run(lambda: out.append(
            host.plugins.get_pre_tool_call_directive(LM, EVERY_ACCOUNT, session_id="a-child-agent", task_id="a-child-task")
        )))
        worker.start()
        worker.join()
        return out[0]

    directive, message = _in_context(child, platform="chatlytics", chat_id=GROUP)
    assert directive == "block" and "session_not_summon" in message
    # And the owner's DM doesn't limit its child.
    source = type("S", (), {"chat_id": OWNER, "user_id": OWNER, "chat_type": "dm"})()
    read_scope.open_chat_turn(OWNER_DM, type("E", (), {"source": source})(), {"allow_admin_from": [OWNER]}, authenticated=True)
    assert _in_context(child, platform="chatlytics", chat_id=OWNER)[0] is None


def test_the_real_host_binds_the_platform_variable_a_child_is_found_by() -> None:
    run = pytest.importorskip("gateway.run")
    from pathlib import Path
    from types import SimpleNamespace

    from gateway.config import Platform
    from gateway.session import SessionSource
    from gateway.session_context import clear_session_vars, get_session_env

    # By behaviour: 0.22.6 reads the same field through a local name, session_env_source.
    source = SessionSource(platform=Platform.TELEGRAM, chat_id="c", chat_type="dm")
    context = SimpleNamespace(source=source, session_key="k", session_id="s", **HOST_TURN_ACTOR)
    tokens = run.GatewayRunner._set_session_env(SimpleNamespace(adapters={}), context)
    try:
        assert get_session_env("HERMES_SESSION_PLATFORM", "") == Platform.TELEGRAM.value
    finally:
        clear_session_vars(tokens)
    cron = pytest.importorskip("cron.scheduler")
    assert 'platform="",' in Path(cron.__file__).read_text("utf-8")


def test_the_memory_middleware_is_what_this_module_uses() -> None:
    # The binding is in the summon's middleware, so a host without it has only the context variables.
    assert memory_strip.llm_request_middleware is llm_request_middleware
