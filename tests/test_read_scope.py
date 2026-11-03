"""chatlytics#956: what a summoned turn may read, and that no other turn is touched.

Placeholders only: this repo is public. The real shapes these use were measured against Lextrove
on 2026-11-02 and are copied here with made-up values.
"""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from typing import Any, Dict, Optional

import pytest

from chatlytics_hermes import memory_strip, read_scope
from chatlytics_hermes.read_scope import LEXTROVE_RULES, open_for_event, pre_tool_call, transform_tool_result
from chatlytics_hermes.trigger_context import SUMMON_TOKEN, render_trigger_context, with_trigger_context
from tests.test_owner_tagging import _dispatch, _install_recorders, _lp_adapter, _post as _webhook, _wh_adapter, _wh_payload
from tests.test_trigger_context import ENVELOPES, OWNER, OWNER_SESSION, PEER, _retry

PHONE = ENVELOPES["from_me_dm"]["chat_phone"]
GROUP = ENVELOPES["from_me_group"]["entity_jid"]
LID = ENVELOPES["from_me_dm_lid"]["entity_jid"]
UNMAPPED = ENVELOPES["from_me_dm_lid_unmapped"]["entity_jid"]
OTHER_SESSION = "fm-other"
SID = "20260101_000000_abc"


def _open(name: str = "from_me_dm", **changes: Any) -> read_scope.SummonScope:
    event = SimpleNamespace(source=SimpleNamespace(thread_id=None))
    scope = open_for_event({**ENVELOPES[name], **changes}, event)
    assert scope is not None and event.source.thread_id == scope.key
    # What a provider request of that turn does.
    read_scope.note_request(True, f"{SUMMON_TOKEN}:{scope.key}", SID, SID)
    return scope


@pytest.fixture(autouse=True)
def _no_leftover_binding() -> Any:
    yield
    read_scope._bound.clear()
    read_scope._scopes.clear()


def _pre(tool: str, args: Any) -> Optional[Dict[str, str]]:
    return pre_tool_call(tool_name=tool, args=args, session_id=SID, task_id=SID, turn_id="t")


def _reason(verdict: Optional[Dict[str, str]]) -> Optional[str]:
    if verdict is None:
        return None
    assert verdict["action"] == "block"
    return verdict["message"].split("(", 1)[1].split(")", 1)[0]


# A summon reads through the server holding the limited key: see tests/test_limited_key.py.
LM = "mcp__lextrove_limited__list_messages"
SM = "mcp__lextrove_limited__search_messages"
UNLIMITED_LM = "mcp__lextrove__list_messages"
DM_READ = {"contact_name": PHONE, "chat_type": "dm", "session": OWNER_SESSION, "mode": "fts"}
GROUP_LIST = {"chat_id": GROUP, "session": OWNER_SESSION}


# --- the exact failed turn --------------------------------------------------------------------


# The call the live test made, with its values replaced: a wrapper around list_messages, an
# @c.us chat id and every account.
FAILED_CALL = {"calls": [{"name": LM, "arguments": {"chat_id": f"{PHONE}@c.us", "session": "all", "limit": 30}}]}


def test_the_exact_failed_call_is_refused_and_says_what_to_do(caplog: pytest.LogCaptureFixture) -> None:
    _open()
    with caplog.at_level(logging.WARNING, logger="chatlytics_hermes"):
        verdict = _pre("tool_call", FAILED_CALL)
    assert _reason(verdict) == "session_not_summon"
    assert "cannot read outside this chat" in verdict["message"]
    assert f"list_messages with chat_id {PEER} and session {OWNER_SESSION}" in verdict["message"]
    line = next(r.getMessage() for r in caplog.records if "summon read refused" in r.getMessage())
    assert f"tool={LM} reason=session_not_summon" in line
    assert "all" not in line.split("reason=")[0]


def test_a_refusal_log_line_holds_no_arguments(caplog: pytest.LogCaptureFixture) -> None:
    _open()
    caplog.clear()  # The scope's own line, if an earlier test's real agent left the root logger at INFO.
    with caplog.at_level(logging.INFO, logger="chatlytics_hermes"):
        _pre(SM, {**DM_READ, "query": "SECRET-WORDS", "session": "all"})
        _pre("session_search", {"query": "SECRET-WORDS"})
        _pre("terminal", {"command": "curl lextrove SECRET-WORDS"})
        _pre("tool_call", {"calls": [{"name": "mcp__x__\nSECRET-WORDS", "arguments": {}}, "bad"]})
    # The one place model text could reach the log is a tool name: it is cut to safe characters,
    # so it can't start a line of its own.
    assert "SECRET-WORDS" not in caplog.text.replace("?SECRET-WORDS", "")
    assert all("summon read refused" in record.getMessage() for record in caplog.records)
    assert not any(chr(10) in record.getMessage() for record in caplog.records)


# --- session block -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value, reason",
    [
        ("all", "session_not_summon"),
        ("ALL", "session_not_summon"),
        (" all ", "session_not_summon"),
        (OTHER_SESSION, "session_not_summon"),
        (f"{OWNER_SESSION},{OTHER_SESSION}", "session_not_summon"),
        (f"{OWNER_SESSION},{OWNER_SESSION}", "session_not_summon"),
        ("", "session_not_summon"),
        ("  ", "session_not_summon"),
        ([OWNER_SESSION], "session_not_summon"),
        (7, "session_not_summon"),
        (None, "session_missing"),
    ],
)
def test_any_other_session_is_refused(value: Any, reason: str) -> None:
    _open()
    assert _reason(_pre(SM, {**DM_READ, "session": value})) == reason
    _open("from_me_group")
    assert _reason(_pre(LM, {**GROUP_LIST, "session": value})) == reason


def test_a_missing_session_is_refused() -> None:
    _open("from_me_group")
    assert _reason(_pre(LM, {"chat_id": GROUP})) == "session_missing"


