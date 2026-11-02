"""chatlytics#956 through the real pipeline: AIAgent's own tool loop against a scripted provider.

Skips on a host without ``llm_request`` middleware. There is no CI on this repo, so nothing runs
this file automatically: run it against the hermes-agent source a release targets. The provider is
a local HTTP server, so everything from the request builder to the tool result is the host's code.
"""

from __future__ import annotations

import json
import logging
import threading
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional

import pytest

from chatlytics_hermes import memory_strip, read_scope
from chatlytics_hermes.memory_strip import llm_request_middleware
from chatlytics_hermes.read_scope import open_for_event, pre_tool_call, transform_tool_result
from chatlytics_hermes.trigger_context import render_trigger_context
from tests.test_owner_tagging import _dispatch, _lp_adapter
from tests.test_trigger_context import ENVELOPES, OWNER, OWNER_SESSION, PEER

PHONE = ENVELOPES["from_me_dm"]["chat_phone"]
GROUP = ENVELOPES["from_me_group"]["entity_jid"]
LM = "mcp__lextrove__list_messages"
SM = "mcp__lextrove__search_messages"
SECRET = "OTHER-ACCOUNT-BODY"
# conftest.py replaces it for every test, after this module is imported.
REAL_SCOPE_READY = read_scope.scope_ready


def _call(name: str, args: Dict[str, Any], call_id: str = "c1") -> Dict[str, Any]:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def _tools(*calls: Dict[str, Any]) -> Dict[str, Any]:
    return {"role": "assistant", "content": None, "tool_calls": list(calls)}


def _text(text: str) -> Dict[str, Any]:
    return {"role": "assistant", "content": text}


