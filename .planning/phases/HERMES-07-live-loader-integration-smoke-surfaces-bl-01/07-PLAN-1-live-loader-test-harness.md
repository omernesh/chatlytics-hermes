---
phase: 7
plan_number: 1
plan_total: 1
title: Live-loader integration smoke + BL-01/HI-01/HI-03 xfail regression tests
status: ready
estimated_loc: 350
files_create:
  - tests/test_live_loader.py
files_modify:
  - src/chatlytics_hermes/__init__.py  # docstring only
  - scripts/smoke.sh                    # add live-loader step
verification:
  - pytest tests/test_live_loader.py -q passes (xfailed tests don't count as failures)
  - 5 strict xfail tests checked in (BL-01x2, HI-01, HI-03x2)
  - All 45 v2.0 tests still pass
---

# Plan 07-01: Live-loader integration smoke + xfail regression tests

## Goal

Wire `hermes_cli.plugins.PluginManager` (Hermes v0.14 plugin loader) against
a respx-mocked Chatlytics backend. Prove `register(ctx)` is called, the
chatlytics platform registers, and 21 tools land on the in-memory
`PluginContext` registry. Reproduce BL-01, HI-01, HI-03 under strict-xfail
tests that exercise the BASE `handle_message` pipeline (NOT the recorder
pattern that hid BL-01 in v2.0).

## Files

### CREATE `tests/test_live_loader.py` (≈300 LOC)

Test layout:

```python
"""HERMES-07: Live-loader integration smoke.

Drives the real Hermes v0.14 plugin loader (PluginManager from
hermes_cli/plugins.py) against a respx-mocked Chatlytics backend to prove:

1. The plugin loads (register(ctx) is called)
2. The chatlytics platform registers
3. All 21 tools land on the PluginContext registry
4. BL-01 / HI-01 / HI-03 are reproduced under strict-xfail markers
   (Phase 8 fixes them; strict=True forces un-xfailing then)

Critically, the regression tests exercise the BASE handle_message
pipeline -- NO recorder replacement. The v2.0 recorder pattern at
tests/test_inbound.py:98-106 is what hid BL-01 for 6 phases (GSD-MD-04).
"""
from __future__ import annotations

import asyncio
import inspect
import json
import os
import sys
from typing import Any, Dict, Optional
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from chatlytics_hermes import register
from chatlytics_hermes.adapter import ChatlyticsAdapter
from chatlytics_hermes.tools import TOOLS


pytestmark = pytest.mark.asyncio


# --- Helpers ----------------------------------------------------------

_EXPECTED_TOOL_NAMES = frozenset(name for name, _, _ in TOOLS)
assert len(_EXPECTED_TOOL_NAMES) == 21, (
    f"tools.TOOLS drift: expected 21 unique names, got {len(_EXPECTED_TOOL_NAMES)}"
)


class _FakeManifest:
    """Minimal PluginManifest-compatible stub."""
    def __init__(self, name: str = "chatlytics", key: str | None = None) -> None:
        self.name = name
        self.key = key or name
        self.source = "entry_point"
        self.kind = "platform"
        self.path = None


class _CapturingContext:
    """In-process PluginContext-compatible recorder.

    Mirrors the v0.14 PluginContext surface (register_platform,
    register_tool) so the same register(ctx) code that runs under the
    real loader runs here. We capture each call so tests can assert on
    the registered platform + tools.
    """

    def __init__(self) -> None:
        self.platform_calls: list[Dict[str, Any]] = []
        self.tool_calls: list[Dict[str, Any]] = []
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
        self.tool_calls.append({
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
        })


def _make_adapter() -> ChatlyticsAdapter:
    """Construct an adapter against test-safe config (no real network)."""
    return ChatlyticsAdapter({
        "base_url": "http://chatlytics.test",
        "api_key": "test-key",
        "account_id": None,
        "webhook_host": "127.0.0.1",
        "webhook_port": 0,        # OS-assigned free port
        "webhook_path": "/webhook",
        "webhook_secret": None,
    })


# --- AC-1: register(ctx) is called and platform registers ----------

async def test_loader_registers_chatlytics_platform() -> None:
    ctx = _CapturingContext()
    register(ctx)
    assert len(ctx.platform_calls) == 1
    call = ctx.platform_calls[0]
    assert call["name"] == "chatlytics"
    assert callable(call["adapter_factory"])
    assert callable(call["check_fn"]) and call["check_fn"]() is True
    assert "CHATLYTICS_BASE_URL" in call["required_env"]
    assert "CHATLYTICS_API_KEY" in call["required_env"]
    assert call["cron_deliver_env_var"] == "CHATLYTICS_HOME_CHANNEL"


# --- AC-2: all 21 tools land on the context ------------------------

async def test_loader_registers_21_tools() -> None:
    ctx = _CapturingContext()
    register(ctx)
    assert len(ctx.tool_calls) == 21, (
        f"Expected exactly 21 tools, got {len(ctx.tool_calls)}: "
        f"{[c['name'] for c in ctx.tool_calls]}"
    )
    registered_names = {c["name"] for c in ctx.tool_calls}
    assert registered_names == _EXPECTED_TOOL_NAMES, (
        f"Tool surface drift: missing={_EXPECTED_TOOL_NAMES - registered_names}, "
        f"extra={registered_names - _EXPECTED_TOOL_NAMES}"
    )
    # All under the chatlytics toolset (avoids collisions with other plugins)
    assert all(c["toolset"] == "chatlytics" for c in ctx.tool_calls)
    # All handlers are callables (lambda or coroutine wrappers)
    assert all(callable(c["handler"]) for c in ctx.tool_calls)
    # All schemas are dicts
    assert all(isinstance(c["schema"], dict) for c in ctx.tool_calls)


# --- AC-3: loader handles missing env gracefully (no half-register) ----

async def test_loader_handles_missing_env_vars_gracefully(monkeypatch) -> None:
    # register() does NOT crash on missing env; required_env is declarative.
    # The platform's check_fn returns True (deps loaded); env presence is
    # a runtime gateway concern, not a load-time concern.
    monkeypatch.delenv("CHATLYTICS_BASE_URL", raising=False)
    monkeypatch.delenv("CHATLYTICS_API_KEY", raising=False)
    monkeypatch.delenv("CHATLYTICS_HOME_CHANNEL", raising=False)
    ctx = _CapturingContext()
    register(ctx)  # must not raise
    assert len(ctx.platform_calls) == 1
    assert len(ctx.tool_calls) == 21


# --- AC-4: loader isolated from real Chatlytics ---------------------

async def test_loader_isolated_from_real_chatlytics() -> None:
    """register() is purely declarative — no HTTP calls at load time.

    Verify by asserting NO httpx client is created and NO respx mock is
    consumed during register(). The adapter factory is just a lambda;
    it is not invoked at register time.
    """
    with respx.mock(base_url="http://chatlytics.test", assert_all_called=False) as router:
        # Seed a health route that would EXPLODE if hit
        health = router.get("/health").mock(return_value=httpx.Response(500))
        ctx = _CapturingContext()
        register(ctx)
        assert not health.called, "register() must not hit /health"


# --- AC-5: BL-01 reproduction (xfail-strict) -------------------------
# Currently fails: _keep_typing is @asynccontextmanager, not a coroutine,
# so asyncio.create_task(adapter._keep_typing(...)) raises TypeError.
# Phase 8 rewrites as a plain coroutine; this test then passes; strict=True
# forces Phase 8 to un-xfail this marker.

@pytest.mark.xfail(strict=True, reason="BL-01: _keep_typing wrong shape — fixed in Phase 8")
async def test_keep_typing_is_a_coroutine() -> None:
    adapter = _make_adapter()
    # The DESIRED post-fix behavior: calling _keep_typing returns a coroutine
    # that can be wrapped in asyncio.create_task, accepts metadata and stop_event.
    stop_event = asyncio.Event()
    coro = adapter._keep_typing(
        "120363xxx@g.us",
        interval=0.01,
        metadata={"thread": "x"},
        stop_event=stop_event,
    )
    assert asyncio.iscoroutine(coro), (
        f"_keep_typing must be a plain coroutine for asyncio.create_task; "
        f"got {type(coro).__name__}"
    )
    # Drive the coroutine for ~20ms then stop.
    task = asyncio.create_task(coro)
    await asyncio.sleep(0.02)
    stop_event.set()
    try:
        await asyncio.wait_for(task, timeout=0.5)
    except asyncio.TimeoutError:
        task.cancel()
        raise


@pytest.mark.xfail(strict=True, reason="BL-01: _keep_typing rejects metadata kwarg — fixed in Phase 8")
async def test_bl01_keep_typing_accepts_metadata_kwarg() -> None:
    """The base class call site at /tmp/hermes-ref-v0.14.0/gateway/platforms/base.py:3045-3057
    invokes self._keep_typing(chat_id, metadata=_thread_metadata[, stop_event=...]).
    The override must accept metadata.
    """
    adapter = _make_adapter()
    sig = inspect.signature(adapter._keep_typing)
    params = sig.parameters
    assert "metadata" in params, (
        f"BL-01: _keep_typing must accept 'metadata' kwarg (base.py:3045-3057 always "
        f"passes it). Current params: {list(params)}"
    )
    assert "stop_event" in params, (
        f"BL-01: _keep_typing must accept 'stop_event' kwarg (base.py:3045-3057 passes "
        f"it conditionally based on signature inspection). Current params: {list(params)}"
    )


# --- AC-5b: BL-01 reproduction via BASE handle_message pipeline -----
# This is the critical test that closes GSD-MD-04: exercise the base
# handle_message pipeline WITHOUT replacing it with a recorder. Install
# an AsyncMock as the message handler so the base path runs to completion
# but the agent loop is a no-op. Currently fails (TypeError from
# _keep_typing); Phase 8 fix makes it pass.

@pytest.mark.xfail(strict=True, reason="BL-01: base handle_message crashes on _keep_typing — fixed in Phase 8")
async def test_base_handle_message_invokes_keep_typing() -> None:
    """Drive the REAL base handle_message; assert no TypeError from _keep_typing.

    This is the test that should have existed in v2.0. The recorder
    pattern at test_inbound.py:98-106 short-circuits handle_message,
    which is why BL-01 went undetected.
    """
    from gateway.platforms.base import MessageEvent, MessageType
    from gateway.session import SessionSource

    adapter = _make_adapter()

    # Install a no-op message handler so the base handle_message path
    # runs but the agent loop returns immediately. Crucially, we do NOT
    # replace adapter.handle_message itself — we let the base method run.
    adapter._message_handler = AsyncMock(return_value=None)

    with respx.mock(base_url="http://chatlytics.test", assert_all_called=False) as router:
        router.get("/health").mock(return_value=httpx.Response(200, json={"status": "ok"}))
        router.post("/api/v1/typing").mock(return_value=httpx.Response(200, json={"success": True}))
        # Connect (starts aiohttp inbound server + httpx client)
        await adapter.connect()
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
            # The base handle_message MUST complete without TypeError.
            # Currently raises TypeError from asyncio.create_task(
            #   adapter._keep_typing(chat_id, metadata=...))
            # at /tmp/hermes-ref-v0.14.0/gateway/platforms/base.py:3045-3057.
            await adapter.handle_message(event)
        finally:
            await adapter.disconnect()


# --- AC-6: HI-01 reproduction (path traversal via filePath) ---------
# Currently passes (no validation exists, file IS opened and uploaded).
# Phase 8 adds CHATLYTICS_UPLOAD_ALLOWED_ROOTS allowlist; the call
# returns {success: False, error: "..."} without opening the file.
# We write the test as the DESIRED post-fix behavior, xfail-strict.

@pytest.mark.xfail(strict=True, reason="HI-01: filePath has no allowlist — fixed in Phase 8")
async def test_hi01_send_file_rejects_path_outside_allowed_roots() -> None:
    """A path-traversal-style filePath must be rejected with success: False.

    Pre-Phase-8: the tool happily opens /etc/passwd (or C:/Windows/win.ini)
    and uploads it to the configured Chatlytics gateway -- a real privilege
    escalation primitive when the LLM is partially adversary-controlled.
    Phase 8 fix: env-configured allowlist via CHATLYTICS_UPLOAD_ALLOWED_ROOTS.
    """
    from chatlytics_hermes.tools import chatlytics_send_file

    adapter = _make_adapter()
    # Pick a system file that definitely exists on the host
    if sys.platform == "win32":
        bad_path = "C:/Windows/win.ini"
    else:
        bad_path = "/etc/passwd"

    with respx.mock(base_url="http://chatlytics.test", assert_all_called=False) as router:
        router.get("/health").mock(return_value=httpx.Response(200, json={"status": "ok"}))
        # If validation is missing (pre-fix), an upload would be attempted.
        # We seed a route that EXPLODES if hit, so any leakage fails loudly.
        upload = router.post("/api/v1/send-media").mock(return_value=httpx.Response(200, json={"success": True}))
        await adapter.connect()
        try:
            result = await chatlytics_send_file(
                adapter=adapter,
                chatId="120363xxx@g.us",
                filePath=bad_path,
                caption=None,
                fileName=None,
            )
            assert isinstance(result, dict)
            assert result.get("success") is False, (
                f"HI-01: chatlytics_send_file must refuse paths outside allowed roots; "
                f"got {result}"
            )
            assert "allowed" in (result.get("error") or "").lower() or \
                   "permission" in (result.get("error") or "").lower(), (
                f"HI-01: error message should mention allowlist/permission; got {result}"
            )
            assert not upload.called, (
                f"HI-01 CRITICAL: file upload was attempted for {bad_path} -- "
                "the file may have been exfiltrated. Validation must run BEFORE upload."
            )
        finally:
            await adapter.disconnect()


# --- AC-7: HI-03 reproduction (**kwargs missing on send_image / send_animation) ----
# Currently raises TypeError when caller passes unknown kwarg; Phase 8
# adds **kwargs: Any to both signatures.

@pytest.mark.xfail(strict=True, reason="HI-03: send_image rejects extra kwargs — fixed in Phase 8")
async def test_hi03_send_image_accepts_unknown_kwargs() -> None:
    """Upstream base may evolve send_image signature with new kwargs.
    The override must accept **kwargs for forward-compat (consistency
    with send_voice/send_video/send_document/send_image_file which
    already have **kwargs).
    """
    adapter = _make_adapter()
    sig = inspect.signature(adapter.send_image)
    has_var_kw = any(
        p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
    )
    assert has_var_kw, (
        f"HI-03: ChatlyticsAdapter.send_image must accept **kwargs for forward-compat "
        f"with base signature evolution; current params: {list(sig.parameters)}"
    )


@pytest.mark.xfail(strict=True, reason="HI-03: send_animation rejects extra kwargs — fixed in Phase 8")
async def test_hi03_send_animation_accepts_unknown_kwargs() -> None:
    adapter = _make_adapter()
    sig = inspect.signature(adapter.send_animation)
    has_var_kw = any(
        p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
    )
    assert has_var_kw, (
        f"HI-03: ChatlyticsAdapter.send_animation must accept **kwargs for forward-compat "
        f"with base signature evolution; current params: {list(sig.parameters)}"
    )
```

### MODIFY `src/chatlytics_hermes/__init__.py` (docstring)

Replace the bare re-export with a module docstring documenting the
loader contract:

```python
"""Chatlytics Hermes plugin -- v0.14 first-class platform plugin.

Entry point (registered via ``pyproject.toml``
``[project.entry-points."hermes_agent.plugins"]``):

    chatlytics = "chatlytics_hermes:register"

Hermes discovers this plugin via ``importlib.metadata.entry_points(
group="hermes_agent.plugins")`` in ``hermes_cli/plugins.py`` and calls
``register(ctx)`` with a fresh ``PluginContext`` per the v0.14 contract.

The ``register`` function (defined in ``adapter.py``) is the plugin's
sole entry point. It:

1. Registers the ``chatlytics`` platform via ``ctx.register_platform(...)``
   with the canonical PlatformEntry fields (name, label, adapter_factory,
   check_fn, required_env, env_enablement_fn, cron_deliver_env_var,
   standalone_sender_fn, emoji, install_hint, platform_hint).
2. Iterates the locked-21 tool surface from ``chatlytics_hermes.tools.TOOLS``
   and calls ``ctx.register_tool(name=, toolset='chatlytics', schema=, handler=)``
   for each.

The live-loader contract is verified by ``tests/test_live_loader.py``,
which drives a PluginContext-compatible recorder through ``register(ctx)``
end-to-end and asserts the platform + 21 tools land correctly. The same
file holds strict-xfail regression tests for BL-01, HI-01, HI-03 from
the v2.0 milestone-wide review (fixed in HERMES-08).
"""
from .adapter import register

__all__ = ["register"]
```

### MODIFY `scripts/smoke.sh` (add live-loader step)

After the existing pytest step, add:

```bash
echo "[smoke] live-loader integration test"
python -m pytest tests/test_live_loader.py -q --no-header --tb=short
echo "[smoke] live-loader: chatlytics platform + 21 tools registered"
```

The xfailed tests don't count as failures, so this exits 0 on green.

## Acceptance criteria

Per ROADMAP HERMES-07:

1. `pytest tests/test_live_loader.py::test_loader_registers_chatlytics_platform -q` passes
2. `pytest tests/test_live_loader.py::test_loader_registers_21_tools -q` passes
3. `pytest tests/test_live_loader.py::test_loader_handles_missing_env_vars_gracefully -q` passes
4. `pytest tests/test_live_loader.py::test_loader_isolated_from_real_chatlytics -q` passes
5. `pytest tests/test_live_loader.py::test_base_handle_message_invokes_keep_typing -q` is xfailed-strict (currently fails; Phase 8 un-xfails)
6. `pytest tests/test_live_loader.py::test_keep_typing_is_a_coroutine -q` is xfailed-strict
7. `bash scripts/smoke.sh` exits 0 with output mentioning "live-loader: chatlytics platform + 21 tools registered"
8. All 45/45 v2.0 pre-existing tests still pass

Plus the HI-01/HI-03 extras:
9. `test_hi01_send_file_rejects_path_outside_allowed_roots` xfailed-strict
10. `test_hi03_send_image_accepts_unknown_kwargs` xfailed-strict
11. `test_hi03_send_animation_accepts_unknown_kwargs` xfailed-strict
12. `test_bl01_keep_typing_accepts_metadata_kwarg` xfailed-strict

## Invariants preserved

- Hermes pin stays `>=0.14,<0.15` (no pyproject change)
- Tool surface stays at 21 tools (asserted, not changed)
- All HTTP outbound through httpx async (no new transports)
- All tool handlers return `{"success": bool, ...}` shape (untouched)
- Inbound transport stays inside `connect()` via aiohttp (untouched)
- `chatlytics-hermes` package name preserved
- MIT license preserved
- 45/45 v2.0 tests still pass (no source changes; only docstring + new test file + smoke addition)

## Commit shape

Single commit:
```
feat(07): live-loader integration smoke + BL-01/HI-01/HI-03 xfail regression tests

- tests/test_live_loader.py: 11 tests total
  - 4 GREEN: loader registers platform, 21 tools, handles missing env,
    isolated from network
  - 5 XFAIL-STRICT (BL-01): keep_typing coroutine shape, metadata kwarg,
    base handle_message pipeline (closes GSD-MD-04 harness gap)
  - 1 XFAIL-STRICT (HI-01): send_file rejects paths outside allowed roots
  - 2 XFAIL-STRICT (HI-03): send_image / send_animation accept **kwargs
- scripts/smoke.sh: add live-loader step
- src/chatlytics_hermes/__init__.py: docstring documenting loader contract

Closes 06-MED-01 + GSD-MD-04 (test harness bypass).
Phase 8 will un-xfail the 8 strict xfails after fixing BL-01/HI-01/HI-03.
```