@pytest.mark.parametrize("value", [OWNER_SESSION, f"  {OWNER_SESSION} ", OWNER_SESSION.upper()])
def test_case_and_whitespace_of_the_right_session_pass(value: str) -> None:
    _open()
    assert _pre(SM, {**DM_READ, "session": value}) is None


# --- chat block --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "contact",
    [PHONE, f"+{PHONE}", f" {PHONE} ", f"{PHONE}@c.us", f"{PHONE}@s.whatsapp.net", f"{PHONE}@C.US",
     f"{PHONE[:3]} {PHONE[3:6]}-{PHONE[6:]}"],
)
def test_the_approved_dm_read_passes_in_every_form_of_the_phone(contact: str) -> None:
    _open()
    assert _pre(SM, {**DM_READ, "contact_name": contact}) is None
    assert _pre(SM, {**DM_READ, "contact_name": contact, "chat_type": "DM", "mode": "FTS"}) is None


@pytest.mark.parametrize(
    "change, reason",
    [
        ({"contact_name": "Dana Levi"}, "chat_not_summon"),
        ({"contact_name": PHONE + "9"}, "chat_not_summon"),
        ({"contact_name": PHONE[:-1]}, "chat_not_summon"),
        ({"contact_name": "15550001111"}, "chat_not_summon"),
        ({"contact_name": None}, "chat_not_summon"),
        ({"chat_type": "group"}, "chat_type_not_summon"),
        ({"chat_type": "all"}, "chat_type_not_summon"),
        ({"chat_type": None}, "chat_type_not_summon"),
        ({"mode": None}, "search_mode_not_fts"),
        ({"mode": "hybrid"}, "search_mode_not_fts"),
        ({"group_name": "Family"}, "arg_not_allowed"),
        ({"context": 5}, "arg_not_allowed"),
        ({"chat_id": "x@c.us"}, "arg_not_allowed"),
    ],
)
def test_a_dm_search_outside_the_chat_is_refused(change: Dict[str, Any], reason: str) -> None:
    _open()
    assert _reason(_pre(SM, {**DM_READ, **change})) == reason


def test_list_messages_with_a_contact_name_is_refused_because_it_returns_group_posts() -> None:
    _open()
    assert _reason(_pre(LM, {"contact_name": PHONE, "session": OWNER_SESSION})) == "arg_not_allowed"


@pytest.mark.parametrize(
    "chat", [f"{PHONE}@c.us", f"{PHONE}@s.whatsapp.net", f" {PHONE}@C.us ", f"{PHONE}:26@s.whatsapp.net", LID]
)
def test_list_messages_passes_for_any_stored_form_of_a_dm(chat: str) -> None:
    _open("from_me_dm_lid")
    assert _pre(LM, {"chat_id": chat, "session": OWNER_SESSION}) is None


@pytest.mark.parametrize(
    "chat", ["999@c.us", f"{PHONE}9@c.us", GROUP, "", None, [f"{PHONE}@c.us"], f"{PHONE}@g.us", "%", "*"]
)
def test_list_messages_for_another_chat_is_refused(chat: Any) -> None:
    _open()
    assert _reason(_pre(LM, {"chat_id": chat, "session": OWNER_SESSION})) in ("chat_not_summon", "chat_missing")


def test_a_read_with_no_chat_is_refused() -> None:
    _open()
    assert _reason(_pre(LM, {"session": OWNER_SESSION})) == "chat_missing"
    assert _reason(_pre(LM, {"session": OWNER_SESSION, "sender_id": OWNER})) == "chat_missing"


def test_a_group_reads_by_its_jid_and_nothing_else() -> None:
    _open("from_me_group")
    assert _pre(LM, GROUP_LIST) is None
    assert _pre(LM, {**GROUP_LIST, "chat_id": GROUP.upper()}) is None
    assert _pre(LM, {**GROUP_LIST, "sender_id": OWNER, "limit": 5, "from": "2026-01-01"}) is None
    assert _pre(SM, {"group_name": GROUP, "session": OWNER_SESSION, "mode": "fts", "query": "plan"}) is None
    assert _pre(SM, {"group_name": GROUP, "session": OWNER_SESSION, "mode": "fts", "chat_type": "group"}) is None
    assert _reason(_pre(SM, {"group_name": "Family", "session": OWNER_SESSION, "mode": "fts"})) == "chat_not_summon"
    assert _reason(_pre(SM, {"contact_name": PHONE, "group_name": GROUP, "session": OWNER_SESSION, "mode": "fts"})) == "arg_not_allowed"
    assert _reason(_pre(LM, {"group_name": GROUP, "session": OWNER_SESSION})) == "arg_not_allowed"
    assert _reason(_pre(LM, {**GROUP_LIST, "chat_id": f"{PHONE}@c.us"})) == "chat_not_summon"


def test_a_stray_phone_on_a_group_envelope_gives_no_phone_form() -> None:
    scope = _open("from_me_group", chat_phone=PHONE)
    assert scope.phone is None and scope.chat_forms == {GROUP}
    assert _reason(_pre(LM, {"chat_id": f"{PHONE}@c.us", "session": OWNER_SESSION})) == "chat_not_summon"


def test_a_dm_with_no_phone_uses_list_messages_only() -> None:
    _open("from_me_dm_lid_unmapped")
    assert _pre(LM, {"chat_id": UNMAPPED, "session": OWNER_SESSION}) is None
    assert _reason(_pre(SM, {**DM_READ})) == "dm_without_phone_use_list_messages"


def test_the_owners_own_chat_has_no_phone_and_does_not_search_by_their_number() -> None:
    env = {k: v for k, v in ENVELOPES["from_me_dm"].items() if k != "chat_phone"}
    scope = open_for_event({**env, "entity_jid": OWNER}, SimpleNamespace(source=SimpleNamespace(thread_id=None)))
    read_scope.note_request(True, f"{SUMMON_TOKEN}:{scope.key}", SID, SID)
    assert _pre(LM, {"chat_id": OWNER, "session": OWNER_SESSION}) is None
    owner_phone = OWNER.split("@")[0]
    assert _reason(_pre(SM, {**DM_READ, "contact_name": owner_phone})) == "dm_without_phone_use_list_messages"


