"""HERMES-04 acceptance tests for the chatlytics-hermes media surface.

Media-send fix (event-loop + endpoint correction milestone): the chatlytics
gateway has NO ``/api/v1/send-media`` or ``/api/v1/upload`` route. The ONLY
send endpoint is ``POST /api/v1/send``, which routes media by a top-level
``type`` field (image/video/file) and carries the media in the WAHA ``file``
passthrough field (``{url, mimetype?, filename?}`` for a remote URL, or
``{data(base64), mimetype, filename}`` for an inlined local file). These tests
assert that real contract; the pre-fix tests asserted the phantom endpoints.

HERMES-15 (v3.0 BREAKING): the v2.0 ``send_image_file`` companion was
removed. The ``TestResourceAutoDetection`` class at the bottom covers
the unified ``send_image(resource: str | Path | bytes)`` auto-detection
contract.

The 8th acceptance test (``_standalone_send`` cron hook) lives in
``tests/test_cron.py`` because it does not require an adapter instance.
"""

from __future__ import annotations

import asyncio
import base64
import json as _json
import tempfile
from pathlib import Path
from typing import Any, Dict

import httpx
import pytest
import respx

from chatlytics_hermes.adapter import ChatlyticsAdapter
from tests._fixtures import FakePlatformConfig

BASE_URL = "https://gateway.test.chatlytics.ai"
API_KEY = "test-api-key-abc123"
CHAT_ID = "120363100000000000@g.us"
SESSION = "3cf11776_logan"
EXPECTED_AUTH = f"Bearer {API_KEY}"


@pytest.fixture
def adapter() -> ChatlyticsAdapter:
    # HERMES-08 HI-01 fix: ``upload_allowed_roots`` MUST be configured
    # for local-file media tests to pass under the new default-deny
    # allowlist.  ``tempfile.gettempdir()`` covers ``tmp_path`` (pytest's
    # per-test temp dir lives under it).
    # Media-send fix: ``/api/v1/send`` REQUIRES a WAHA session (the P-19
    # contract shared with text send()), so the fixture pins one.
    return ChatlyticsAdapter(
        FakePlatformConfig(
            extra={
                "base_url": BASE_URL,
                "api_key": API_KEY,
                "account_id": "acct-1",
                "session": SESSION,
                "upload_allowed_roots": tempfile.gettempdir(),
            }
        )
    )


@pytest.fixture
def mock_router():
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        yield router


# --- AC-1: send_image(url) posts to /api/v1/send (type=image) ---------

async def test_send_image_url_path(
    adapter: ChatlyticsAdapter, mock_router: respx.MockRouter
) -> None:
    mock_router.get("/health").mock(return_value=httpx.Response(200, json={}))
    send_route = mock_router.post("/api/v1/send").mock(
        return_value=httpx.Response(200, json={"success": True, "messageId": "m-img"})
    )
    await adapter.connect()
    result = await adapter.send_image(
        CHAT_ID, "https://cdn.test/x.png", caption="hi there"
    )
    assert result.success is True
    assert result.message_id == "m-img"

    body = _json.loads(send_route.calls.last.request.content)
    assert body["chatId"] == CHAT_ID
    assert body["session"] == SESSION
    assert body["type"] == "image"
    assert body["caption"] == "hi there"
    # Remote URL rides the WAHA ``file`` passthrough field.
    assert body["file"]["url"] == "https://cdn.test/x.png"
    assert send_route.calls.last.request.headers["authorization"] == EXPECTED_AUTH
    await adapter.disconnect()


# --- AC-2: send_voice routes through /api/v1/send (type=file) ----------

