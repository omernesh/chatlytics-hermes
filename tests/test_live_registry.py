"""v4.5.2 — live-adapter registry fallback tests.

Root cause (BotDaddy "adapter is not connected" on longpoll-only gateways):
hermes-agent's real ``hermes_cli.plugins.PluginContext`` (0.16.0) exposes
NEITHER ``get_platform`` NOR a ``platforms`` mapping, so the register-time
ctx captured by ``_make_tool_handler`` could never resolve the live adapter
and EVERY chatlytics_* tool failed with "adapter is not connected" even
while the longpoll loop was alive.

Fix under test: ``connect()`` registers the adapter in the module-level
``_LIVE_ADAPTERS`` registry; ``_lookup_adapter`` falls back to it after the
ctx probes; ``disconnect()`` unregisters (identity-guarded).
"""

from __future__ import annotations

import asyncio
import json
import logging
from types import SimpleNamespace
from typing import Any, Dict, Optional, Tuple

import httpx
import pytest
import respx
from gateway import session_context as sc

from chatlytics_hermes import read_scope
from chatlytics_hermes.adapter import (
    ChatlyticsAdapter,
    _LIVE_ADAPTERS,
    _LIVE_BY_PROFILE,
    _make_tool_handler,
    _register_live_adapter,
    _SUMMON_ADAPTERS,
    _unregister_live_adapter,
)
from chatlytics_hermes.tools import TOOLS, chatlytics_health, chatlytics_login, chatlytics_send
from tests._fixtures import FakePlatformConfig
from tests.test_summon_outbound import _adapter, _deliver, _Hub
from tests.test_trigger_context import ENVELOPES, OWNER_SESSION, PEER

pytestmark = pytest.mark.asyncio

BASE_URL = "https://gateway.test.chatlytics.ai"
BOT_TOKEN = "sk_bot_" + "A" * 43


class _BareCtx:
    """Faithful stand-in for hermes_cli.plugins.PluginContext (0.16.0):
    NO ``get_platform`` method, NO ``platforms`` attribute."""


def _make_adapter(inbound_mode: str = "longpoll") -> ChatlyticsAdapter:
    extra: Dict[str, Any] = {
        "base_url": BASE_URL,
        "bot_token": BOT_TOKEN,
        "inbound_mode": inbound_mode,
    }
    return ChatlyticsAdapter(FakePlatformConfig(extra=extra))


def _mock_gateway(router: respx.Router) -> None:
    router.get("/health").mock(
        return_value=httpx.Response(
            200, json={"webhook_registered": True, "sessions": ["3cf11776_logan"]}
        )
    )
    router.get("/api/v1/bot/me").mock(
        return_value=httpx.Response(200, json={"bot": {"name": "TestBot"}})
    )
    # Longpoll GET: empty batches keep the loop harmlessly idle for the
    # short life of the test.
    router.get("/api/v1/bot/updates").mock(
        return_value=httpx.Response(200, json={"envelopes": [], "cursor": "c0"})
    )
    router.post("/api/v1/bot/updates/ack").mock(
        return_value=httpx.Response(200, json={"acked": 0, "cursor": "c0"})
    )


# --- E2E: longpoll-only connect() makes tools resolvable on a bare ctx -----


async def test_health_and_login_work_on_longpoll_only_gateway() -> None:
    """The BotDaddy repro: bare PluginContext + longpoll inbound. After
    connect(), chatlytics_health / chatlytics_login resolve the live
    adapter via the registry fallback and return real /health truth."""
    adapter = _make_adapter("longpoll")
    health_bound = _make_tool_handler(_BareCtx(), "chatlytics_health", chatlytics_health)
    login_bound = _make_tool_handler(_BareCtx(), "chatlytics_login", chatlytics_login)

    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        _mock_gateway(router)

        assert await adapter.connect() is True
        assert _LIVE_ADAPTERS.get("chatlytics") is adapter

        health_payload = json.loads(await health_bound())
        assert health_payload["success"] is True
        assert health_payload["webhook_registered"] is True

        login_payload = json.loads(await login_bound())
        assert login_payload["success"] is True

        await adapter.disconnect()

    assert "chatlytics" not in _LIVE_ADAPTERS

    # After disconnect the tools report not-connected again (no stale entry).
    stale = json.loads(await health_bound())
    assert stale["success"] is False
    assert "not connected" in stale["error"]