# --- every tool of the server ------------------------------------------------------------------

# The server's own list, as of the instructions it sends ("All 34 tools"). A tool it adds is
# refused until someone adds a rule, and this test then fails to make them look at it.
LEXTROVE_TOOLS = {
    "export_messages", "get_analytics", "get_avatar", "get_complex_analytics", "get_composite_analytics",
    "get_contact", "get_content_analytics", "get_domain_analytics", "get_entity_profile", "get_group",
    "get_media_url", "get_message", "get_newsletter", "get_poll", "get_presence", "get_sender_analytics",
    "get_volume_analytics", "list_calls", "list_contacts", "list_entity_digests", "list_group_events",
    "list_groups", "list_links", "list_locations", "list_media", "list_messages", "list_newsletters",
    "list_poll_votes", "list_polls", "list_reactions", "list_sessions", "list_unavailable_polls",
    "resolve_entity", "search_messages",
}


def test_every_lextrove_tool_has_a_rule() -> None:
    assert set(LEXTROVE_RULES) == LEXTROVE_TOOLS
    assert len(LEXTROVE_TOOLS) == 34


NEVER_ALLOWED = sorted(LEXTROVE_TOOLS - {
    "list_messages", "search_messages", "list_media", "list_links", "list_polls", "list_locations", "list_reactions",
    "list_group_events", "list_contacts", "get_group", "get_message", "get_media_url", "get_poll", "list_poll_votes",
})


@pytest.mark.parametrize("name", NEVER_ALLOWED)
@pytest.mark.parametrize("env", ["from_me_dm", "from_me_group"])
def test_a_tool_that_cannot_be_scoped_is_refused_with_the_best_arguments(name: str, env: str) -> None:
    _open(env)
    chat = ENVELOPES[env]["entity_jid"]
    args = {"chat_id": chat, "session": OWNER_SESSION, "contact_name": PHONE, "chat_type": "dm", "endpoint": "chat-report",
            "contact_id": chat, "entity_id": chat, "entity_type": "contact", "query": PHONE, "newsletter_id": chat}
    assert _pre(f"mcp__lextrove_limited__{name}", args) is not None
    assert _pre(f"mcp__lextrove_limited__{name}", {"chat_id": chat, "session": OWNER_SESSION}) is not None


@pytest.mark.parametrize("name", ["list_media", "list_links", "list_polls", "list_locations", "list_reactions"])
def test_the_chat_filter_tools_follow_the_same_rule(name: str) -> None:
    tool = f"mcp__lextrove_limited__{name}"
    _open("from_me_group")
    assert _pre(tool, GROUP_LIST) is None
    assert _pre(tool, {**GROUP_LIST, "session": "all"}) is not None
    assert _pre(tool, {"session": OWNER_SESSION}) is not None
    assert _pre(tool, {**GROUP_LIST, "chat_id": "120363000000000999@g.us"}) is not None
    assert _pre(tool, {"chat_id": GROUP}) is not None  # some of these default to every session


def test_a_reaction_by_message_id_alone_is_refused() -> None:
    _open("from_me_group")
    assert _reason(_pre("mcp__lextrove_limited__list_reactions", {"message_id": "m1", "session": OWNER_SESSION})) == "arg_not_allowed"


@pytest.mark.parametrize(
    "tool, args",
    [
        ("mcp__lextrove_limited__list_group_events", {"chat_id": GROUP, "session": OWNER_SESSION}),
        ("mcp__lextrove_limited__list_contacts", {"chat_id": GROUP, "session": OWNER_SESSION}),
        ("mcp__lextrove_limited__get_group", {"group_id": GROUP, "session": OWNER_SESSION}),
    ],
)
def test_the_group_facts_are_for_that_group_only(tool: str, args: Dict[str, Any]) -> None:
    _open("from_me_group")
    assert _pre(tool, args) is None
    assert _pre(tool, {**args, "session": "all"}) is not None
    assert _pre(tool, {k: v for k, v in args.items() if k != "session"}) is not None
    other = {k: ("120363000000000999@g.us" if v == GROUP else v) for k, v in args.items()}
    assert _pre(tool, other) is not None
    _open()
    assert _reason(_pre(tool, args)) == "not_a_group_chat"


def test_list_contacts_cannot_search_or_browse() -> None:
    _open("from_me_group")
    assert _pre("mcp__lextrove_limited__list_contacts", {"search": "Dana", "session": OWNER_SESSION, "chat_id": GROUP}) is not None
    assert _pre("mcp__lextrove_limited__list_contacts", {"search": "Dana", "session": OWNER_SESSION}) is not None


@pytest.mark.parametrize(
    "name",
    ["mcp__lextrove_limited__read_resource", "mcp__lextrove_limited__get_prompt", "mcp__lextrove_limited__list_resources",
     "mcp__lextrove_limited__a_new_tool", "mcp_lextrove_a_new_tool", "lextrove_a_new_tool", "MCP__LexTrove__New"],
)
def test_an_unknown_tool_on_that_server_is_refused(name: str) -> None:
    _open()
    assert _reason(_pre(name, {"chat_id": PEER, "session": OWNER_SESSION})) == "unknown_lextrove_tool"


@pytest.mark.parametrize(
    "name",
    [LM, "MCP__LEXTROVE_LIMITED__LIST_MESSAGES", " mcp__lextrove_limited__list_messages ", "mcp__lextrove-limited__list_messages"],
)
def test_every_spelling_of_the_limited_servers_tool_is_held_to_the_rules(name: str) -> None:
    _open("from_me_group")
    assert _reason(_pre(name, {**GROUP_LIST, "session": "all"})) == "session_not_summon"
    assert _pre(name, GROUP_LIST) is None