class Provider:
    """An OpenAI-compatible endpoint that plays a script and records every request."""

    def __init__(self, script: List[Any]) -> None:
        self.script, self.requests = list(script), []
        provider = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args: Any) -> None:
                pass

            def do_POST(self) -> None:  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if not self.path.endswith("/chat/completions"):  # the host's model-metadata probes
                    self.send_response(404)
                    self.end_headers()
                    return
                provider.requests.append(body)
                step = provider.script.pop(0) if provider.script else _text("done")
                if isinstance(step, int):
                    self.send_response(step)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": {"message": "weekly limit", "type": "rate_limit"}}).encode())
                    return
                message = {"role": "assistant", **step}
                finish = "tool_calls" if step.get("tool_calls") else "stop"
                if body.get("stream"):
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.end_headers()
                    delta = dict(message)
                    if delta.get("tool_calls"):
                        delta["tool_calls"] = [{"index": i, **c} for i, c in enumerate(delta["tool_calls"])]
                    for payload in (
                        {"id": "x", "object": "chat.completion.chunk", "model": "m",
                         "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                        {"id": "x", "object": "chat.completion.chunk", "model": "m",
                         "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]},
                    ):
                        self.wfile.write(f"data: {json.dumps(payload)}\n\n".encode())
                    self.wfile.write(b"data: [DONE]\n\n")
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "id": "x", "object": "chat.completion", "created": 1, "model": "m",
                    "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                }).encode())

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def host(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Any]:
    """A real agent factory in a throwaway home, with this plugin's hooks in the host's chains."""
    middleware = pytest.importorskip("hermes_cli.middleware", reason="host older than 0.22")
    if not hasattr(middleware, "apply_llm_request_middleware"):
        pytest.skip("host has no llm_request middleware")
    run_agent = pytest.importorskip("run_agent")
    plugins = pytest.importorskip("hermes_cli.plugins")
    registry = pytest.importorskip("tools.registry").registry

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    manager = plugins.PluginManager()
    manager._discovered = True
    manager._middleware["llm_request"] = [llm_request_middleware]
    manager._hooks["pre_tool_call"] = [pre_tool_call]
    manager._hooks["transform_tool_result"] = [transform_tool_result]
    monkeypatch.setattr(plugins, "_delivery_manager", lambda: manager)
    monkeypatch.setattr(memory_strip, "_registered", True)
    monkeypatch.setattr(read_scope, "_registered", True)
    monkeypatch.setattr(read_scope, "_chain_ok", None)

    served: List[Any] = []
    providers: List[Provider] = []
    names: List[str] = []

    def tool(name: str, answer: Callable[[Dict[str, Any]], Any], toolset: str = "mcp-lextrove") -> None:
        def handler(args: Dict[str, Any], **_: Any) -> str:
            served.append((name, args))
            return json.dumps({"result": json.dumps(answer(args))})

        registry.register(name=name, toolset=toolset, handler=handler, schema={
            "name": name, "description": f"test {name}",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": True},
        })
        names.append(name)

    def provider(*script: Any) -> Provider:
        made = Provider(list(script))
        providers.append(made)
        return made

    def agent(primary: Provider, *, prompt: Optional[str], session_id: str, fallback: Optional[Provider] = None,
              visible: bool = False, **kwargs: Any) -> Any:
        """``visible``: the tools in the model's list too, not only behind the Tool Search bridge."""
        built = run_agent.AIAgent(
            model="test-model", base_url=primary.url, api_key="x", provider="custom", platform="whatsapp",
            quiet_mode=True, skip_context_files=True, skip_memory=True, save_trajectories=False,
            enabled_toolsets=["mcp-lextrove"], ephemeral_system_prompt=prompt, session_id=session_id,
            fallback_model={"provider": "custom", "model": "fallback-model", "base_url": fallback.url, "api_key": "y"} if fallback else None,
            max_iterations=30, **kwargs,
        )
        if visible:
            built.valid_tool_names = set(built.valid_tool_names) | set(names)
            built.tools = list(built.tools) + [
                {"type": "function", "function": {"name": name, "description": "x", "parameters": {"type": "object", "properties": {}}}}
                for name in names
            ]
        return built

    yield types.SimpleNamespace(agent=agent, tool=tool, provider=provider, served=served, manager=manager, plugins=plugins)
    for name in names:
        registry.deregister(name)
    for made in providers:
        made.close()


def _scope(env_name: str = "from_me_dm") -> Any:
    env = ENVELOPES[env_name]
    scope = open_for_event(env, types.SimpleNamespace(source=types.SimpleNamespace(thread_id=None)))
    assert scope is not None
    return scope, render_trigger_context(env, scope.key)


def _good_search(_args: Dict[str, Any]) -> Dict[str, Any]:
    return {"data": {
        "items": [{"id": "m1", "chat_id": f"{PHONE}@s.whatsapp.net", "chat_type": "contact", "body": "THIS-CHAT-BODY"}],
        "resolved_entity": {"ambiguous": False, "similarity_score": 1.0, "identifier": f"{PHONE}@c.us"},
    }}


def _everyone(_args: Dict[str, Any]) -> Dict[str, Any]:
    return {"data": {"items": [{"id": "m9", "chat_id": "999@c.us", "body": SECRET}], "resolved_entity": None}}


def _tool_rows(result: Dict[str, Any]) -> List[str]:
    return [m["content"] for m in result["messages"] if m.get("role") == "tool"]


FAILED_ARGS = {"chat_id": f"{PHONE}@c.us", "session": "all", "limit": 30}
DM_READ = {"contact_name": PHONE, "chat_type": "dm", "session": OWNER_SESSION, "mode": "fts"}


# --- the failed turn, replayed -----------------------------------------------------------------


def test_the_failed_turn_through_the_real_wrapper_never_reaches_the_tool(host: Any, caplog: pytest.LogCaptureFixture) -> None:
    host.tool(LM, _everyone)
    _scope_, line = _scope()
    provider = host.provider(_tools(_call("tool_call", {"calls": [{"name": LM, "arguments": FAILED_ARGS}]})), _text("I cannot read that."))
    agent = host.agent(provider, prompt=line, session_id="20260101_000000_a")

    with caplog.at_level(logging.WARNING, logger="chatlytics_hermes"):
        result = agent.run_conversation("[owner reply] summarize this chat", conversation_history=[], task_id="20260101_000000_a")

    assert host.served == []
    row = _tool_rows(result)[0]
    assert "session_not_summon" in row and "cannot read outside this chat" in row and SECRET not in row
    assert f"tool={LM} reason=session_not_summon" in caplog.text
    assert line.split("reference")[0] in provider.requests[0]["messages"][0]["content"]


def test_a_direct_call_is_refused_the_same_way(host: Any) -> None:
    host.tool(LM, _everyone)
    _s, line = _scope()
    provider = host.provider(_tools(_call(LM, FAILED_ARGS)), _text("no"))
    result = host.agent(provider, prompt=line, session_id="20260101_000000_b", visible=True).run_conversation("q", conversation_history=[])
    assert host.served == []
    assert any("session_not_summon" in row for row in _tool_rows(result))


def test_parallel_entries_in_one_wrapper_are_each_enforced(host: Any) -> None:
    host.tool(SM, _good_search)
    host.tool(LM, _everyone)
    _s, line = _scope()
    batch = {"calls": [{"name": SM, "arguments": DM_READ}, {"name": LM, "arguments": FAILED_ARGS}]}
    provider = host.provider(_tools(_call("tool_call", batch)), _text("ok"))
    result = host.agent(provider, prompt=line, session_id="20260101_000000_c").run_conversation("q", conversation_history=[])
    assert [name for name, _ in host.served] == [SM]
    rows = _tool_rows(result)
    assert any("THIS-CHAT-BODY" in row for row in rows)
    assert any("session_not_summon" in row for row in rows) and not any(SECRET in row for row in rows)


def test_parallel_direct_calls_in_one_response_are_each_enforced(host: Any) -> None:
    host.tool(SM, _good_search)
    host.tool(LM, _everyone)
    _s, line = _scope()
    provider = host.provider(_tools(_call(SM, DM_READ, "c1"), _call(LM, FAILED_ARGS, "c2")), _text("ok"))
    result = host.agent(provider, prompt=line, session_id="20260101_000000_d", visible=True).run_conversation("q", conversation_history=[])
    assert [name for name, _ in host.served] == [SM]
    assert not any(SECRET in row for row in _tool_rows(result))


def test_a_wrapper_the_host_cannot_unwrap_is_still_enforced(host: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    # agent/tool_executor.py swallows any error of its unwrap: the hook then sees "tool_call", and
    # the dispatcher runs the entry with this hook skipped. So this hook is the only check there is.
    executor = pytest.importorskip("agent.tool_executor")
    host.tool(LM, _everyone)
    _s, line = _scope()

    def broken(_agent: Any) -> Any:
        raise RuntimeError("scope lookup")

    monkeypatch.setattr(executor, "_tool_search_scoped_names", broken)
    provider = host.provider(_tools(_call("tool_call", {"calls": [{"name": LM, "arguments": FAILED_ARGS}]})), _text("no"))
    result = host.agent(provider, prompt=line, session_id="20260101_000000_e").run_conversation("q", conversation_history=[])
    assert host.served == []
    assert any("session_not_summon" in row for row in _tool_rows(result))


# --- the right read ----------------------------------------------------------------------------


def test_the_right_read_works_across_a_long_tool_loop(host: Any) -> None:
    host.tool(SM, _good_search)
    _s, line = _scope()
    script = [_tools(_call("tool_call", {"calls": [{"name": SM, "arguments": DM_READ}]}, f"c{i}")) for i in range(15)]
    provider = host.provider(*script, _text("the summary"))
    agent = host.agent(provider, prompt=line, session_id="20260101_000000_f")
    # The host's own repeat-call guardrail would stop 15 identical calls: vary them, as a real read does.
    for i, step in enumerate(script):
        step["tool_calls"][0]["function"]["arguments"] = json.dumps(
            {"calls": [{"name": SM, "arguments": {**DM_READ, "limit": i + 1}}]}
        )
    result = agent.run_conversation("q", conversation_history=[])
    assert result["final_response"] == "the summary"
    assert len(host.served) == 15
    assert all("THIS-CHAT-BODY" in row for row in _tool_rows(result))
    # Every iteration still carries the line and its own earlier rows.
    assert all("Chatlytics turn context" in r["messages"][0]["content"] for r in provider.requests)
    assert sum(m["role"] == "tool" for m in provider.requests[-1]["messages"]) == 15


def test_a_result_for_the_wrong_person_is_replaced_before_the_model_sees_it(host: Any) -> None:
    def fuzzy(_args: Dict[str, Any]) -> Dict[str, Any]:
        return {"data": {
            "items": [{"id": "m9", "chat_id": "888@s.whatsapp.net", "body": SECRET}],
            "resolved_entity": {"ambiguous": False, "similarity_score": 0.91, "identifier": "888@c.us"},
        }}

    host.tool(SM, fuzzy)
    _s, line = _scope()
    provider = host.provider(_tools(_call(SM, DM_READ)), _text("no"))
    result = host.agent(provider, prompt=line, session_id="20260101_000000_g", visible=True).run_conversation("q", conversation_history=[])
    assert len(host.served) == 1  # it ran,
    assert SECRET not in json.dumps(provider.requests[-1])  # and the model never saw it
    assert any("result_match_not_positive" in row for row in _tool_rows(result))


# --- the fallback path -------------------------------------------------------------------------


def test_the_scope_and_the_line_survive_a_fallback_mid_turn(host: Any, caplog: pytest.LogCaptureFixture) -> None:
    # The failed turn: the primary hits a weekly limit and the turn runs on a weaker model.
    host.tool(LM, _everyone)
    _s, line = _scope()
    primary = host.provider(429, 429, 429, 429, 429, 429)
    fallback = host.provider(_tools(_call(LM, FAILED_ARGS)), _text("I cannot read that."))
    agent = host.agent(primary, prompt=line, session_id="20260101_000000_h", fallback=fallback, visible=True)

    with caplog.at_level(logging.WARNING, logger="chatlytics_hermes"):
        result = agent.run_conversation("q", conversation_history=[])

    assert primary.requests and fallback.requests, "the fallback path did not run"
    assert all("fallback-model" == r["model"] for r in fallback.requests)
    assert all(f"{read_scope._SCOPE_KEY.findall(line)[-1]}" in r["messages"][0]["content"] for r in fallback.requests)
    assert host.served == []
    assert "reason=session_not_summon" in caplog.text
    assert not any(SECRET in row for row in _tool_rows(result))


def test_the_same_call_on_an_ordinary_turn_runs(host: Any) -> None:
    # The control for everything above.
    host.tool(LM, _everyone)
    provider = host.provider(_tools(_call(LM, FAILED_ARGS)), _text("done"))
    result = host.agent(provider, prompt=None, session_id="20260101_000000_i", visible=True).run_conversation("q", conversation_history=[])
    assert [name for name, _ in host.served] == [LM]
    assert any(SECRET in row for row in _tool_rows(result))


# --- the other ways in -------------------------------------------------------------------------


def test_the_rpc_path_of_execute_code_is_bound_by_task_id(host: Any) -> None:
    # tools/code_execution_rpc.py: handle_function_call(name, args, task_id=task_id), no session id.
    model_tools = pytest.importorskip("model_tools")
    host.tool(LM, _everyone)
    scope, _ = _scope()
    read_scope.note_request(True, scope.key, "the-session", "the-task")
    out = model_tools.handle_function_call(LM, FAILED_ARGS, task_id="the-task")
    assert "session_not_summon" in out and host.served == []


def test_the_hook_worker_sees_the_context_variable(host: Any) -> None:
    # Hooks run on a timeout worker (hermes_cli/plugins_dispatch.py), and subagents on copied
    # contexts (tools/delegate_tool_dispatch.py). Neither ids nor binding here: only the variable.
    session_context = pytest.importorskip("gateway.session_context")
    scope, _ = _scope()
    directive, message = host.plugins.get_pre_tool_call_directive(LM, FAILED_ARGS)
    assert directive is None  # the control

    tokens = session_context.set_session_vars(thread_id=scope.key)
    try:
        import contextvars

        out: List[Any] = []
        child = contextvars.copy_context()  # what the delegate tool does for a child agent
        worker = threading.Thread(target=lambda: child.run(lambda: out.append(host.plugins.get_pre_tool_call_directive(LM, FAILED_ARGS, session_id="a-child-agent"))))
        worker.start()
        worker.join()
    finally:
        session_context.clear_session_vars(tokens)
    assert out[0][0] == "block" and "session_not_summon" in out[0][1]


def test_the_delegate_tool_really_copies_the_context_for_a_child() -> None:
    # Why the variable reaches a subagent. A source check, because driving it needs a whole child run.
    dispatch = pytest.importorskip("tools.delegate_tool_dispatch")
    assert "executor.submit(contextvars.copy_context().run, batch.run_child" in Path(dispatch.__file__).read_text("utf-8")


def test_the_host_turn_sets_the_thread_variable_from_the_source() -> None:
    run = pytest.importorskip("gateway.run")
    source = Path(run.__file__).read_text("utf-8")
    assert "thread_id=str(context.source.thread_id) if context.source.thread_id else" in source


# --- readiness on the real host ----------------------------------------------------------------


def test_the_real_host_is_ready_and_a_broken_chain_is_not(host: Any) -> None:
    seen: List[Any] = []

    def other(**kwargs: Any) -> None:
        seen.append(kwargs["tool_name"])

    host.manager._hooks["pre_tool_call"].insert(0, other)
    assert REAL_SCOPE_READY() is None
    assert REAL_SCOPE_READY() is None
    assert seen == [read_scope._PROBE_TOOL]  # other plugins see one probe per chain
    assert read_scope._PROBE_ID not in read_scope._bound

    host.manager._hooks["pre_tool_call"].remove(pre_tool_call)
    assert "no longer registered" in (REAL_SCOPE_READY() or "")
    host.manager._hooks["pre_tool_call"].append(pre_tool_call)
    host.manager._hooks["transform_tool_result"].clear()
    assert "transform_tool_result" in (REAL_SCOPE_READY() or "")


def test_a_host_whose_dispatch_ignores_the_hook_is_not_ready(host: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(host.plugins, "get_pre_tool_call_directive", lambda *_a, **_k: (None, None))
    assert "did not refuse" in (REAL_SCOPE_READY() or "")


def test_the_real_plugin_context_registers_what_the_probe_looks_for(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # The readiness check looks for these functions in the host's chain. If the host wrapped them,
    # every summon would be refused in production with every test here still green.
    plugins = pytest.importorskip("hermes_cli.plugins")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    manager = plugins.PluginManager()
    manager._discovered = True
    monkeypatch.setattr(plugins, "_delivery_manager", lambda: manager)
    monkeypatch.setattr(read_scope, "_registered", False)
    monkeypatch.setattr(read_scope, "_chain_ok", None)
    ctx = plugins.PluginContext(plugins.PluginManifest(name="chatlytics"), manager)
    read_scope.register_scope(ctx)
    assert REAL_SCOPE_READY() is None


def test_the_mcp_tool_name_shape_is_the_real_one() -> None:
    schema = pytest.importorskip("tools.mcp_tool_schema")
    assert schema.mcp_prefixed_tool_name("lextrove", "list_messages") == LM
    assert read_scope._lextrove_tool(schema.mcp_prefixed_tool_name("lextrove", "read_resource")) == "unknown"


def test_the_wrapper_shapes_are_the_hosts() -> None:
    # read_scope._wrapped_calls follows this function: any shape it accepts, the plugin must read.
    validation = pytest.importorskip("tools.tool_search_validation")
    for shape in (
        {"calls": [{"name": "t", "arguments": {"a": 1}}]},
        {"calls": json.dumps([{"name": "t", "arguments": {"a": 1}}])},
        {"calls": {"name": "t", "arguments": {"a": 1}}},
        {"name": "t", "arguments": {"a": 1}},
    ):
        entries, error = validation.normalize_tool_call_entries(shape)
        assert error is None and entries[0]["name"] == "t"
        assert read_scope._wrapped_calls(shape)[0][0] == "t"


# --- a clean start, on the real session store ---------------------------------------------------


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    session = pytest.importorskip("gateway.session")
    config = pytest.importorskip("gateway.config")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    return session.SessionStore(tmp_path, config.GatewayConfig())


def _adapter() -> Any:
    return _lp_adapter(allow_admin_from=[OWNER], group_allow_admin_from=[OWNER])


@pytest.mark.parametrize("summon, ordinary", [("from_me_dm", "third_party_dm"), ("from_me_group", None)])
async def test_each_summon_gets_a_session_with_no_history_and_later_turns_keep_theirs(store: Any, summon: str, ordinary: Optional[str]) -> None:
    adapter = _adapter()
    base_env = ENVELOPES[ordinary] if ordinary else {k: v for k, v in ENVELOPES[summon].items() if k != "trigger_source"}
    chat = await _dispatch(adapter, dict(base_env))
    assert chat.source.thread_id is None
    entry = store.get_or_create_session(chat.source)
    store.append_to_transcript(entry.session_id, {"role": "user", "content": "OLD-QUESTION"})
    store.append_to_transcript(entry.session_id, {"role": "assistant", "content": "OLD-ANSWER"})

    first = store.get_or_create_session((await _dispatch(adapter, dict(ENVELOPES[summon]))).source)
    assert first.session_id != entry.session_id
    assert store.load_transcript(first.session_id) == []
    store.append_to_transcript(first.session_id, {"role": "assistant", "content": "FIRST-SUMMON-RESULT"})

    second = store.get_or_create_session((await _dispatch(adapter, dict(ENVELOPES[summon]))).source)
    assert second.session_id not in (entry.session_id, first.session_id)
    assert store.load_transcript(second.session_id) == []

    later = store.get_or_create_session((await _dispatch(adapter, dict(base_env))).source)
    assert later.session_id == entry.session_id
    text = json.dumps(store.load_transcript(later.session_id))
    assert "OLD-ANSWER" in text and "FIRST-SUMMON-RESULT" not in text


def test_the_scope_key_is_a_safe_session_key_part_and_round_trips() -> None:
    session = pytest.importorskip("gateway.session")
    platforms = pytest.importorskip("gateway.config")
    source = session.SessionSource(platform=platforms.Platform("chatlytics"), chat_id=PEER, chat_type="dm", user_id=OWNER)
    event = types.SimpleNamespace(source=source)
    scope = open_for_event(ENVELOPES["from_me_dm"], event)
    assert scope is not None
    key = session.build_session_key(source)
    assert key.endswith(f":{PEER}:{scope.key}")
    assert session.SessionSource.from_dict(source.to_dict()).thread_id == scope.key


async def test_native_admission_accepts_a_summon_with_its_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    envelope = pytest.importorskip("gateway.session_envelope")
    adapter = _adapter()
    event = await _dispatch(adapter, dict(ENVELOPES["from_me_dm"]))
    assert event.source.thread_id
    runner = types.SimpleNamespace(
        _adapter_for_source=lambda _source: adapter,
        _adapter_flag=lambda *_a, **_k: False,
        config=types.SimpleNamespace(multiplex_profiles=False),
        _is_user_authorized=lambda _source: True,
        session_store=types.SimpleNamespace(_generate_session_key=lambda source: adapter._control_session_key(source)),
    )
    # fresh_roles: the gateway's reauthorize_roles has checked the owner pin by this point.
    assert envelope._event_clause(runner, event, None, True) is None
    encoded = event.source.to_dict()
    assert encoded["thread_id"] == event.source.thread_id