# --- v4.5.7 regression guard: connect() tolerates the framework's ----------
# --- is_reconnect keyword (and any future kwarg) ----------------------------


async def test_connect_accepts_is_reconnect_keyword() -> None:
    """The Hermes gateway framework calls ``adapter.connect(*,
    is_reconnect=...)`` on every (re)connect. Before v4.5.7 the signature
    was ``connect(self)`` with no such parameter: cold boot worked (no
    kwarg passed), but the FIRST reconnect raised ``TypeError:
    ChatlyticsAdapter.connect() got an unexpected keyword argument
    'is_reconnect'`` -- which the framework could not recover from, so the
    inbound longpoll consumer stayed dead until a full gateway restart
    (observed in production: 55h dead consumer -> bot_longpoll_queue_undrained
    alert). This locks in the fix so a future edit to connect()'s signature
    cannot silently reintroduce the crash-loop.
    """
    adapter = _make_adapter("longpoll")
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        _mock_gateway(router)
        # The literal call shape the gateway framework issues on reconnect.
        assert await adapter.connect(is_reconnect=True) is True
        await adapter.disconnect()

    # Defensive: connect() must also swallow any kwarg NOT literally named
    # is_reconnect, so a future framework addition doesn't reopen this bug.
    import inspect

    sig = inspect.signature(ChatlyticsAdapter.connect)
    assert any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
    ), (
        "connect() must accept **kwargs so future framework-injected "
        "keywords cannot reintroduce the v4.5.6 reconnect TypeError"
    )


async def test_bare_ctx_without_live_adapter_still_reports_not_connected() -> None:
    """No connect() ever happened: the registry fallback returns None and
    the canonical not-connected failure shape is preserved."""
    bound = _make_tool_handler(_BareCtx(), "chatlytics_health", chatlytics_health)
    payload = json.loads(await bound())
    assert payload["success"] is False
    assert "adapter is not connected" in payload["error"]


# --- Registry unit semantics ------------------------------------------------


async def test_disconnect_is_identity_guarded() -> None:
    """A stale adapter's disconnect() must not clobber a newer live one
    (overlapping reconnect)."""
    old = _make_adapter()
    new = _make_adapter()
    _register_live_adapter(old)
    _register_live_adapter(new)  # reconnect superseded `old`

    await old.disconnect()  # no client/runner/poll task — pure teardown
    assert _LIVE_ADAPTERS.get("chatlytics") is new

    _unregister_live_adapter(new)
    assert "chatlytics" not in _LIVE_ADAPTERS


async def test_ctx_accessors_still_win_over_registry() -> None:
    """Regression: a harness ctx that DOES expose get_platform keeps its
    adapter — the registry is a fallback, not an override."""

    class _Entry:
        def __init__(self, adapter: Any) -> None:
            self.adapter = adapter

    ctx_adapter = _make_adapter()
    registry_adapter = _make_adapter()
    _register_live_adapter(registry_adapter)

    sentinel_client = object()
    ctx_adapter._client = sentinel_client  # type: ignore[assignment]

    seen: Dict[str, Any] = {}

    async def _probe(client, **kwargs):
        seen["client"] = client
        return {"success": True}

    class _Ctx:
        def get_platform(self, name: str):
            return _Entry(ctx_adapter) if name == "chatlytics" else None

    bound = _make_tool_handler(_Ctx(), "chatlytics_health", _probe)
    payload = json.loads(await bound())
    assert payload["success"] is True
    assert seen["client"] is sentinel_client