@pytest.mark.parametrize(
    "name",
    [UNLIMITED_LM, "mcp_lextrove_list_messages", "MCP__LEXTROVE__LIST_MESSAGES",
     "mcp__lextrove-eu__list_messages", "mcp__renamed_archive__list_messages", "list_messages"],
)
def test_every_spelling_on_any_other_server_is_refused_even_in_scope(name: str) -> None:
    _open("from_me_group")
    assert _reason(_pre(name, GROUP_LIST)) == read_scope._NEEDS_LIMITED


# --- reads by id -------------------------------------------------------------------------------


def _mcp(data: Any, **top: Any) -> str:
    """The host's MCP envelope around a tool's JSON text."""
    return json.dumps({"result": json.dumps({"data": data, **top})})


def _post(tool: str, result: Any) -> Optional[str]:
    return transform_tool_result(tool_name=tool, result=result, args={}, session_id=SID, task_id=SID, turn_id="t")


def _row(chat: str, row_id: str = "m1") -> Dict[str, Any]:
    return {"id": row_id, "chat_id": chat, "body": "text", "chat_type": "contact"}


def _entity(identifier: str, **change: Any) -> Dict[str, Any]:
    return {"ambiguous": False, "similarity_score": 1.0, "identifier": identifier, **change}


def _search(rows: Any, entity: Any) -> str:
    return _mcp({"items": rows, "resolved_entity": entity, "pagination": {"count": len(rows)}})


@pytest.mark.parametrize(
    "tool, args",
    [
        ("mcp__lextrove_limited__get_message", {"message_id": "m1"}),
        ("mcp__lextrove_limited__get_media_url", {"message_id": "m1"}),
        ("mcp__lextrove_limited__get_media_url", {"message_ids": ["m1"]}),
        ("mcp__lextrove_limited__get_media_url", {"message_id": "m1", "durable": False}),
        ("mcp__lextrove_limited__get_poll", {"poll_id": "m1"}),
        ("mcp__lextrove_limited__list_poll_votes", {"poll_id": "m1", "session": OWNER_SESSION}),
    ],
)
def test_a_read_by_id_needs_an_id_this_chat_returned(tool: str, args: Dict[str, Any]) -> None:
    _open("from_me_group")
    assert _reason(_pre(tool, args)) == "id_not_from_this_chat"
    assert _post(LM, _mcp({"items": [_row(GROUP)]})) is None
    assert _pre(tool, args) is None
    other = {k: (["m9"] if isinstance(v, list) else "m9" if v == "m1" else v) for k, v in args.items()}
    assert _reason(_pre(tool, other)) == "id_not_from_this_chat"


def test_one_foreign_id_in_a_bulk_read_refuses_it_all() -> None:
    _open("from_me_group")
    _post(LM, _mcp({"items": [_row(GROUP)]}))
    assert _reason(_pre("mcp__lextrove_limited__get_media_url", {"message_ids": ["m1", "m9"]})) == "id_not_from_this_chat"
    assert _reason(_pre("mcp__lextrove_limited__get_media_url", {"message_ids": []})) == "id_not_from_this_chat"
    assert _reason(_pre("mcp__lextrove_limited__get_media_url", {})) == "id_not_from_this_chat"


def test_an_id_seen_by_one_summon_is_not_good_for_the_next() -> None:
    _open("from_me_group")
    _post(LM, _mcp({"items": [_row(GROUP)]}))
    _open("from_me_group")
    assert _reason(_pre("mcp__lextrove_limited__get_message", {"message_id": "m1"})) == "id_not_from_this_chat"


def test_poll_votes_also_need_the_session() -> None:
    _open("from_me_group")
    _post("mcp__lextrove_limited__list_polls", _mcp([{"id": "p1", "chat_id": GROUP}]))
    assert _reason(_pre("mcp__lextrove_limited__list_poll_votes", {"poll_id": "p1"})) == "session_missing"
    assert _reason(_pre("mcp__lextrove_limited__list_poll_votes", {"poll_id": "p1", "session": "all"})) == "session_not_summon"


# --- the wrapper -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "args",
    [
        FAILED_CALL,
        {"calls": json.dumps(FAILED_CALL["calls"])},
        {"calls": FAILED_CALL["calls"][0]},
        {"name": LM, "arguments": FAILED_CALL["calls"][0]["arguments"]},
        {"calls": [{"name": LM, "arguments": json.dumps(FAILED_CALL["calls"][0]["arguments"])}]},
    ],
    ids=["list", "json string", "one object", "legacy single", "arguments as json"],
)
def test_every_wrapper_shape_the_host_accepts_is_seen_through(args: Dict[str, Any]) -> None:
    _open()
    assert _reason(_pre("tool_call", args)) == "session_not_summon"


def test_one_bad_entry_among_parallel_calls_refuses_the_call() -> None:
    _open("from_me_group")
    good = {"name": LM, "arguments": GROUP_LIST}
    for bad in ({"name": LM, "arguments": {**GROUP_LIST, "session": "all"}}, {"name": "session_search", "arguments": {"query": "x"}}):
        assert _pre("tool_call", {"calls": [good, bad]}) is not None
        assert _pre("tool_call", {"calls": [bad, good]}) is not None
    assert _pre("tool_call", {"calls": [good, {"name": "web_search", "arguments": {"query": "x"}}]}) is None


@pytest.mark.parametrize(
    "args",
    [
        {},
        {"calls": []},
        {"calls": "not json"},
        {"calls": ["bad"]},
        {"calls": 7},
        {"calls": [{"name": LM, "arguments": "not json"}]},
        {"calls": [{"name": "tool_call", "arguments": FAILED_CALL}]},
        {"calls": [{"name": LM}]},
    ],
)
def test_a_wrapper_that_cannot_be_read_is_refused(args: Dict[str, Any]) -> None:
    _open()
    assert _pre("tool_call", args) is not None


def test_the_catalog_tools_stay_open() -> None:
    _open()
    assert _pre("tool_search", {"queries": ["lextrove messages"]}) is None
    assert _pre("tool_describe", {"names": [LM]}) is None