async def test_send_voice_routes_via_send(
    adapter: ChatlyticsAdapter, mock_router: respx.MockRouter
) -> None:
    """Voice is not a proxy-send ``type``; it degrades to ``file``.

    A true WhatsApp voice bubble (push-to-talk) is only reachable on the
    direct WAHA path, not via ``/api/v1/send`` (whose ``type`` set is
    text/image/video/file). The audio still delivers as a downloadable
    attachment under ``type=file``.
    """
    mock_router.get("/health").mock(return_value=httpx.Response(200, json={}))
    send_route = mock_router.post("/api/v1/send").mock(
        return_value=httpx.Response(200, json={"success": True, "messageId": "m-voice"})
    )
    await adapter.connect()
    result = await adapter.send_voice(CHAT_ID, "https://cdn.test/v.ogg")
    assert result.success is True

    body = _json.loads(send_route.calls.last.request.content)
    assert body["type"] == "file", (
        f"send_voice must route as type=file via /api/v1/send, got {body['type']!r}"
    )
    assert body["file"]["url"] == "https://cdn.test/v.ogg"
    await adapter.disconnect()


# --- AC-3: send_video -------------------------------------------------

async def test_send_video(
    adapter: ChatlyticsAdapter, mock_router: respx.MockRouter
) -> None:
    mock_router.get("/health").mock(return_value=httpx.Response(200, json={}))
    send_route = mock_router.post("/api/v1/send").mock(
        return_value=httpx.Response(200, json={"success": True, "messageId": "m-vid"})
    )
    await adapter.connect()
    result = await adapter.send_video(
        CHAT_ID, "https://cdn.test/clip.mp4", caption="check this clip"
    )
    assert result.success is True

    body = _json.loads(send_route.calls.last.request.content)
    assert body["type"] == "video"
    assert body["file"]["url"] == "https://cdn.test/clip.mp4"
    assert body["caption"] == "check this clip"
    await adapter.disconnect()


# --- AC-4: send_document with filename --------------------------------

async def test_send_document_with_filename(
    adapter: ChatlyticsAdapter, mock_router: respx.MockRouter
) -> None:
    mock_router.get("/health").mock(return_value=httpx.Response(200, json={}))
    send_route = mock_router.post("/api/v1/send").mock(
        return_value=httpx.Response(200, json={"success": True, "messageId": "m-doc"})
    )
    await adapter.connect()
    result = await adapter.send_document(
        CHAT_ID, "https://cdn.test/d.pdf", file_name="report.pdf"
    )
    assert result.success is True

    body = _json.loads(send_route.calls.last.request.content)
    assert body["type"] == "file"
    # Caller-supplied display name surfaces as the top-level filename.
    assert body["filename"] == "report.pdf"
    assert body["file"]["url"] == "https://cdn.test/d.pdf"
    await adapter.disconnect()


# --- AC-5: send_animation ---------------------------------------------

async def test_send_animation(
    adapter: ChatlyticsAdapter, mock_router: respx.MockRouter
) -> None:
    """Animation delivers as inline video (``type=video``).

    WhatsApp has no native GIF primitive -- clients render short MP4s in a
    loop -- so the gateway routes animations under ``type=video``.
    """
    mock_router.get("/health").mock(return_value=httpx.Response(200, json={}))
    send_route = mock_router.post("/api/v1/send").mock(
        return_value=httpx.Response(200, json={"success": True, "messageId": "m-ani"})
    )
    await adapter.connect()
    result = await adapter.send_animation(
        CHAT_ID, "https://cdn.test/a.gif", caption="lol"
    )
    assert result.success is True

    body = _json.loads(send_route.calls.last.request.content)
    assert body["type"] == "video", (
        f"animation must route as type=video, got {body['type']!r}"
    )
    assert body["file"]["url"] == "https://cdn.test/a.gif"
    await adapter.disconnect()


# --- AC-6: send_image with a Path object inlines local bytes ----------

