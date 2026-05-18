"""HERMES-04 acceptance tests for the chatlytics-hermes media surface.

Seven ``respx``-mocked tests cover the 6 media handlers (``send_image``,
``send_voice``, ``send_video``, ``send_document``, ``send_animation``,
``send_image_file``) plus the ``_keep_typing`` 30s heartbeat
asynccontextmanager.

The 8th acceptance test (``_standalone_send`` cron hook) lives in
``tests/test_cron.py`` because it does not require an adapter instance.
"""

from __future__ import annotations

import asyncio
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
EXPECTED_AUTH = f"Bearer {API_KEY}"


@pytest.fixture
def adapter() -> ChatlyticsAdapter:
    # HERMES-08 HI-01 fix: ``upload_allowed_roots`` MUST be configured
    # for local-file media tests to pass under the new default-deny
    # allowlist.  ``tempfile.gettempdir()`` covers ``tmp_path`` (pytest's
    # per-test temp dir lives under it).
    return ChatlyticsAdapter(
        FakePlatformConfig(
            extra={
                "base_url": BASE_URL,
                "api_key": API_KEY,
                "account_id": "acct-1",
                "upload_allowed_roots": tempfile.gettempdir(),
            }
        )
    )


@pytest.fixture
def mock_router():
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        yield router


# --- AC-1: send_image(url) posts to /api/v1/send-media ----------------

async def test_send_image_url_path(
    adapter: ChatlyticsAdapter, mock_router: respx.MockRouter
) -> None:
    mock_router.get("/health").mock(return_value=httpx.Response(200, json={}))
    media_route = mock_router.post("/api/v1/send-media").mock(
        return_value=httpx.Response(200, json={"success": True, "messageId": "m-img"})
    )
    await adapter.connect()
    result = await adapter.send_image(
        CHAT_ID, "https://cdn.test/x.png", caption="hi there"
    )
    assert result.success is True
    assert result.message_id == "m-img"

    body = _json.loads(media_route.calls.last.request.content)
    assert body["chatId"] == CHAT_ID
    assert body["mediaType"] == "image"
    assert body["mediaUrl"] == "https://cdn.test/x.png"
    assert body["caption"] == "hi there"
    assert media_route.calls.last.request.headers["authorization"] == EXPECTED_AUTH
    await adapter.disconnect()


# --- AC-2: send_voice yields mediaType=voice (NOT "audio") -----------

async def test_send_voice_yields_voice_message(
    adapter: ChatlyticsAdapter, mock_router: respx.MockRouter
) -> None:
    """Regression guard: WhatsApp voice bubbles vs media-player audio.

    Chatlytics distinguishes ``mediaType=voice`` (push-to-talk UX,
    waveform) from ``mediaType=audio`` (full media player).  This
    handler must always emit voice.
    """
    mock_router.get("/health").mock(return_value=httpx.Response(200, json={}))
    media_route = mock_router.post("/api/v1/send-media").mock(
        return_value=httpx.Response(200, json={"success": True, "messageId": "m-voice"})
    )
    await adapter.connect()
    result = await adapter.send_voice(CHAT_ID, "https://cdn.test/v.ogg")
    assert result.success is True

    body = _json.loads(media_route.calls.last.request.content)
    assert body["mediaType"] == "voice", (
        f"send_voice must emit mediaType=voice, got {body['mediaType']!r}"
    )
    assert body["mediaUrl"] == "https://cdn.test/v.ogg"
    await adapter.disconnect()


# --- AC-3: send_video -------------------------------------------------

async def test_send_video(
    adapter: ChatlyticsAdapter, mock_router: respx.MockRouter
) -> None:
    mock_router.get("/health").mock(return_value=httpx.Response(200, json={}))
    media_route = mock_router.post("/api/v1/send-media").mock(
        return_value=httpx.Response(200, json={"success": True, "messageId": "m-vid"})
    )
    await adapter.connect()
    result = await adapter.send_video(
        CHAT_ID, "https://cdn.test/clip.mp4", caption="check this clip"
    )
    assert result.success is True

    body = _json.loads(media_route.calls.last.request.content)
    assert body["mediaType"] == "video"
    assert body["mediaUrl"] == "https://cdn.test/clip.mp4"
    assert body["caption"] == "check this clip"
    await adapter.disconnect()


