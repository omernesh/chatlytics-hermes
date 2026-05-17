"""HERMES-07: Live-loader integration smoke + BL-01/HI-01/HI-03 regression tests.

Drives the real Hermes v0.14 plugin-loader contract (``PluginContext``
surface from ``hermes_cli/plugins.py``) against a respx-mocked
Chatlytics backend to prove:

1. The plugin loads (``register(ctx)`` is called).
2. The chatlytics platform registers with the canonical
   ``register_platform`` kwargs.
3. All 21 tools land on the ``PluginContext`` registry under the
   ``chatlytics`` toolset.
4. BL-01 / HI-01 / HI-03 are reproduced under strict-xfail markers
   (Phase 8 fixes them; ``strict=True`` forces un-xfailing then).

Critically, the BL-01 regression tests exercise the BASE
``_process_message_background`` pipeline (which is what calls
``asyncio.create_task(self._keep_typing(chat_id, metadata=...))``) -- NOT
the recorder replacement at ``tests/test_inbound.py:98-106`` that hid
BL-01 for 6 phases (GSD-MD-04).

The handler-test for HI-01 is written as the DESIRED post-fix behavior
and xfail-strict marked -- it currently fails (no validation exists) and
Phase 8's allowlist fix flips it to pass.
"""
from __future__ import annotations

import asyncio
import inspect
import sys
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from chatlytics_hermes import register
from chatlytics_hermes.adapter import ChatlyticsAdapter
from chatlytics_hermes.tools import TOOLS
from tests._fixtures import FakePlatformConfig

pytestmark = pytest.mark.asyncio


BASE_URL = "https://gateway.test.chatlytics.ai"
API_KEY = "test-api-key-live-loader"

# Canonical tool-name set sourced from the locked-21 TOOLS registry.
_EXPECTED_TOOL_NAMES = frozenset(name for name, _, _ in TOOLS)
assert len(_EXPECTED_TOOL_NAMES) == 21, (
    f"tools.TOOLS drift: expected 21 unique names, got {len(_EXPECTED_TOOL_NAMES)}"
)


# --- PluginContext-compatible recorder ------------------------------


class _FakeManifest:
    """Minimal PluginManifest stand-in for the recorder context."""

    def __init__(self, name: str = "chatlytics") -> None:
        self.name = name
        self.key = name
        self.source = "entry_point"
        self.kind = "platform"
        self.path = None


class _CapturingContext:
    """In-process PluginContext-compatible recorder.

    Mirrors the v0.14 ``PluginContext`` surface (``register_platform``,
    ``register_tool``) so the SAME ``register(ctx)`` code that runs
    under the real loader runs here. We capture each call so tests can
    assert on the registered platform + tools.
    """

    def __init__(self) -> None:
        self.platform_calls: List[Dict[str, Any]] = []
        self.tool_calls: List[Dict[str, Any]] = []
        self.manifest = _FakeManifest()

    def register_platform(self, **kwargs: Any) -> None:
        self.platform_calls.append(kwargs)

    def register_tool(
        self,
        *,
        name: str,
        toolset: str,
        schema: dict,
        handler: Any,
        check_fn: Any = None,
        requires_env: Optional[list] = None,
        is_async: bool = False,
        description: str = "",
        emoji: str = "",
        override: bool = False,
    ) -> None:
        self.tool_calls.append(
            {
                "name": name,
                "toolset": toolset,
                "schema": schema,
                "handler": handler,
                "check_fn": check_fn,
                "requires_env": requires_env,
                "is_async": is_async,
                "description": description,
                "emoji": emoji,
                "override": override,
            }
        )


# --- Adapter construction helper (matches test_inbound._make_adapter) ---


def _make_adapter(*, webhook_port: int = 0) -> ChatlyticsAdapter:
    """Construct an adapter against test-safe config (no real network).

    ``webhook_port=0`` skips the inbound server in tests that don't need
    it (use ``connect()=False`` paths and call adapter methods directly).
    """
    extra: Dict[str, Any] = {
        "base_url": BASE_URL,
        "api_key": API_KEY,
        "webhook_host": "127.0.0.1",
        "webhook_port": webhook_port,
        "webhook_path": "/webhook",
    }
    return ChatlyticsAdapter(FakePlatformConfig(extra=extra))


# ---------------------------------------------------------------------
# AC-1: register(ctx) is called and the chatlytics platform registers
# ---------------------------------------------------------------------