async def test_degraded_no_credential_connect_registers() -> None:
    """Degraded (no token) connect() still registers the adapter so the
    exempt status tools resolve it and report the degraded truth."""
    adapter = ChatlyticsAdapter(
        FakePlatformConfig(extra={"base_url": BASE_URL, "inbound_mode": "longpoll"})
    )
    assert await adapter.connect() is True
    assert _LIVE_ADAPTERS.get("chatlytics") is adapter
    # client is None in the degraded state — health reports not-connected
    # (pre-existing v4.1.5 contract, unchanged by the registry).
    bound = _make_tool_handler(_BareCtx(), "chatlytics_health", chatlytics_health)
    payload = json.loads(await bound())
    assert payload["success"] is False
    await adapter.disconnect()
    assert "chatlytics" not in _LIVE_ADAPTERS


# --- chatlytics#956: a tool runs on the adapter of its own turn ---------------------------------
#
# Each profile of a gateway with multiplex_profiles has its own adapter, and the last to connect held
# the slot. A summon on bot A sent with bot B's bearer, and the hub re-pinned it to B's number.

ALPHA_SESSION = "11111111_alpha"
BRAVO_SESSION = "22222222_bravo"


@pytest.fixture
def _clean_scopes() -> Any:
    yield
    read_scope._bound.clear()
    read_scope._scopes.clear()


def _profile_bot(profile: Optional[str], session: str, *, live: bool = True) -> Tuple[Any, Any, Any]:
    adapter, hub, conn = _adapter(_Hub(own=session), own=session)
    if profile:
        adapter.set_owner_profile(profile)
    if live:
        _register_live_adapter(adapter)
    return adapter, hub, conn


def _send_tool(ctx: Any = None) -> Any:
    return _make_tool_handler(ctx or _BareCtx(), "chatlytics_send", chatlytics_send)


async def _in_session(call: Any, **bound: Any) -> Any:
    """``call`` in a task of its own, with exactly these session variables, as the gateway binds them."""

    async def run() -> Any:
        sc.reset_session_vars()
        if bound:
            sc.set_session_vars(**bound)
        return await call()

    return await asyncio.create_task(run())


async def _on_route(adapter: Any, hub: Any, conn: Any) -> str:
    summon = await _deliver(adapter, ENVELOPES["from_me_dm"], conn)
    hub.routes[PEER] = OWNER_SESSION
    return summon.source.thread_id


@pytest.mark.parametrize("profile", ["alpha", ""])
async def test_a_summons_tool_uses_its_own_adapter_when_another_connected_last(
    profile: str, _clean_scopes: Any
) -> None:
    alpha, alpha_hub, alpha_conn = _profile_bot("alpha", ALPHA_SESSION)
    key = await _on_route(alpha, alpha_hub, alpha_conn)
    _, bravo_hub, _ = _profile_bot("bravo", BRAVO_SESSION)
    tool = _send_tool()
    out = await _in_session(
        lambda: tool({"chatId": PEER, "text": "x"}), platform="chatlytics", thread_id=key, profile=profile
    )
    assert json.loads(out)["success"] is True
    assert alpha_hub.left == [(OWNER_SESSION, PEER, "x")]
    assert alpha_hub.bodies[0][1]["session"] == OWNER_SESSION
    assert bravo_hub.bodies == []


async def test_a_summons_tool_found_by_its_task_id_uses_its_own_adapter(_clean_scopes: Any) -> None:
    alpha, alpha_hub, alpha_conn = _profile_bot("alpha", ALPHA_SESSION)
    key = await _on_route(alpha, alpha_hub, alpha_conn)
    _, bravo_hub, _ = _profile_bot("bravo", BRAVO_SESSION)
    read_scope.note_request(True, f"x:{key}", "s1", "task-1")
    tool = _send_tool()
    await _in_session(lambda: tool({"chatId": PEER, "text": "x"}, task_id="task-1"))
    assert alpha_hub.left == [(OWNER_SESSION, PEER, "x")] and bravo_hub.bodies == []


