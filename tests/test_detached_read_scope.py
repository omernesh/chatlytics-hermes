"""chatlytics#974: a conversation its chat moved past stays under its read limit, and so do its subagents.

hermes-agent 0.22.6 keeps such a conversation running from the apps. This drives the host's own code: its
``name_lane`` detaches the conversation, its ``_set_session_env`` binds the turn, and in the last section its
``AIAgent`` and ``delegate_task`` run a real child against a scripted provider. The runner around them is a
stub, and no app was involved: nothing here is a live detached turn.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import threading
from types import SimpleNamespace as NS
from typing import Any, Callable, Dict, Iterator, Optional

import pytest

from chatlytics_hermes import read_scope
from chatlytics_hermes.memory_strip import llm_request_middleware
from chatlytics_hermes.read_scope import open_chat_turn, open_for_event, pre_tool_call, transform_tool_result
from chatlytics_hermes.tools import chatlytics_send
from tests.test_chat_read_scope import BOT, EVERY_ACCOUNT, GROUP, LM, MEMBER, MEMBER_GROUP, OWNER, OWNER_DM, _reason
from tests.test_read_scope_real_agent import Provider, _call, _everyone, _text, _tools, host  # noqa: F401
from tests.test_summon_outbound import _adapter as _hub_adapter, _bound_tool, _deliver
from tests.test_trigger_context import ENVELOPES, OWNER_SESSION

TURN_ID = "20260101_000000_old"
GROUP_READ = {"chat_id": GROUP, "session": BOT}
ALLOW_OWNER = {"allow_admin_from": [OWNER]}


def _hook(tool: str, args: Any, ids: str = "a-child") -> Optional[Dict[str, str]]:
    return pre_tool_call(tool_name=tool, args=args, session_id=ids, task_id=ids, turn_id="t")


def _no_line_request(session_id: str, task_id: str) -> None:
    """A provider request of an app's turn, through the real middleware: ``execute_admission`` builds its
    event with no ``channel_prompt``, so the system prompt has no context line."""
    request = {"messages": [{"role": "system", "content": "the system prompt"}, {"role": "user", "content": "q"}]}
    assert llm_request_middleware(request=request, session_id=session_id, task_id=task_id, turn_id="t") is None


class _Store:
    """What the host's helpers ask of a session store. ``row`` is the state.db row of a conversation."""

    def __init__(self) -> None:
        self.row: Optional[Dict[str, Any]] = None

    def _named_profile_for_key(self, _key: str) -> None:
        return None

    def lookup_by_session_key(self, _key: str) -> None:
        return None

    def _db_for_key(self, _key: str) -> Any:
        return NS(get_session=lambda _sid: self.row)