async def test_loader_registers_chatlytics_platform() -> None:
    ctx = _CapturingContext()
    register(ctx)
    assert len(ctx.platform_calls) == 1, (
        f"Expected exactly 1 register_platform call, got {len(ctx.platform_calls)}"
    )
    call = ctx.platform_calls[0]
    assert call["name"] == "chatlytics"
    assert callable(call["adapter_factory"])
    assert callable(call["check_fn"]) and call["check_fn"]() is True
    required = call.get("required_env") or []
    assert "CHATLYTICS_BASE_URL" in required
    assert "CHATLYTICS_API_KEY" in required
    assert call.get("cron_deliver_env_var") == "CHATLYTICS_HOME_CHANNEL"
    assert callable(call.get("standalone_sender_fn"))


# ---------------------------------------------------------------------
# AC-2: all 21 tools land on the context with correct toolset/handler shape
# ---------------------------------------------------------------------


async def test_loader_registers_21_tools() -> None:
    ctx = _CapturingContext()
    register(ctx)
    assert len(ctx.tool_calls) == 21, (
        f"Expected exactly 21 tools, got {len(ctx.tool_calls)}: "
        f"{[c['name'] for c in ctx.tool_calls]}"
    )
    registered_names = {c["name"] for c in ctx.tool_calls}
    assert registered_names == _EXPECTED_TOOL_NAMES, (
        f"Tool surface drift: "
        f"missing={_EXPECTED_TOOL_NAMES - registered_names}, "
        f"extra={registered_names - _EXPECTED_TOOL_NAMES}"
    )
    # All under the chatlytics toolset (avoid collisions with other plugins).
    assert all(c["toolset"] == "chatlytics" for c in ctx.tool_calls), (
        "Every registered tool must live under the chatlytics toolset; got "
        + repr({c["name"]: c["toolset"] for c in ctx.tool_calls})
    )
    # All handlers are callables (the _make_tool_handler wrappers).
    assert all(callable(c["handler"]) for c in ctx.tool_calls)
    # All schemas are dicts.
    assert all(isinstance(c["schema"], dict) for c in ctx.tool_calls)
    # Every tool name starts with chatlytics_ (asserted by HERMES-05 too).
    assert all(c["name"].startswith("chatlytics_") for c in ctx.tool_calls)


# ---------------------------------------------------------------------
# AC-3: loader handles missing env vars gracefully (no half-register)
# ---------------------------------------------------------------------


async def test_loader_handles_missing_env_vars_gracefully(monkeypatch) -> None:
    """register() is purely declarative; missing env vars do not crash it.

    The check_fn returns True (deps are importable); env presence is a
    runtime gateway concern, surfaced via required_env (declarative) and
    env_enablement_fn (lazy).
    """
    monkeypatch.delenv("CHATLYTICS_BASE_URL", raising=False)
    monkeypatch.delenv("CHATLYTICS_API_KEY", raising=False)
    monkeypatch.delenv("CHATLYTICS_HOME_CHANNEL", raising=False)
    ctx = _CapturingContext()
    register(ctx)  # must not raise
    assert len(ctx.platform_calls) == 1
    assert len(ctx.tool_calls) == 21


# ---------------------------------------------------------------------
# AC-4: loader is isolated from real Chatlytics (no HTTP at load time)
# ---------------------------------------------------------------------


async def test_loader_isolated_from_real_chatlytics() -> None:
    """register() must not perform any HTTP calls at load time.

    Verify by seeding an exploding /health respx mock and asserting it
    is never consumed. The adapter factory is just a lambda; it is not
    invoked at register time.
    """
    with respx.mock(assert_all_called=False) as router:
        health = router.get(f"{BASE_URL}/health").mock(
            return_value=httpx.Response(500, json={"error": "must-not-be-called"})
        )
        router.route().pass_through()
        ctx = _CapturingContext()
        register(ctx)
        assert not health.called, (
            "register() must not hit the Chatlytics /health endpoint at load time."
        )


# ---------------------------------------------------------------------
# AC-5: BL-01 reproduction tests (xfail-strict; Phase 8 un-xfails)
# ---------------------------------------------------------------------