@pytest.mark.parametrize(
    "platform,profile,expected",
    [
        ("chatlytics", "alpha", "alpha"),
        ("chatlytics", "bravo", "bravo"),
        ("chatlytics", "", "primary"),
        ("chatlytics", "default", "primary"),
        # A turn of that profile on another platform, or on a surface with no platform.
        ("telegram", "alpha", "alpha"),
        ("telegram", "", "primary"),
        ("", "alpha", "alpha"),
    ],
)
async def test_an_ordinary_turns_tool_uses_its_profiles_adapter(platform: str, profile: str, expected: str) -> None:
    primary, primary_hub, _ = _profile_bot(None, "33333333_primary")
    alpha, alpha_hub, _ = _profile_bot("alpha", ALPHA_SESSION)
    bravo, bravo_hub, _ = _profile_bot("bravo", BRAVO_SESSION)
    hubs = {"primary": primary_hub, "alpha": alpha_hub, "bravo": bravo_hub}
    sessions = {"primary": "33333333_primary", "alpha": ALPHA_SESSION, "bravo": BRAVO_SESSION}
    tool = _send_tool()
    await _in_session(lambda: tool({"chatId": PEER, "text": "x"}), platform=platform, profile=profile)
    for name, hub in hubs.items():
        assert len(hub.bodies) == (1 if name == expected else 0), name
    assert hubs[expected].left == [(sessions[expected], PEER, "x")]


# A turn with no adapter of its own, on a gateway with several, doesn't take the one that connected last.
NO_PROFILE = [
    {},  # no session context at all: it was lost
    {"platform": "", "profile": ""},  # a cron job binds neither, and may be any profile's
]


def _misses(caplog: Any) -> list:
    return [r.getMessage() for r in caplog.records if "has no profile on its turn" in r.getMessage()]


@pytest.mark.parametrize(
    "bound",
    [
        {"platform": "chatlytics", "profile": "no-such-profile"},
        {"platform": "telegram", "profile": "no-such-profile"},
        {"platform": "", "profile": "no-such-profile"},
        # The primary's turn, with the primary not connected.
        {"platform": "chatlytics", "profile": ""},
        {"platform": "telegram", "profile": "default"},
    ],
)
async def test_a_named_profile_with_no_adapter_is_not_connected_among_several(
    bound: Dict[str, str], caplog: Any
) -> None:
    _, alpha_hub, _ = _profile_bot("alpha", ALPHA_SESSION)
    _, bravo_hub, _ = _profile_bot("bravo", BRAVO_SESSION)
    tool = _send_tool()
    caplog.set_level(logging.WARNING)
    out = await _in_session(lambda: tool({"chatId": PEER, "text": "x"}), **bound)
    assert "not connected" in json.loads(out)["error"]
    assert alpha_hub.bodies == [] and bravo_hub.bodies == []
    assert _misses(caplog) == []  # it names a profile: nothing about it is lost


async def test_a_named_profile_with_no_adapter_doesnt_take_the_default_either() -> None:
    _, primary_hub, _ = _profile_bot(None, "33333333_primary")
    _, bravo_hub, _ = _profile_bot("bravo", BRAVO_SESSION)
    tool = _send_tool()
    out = await _in_session(
        lambda: tool({"chatId": PEER, "text": "x"}), platform="chatlytics", profile="no-such-profile"
    )
    assert "not connected" in json.loads(out)["error"]
    assert primary_hub.bodies == [] and bravo_hub.bodies == []