def test_the_right_read_through_the_wrapper_passes() -> None:
    _open()
    assert _pre("tool_call", {"calls": [{"name": SM, "arguments": DM_READ}]}) is None


# --- other routes ------------------------------------------------------------------------------


def test_session_search_is_refused() -> None:
    _open()
    assert _reason(_pre("session_search", {"query": "summary"})) == "transcripts_of_other_chats"


@pytest.mark.parametrize("tool", ["chatlytics_read", "mcp__chatlytics_cloud__read_messages"])
def test_the_hubs_read_of_the_bots_number_is_refused(tool: str) -> None:
    _open()
    assert _reason(_pre(tool, {"chatId": PEER})) == "chatlytics_reads_another_account"


@pytest.mark.parametrize("action", ["getMessages", "getChatMessages", "searchMessages", "readChatMessages", "list_chats", "getChatsOverview", "exportChat"])
@pytest.mark.parametrize("tool", ["chatlytics_dispatch", "mcp__chatlytics_cloud__chatlytics_dispatch_action"])
def test_a_read_through_the_hubs_dispatch_is_refused(tool: str, action: str) -> None:
    _open()
    assert _reason(_pre(tool, {"action": action})) == "chatlytics_read_action"


def test_a_dispatch_without_a_string_action_is_refused() -> None:
    _open()
    assert _pre("chatlytics_dispatch", {"action": 7}) is not None
    assert _pre("chatlytics_dispatch", {}) is not None


@pytest.mark.parametrize(
    "tool, args",
    [
        ("chatlytics_send", {"chatId": PEER, "text": "hi"}),
        ("chatlytics_dispatch", {"action": "send", "target": PEER}),
        ("chatlytics_dispatch", {"action": "createGroup"}),
        ("chatlytics_resolve_entity", {"query": "Dana"}),
        ("chatlytics_directory", {"search": "Dana"}),
        ("memory", {"action": "add", "target": "memory", "content": "x"}),
        ("skill_view", {"name": "whatsapp-chat-summary"}),
        ("web_search", {"query": "weather"}),
        ("terminal", {"command": "date"}),
        ("terminal", {"command": "python ~/.hermes/skills/x/publish-link.py https://example.com/a"}),
        ("execute_code", {"code": "print(1)"}),
        ("delegate_task", {"goal": "do it"}),
        ("mcp__qmd__search", {"query": "meeting notes"}),
        ("mcp__gmail__send_email", {"to": "x"}),
    ],
)
def test_everything_else_stays_on_a_summoned_turn(tool: str, args: Dict[str, Any]) -> None:
    _open()
    assert _pre(tool, args) is None


@pytest.mark.parametrize(
    "tool, args",
    [
        ("terminal", {"command": "curl https://mcp.lextrove.example/x"}),
        ("terminal", {"command": "sqlite3 ~/.hermes/state.db 'select 1'"}),
        ("terminal", {"command": "cat .hermes/config.yaml"}),
        ("terminal", {"command": "cat ~/.hermes/profiles/other/.env"}),
        ("terminal", {"command": "grep -r x ~/.hermes/ | head"}),
        ("execute_code", {"code": "open('state.db')"}),
        ("read_file", {"path": "/home/x/.hermes/sessions/sessions.json"}),
        ("search_files", {"pattern": "x", "path": "/home/x/.hermes"}),
        ("cronjob", {"prompt": "read lextrove every hour"}),
        ("tool_call", {"calls": [{"name": "terminal", "arguments": {"command": "use lextrove"}}]}),
    ],
)
def test_shell_and_code_tools_naming_the_archive_or_host_stores_are_refused(tool: str, args: Dict[str, Any]) -> None:
    _open()
    assert _reason(_pre(tool, args)) == "names_the_archive_or_host_store"


# --- the result --------------------------------------------------------------------------------


def _measured_dm_rows() -> Any:
    return [_row(f"{PHONE}@s.whatsapp.net", "m1"), _row(f"{PHONE}@c.us", "m2")]


def test_a_measured_dm_search_result_passes_and_its_ids_are_kept() -> None:
    scope = _open()
    assert _post(SM, _search(_measured_dm_rows(), _entity(f"{PHONE}@c.us"))) is None
    assert {"m1", "m2"} <= scope.seen_ids


@pytest.mark.parametrize(
    "entity, reason",
    [
        (None, "result_match_not_positive"),
        ({}, "result_match_not_positive"),
        (_entity(f"{PHONE}@c.us", ambiguous=True), "result_match_not_positive"),
        ({"similarity_score": 1.0, "identifier": f"{PHONE}@c.us"}, "result_match_not_positive"),
        (_entity(f"{PHONE}@c.us", similarity_score=0.93), "result_match_not_positive"),
        (_entity(f"{PHONE}@c.us", similarity_score="1"), "result_match_not_positive"),
        ({"ambiguous": False, "identifier": f"{PHONE}@c.us"}, "result_match_not_positive"),
        (_entity(f"{PHONE}9@c.us"), "result_other_entity"),
        (_entity("200000000000099@lid"), "result_other_entity"),
        (_entity(""), "result_other_entity"),
        ({"ambiguous": False, "similarity_score": 1.0}, "result_other_entity"),
    ],
)
def test_a_dm_search_without_a_positive_match_never_reaches_the_model(
    entity: Any, reason: str, caplog: pytest.LogCaptureFixture
) -> None:
    _open()
    with caplog.at_level(logging.WARNING, logger="chatlytics_hermes"):
        out = _post(SM, _search([_row(f"{PHONE}@c.us")], entity))
    assert reason in json.loads(out)["error"]
    assert "text" not in out.replace("context", "")
    assert f"reason={reason}" in caplog.text
    assert '"body"' not in caplog.text


def test_a_search_with_one_row_from_another_chat_never_reaches_the_model() -> None:
    _open()
    rows = [_row(f"{PHONE}@c.us"), {"id": "m9", "chat_id": "999@c.us", "body": "OTHER"}]
    out = _post(SM, _search(rows, _entity(f"{PHONE}@c.us")))
    assert "result_row_other_chat" in out and "OTHER" not in out