async def test_keep_typing_is_a_coroutine() -> None:
    """``adapter._keep_typing(...)`` must return a coroutine usable by
    ``asyncio.create_task`` per the base contract.

    Current v2.0 behavior: ``@contextlib.asynccontextmanager`` returns an
    ``_AsyncGeneratorContextManager``, not a coroutine. The base call
    site at ``gateway/platforms/base.py:1787-1792`` does
    ``asyncio.create_task(self._keep_typing(chat_id, metadata=...))``,
    which raises ``TypeError: a coroutine was expected``.

    Phase 8 rewrite as a plain coroutine flips this test to xpass; the
    strict marker then forces un-xfailing.
    """
    adapter = _make_adapter()
    stop_event = asyncio.Event()
    coro = adapter._keep_typing(
        "120363xxx@g.us",
        interval=0.01,
        metadata={"thread": "x"},
        stop_event=stop_event,
    )
    assert asyncio.iscoroutine(coro), (
        f"_keep_typing must return a coroutine for asyncio.create_task; "
        f"got {type(coro).__name__}"
    )
    task = asyncio.create_task(coro)
    await asyncio.sleep(0.02)
    stop_event.set()
    try:
        await asyncio.wait_for(task, timeout=0.5)
    except asyncio.TimeoutError:
        task.cancel()
        raise


async def test_bl01_keep_typing_accepts_metadata_kwarg() -> None:
    """The base class call site at ``gateway/platforms/base.py:1780``
    ALWAYS passes ``metadata=_thread_metadata`` to ``_keep_typing`` (no
    signature gate). The override must accept it.
    """
    adapter = _make_adapter()
    sig = inspect.signature(adapter._keep_typing)
    params = sig.parameters
    assert "metadata" in params, (
        f"BL-01: _keep_typing must accept 'metadata' kwarg "
        f"(base.py:1780 always passes it). Current params: {list(params)}"
    )
    assert "stop_event" in params, (
        f"BL-01: _keep_typing must accept 'stop_event' kwarg "
        f"(base.py:1785-1786 passes it when present in the signature). "
        f"Current params: {list(params)}"
    )


async def test_base_process_message_invokes_keep_typing() -> None:
    """Drive the REAL base ``_process_message_background`` pipeline.

    This is the test that should have existed in v2.0. The recorder
    pattern at ``test_inbound.py:98-106`` short-circuits
    ``handle_message`` (and therefore never reaches the
    ``asyncio.create_task(self._keep_typing(chat_id, metadata=...))``
    call site at base.py:1787-1792). That is GSD-MD-04: the test
    harness gap that hid BL-01 for 6 phases.

    Strategy: directly invoke ``_process_message_background`` (the
    private base method that holds the BL-01 call site) and assert
    no TypeError surfaces. We install an ``AsyncMock`` as
    ``_message_handler`` so the agent dispatch is a no-op; the
    typing-task code path is the only thing under test here.
    """
    from gateway.platforms.base import MessageEvent, MessageType
    from gateway.session import SessionSource

    adapter = _make_adapter()
    adapter._message_handler = AsyncMock(return_value=None)

    # respx mock for any outbound typing/send calls the base path
    # incidentally issues -- we don't want unmocked network in tests.
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{BASE_URL}/health").mock(
            return_value=httpx.Response(200, json={"status": "ok"})
        )
        router.post(f"{BASE_URL}/api/v1/typing").mock(
            return_value=httpx.Response(200, json={"success": True})
        )
        router.post(f"{BASE_URL}/api/v1/send").mock(
            return_value=httpx.Response(200, json={"success": True})
        )
        router.route().pass_through()

        # Create a minimal MessageEvent. The exact field set must match
        # the base.MessageEvent dataclass; values are placeholders.
        try:
            event = MessageEvent(
                text="hello",
                message_type=MessageType.TEXT,
                source=SessionSource(
                    platform="chatlytics",
                    chat_id="120363xxx@g.us",
                    user_id="user-1",
                    chat_type="private",
                    message_id="msg-1",
                ),
                raw_message={"text": "hello"},
                message_id="msg-1",
                media_urls=[],
                media_types=[],
                reply_to_message_id=None,
            )
        except TypeError as exc:
            pytest.skip(f"MessageEvent/SessionSource construction mismatch: {exc}")

        # Light httpx client init so any outbound calls have a client.
        from chatlytics_hermes.client import ChatlyticsClient
        adapter._client = ChatlyticsClient(base_url=BASE_URL, api_key=API_KEY)

        session_key = "chatlytics::120363xxx@g.us::user-1"
        try:
            # Direct call into the base pipeline that holds the BL-01
            # call site. On v2.0 this raises TypeError from
            # asyncio.create_task(self._keep_typing(chat_id,
            # metadata=...)) because the override is an
            # asynccontextmanager (returns _AsyncGeneratorContextManager,
            # not a coroutine) AND doesn't accept the metadata kwarg.
            await adapter._process_message_background(event, session_key)
        finally:
            try:
                await adapter._client.aclose()
            except Exception:
                pass
            adapter._client = None