@pytest.mark.parametrize("own", [None, "alpha"])
@pytest.mark.parametrize(
    "bound",
    [
        *NO_PROFILE,
        {"platform": "chatlytics", "profile": "no-such-profile"},
        {"platform": "telegram", "profile": ""},
    ],
)
async def test_a_lone_adapter_serves_any_turn_and_says_nothing(
    own: Optional[str], bound: Dict[str, str], caplog: Any
) -> None:
    _, hub, _ = _profile_bot(own, ALPHA_SESSION)
    tool = _send_tool()
    caplog.set_level(logging.WARNING)
    await _in_session(lambda: tool({"chatId": PEER, "text": "x"}), **bound)
    assert hub.left == [(ALPHA_SESSION, PEER, "x")]
    assert _misses(caplog) == []


@pytest.mark.parametrize("bound", NO_PROFILE)
async def test_no_profile_uses_the_default_adapter_when_another_connected_last(
    bound: Dict[str, str], caplog: Any
) -> None:
    _, primary_hub, _ = _profile_bot(None, "33333333_primary")
    _, alpha_hub, _ = _profile_bot("alpha", ALPHA_SESSION)
    _, last_hub, _ = _profile_bot("bravo", BRAVO_SESSION)
    tool = _send_tool()
    caplog.set_level(logging.WARNING)
    await _in_session(lambda: tool({"chatId": PEER, "text": "a-private-argument"}), **bound)
    assert primary_hub.left == [("33333333_primary", PEER, "a-private-argument")]
    assert alpha_hub.bodies == [] and last_hub.bodies == []
    misses = _misses(caplog)
    assert len(misses) == 1 and "chatlytics_send" in misses[0]
    assert "a-private-argument" not in caplog.text and PEER not in caplog.text


@pytest.mark.parametrize("bound", NO_PROFILE)
async def test_no_profile_and_no_default_is_not_connected_among_several(bound: Dict[str, str], caplog: Any) -> None:
    _, alpha_hub, _ = _profile_bot("alpha", ALPHA_SESSION)
    _, bravo_hub, _ = _profile_bot("bravo", BRAVO_SESSION)
    tool = _send_tool()
    caplog.set_level(logging.WARNING)
    out = await _in_session(lambda: tool({"chatId": PEER, "text": "x"}), **bound)
    assert "not connected" in json.loads(out)["error"]
    assert alpha_hub.bodies == [] and bravo_hub.bodies == []
    misses = _misses(caplog)
    assert len(misses) == 1 and "chatlytics_send" in misses[0] and "not connected" in misses[0]


async def test_the_lookup_through_a_ctx_entry_borrows_nothing_either() -> None:
    _, alpha_hub, _ = _profile_bot("alpha", ALPHA_SESSION)
    _, bravo_hub, _ = _profile_bot("bravo", BRAVO_SESSION)
    tool = _send_tool(SimpleNamespace(platforms={"chatlytics": SimpleNamespace(adapter=None)}))
    out = await _in_session(lambda: tool({"chatId": PEER, "text": "x"}), platform="chatlytics", profile="nope")
    assert "not connected" in json.loads(out)["error"]
    assert alpha_hub.bodies == [] and bravo_hub.bodies == []


async def test_a_ctx_entry_without_an_adapter_still_picks_the_turns_profile() -> None:
    _, alpha_hub, _ = _profile_bot("alpha", ALPHA_SESSION)
    _, bravo_hub, _ = _profile_bot("bravo", BRAVO_SESSION)
    ctx = SimpleNamespace(platforms={"chatlytics": SimpleNamespace(adapter=None)})
    tool = _send_tool(ctx)
    await _in_session(lambda: tool({"chatId": PEER, "text": "x"}), platform="chatlytics", profile="alpha")
    assert len(alpha_hub.bodies) == 1 and bravo_hub.bodies == []


async def test_a_summon_with_no_owning_adapter_is_refused_and_nothing_is_sent(caplog: Any) -> None:
    _, alpha_hub, _ = _profile_bot("alpha", ALPHA_SESSION)
    _, bravo_hub, _ = _profile_bot("bravo", BRAVO_SESSION)
    tool = _send_tool()
    caplog.set_level(logging.ERROR)
    out = await _in_session(
        lambda: tool({"chatId": PEER, "text": "x"}),
        platform="chatlytics", profile="alpha", thread_id=read_scope.LOST_KEY,
    )
    assert json.loads(out) == {"success": False, "error": read_scope.SummonScopeLost.error}
    assert alpha_hub.bodies == [] and bravo_hub.bodies == []
    assert "summon send refused, summon_scope_lost" in caplog.text