@pytest.fixture
def gateway(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """The host's ``SessionAuthority`` and ``_set_session_env``, on a stub runner."""
    pytest.importorskip("gateway.session_detached", reason="host older than 0.22.6 has no detached conversations")
    from gateway import run
    from gateway.config import Platform
    from gateway.session import SessionSource, build_session_key
    from gateway.session_authority import LiveSession, SessionAuthority

    authority = SessionAuthority.__new__(SessionAuthority)
    runner = NS(session_store=_Store(), session_authority=authority, adapters={})
    authority.runner, authority.sessions, authority.profile_id = runner, {}, "default"
    monkeypatch.setattr(run, "_gateway_runner_ref", lambda: runner)

    def moved_past(platform: str, chat: str, **fields: Any) -> Callable[[], Any]:
        """The chat's ``/new``: returns a function that makes the context the next turn of the old one binds."""
        source = SessionSource(platform=Platform(platform), chat_id=chat, **fields)
        route = build_session_key(source)
        live = authority.sessions[TURN_ID] = LiveSession(source, route)
        authority.name_lane(route, "20260101_000000_new", source)
        assert live.detached and live.route.endswith(f":detached:{TURN_ID}")
        assert build_session_key(live.source) == live.route  # the key the host's turn runs under
        context = NS(source=live.source, session_key=live.route, session_id=TURN_ID)

        def bound() -> Any:
            turn = contextvars.copy_context()
            turn.run(run.GatewayRunner._set_session_env, runner, context)
            return turn

        return bound

    yield NS(moved_past=moved_past, runner=runner, authority=authority, run=run)


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    yield
    for table in (read_scope._bound, read_scope._scopes, read_scope._chat_scopes, read_scope._chat_turns):
        table.clear()


def _member_turn(gateway: Any) -> Any:
    source = NS(chat_id=GROUP, profile=None)
    assert open_chat_turn(MEMBER_GROUP, NS(source=source), {}, authenticated=True) is not None
    return gateway.moved_past("chatlytics", GROUP, chat_type="group", user_id=MEMBER)()


def _summon_turn(gateway: Any) -> Any:
    env = ENVELOPES["from_me_group"]
    source = NS(thread_id=None, chat_id=env["entity_jid"], profile=None)
    scope = open_for_event(env, NS(source=source))
    assert scope is not None
    turn = gateway.moved_past("chatlytics", GROUP, chat_type="group", user_id=OWNER, thread_id=scope.key)()
    return NS(context=turn, scope=scope)


def _owner_dm_turn(gateway: Any) -> Any:
    source = NS(chat_id=OWNER, user_id=OWNER, chat_type="dm", profile=None)
    assert open_chat_turn(OWNER_DM, NS(source=source), ALLOW_OWNER, authenticated=True) is None
    return gateway.moved_past("chatlytics", OWNER, chat_type="dm", user_id=OWNER)()


# --- what the host binds ------------------------------------------------------------------------


def test_the_host_binds_a_detached_turn_with_neither_way_in(gateway: Any) -> None:
    # Why the key is needed. If the host starts binding the chat here, this says so.
    from gateway.session_context import bound_session_key, get_session_env

    def read() -> Any:
        return (
            get_session_env("HERMES_SESSION_PLATFORM"), get_session_env("HERMES_SESSION_THREAD_ID"),
            get_session_env("HERMES_SESSION_CHAT_ID"), bound_session_key(),
        )

    platform, thread, chat, key = _member_turn(gateway).copy().run(read)
    assert (platform, thread, chat) == ("local", "", TURN_ID)
    assert key.endswith(f":detached:{TURN_ID}") and read_scope._DETACHED_KEY.fullmatch(key)


# --- limited ------------------------------------------------------------------------------------


def test_a_subagent_of_a_limited_conversation_stays_limited(gateway: Any) -> None:
    child = _member_turn(gateway).copy()
    assert _reason(child.run(_hook, LM, EVERY_ACCOUNT)) == "session_not_summon"
    assert child.run(_hook, LM, GROUP_READ) is None


def test_the_turn_itself_with_no_context_line_stays_limited(gateway: Any) -> None:
    # Measured on the code: its request has no line, and that used to release its ids.
    turn = _member_turn(gateway)
    turn.run(_no_line_request, TURN_ID, "the-task")
    assert _reason(turn.run(_hook, LM, EVERY_ACCOUNT, TURN_ID)) == "session_not_summon"


def test_execute_code_of_the_turn_is_found_by_its_task_id(gateway: Any) -> None:
    # Its RPC thread has no context variables, only the task id that the request bound.
    turn = _member_turn(gateway)
    turn.run(_no_line_request, "a-child", "the-task")
    out: list = []
    worker = threading.Thread(target=lambda: out.append(pre_tool_call(tool_name=LM, args=EVERY_ACCOUNT, task_id="the-task")))
    worker.start()
    worker.join()
    assert _reason(out[0]) == "session_not_summon"


def test_a_result_of_another_chat_is_refused_for_the_subagent_too(gateway: Any) -> None:
    child = _member_turn(gateway).copy()
    rows = '{"data": {"items": [{"id": "m1", "chat_id": "999@c.us", "body": "x"}]}}'
    out = child.run(lambda: transform_tool_result(tool_name=LM, result=rows, session_id="a-child", task_id="a-child"))
    assert out is not None and "result_row_other_chat" in out


def test_no_entry_for_the_chat_is_limited_and_reads_nothing(gateway: Any) -> None:
    # A restart: the host mounts the old conversation detached, and this process has no verdict for its chat.
    child = gateway.moved_past("chatlytics", GROUP, chat_type="group", user_id=MEMBER)().copy()
    assert _reason(child.run(_hook, LM, GROUP_READ)) == "no_scope_for_this_turn"


# --- summoned -----------------------------------------------------------------------------------


def test_a_subagent_of_a_summoned_conversation_keeps_the_summons_scope(gateway: Any) -> None:
    turn = _summon_turn(gateway)
    child = turn.context.copy()
    assert _reason(child.run(_hook, LM, EVERY_ACCOUNT)) == "session_not_summon"
    assert child.run(_hook, LM, {"chat_id": GROUP, "session": OWNER_SESSION}) is None
    assert _reason(child.run(_hook, "mcp__hub__send_message", {})) == read_scope._HUB_WRITE_REFUSED


def test_a_summon_whose_scope_is_gone_reads_nothing(gateway: Any) -> None:
    turn = _summon_turn(gateway)
    read_scope._scopes.clear()
    assert _reason(turn.context.copy().run(_hook, LM, {"chat_id": GROUP, "session": OWNER_SESSION})) == "no_scope_for_this_turn"


def test_a_tool_of_a_summons_app_turn_is_found_as_the_summon(gateway: Any) -> None:
    # A send of this turn then follows the summon's account rules, not an ordinary turn's.
    turn = _summon_turn(gateway)
    turn.context.run(_no_line_request, TURN_ID, "the-task")
    assert read_scope.turn_key_for_tool("the-task") == turn.scope.key


# --- fails closed -------------------------------------------------------------------------------


def test_no_runner_is_refused(gateway: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    turn = _member_turn(gateway)
    monkeypatch.setattr(gateway.run, "_gateway_runner_ref", lambda: None)
    assert _reason(turn.copy().run(_hook, LM, GROUP_READ)) == "no_scope_for_this_turn"


def test_a_host_that_renamed_its_helper_is_refused(gateway: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from gateway import session_detached

    turn = _member_turn(gateway)
    monkeypatch.delattr(session_detached, "detached_live_source")
    assert _reason(turn.copy().run(_hook, LM, GROUP_READ)) == "no_scope_for_this_turn"


@pytest.mark.parametrize("row, limited", [
    ({"source": "chatlytics"}, True), ({"source": ""}, True), (None, True), ({"source": "telegram"}, False),
])
def test_a_conversation_the_chat_took_back_is_judged_by_its_row(gateway: Any, row: Any, limited: bool) -> None:
    # A turn still running on the detached key, or a background child, after the chat's /resume.
    turn = gateway.moved_past("telegram", "123", chat_type="dm", user_id="1")()
    del gateway.authority.sessions[TURN_ID]
    gateway.runner.session_store.row = row
    verdict = turn.copy().run(_hook, LM, EVERY_ACCOUNT)
    assert (_reason(verdict) == "no_scope_for_this_turn") if limited else (verdict is None)


# --- not limited --------------------------------------------------------------------------------


def test_the_owners_own_dm_stays_unlimited(gateway: Any) -> None:
    turn = _owner_dm_turn(gateway)
    assert turn.copy().run(_hook, LM, EVERY_ACCOUNT) is None
    turn.run(_no_line_request, TURN_ID, TURN_ID)
    assert turn.run(_hook, LM, EVERY_ACCOUNT, TURN_ID) is None
    assert TURN_ID not in read_scope._bound


def test_a_telegram_conversation_stays_unlimited(gateway: Any) -> None:
    turn = gateway.moved_past("telegram", "123", chat_type="dm", user_id="1")()
    assert turn.copy().run(_hook, LM, EVERY_ACCOUNT) is None
    turn.run(_no_line_request, TURN_ID, TURN_ID)
    assert TURN_ID not in read_scope._bound


@pytest.mark.parametrize("session_vars", [
    {"platform": "local", "chat_id": "a-session", "session_key": "agent:main:local:dm:a-session"},  # the desktop
    {"platform": "telegram", "chat_id": "123", "session_key": "agent:main:telegram:dm:123"},
    {"cron_session": "1", "session_key": "agent:main:cron:a-job"},
    {},
])
def test_turns_on_no_detached_key_are_not_touched(session_vars: Dict[str, Any]) -> None:
    # On every host. A failure of the lookup can't reach these: none of them gets that far.
    from gateway import session_context as sc

    def run() -> Any:
        sc.set_session_vars(**session_vars)
        return _hook(LM, EVERY_ACCOUNT)

    assert contextvars.copy_context().run(run) is None


def test_a_detached_key_in_the_process_environment_does_not_count(gateway: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    # What a `hermes` command started by a shell tool inherits: no context, and this process's own tables.
    monkeypatch.setenv("HERMES_SESSION_KEY", f"agent:main:detached:{TURN_ID}")
    monkeypatch.setenv("HERMES_SESSION_PLATFORM", "local")
    assert contextvars.Context().run(_hook, LM, EVERY_ACCOUNT) is None


# --- sends --------------------------------------------------------------------------------------


async def test_a_send_tool_of_a_limited_conversation_still_reaches_the_chat(gateway: Any) -> None:
    # Open, and not this issue: the host keeps a detached turn's REPLIES from the chat, and this tool posts
    # to the hub itself. Docs: "What this does not stop". What it can send is limited by what it can read.
    adapter, hub, conn = _hub_adapter()
    event = await _deliver(adapter, MEMBER_GROUP, conn)
    assert event is not None
    turn = gateway.moved_past("chatlytics", GROUP, chat_type="group", user_id=MEMBER)()
    tool = _bound_tool(adapter, chatlytics_send, "chatlytics_send")
    out = await asyncio.create_task(tool({"chatId": GROUP, "text": "from the app"}), context=turn)
    assert json.loads(out)["success"] is True
    assert hub.left == [(BOT, GROUP, "from the app")]


async def test_a_send_tool_of_a_summons_app_turn_follows_the_summons_account(gateway: Any) -> None:
    adapter, hub, conn = _hub_adapter()
    env = ENVELOPES["from_me_group"]
    event = await _deliver(adapter, env, conn)
    assert event is not None
    hub.routes[GROUP] = OWNER_SESSION
    turn = gateway.moved_past("chatlytics", GROUP, chat_type="group", user_id=OWNER, thread_id=event.source.thread_id)()
    turn.run(_no_line_request, TURN_ID, "the-task")
    tool = _bound_tool(adapter, chatlytics_send, "chatlytics_send")
    await asyncio.create_task(tool({"chatId": GROUP, "text": "from the app"}, task_id="the-task"), context=turn)
    assert hub.left == [(OWNER_SESSION, GROUP, "from the app")]


# --- a real child -------------------------------------------------------------------------------


def _run_child(gateway: Any, host: Any, turn: Any, monkeypatch: pytest.MonkeyPatch) -> Provider:  # noqa: F811
    """A real agent on the turn's context calls ``delegate_task``, and the real child reads everything."""
    import run_agent

    host.tool(LM, _everyone)
    wrapped = _call("tool_call", {"calls": [{"name": LM, "arguments": EVERY_ACCOUNT}]})
    provider = host.provider(
        _tools(_call("delegate_task", {"goal": "read everything", "toolsets": ["mcp-lextrove"]})),
        _tools(wrapped), _text("child done"), _text("parent done"),
    )
    agent = run_agent.AIAgent(
        model="test-model", base_url=provider.url, api_key="x", provider="custom", platform="whatsapp",
        quiet_mode=True, skip_context_files=True, skip_memory=True, save_trajectories=False,
        enabled_toolsets=["mcp-lextrove", "delegation"], session_id=TURN_ID, max_iterations=5,
    )
    turn.run(agent.run_conversation, "q", conversation_history=[], task_id=TURN_ID)
    return provider


def _tool_text(provider: Provider) -> str:
    return "\n".join(
        str(message.get("content")) for request in provider.requests for message in request["messages"] if message.get("role") == "tool"
    )


def test_a_real_child_of_a_limited_conversation_is_refused(
    gateway: Any, host: Any, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    provider = _run_child(gateway, host, _member_turn(gateway), monkeypatch)
    assert host.served == []
    assert "session_not_summon" in _tool_text(provider)


def test_a_real_child_of_the_owners_dm_reads(gateway: Any, host: Any, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    # The control: the same child under the same script really reaches the tool when nothing limits it.
    _run_child(gateway, host, _owner_dm_turn(gateway), monkeypatch)
    assert [name for name, _ in host.served] == [LM]