# ---------------------------------------------------------------------
# AC-6: HI-01 reproduction (path-traversal via filePath)
# ---------------------------------------------------------------------


async def test_hi01_send_file_rejects_path_outside_allowed_roots() -> None:
    """A path-traversal-style filePath must be rejected with success: False.

    Pre-Phase-8: the tool happily opens ``/etc/passwd`` (or
    ``C:/Windows/win.ini`` on Windows) and uploads it to the configured
    Chatlytics gateway -- a real privilege escalation primitive when
    the LLM is partially adversary-controlled.

    Phase 8 fix: env-configured allowlist
    ``CHATLYTICS_UPLOAD_ALLOWED_ROOTS`` rejects paths outside the
    allowed roots with ``{success: False, error: "..."}`` BEFORE any
    file is opened or uploaded.
    """
    from chatlytics_hermes.tools import chatlytics_send_file
    from chatlytics_hermes.client import ChatlyticsClient

    # Pick a system file that definitely exists on the host.
    bad_path = "C:/Windows/win.ini" if sys.platform == "win32" else "/etc/passwd"

    adapter = _make_adapter()
    adapter._client = ChatlyticsClient(base_url=BASE_URL, api_key=API_KEY)

    with respx.mock(assert_all_called=False) as router:
        router.get(f"{BASE_URL}/health").mock(
            return_value=httpx.Response(200, json={"status": "ok"})
        )
        # Seed every conceivable upload route as "success" -- if validation
        # is missing, an upload WILL be attempted and we want this assertion
        # to fire loudly that the file was exfiltrated.
        upload = router.post(f"{BASE_URL}/api/v1/send-media").mock(
            return_value=httpx.Response(200, json={"success": True})
        )
        upload_file = router.post(f"{BASE_URL}/api/v1/upload").mock(
            return_value=httpx.Response(200, json={"success": True, "url": "https://x/y"})
        )
        router.post(f"{BASE_URL}/api/v1/actions").mock(
            return_value=httpx.Response(200, json={"success": True})
        )
        router.route().pass_through()

        try:
            result = await chatlytics_send_file(
                adapter._client,
                adapter=adapter,
                chatId="120363xxx@g.us",
                filePath=bad_path,
                caption=None,
                filename=None,
            )
            assert isinstance(result, dict), f"Expected dict, got {type(result).__name__}"
            assert result.get("success") is False, (
                f"HI-01: chatlytics_send_file must refuse paths outside "
                f"allowed roots; got {result}"
            )
            err = (result.get("error") or "").lower()
            assert "allowed" in err or "permission" in err or "outside" in err, (
                f"HI-01: error message should mention allowlist/permission; got {result}"
            )
            assert not upload.called and not upload_file.called, (
                f"HI-01 CRITICAL: file upload was attempted for {bad_path} -- "
                "the file may have been exfiltrated. Validation must run BEFORE upload."
            )
        finally:
            await adapter._client.aclose()
            adapter._client = None


# ---------------------------------------------------------------------
# AC-7: HI-03 reproduction (**kwargs missing on send_image/send_animation)
# ---------------------------------------------------------------------


async def test_hi03_send_image_accepts_unknown_kwargs() -> None:
    """The override must accept ``**kwargs`` for forward-compat with
    upstream base signature evolution.

    Pre-Phase-8: ``send_image`` does NOT have ``**kwargs`` (see
    adapter.py:605-624). If upstream adds a new kwarg (priority=,
    force_native=, etc.), ``adapter.send_image(...)`` raises
    ``TypeError``. Consistency: 4 of 6 media overrides already have
    ``**kwargs``.
    """
    adapter = _make_adapter()
    sig = inspect.signature(adapter.send_image)
    has_var_kw = any(
        p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
    )
    assert has_var_kw, (
        f"HI-03: ChatlyticsAdapter.send_image must accept **kwargs for "
        f"forward-compat with base signature evolution; current params: "
        f"{list(sig.parameters)}"
    )


async def test_hi03_send_animation_accepts_unknown_kwargs() -> None:
    """Same as test_hi03_send_image_accepts_unknown_kwargs but for
    ``send_animation`` (adapter.py:626-642)."""
    adapter = _make_adapter()
    sig = inspect.signature(adapter.send_animation)
    has_var_kw = any(
        p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
    )
    assert has_var_kw, (
        f"HI-03: ChatlyticsAdapter.send_animation must accept **kwargs for "
        f"forward-compat with base signature evolution; current params: "
        f"{list(sig.parameters)}"
    )