# --- AC-4: send_document with filename --------------------------------

async def test_send_document_with_filename(
    adapter: ChatlyticsAdapter, mock_router: respx.MockRouter
) -> None:
    mock_router.get("/health").mock(return_value=httpx.Response(200, json={}))
    media_route = mock_router.post("/api/v1/send-media").mock(
        return_value=httpx.Response(200, json={"success": True, "messageId": "m-doc"})
    )
    await adapter.connect()
    result = await adapter.send_document(
        CHAT_ID, "https://cdn.test/d.pdf", file_name="report.pdf"
    )
    assert result.success is True

    body = _json.loads(media_route.calls.last.request.content)
    assert body["mediaType"] == "file"
    assert body["filename"] == "report.pdf"
    assert body["mediaUrl"] == "https://cdn.test/d.pdf"
    await adapter.disconnect()


# --- AC-5: send_animation ---------------------------------------------

async def test_send_animation(
    adapter: ChatlyticsAdapter, mock_router: respx.MockRouter
) -> None:
    """Animation media type per Chatlytics convention.

    The Chatlytics gateway delivers gif/mp4 animations under
    ``mediaType=video`` (WhatsApp has no native GIF primitive -- clients
    render short MP4s in a loop).  We accept either ``"video"`` or
    ``"gif"`` for forward-compat with any future gateway change.
    """
    mock_router.get("/health").mock(return_value=httpx.Response(200, json={}))
    media_route = mock_router.post("/api/v1/send-media").mock(
        return_value=httpx.Response(200, json={"success": True, "messageId": "m-ani"})
    )
    await adapter.connect()
    result = await adapter.send_animation(
        CHAT_ID, "https://cdn.test/a.gif", caption="lol"
    )
    assert result.success is True

    body = _json.loads(media_route.calls.last.request.content)
    assert body["mediaType"] in {"video", "gif"}, (
        f"animation mediaType must be video or gif, got {body['mediaType']!r}"
    )
    assert body["mediaUrl"] == "https://cdn.test/a.gif"
    await adapter.disconnect()


# --- AC-6: send_image with a Path object uploads local bytes -----------

async def test_send_image_local_path_uploads_bytes(
    adapter: ChatlyticsAdapter,
    mock_router: respx.MockRouter,
    tmp_path: Path,
) -> None:
    """HERMES-15: send_image(Path) reads bytes, uploads, references URL.

    Renamed from ``test_send_image_file_uploads_local_bytes`` when the
    v2.0 ``send_image_file`` companion was deleted. The unified
    ``send_image`` method now accepts a ``Path`` object and routes
    through the auto-detection branch in ``_resolve_media_url``.
    """
    img_path = tmp_path / "x.png"
    fake_png = b"\x89PNG\r\n\x1a\n" + b"some-bytes-x123"
    img_path.write_bytes(fake_png)

    mock_router.get("/health").mock(return_value=httpx.Response(200, json={}))
    upload_route = mock_router.post("/api/v1/upload").mock(
        return_value=httpx.Response(
            200, json={"url": "https://cdn.test/uploaded.png"}
        )
    )
    media_route = mock_router.post("/api/v1/send-media").mock(
        return_value=httpx.Response(
            200, json={"success": True, "messageId": "m-imgfile"}
        )
    )

    await adapter.connect()
    # HERMES-15: was adapter.send_image_file(CHAT_ID, str(img_path), ...).
    result = await adapter.send_image(CHAT_ID, Path(img_path), caption="local")
    assert result.success is True
    assert result.message_id == "m-imgfile"

    # Upload was called with the file bytes.
    assert upload_route.called
    upload_req = upload_route.calls.last.request
    # multipart body must contain the raw file bytes.
    assert fake_png in upload_req.content
    assert upload_req.headers["authorization"] == EXPECTED_AUTH

    # Send-media references the returned URL.
    media_body = _json.loads(media_route.calls.last.request.content)
    assert media_body["mediaUrl"] == "https://cdn.test/uploaded.png"
    assert media_body["mediaType"] == "image"
    assert media_body["caption"] == "local"
    await adapter.disconnect()