def test_a_search_row_with_no_chat_id_is_not_proven() -> None:
    _open()
    out = _post(SM, _search([{"id": "m9", "body": "OTHER"}], _entity(f"{PHONE}@c.us")))
    assert "result_row_without_chat" in out and "OTHER" not in out


def test_a_search_result_that_cannot_be_read_is_refused() -> None:
    _open()
    for bad in ("not json", {"result": "not json"}, None, 7):
        assert "error" in json.loads(_post(SM, bad) or "{}")


def test_a_tool_error_passes_through() -> None:
    # Measured: a phone with no contact row is a 404 with no rows, and the model must see why.
    _open()
    error = json.dumps({"error": "API error (404): no contact matching"})
    assert _post(SM, error) is None


def test_a_positive_search_with_no_rows_passes() -> None:
    _open()
    assert _post(SM, _search([], _entity(f"{PHONE}@c.us"))) is None


def test_a_group_search_needs_the_groups_own_entity() -> None:
    _open("from_me_group")
    assert _post(SM, _search([_row(GROUP)], _entity(GROUP))) is None
    assert "result_other_entity" in _post(SM, _search([_row(GROUP)], _entity("120363000000000999@g.us")))


def test_a_list_by_chat_id_also_refuses_a_row_of_another_chat() -> None:
    _open("from_me_group")
    assert "result_row_other_chat" in _post(LM, _mcp({"items": [_row("120363000000000999@g.us")]}))
    assert "result_row_other_chat" in _post("mcp__lextrove_limited__list_media", _mcp([_row("120363000000000999@g.us")]))
    assert "result_row_other_chat" in _post("mcp__lextrove_limited__get_message", _mcp(_row("120363000000000999@g.us")))


def test_a_list_too_long_to_parse_stays_because_its_arguments_prove_it() -> None:
    _open("from_me_group")
    assert _post(LM, "not json, cut by the host") is None


def test_a_row_id_is_only_kept_from_a_source_that_is_a_chat_read() -> None:
    scope = _open("from_me_group")
    assert _post("mcp__lextrove_limited__get_group", _mcp({"id": "g1", "chat_id": GROUP})) is None
    assert "g1" not in scope.seen_ids


# --- failure of the enforcement itself ---------------------------------------------------------


def test_a_failing_rule_refuses(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    _open()

    def boom(*_a: Any) -> None:
        raise RuntimeError("SECRET-DETAIL-IN-TRACEBACK-IS-OK")

    monkeypatch.setitem(LEXTROVE_RULES, "list_messages", boom)
    assert _reason(_pre(LM, {"session": "x"})) == "check_failed"
    assert "reason=check_failed" in caplog.text


def test_a_failing_scope_lookup_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: Any) -> None:
        raise RuntimeError("lookup")

    monkeypatch.setattr(read_scope, "_scope_for", boom)
    assert _reason(_pre("terminal", {"command": "date"})) == "scope_lookup_failed"
    out = transform_tool_result(tool_name=LM, result="x", session_id=SID, task_id=SID)
    assert "result_check_failed" in out


def test_a_failing_result_check_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    _open()

    def boom(*_a: Any) -> None:
        raise RuntimeError("check")

    monkeypatch.setattr(read_scope, "_result_problem", boom)
    assert "result_check_failed" in _post(SM, _search([], _entity(f"{PHONE}@c.us")))


def test_arguments_of_the_wrong_type_refuse_a_lextrove_read() -> None:
    _open()
    assert _reason(_pre(LM, "not json")) == "args_unreadable"
    assert _reason(_pre(LM, [1])) == "args_unreadable"
    assert _reason(_pre(LM, None)) == "session_missing"


# --- no scope, and no other turn ---------------------------------------------------------------


def test_a_line_pinned_before_a_restart_can_read_nothing() -> None:
    read_scope.note_request(True, "", SID, SID)
    assert _reason(_pre(LM, {"chat_id": PEER, "session": OWNER_SESSION})) == "no_scope_for_this_turn"
    assert _reason(_pre(SM, DM_READ)) == "no_scope_for_this_turn"
    assert _pre("web_search", {"query": "x"}) is None


def test_an_unknown_scope_key_can_read_nothing() -> None:
    read_scope.note_request(True, f"{SUMMON_TOKEN}:summon-0000000000000000", SID, SID)
    assert _reason(_pre(LM, {"chat_id": PEER, "session": OWNER_SESSION})) == "no_scope_for_this_turn"


NOT_SUMMONED = [
    (UNLIMITED_LM, {"chat_id": "anything@c.us", "session": "all"}),
    ("mcp__lextrove__search_messages", {"query": "x", "session": "all"}),
    ("mcp__lextrove__export_messages", {}),
    ("mcp__lextrove__resolve_entity", {"query": "Dana"}),
    ("mcp__lextrove__a_new_tool", {}),
    ("session_search", {"query": "x"}),
    ("chatlytics_read", {"chatId": PEER}),
    ("terminal", {"command": "sqlite3 ~/.hermes/state.db"}),
    ("tool_call", {"calls": [{"name": UNLIMITED_LM, "arguments": {"session": "all"}}]}),
    ("tool_call", {"calls": "not json"}),
]


@pytest.mark.parametrize("tool, args", NOT_SUMMONED)
def test_no_other_turn_is_touched(tool: str, args: Dict[str, Any]) -> None:
    _open()  # A summon elsewhere must not matter.
    verdict = pre_tool_call(tool_name=tool, args=args, session_id="ordinary", task_id="ordinary", turn_id="t")
    assert verdict is None
    assert transform_tool_result(tool_name=tool, result="not json", args=args, session_id="ordinary", task_id="ordinary") is None


def test_a_request_with_no_line_releases_the_session() -> None:
    _open()
    assert _pre(UNLIMITED_LM, {"session": "all"}) is not None
    read_scope.note_request(False, "", SID, SID)
    assert _pre(UNLIMITED_LM, {"session": "all"}) is None