async def test_send_image_local_path_inlines_bytes(
    adapter: ChatlyticsAdapter,
    mock_router: respx.MockRouter,
    tmp_path: Path,
) -> None:
    """send_image(Path) reads bytes and base64-inlines them into ``file.data``.

    Media-send fix: there is no server upload endpoint, so a local file is
    inlined as base64 in the WAHA ``file`` field (``{data, mimetype,
    filename}``) instead of being uploaded to the (non-existent)
    ``/api/v1/upload``.
    """
    img_path = tmp_path / "x.png"
    fake_png = b"\x89PNG\r\n\x1a\n" + b"some-bytes-x123"
    img_path.write_bytes(fake_png)

    mock_router.get("/health").mock(return_value=httpx.Response(200, json={}))
    send_route = mock_router.post("/api/v1/send").mock(
        return_value=httpx.Response(
            200, json={"success": True, "messageId": "m-imgfile"}
        )
    )

    await adapter.connect()
    result = await adapter.send_image(CHAT_ID, Path(img_path), caption="local")
    assert result.success is True
    assert result.message_id == "m-imgfile"

    body = _json.loads(send_route.calls.last.request.content)
    assert body["type"] == "image"
    assert body["caption"] == "local"
    # Local bytes are base64-inlined under file.data (NOT uploaded).
    assert body["file"]["data"] == base64.b64encode(fake_png).decode("ascii")
    assert body["file"]["filename"] == "x.png"
    assert body["file"]["mimetype"] == "image/png"
    await adapter.disconnect()


# --- 04-MED-02 regression: file read off the event loop ---------------