async def test_every_tool_refuses_a_summon_with_no_owning_adapter() -> None:
    _, hub, _ = _profile_bot("alpha", ALPHA_SESSION)
    assert len(TOOLS) == 24
    for name, _schema, handler in TOOLS:
        tool = _make_tool_handler(_BareCtx(), name, handler)
        out = await _in_session(lambda: tool({}), platform="chatlytics", thread_id=read_scope.LOST_KEY)
        assert json.loads(out) == {"success": False, "error": read_scope.SummonScopeLost.error}, name
    assert hub.bodies == []


async def test_a_summon_whose_adapter_disconnected_is_refused_and_doesnt_use_the_slot(_clean_scopes: Any) -> None:
    alpha, alpha_hub, alpha_conn = _profile_bot("alpha", ALPHA_SESSION)
    key = await _on_route(alpha, alpha_hub, alpha_conn)
    _, bravo_hub, _ = _profile_bot("bravo", BRAVO_SESSION)
    _unregister_live_adapter(alpha)
    tool = _send_tool()
    out = await _in_session(
        lambda: tool({"chatId": PEER, "text": "x"}), platform="chatlytics", profile="alpha", thread_id=key
    )
    assert json.loads(out)["error"] == read_scope.SummonScopeLost.error
    assert alpha_hub.bodies == [] and bravo_hub.bodies == []


async def test_a_media_tool_gets_the_summons_adapter_and_its_client(_clean_scopes: Any) -> None:
    alpha, alpha_hub, alpha_conn = _profile_bot("alpha", ALPHA_SESSION)
    key = await _on_route(alpha, alpha_hub, alpha_conn)
    _profile_bot("bravo", BRAVO_SESSION)
    seen: Dict[str, Any] = {}

    async def _media(client: Any, adapter: Any = None) -> Dict[str, Any]:
        seen.update(client=client, adapter=adapter)
        return {"success": True}

    tool = _make_tool_handler(_BareCtx(), "chatlytics_send_image", _media)
    await _in_session(lambda: tool({}), platform="chatlytics", thread_id=key)
    assert seen == {"client": alpha_hub, "adapter": alpha}


async def test_the_summon_owners_are_bounded_like_the_scopes(
    monkeypatch: pytest.MonkeyPatch, _clean_scopes: Any
) -> None:
    monkeypatch.setattr(read_scope, "_SCOPES_MAX", 3)
    alpha, _, conn = _profile_bot("alpha", ALPHA_SESSION)
    for _i in range(5):
        await _deliver(alpha, ENVELOPES["from_me_dm"], conn)
    assert len(_SUMMON_ADAPTERS) == 3
    assert list(_SUMMON_ADAPTERS) == list(read_scope._scopes)


async def test_a_summon_stays_on_its_adapters_turns() -> None:
    alpha, _, conn = _profile_bot("alpha", ALPHA_SESSION)
    bravo, _, _ = _profile_bot("bravo", BRAVO_SESSION)
    summon = await _deliver(alpha, ENVELOPES["from_me_dm"], conn)
    assert summon.source.thread_id in alpha._summon_turns and bravo._summon_turns == {}
    read_scope._scopes.clear()


async def test_a_reconnect_moves_the_profiles_entry_to_the_new_adapter() -> None:
    old, old_hub, _ = _profile_bot("alpha", ALPHA_SESSION)
    _, bravo_hub, _ = _profile_bot("bravo", BRAVO_SESSION)
    new, new_hub, _ = _profile_bot("alpha", ALPHA_SESSION)
    _register_live_adapter(_profile_bot("bravo", BRAVO_SESSION, live=False)[0])
    _unregister_live_adapter(old)  # the old one's late disconnect leaves the new one's entry
    tool = _send_tool()
    await _in_session(lambda: tool({"chatId": PEER, "text": "x"}), platform="chatlytics", profile="alpha")
    assert len(new_hub.bodies) == 1 and old_hub.bodies == [] and bravo_hub.bodies == []