def test_a_request_with_no_line_does_not_release_another_session() -> None:
    _open()
    read_scope.note_request(False, "", "ordinary", "ordinary")
    assert _pre(LM, {"session": "all"}) is not None


def test_the_middleware_binds_and_releases_on_the_request() -> None:
    env = ENVELOPES["from_me_dm"]
    scope = open_for_event(env, SimpleNamespace(source=SimpleNamespace(thread_id=None)))
    line = render_trigger_context(env, scope.key)
    summoned = {"messages": [{"role": "system", "content": f"persona\n\n{line}"}, {"role": "user", "content": "q"}]}
    ordinary = {"messages": [{"role": "system", "content": "persona"}, {"role": "user", "content": "q"}]}

    memory_strip.llm_request_middleware(request=summoned, session_id="s1", task_id="t1", turn_id="x")
    assert read_scope._bound["s1"] is scope and read_scope._bound["t1"] is scope
    assert pre_tool_call(tool_name=UNLIMITED_LM, args={"session": "all"}, session_id="s1") is not None
    assert pre_tool_call(tool_name=UNLIMITED_LM, args={"session": "all"}, task_id="t1") is not None

    memory_strip.llm_request_middleware(request=ordinary, session_id="s1", task_id="t1", turn_id="y")
    assert pre_tool_call(tool_name=UNLIMITED_LM, args={"session": "all"}, session_id="s1", task_id="t1") is None


def test_a_failing_note_does_not_lock_down_an_ordinary_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: Any) -> None:
        raise RuntimeError("note")

    monkeypatch.setattr(read_scope, "note_request", boom)
    ordinary = {"messages": [{"role": "system", "content": "persona"}, {"role": "user", "content": "q"}]}
    assert memory_strip.llm_request_middleware(request=ordinary, session_id="s", task_id="t", turn_id="x") is None


def test_the_memory_probe_binds_nothing() -> None:
    request = {"messages": [{"role": "system", "content": render_trigger_context(ENVELOPES["from_me_dm"])}]}
    memory_strip.llm_request_middleware(request=request, session_id="p", task_id="p", turn_id=memory_strip._PROBE_TURN)
    assert not read_scope._bound


def test_a_scope_key_in_a_stored_entry_before_the_token_is_not_read() -> None:
    env = ENVELOPES["from_me_dm"]
    real = open_for_event(env, SimpleNamespace(source=SimpleNamespace(thread_id=None)))
    other = open_for_event({**ENVELOPES["from_me_group"]}, SimpleNamespace(source=SimpleNamespace(thread_id=None)))
    system = f"entry {other.key}\n\n{render_trigger_context(env, real.key)}\n\nskill text {other.key}"
    memory_strip.llm_request_middleware(
        request={"messages": [{"role": "system", "content": system}]}, session_id="s1", task_id="t1", turn_id="x",
    )
    assert read_scope._bound["s1"] is real


# --- the dispatch ------------------------------------------------------------------------------


def _adapter() -> Any:
    return _lp_adapter(allow_admin_from=[OWNER], group_allow_admin_from=[OWNER])


async def test_a_summon_gets_a_scope_of_its_own_from_the_envelope() -> None:
    adapter = _adapter()
    first = await _dispatch(adapter, dict(ENVELOPES["from_me_dm"]))
    second = await _dispatch(adapter, dict(ENVELOPES["from_me_dm"]))

    assert first.source.thread_id and second.source.thread_id != first.source.thread_id
    assert first.source.chat_id == second.source.chat_id == PEER
    scope = read_scope._scopes[first.source.thread_id]
    assert (scope.session, scope.chat_id, scope.phone) == (OWNER_SESSION, PEER, PHONE)
    assert f"{SUMMON_TOKEN}:{scope.key}" in first.channel_prompt


async def test_an_ordinary_message_gets_no_scope_and_keeps_its_session() -> None:
    adapter = _adapter()
    event = await _dispatch(adapter, dict(ENVELOPES["third_party_dm"]))
    assert event.source.thread_id is None
    assert not read_scope._scopes
    assert adapter._control_session_key(event.source) == adapter._control_session_key(
        (await _dispatch(adapter, dict(ENVELOPES["third_party_dm"]))).source
    )


async def test_the_session_keys_differ_only_for_the_summon() -> None:
    adapter = _adapter()
    ordinary = await _dispatch(adapter, dict(ENVELOPES["third_party_dm"]))
    summon = await _dispatch(adapter, dict(ENVELOPES["from_me_dm"]))
    assert adapter._control_session_key(summon.source) != adapter._control_session_key(ordinary.source)
    assert adapter._control_session_key(summon.source).endswith(summon.source.thread_id)


async def test_a_group_summon_gets_its_own_session_too() -> None:
    adapter = _adapter()
    first = await _dispatch(adapter, dict(ENVELOPES["from_me_group"]))
    second = await _dispatch(adapter, dict(ENVELOPES["from_me_group"]))
    assert adapter._control_session_key(first.source) != adapter._control_session_key(second.source)


async def test_a_retry_of_a_summon_starts_clean_again() -> None:
    adapter = _adapter()
    events, _ = _install_recorders(adapter)
    await adapter._dispatch_envelope(dict(ENVELOPES["from_me_dm"]))
    await adapter._dispatch_envelope(_retry(OWNER_SESSION))
    assert len(events) == 2 and events[0].source.thread_id != events[1].source.thread_id