async def test_send_image_local_path_reads_off_event_loop(
    adapter: ChatlyticsAdapter,
    mock_router: respx.MockRouter,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Local-file resolution must read via ``asyncio.to_thread``.

    Regression for 04-REVIEW MED-02: the local-file branch previously
    called ``open()`` + ``fh.read()`` directly on the event loop thread,
    which blocks all other coroutines for the duration of the read on
    multi-MB files. The read stays wrapped in ``asyncio.to_thread`` in the
    media-send fix's ``_resolve_media_file_field``.

    We assert the wrap is in effect by capturing the thread on which
    ``open()`` runs: it must NOT be the main thread (the loop thread).
    """
    import builtins
    import threading

    img_path = tmp_path / "x.png"
    fake_png = b"\x89PNG\r\n\x1a\n" + b"check-thread"
    img_path.write_bytes(fake_png)

    mock_router.get("/health").mock(return_value=httpx.Response(200, json={}))
    mock_router.post("/api/v1/send").mock(
        return_value=httpx.Response(200, json={"success": True, "messageId": "m-thr"})
    )

    main_thread = threading.main_thread()
    threads_seen: list[threading.Thread] = []
    real_open = builtins.open

    def spy_open(*args, **kwargs):  # noqa: ANN001 -- builtin shim
        if args and str(args[0]) == str(img_path):
            threads_seen.append(threading.current_thread())
        return real_open(*args, **kwargs)

    monkeypatch.setattr(builtins, "open", spy_open)

    await adapter.connect()
    result = await adapter.send_image(CHAT_ID, Path(img_path), caption="thr")
    assert result.success is True

    assert threads_seen, "open() was never called against the test file"
    for thr in threads_seen:
        assert thr is not main_thread, (
            f"open() on local media path ran on the main/event-loop thread "
            f"({thr!r}); _resolve_media_file_field must use asyncio.to_thread"
        )
    await adapter.disconnect()


# ---------------------------------------------------------------------------
# HERMES-15: send_image resource auto-detection (BREAKING library API)
# ---------------------------------------------------------------------------
#
# v3.0 BREAKING — see CHANGELOG entry "BREAKING — adapter send_* unified
# resource shape". The v2.0 send_image / send_image_file split collapsed
# into a single send_image(resource) that auto-detects URL vs Path vs
# string-path-exists vs raw bytes. Anything else raises ValueError and
# surfaces as SendResult(success=False, error=...).


class TestResourceAutoDetection:
    """Adapter.send_image auto-detection branches (HERMES-15)."""

    async def test_url_string_passes_through_as_file_url(
        self,
        adapter: ChatlyticsAdapter,
        mock_router: respx.MockRouter,
    ) -> None:
        """Branch 3: http(s):// string -> file.url passthrough, no inlining."""
        mock_router.get("/health").mock(return_value=httpx.Response(200, json={}))
        send_route = mock_router.post("/api/v1/send").mock(
            return_value=httpx.Response(200, json={"success": True, "messageId": "m-url"})
        )

        await adapter.connect()
        result = await adapter.send_image(
            CHAT_ID, "https://cdn.test/cat.jpg", caption="url branch"
        )
        assert result.success is True

        body = _json.loads(send_route.calls.last.request.content)
        assert body["file"]["url"] == "https://cdn.test/cat.jpg"
        # URL passthrough must NOT inline bytes.
        assert "data" not in body["file"]
        await adapter.disconnect()

    async def test_path_object_inlines_base64(
        self,
        adapter: ChatlyticsAdapter,
        mock_router: respx.MockRouter,
        tmp_path: Path,
    ) -> None:
        """Branch 2: explicit Path object -> base64-inlined file.data."""
        img_path = tmp_path / "p.png"
        img_path.write_bytes(b"path-object-bytes")

        mock_router.get("/health").mock(return_value=httpx.Response(200, json={}))
        send_route = mock_router.post("/api/v1/send").mock(
            return_value=httpx.Response(200, json={"success": True, "messageId": "m-path"})
        )

        await adapter.connect()
        result = await adapter.send_image(CHAT_ID, Path(img_path))
        assert result.success is True
        body = _json.loads(send_route.calls.last.request.content)
        assert body["file"]["data"] == base64.b64encode(b"path-object-bytes").decode("ascii")
        await adapter.disconnect()

    async def test_string_path_that_exists_inlines_base64(
        self,
        adapter: ChatlyticsAdapter,
        mock_router: respx.MockRouter,
        tmp_path: Path,
    ) -> None:
        """Branch 4: ``str`` whose path exists -> base64 inline (parity with Path)."""
        img_path = tmp_path / "s.png"
        img_path.write_bytes(b"string-path-bytes")

        mock_router.get("/health").mock(return_value=httpx.Response(200, json={}))
        send_route = mock_router.post("/api/v1/send").mock(
            return_value=httpx.Response(200, json={"success": True, "messageId": "m-str"})
        )

        await adapter.connect()
        result = await adapter.send_image(CHAT_ID, str(img_path))
        assert result.success is True
        body = _json.loads(send_route.calls.last.request.content)
        assert body["file"]["data"] == base64.b64encode(b"string-path-bytes").decode("ascii")
        await adapter.disconnect()

    async def test_unresolvable_string_returns_invalid_resource_error(
        self,
        adapter: ChatlyticsAdapter,
        mock_router: respx.MockRouter,
        tmp_path: Path,
    ) -> None:
        """Branch 5: str that's neither URL nor existing path -> SendResult(False, ...).

        ``_resolve_media_file_field`` raises ``ValueError`` and
        ``_send_media_payload`` catches it and surfaces a clean failure dict
        to the caller instead of an uncaught raise.
        """
        import uuid

        nonexistent = str(
            tmp_path / f"definitely-not-a-real-path-{uuid.uuid4().hex}"
        )
        assert not Path(nonexistent).exists(), (
            "Test invariant: synthesized path must not exist"
        )

        mock_router.get("/health").mock(return_value=httpx.Response(200, json={}))
        # No send route mocked — should never be called.

        await adapter.connect()
        result = await adapter.send_image(CHAT_ID, nonexistent)
        assert result.success is False
        assert "Invalid resource" in (result.error or "")
        await adapter.disconnect()

    def test_send_image_file_symbol_is_gone(
        self, adapter: ChatlyticsAdapter
    ) -> None:
        """HERMES-15 acceptance criterion 4: send_image_file must NOT be usable.

        The base class ``BasePlatformAdapter`` defines a text-fallback
        default for ``send_image_file``, which would silently degrade
        a v2.x photo-send caller into a text bubble. ``ChatlyticsAdapter``
        shadows the inherited method with a ``_RemovedMethod`` descriptor
        that raises ``AttributeError`` on access — v2.x callers see a
        clear migration error pointing at ``send_image``.
        """
        # AC #4 literal: getattr with default returns None on AttributeError.
        assert getattr(adapter, "send_image_file", None) is None, (
            "send_image_file access must raise AttributeError (caught "
            "by getattr-with-default) — no usable shim or alias"
        )
        # Load-bearing contract: direct access raises with migration text.
        with pytest.raises(AttributeError, match="send_image_file was removed"):
            adapter.send_image_file  # noqa: B018 — access triggers AttributeError


# --- AC-7: _keep_typing heartbeats every interval --------------------

async def test_keep_typing_heartbeats_every_30s(
    adapter: ChatlyticsAdapter, mock_router: respx.MockRouter
) -> None:
    """asynccontextmanager fires immediately + reissues every interval seconds.

    Uses ``interval=0.05`` to keep the test sub-second.  Asserts:

    1. At least one typing call lands immediately on __aenter__
       (the initial fire).
    2. At least one additional typing call lands during a 0.12s body
       sleep (>= 2 intervals worth), proving the heartbeat loop fires.
    3. After __aexit__, no further typing calls land for at least
       0.15s, proving the background task was cancelled cleanly.

    The lower bound is ``>= 2`` (initial + 1 beat) rather than ``>= 3``
    to tolerate scheduling jitter on busy CI runners.
    """
    mock_router.get("/health").mock(return_value=httpx.Response(200, json={}))
    typing_route = mock_router.post("/api/v1/typing").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    await adapter.connect()

    # HERMES-08 BL-01 fix: the async-cm flavor is now ``_typing_scope``.
    # ``_keep_typing`` is the plain coroutine that honors the upstream
    # base contract (called by ``asyncio.create_task`` in
    # gateway.platforms.base._process_message_background).
    async with adapter._typing_scope(CHAT_ID, interval=0.05):
        await asyncio.sleep(0.12)
        in_block = typing_route.call_count

    assert in_block >= 2, (
        f"Expected initial fire + at least one heartbeat, got {in_block} calls"
    )

    # After __aexit__, the background task must be cancelled -- no
    # further typing calls land even if we wait a few intervals.
    prior = typing_route.call_count
    await asyncio.sleep(0.15)
    assert typing_route.call_count == prior, (
        f"Heartbeat continued after context-manager exit: "
        f"{typing_route.call_count - prior} extra calls"
    )

    # Every typing call carried Bearer auth.
    for call in typing_route.calls:
        assert call.request.headers.get("authorization") == EXPECTED_AUTH

    await adapter.disconnect()


async def test_keep_typing_swallows_send_typing_errors(
    adapter: ChatlyticsAdapter, mock_router: respx.MockRouter
) -> None:
    """Heartbeat must never propagate -- typing is a UX hint, not critical.

    Companion to AC-7: confirms a failing typing endpoint does not
    crash the wrapped body.  Not in the 8-test ROADMAP list but worth
    asserting given the heartbeat runs as a background task whose
    exceptions would otherwise be silently dropped.
    """
    mock_router.get("/health").mock(return_value=httpx.Response(200, json={}))
    mock_router.post("/api/v1/typing").mock(
        return_value=httpx.Response(500, text="upstream broken")
    )
    await adapter.connect()
    body_ran = False
    async with adapter._typing_scope(CHAT_ID, interval=0.05):
        await asyncio.sleep(0.06)
        body_ran = True
    assert body_ran, "_typing_scope must not abort the wrapped body on typing errors"
    await adapter.disconnect()