async def test_an_adapter_that_changes_profile_leaves_its_old_entry() -> None:
    alpha, alpha_hub, _ = _profile_bot("alpha", ALPHA_SESSION)
    _, bravo_hub, _ = _profile_bot("bravo", BRAVO_SESSION)
    alpha.set_owner_profile("charlie")
    _register_live_adapter(alpha)
    _register_live_adapter(_profile_bot("bravo", BRAVO_SESSION, live=False)[0])
    tool = _send_tool()
    await _in_session(lambda: tool({"chatId": PEER, "text": "x"}), platform="chatlytics", profile="alpha")
    assert alpha_hub.bodies == []  # alpha's entry is gone, so this turn has no adapter
    await _in_session(lambda: tool({"chatId": PEER, "text": "x"}), platform="chatlytics", profile="charlie")
    assert len(alpha_hub.bodies) == 1 and bravo_hub.bodies == []


async def test_a_disconnected_profile_is_never_chosen() -> None:
    alpha, alpha_hub, _ = _profile_bot("alpha", ALPHA_SESSION)
    bravo, bravo_hub, _ = _profile_bot("bravo", BRAVO_SESSION)
    tool = _send_tool()
    # The slot's holder goes first: alpha's turn keeps alpha, a turn of nothing known has no adapter.
    _unregister_live_adapter(bravo)
    await _in_session(lambda: tool({"chatId": PEER, "text": "x"}), platform="chatlytics", profile="alpha")
    assert len(alpha_hub.bodies) == 1
    out = await _in_session(lambda: tool({"chatId": PEER, "text": "x"}), platform="chatlytics", profile="bravo")
    assert "not connected" in json.loads(out)["error"] and bravo_hub.bodies == []
    out = await _in_session(lambda: tool({"chatId": PEER, "text": "x"}))
    assert "not connected" in json.loads(out)["error"]
    _unregister_live_adapter(alpha)
    out = await _in_session(lambda: tool({"chatId": PEER, "text": "x"}), platform="chatlytics", profile="alpha")
    assert "not connected" in json.loads(out)["error"] and len(alpha_hub.bodies) == 1


async def test_a_superseded_adapters_disconnect_doesnt_remove_the_newer_one_of_its_profile() -> None:
    old, _, _ = _profile_bot("alpha", ALPHA_SESSION)
    new, new_hub, _ = _profile_bot("alpha", ALPHA_SESSION)
    _unregister_live_adapter(old)
    assert _LIVE_BY_PROFILE["alpha"] is new
    tool = _send_tool()
    await _in_session(lambda: tool({"chatId": PEER, "text": "x"}), platform="chatlytics", profile="alpha")
    assert len(new_hub.bodies) == 1


@pytest.mark.parametrize(
    "bound",
    [
        {},
        {"platform": "chatlytics"},
        {"platform": "telegram", "profile": "routed-elsewhere"},
    ],
)
async def test_one_adapter_behaves_as_it_did(bound: Dict[str, str], _clean_scopes: Any) -> None:
    adapter, hub, conn = _profile_bot(None, ALPHA_SESSION)
    tool = _send_tool()
    await _in_session(lambda: tool({"chatId": PEER, "text": "x"}), **bound)
    assert hub.left == [(ALPHA_SESSION, PEER, "x")]
    key = await _on_route(adapter, hub, conn)
    await _in_session(lambda: tool({"chatId": PEER, "text": "y"}), **{**bound, "thread_id": key})
    assert hub.left[-1] == (OWNER_SESSION, PEER, "y")
    _unregister_live_adapter(adapter)
    out = await _in_session(lambda: tool({"chatId": PEER, "text": "z"}), **bound)
    assert "not connected" in json.loads(out)["error"]