async def test_a_summon_the_scope_cannot_open_for_is_not_dispatched(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    adapter = _adapter()
    events, _ = _install_recorders(adapter)
    monkeypatch.setattr(read_scope, "open_for_event", lambda *_a: None)
    await adapter._dispatch_envelope(dict(ENVELOPES["from_me_dm"]))
    assert not events
    assert "read scope could not be opened" in caplog.text


async def test_a_host_that_cannot_enforce_gets_no_summoned_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _adapter()
    events, _ = _install_recorders(adapter)
    monkeypatch.setattr(read_scope, "scope_ready", lambda: "no hooks")
    await adapter._dispatch_envelope(dict(ENVELOPES["from_me_dm"]))
    assert not events
    await adapter._dispatch_envelope(dict(ENVELOPES["third_party_dm"]))
    assert len(events) == 1  # and an ordinary message doesn't care


async def test_a_signed_webhook_summon_gets_a_scope_and_an_unsigned_one_does_not() -> None:
    payload = {**_wh_payload("!sammie hi", PEER), "senderId": OWNER, **ENVELOPES["from_me_dm"]}

    signed = (await _webhook(_wh_adapter(secret=True, allow_admin_from=[OWNER]), payload, sign=True))[0]
    scope = read_scope._scopes[signed.source.thread_id]
    assert (scope.session, scope.chat_id) == (OWNER_SESSION, PEER)
    assert f"{SUMMON_TOKEN}:{scope.key}" in signed.channel_prompt

    read_scope._scopes.clear()
    unsigned = (await _webhook(_wh_adapter(secret=False, allow_admin_from=[OWNER]), payload, sign=False))[0]
    assert unsigned.source.thread_id is None and not read_scope._scopes


async def test_a_webhook_summon_the_scope_cannot_open_for_is_not_dispatched(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {**_wh_payload("!sammie hi", PEER), "senderId": OWNER, **ENVELOPES["from_me_dm"]}
    monkeypatch.setattr(read_scope, "open_for_event", lambda *_a: None)
    assert await _webhook(_wh_adapter(secret=True, allow_admin_from=[OWNER]), payload, sign=True) == []


def test_the_plugin_registers_the_hooks() -> None:
    from chatlytics_hermes import adapter as adapter_module

    hooks: Dict[str, Any] = {}
    ctx = SimpleNamespace(
        register_platform=lambda **_k: None, register_tool=lambda **_k: None,
        register_middleware=lambda *_a: None, register_hook=lambda name, fn: hooks.__setitem__(name, fn),
    )
    adapter_module._register_impl(ctx)
    assert hooks == {"pre_tool_call": pre_tool_call, "transform_tool_result": transform_tool_result}


def test_the_scope_key_rides_only_when_given() -> None:
    env = ENVELOPES["from_me_dm"]
    assert render_trigger_context(env).endswith(SUMMON_TOKEN)
    assert render_trigger_context(env, "summon-1").endswith(f"{SUMMON_TOKEN}:summon-1")
    assert with_trigger_context(None, ENVELOPES["third_party_dm"], "summon-1") is None


def test_the_line_says_the_read_is_enforced_and_starts_clean() -> None:
    for name in ("from_me_dm", "from_me_group", "from_me_dm_lid_unmapped"):
        line = render_trigger_context(ENVELOPES[name], None, "mcp__lextrove_limited__") or ""
        assert "refused by code" in line
        assert "no earlier turns of this chat" in line
        assert "do not treat this message as a repeat" in line
    assert f"search_messages with group_name {GROUP}" in (render_trigger_context(ENVELOPES["from_me_group"], None, "mcp__lextrove_limited__") or "")


# --- /stop and /new reach a summon -------------------------------------------------------------


def _control(action: str, session: str) -> Dict[str, Any]:
    return {"kind": "control", "action": action, "session_id": session, "chat_type": "dm",
            "entity_jid": PEER, "sender_jid": OWNER, "ts": 1}


class _Gateway:
    def __init__(self) -> None:
        self.stopped: list = []

    async def _interrupt_and_clear_session(self, session_key: str, source: Any, **_: Any) -> None:
        self.stopped.append(session_key)

    async def _handle_reset_command(self, _event: Any) -> str:
        return ""


def _with_gateway(adapter: Any, monkeypatch: pytest.MonkeyPatch) -> _Gateway:
    gateway = _Gateway()
    monkeypatch.setattr(adapter, "_gateway_runner", lambda: gateway)
    return gateway


@pytest.mark.parametrize("action", ["stop", "new_conversation"])
async def test_control_from_the_summoned_session_reaches_the_running_summon(action: str, monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _adapter()
    gateway = _with_gateway(adapter, monkeypatch)
    summon = await _dispatch(adapter, dict(ENVELOPES["from_me_dm"]))
    await adapter._dispatch_envelope(_control(action, OWNER_SESSION))
    assert adapter._control_session_key(summon.source) in gateway.stopped
    assert any(not key.endswith(summon.source.thread_id) for key in gateway.stopped)


async def test_a_peers_stop_on_the_bots_session_does_not_reach_the_owners_summon(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _adapter()
    gateway = _with_gateway(adapter, monkeypatch)
    summon = await _dispatch(adapter, dict(ENVELOPES["from_me_dm"]))
    await adapter._dispatch_envelope(_control("stop", ENVELOPES["third_party_dm"]["session_id"]))
    assert adapter._control_session_key(summon.source) not in gateway.stopped
    assert gateway.stopped  # its own conversation still stops


async def test_stop_with_no_summon_does_what_it_did(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _adapter()
    gateway = _with_gateway(adapter, monkeypatch)
    await adapter._dispatch_envelope(_control("stop", OWNER_SESSION))
    assert len(gateway.stopped) == 1


# --- readiness ---------------------------------------------------------------------------------


def test_registering_needs_register_hook(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.undo()
    monkeypatch.setattr(read_scope, "_registered", False)
    read_scope.register_scope(SimpleNamespace())
    assert read_scope._registered is False
    assert "no register_hook" in caplog.text
    assert "not registered" in (read_scope.scope_ready() or "")


def test_registering_adds_both_hooks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(read_scope, "_registered", False)
    calls: Dict[str, Any] = {}
    read_scope.register_scope(SimpleNamespace(register_hook=lambda name, fn: calls.__setitem__(name, fn)))
    assert calls == {"pre_tool_call": pre_tool_call, "transform_tool_result": transform_tool_result}
    assert read_scope._registered is True