# --- 04-MED-02 regression: file read off the event loop ---------------

async def test_send_image_local_path_reads_off_event_loop(
    adapter: ChatlyticsAdapter,
    mock_router: respx.MockRouter,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_resolve_media_url`` must read local files via ``asyncio.to_thread``.

    Regression for 04-REVIEW MED-02 (surfaced through 05-REVIEW MED-02):
    the local-file branch of ``_resolve_media_url`` previously called
    ``open()`` + ``fh.read()`` directly on the event loop thread, which
    blocks all other coroutines for the duration of the read on
    multi-MB files. HERMES-06 wraps the read in ``asyncio.to_thread``.

    HERMES-15: renamed from ``test_send_image_file_reads_off_event_loop``
    when the v2.0 ``send_image_file`` companion was deleted; the call
    now goes through ``adapter.send_image(Path(...))`` (Branch 2 of
    ``_resolve_media_url``) and still exercises the same
    ``asyncio.to_thread`` wrap.

    We assert the wrap is in effect by capturing the thread on which
    ``open()`` runs: it must NOT be the main thread (the loop thread).
    """
    import builtins
    import threading

    img_path = tmp_path / "x.png"
    fake_png = b"\x89PNG\r\n\x1a\n" + b"check-thread"
    img_path.write_bytes(fake_png)

    mock_router.get("/health").mock(return_value=httpx.Response(200, json={}))
    mock_router.post("/api/v1/upload").mock(
        return_value=httpx.Response(200, json={"url": "https://cdn.test/x.png"})
    )
    mock_router.post("/api/v1/send-media").mock(
        return_value=httpx.Response(200, json={"success": True, "messageId": "m-thr"})
    )

    main_thread = threading.main_thread()
    threads_seen: list[threading.Thread] = []
    real_open = builtins.open

    def spy_open(*args, **kwargs):  # noqa: ANN001 -- builtin shim
        # Record the thread that performed the open. ``_resolve_media_url``
        # is the only path under test that opens ``img_path`` -- other
        # opens (e.g. inside respx, pytest plumbing) MAY hit the main
        # thread legitimately, so only record opens against our fixture
        # file.
        if args and str(args[0]) == str(img_path):
            threads_seen.append(threading.current_thread())
        return real_open(*args, **kwargs)

    monkeypatch.setattr(builtins, "open", spy_open)

    await adapter.connect()
    # HERMES-15: was adapter.send_image_file(CHAT_ID, str(img_path), ...).
    result = await adapter.send_image(CHAT_ID, Path(img_path), caption="thr")
    assert result.success is True

    assert threads_seen, "open() was never called against the test file"
    for thr in threads_seen:
        assert thr is not main_thread, (
            f"open() on local media path ran on the main/event-loop thread "
            f"({thr!r}); _resolve_media_url must use asyncio.to_thread"
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

    async def test_url_string_passes_through_without_upload(
        self,
        adapter: ChatlyticsAdapter,
        mock_router: respx.MockRouter,
    ) -> None:
        """Branch 3: http(s):// string -> mediaUrl passthrough, NO upload call."""
        mock_router.get("/health").mock(return_value=httpx.Response(200, json={}))
        upload_route = mock_router.post("/api/v1/upload").mock(
            return_value=httpx.Response(200, json={"url": "should-not-be-used"})
        )
        media_route = mock_router.post("/api/v1/send-media").mock(
            return_value=httpx.Response(200, json={"success": True, "messageId": "m-url"})
        )

        await adapter.connect()
        result = await adapter.send_image(
            CHAT_ID, "https://cdn.test/cat.jpg", caption="url branch"
        )
        assert result.success is True
        assert not upload_route.called, (
            "URL passthrough must not trigger an upload"
        )

        body = _json.loads(media_route.calls.last.request.content)
        assert body["mediaUrl"] == "https://cdn.test/cat.jpg"
        await adapter.disconnect()

    async def test_path_object_uploads_via_multipart(
        self,
        adapter: ChatlyticsAdapter,
        mock_router: respx.MockRouter,
        tmp_path: Path,
    ) -> None:
        """Branch 2: explicit Path object -> multipart upload + URL reference."""
        img_path = tmp_path / "p.png"
        img_path.write_bytes(b"path-object-bytes")

        mock_router.get("/health").mock(return_value=httpx.Response(200, json={}))
        upload_route = mock_router.post("/api/v1/upload").mock(
            return_value=httpx.Response(200, json={"url": "https://cdn.test/p.png"})
        )
        mock_router.post("/api/v1/send-media").mock(
            return_value=httpx.Response(200, json={"success": True, "messageId": "m-path"})
        )

        await adapter.connect()
        result = await adapter.send_image(CHAT_ID, Path(img_path))
        assert result.success is True
        assert upload_route.called, "Path branch must upload via multipart"
        assert b"path-object-bytes" in upload_route.calls.last.request.content
        await adapter.disconnect()

    async def test_string_path_that_exists_uploads_via_multipart(
        self,
        adapter: ChatlyticsAdapter,
        mock_router: respx.MockRouter,
        tmp_path: Path,
    ) -> None:
        """Branch 4: ``str`` whose path exists -> multipart upload (parity with Path)."""
        img_path = tmp_path / "s.png"
        img_path.write_bytes(b"string-path-bytes")

        mock_router.get("/health").mock(return_value=httpx.Response(200, json={}))
        upload_route = mock_router.post("/api/v1/upload").mock(
            return_value=httpx.Response(200, json={"url": "https://cdn.test/s.png"})
        )
        mock_router.post("/api/v1/send-media").mock(
            return_value=httpx.Response(200, json={"success": True, "messageId": "m-str"})
        )

        await adapter.connect()
        result = await adapter.send_image(CHAT_ID, str(img_path))
        assert result.success is True
        assert upload_route.called, (
            "String-path-that-exists branch must upload via multipart"
        )
        assert b"string-path-bytes" in upload_route.calls.last.request.content
        await adapter.disconnect()

    async def test_unresolvable_string_returns_invalid_resource_error(
        self,
        adapter: ChatlyticsAdapter,
        mock_router: respx.MockRouter,
    ) -> None:
        """Branch 5: str that's neither URL nor existing path -> SendResult(False, ...).

        ``_resolve_media_url`` raises ``ValueError`` and
        ``_send_media_payload`` catches it and surfaces a clean
        failure dict to the caller instead of an uncaught raise.
        """
        mock_router.get("/health").mock(return_value=httpx.Response(200, json={}))
        # No upload route mocked — should never be called.

        await adapter.connect()
        result = await adapter.send_image(CHAT_ID, "not-a-url-not-a-path-zzz")
        assert result.success is False
        assert "Invalid resource" in (result.error or "")
        await adapter.disconnect()

    def test_send_image_file_symbol_is_gone(
        self, adapter: ChatlyticsAdapter
    ) -> None:
        """HERMES-15 acceptance criterion 4: send_image_file must NOT exist.

        Two-part check honoring the "clean break, no deprecation alias"
        intent — the base class ``BasePlatformAdapter`` defines a
        text-fallback default for ``send_image_file``, which would
        silently degrade a v2.x photo-send caller into a text bubble.
        The adapter explicitly blocks inherited access in
        ``__getattribute__`` to surface a clear migration error:

        1. Our class does NOT define ``send_image_file`` in its own
           ``__dict__`` — the v2.0 override is gone.
        2. Instance attribute access raises ``AttributeError`` with
           migration guidance — direct callers of
           ``adapter.send_image_file(...)`` see a clear error pointing
           at ``send_image`` instead of silently degrading.
        """
        assert "send_image_file" not in ChatlyticsAdapter.__dict__, (
            "send_image_file must be fully removed from the class "
            "(no shim, no alias) — found in ChatlyticsAdapter.__dict__"
        )
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