async def test_two_connected_gateways_send_on_the_bearer_of_the_turns_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Through real connect() and disconnect(), read at the request's Authorization header."""
    token_a, token_b = "sk_bot_" + "A" * 43, "sk_bot_" + "B" * 43

    async def _idle(_self: Any, _conn: Any) -> None:
        # This test is about the bearer. Two real poll loops against respx hung it.
        await asyncio.Event().wait()

    monkeypatch.setattr(ChatlyticsAdapter, "_inbound_loop", _idle)

    def _make(token: str, profile: str) -> ChatlyticsAdapter:
        adapter = ChatlyticsAdapter(
            FakePlatformConfig(extra={"base_url": BASE_URL, "bot_token": token, "inbound_mode": "longpoll"})
        )
        adapter.set_owner_profile(profile)
        return adapter

    alpha, bravo = _make(token_a, "alpha"), _make(token_b, "bravo")
    tool = _send_tool()
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        _mock_gateway(router)
        send = router.post("/api/v1/send").mock(return_value=httpx.Response(200, json={"success": True}))
        assert await alpha.connect() is True
        assert await bravo.connect() is True

        await _in_session(lambda: tool({"chatId": PEER, "text": "x"}), platform="chatlytics", profile="alpha")
        assert send.calls[-1].request.headers["authorization"] == f"Bearer {token_a}"
        await _in_session(lambda: tool({"chatId": PEER, "text": "x"}), platform="chatlytics", profile="bravo")
        assert send.calls[-1].request.headers["authorization"] == f"Bearer {token_b}"

        # A turn naming nothing, and neither is the default profile's: bravo's bearer isn't used for it.
        calls = len(send.calls)
        out = await _in_session(lambda: tool({"chatId": PEER, "text": "x"}), platform="", profile="")
        assert "not connected" in json.loads(out)["error"] and len(send.calls) == calls

        # bravo goes down: alpha keeps its own bearer, and nothing closed is chosen.
        await bravo.disconnect()
        await _in_session(lambda: tool({"chatId": PEER, "text": "x"}), platform="chatlytics", profile="alpha")
        assert send.calls[-1].request.headers["authorization"] == f"Bearer {token_a}"
        calls = len(send.calls)
        out = await _in_session(lambda: tool({"chatId": PEER, "text": "x"}), platform="chatlytics", profile="bravo")
        assert "not connected" in json.loads(out)["error"] and len(send.calls) == calls

        await alpha.disconnect()
    assert _LIVE_BY_PROFILE == {}


async def test_a_cron_job_sends_on_the_default_bearer_when_another_connected_last(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cron job binds platform and profile to "". Read at the request's Authorization header."""
    token_default, token_b = "sk_bot_" + "A" * 43, "sk_bot_" + "B" * 43

    async def _idle(_self: Any, _conn: Any) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(ChatlyticsAdapter, "_inbound_loop", _idle)

    def _make(token: str, profile: Optional[str]) -> ChatlyticsAdapter:
        adapter = ChatlyticsAdapter(
            FakePlatformConfig(extra={"base_url": BASE_URL, "bot_token": token, "inbound_mode": "longpoll"})
        )
        adapter.set_owner_profile(profile)
        return adapter

    primary, bravo = _make(token_default, None), _make(token_b, "bravo")
    tool = _send_tool()
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        _mock_gateway(router)
        send = router.post("/api/v1/send").mock(return_value=httpx.Response(200, json={"success": True}))
        assert await primary.connect() is True
        assert await bravo.connect() is True
        await _in_session(lambda: tool({"chatId": PEER, "text": "x"}), platform="", profile="")
        assert send.calls[-1].request.headers["authorization"] == f"Bearer {token_default}"
        await bravo.disconnect()
        await primary.disconnect()
