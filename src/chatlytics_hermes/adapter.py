"""Chatlytics WhatsApp Platform Adapter for Hermes Agent (v0.14+).

This module defines the structural plugin contract for the Chatlytics
platform: a ``BasePlatformAdapter`` subclass plus the ``register(ctx)``
entry point that Hermes discovers via the ``hermes_agent.plugins`` entry
point group declared in ``pyproject.toml``.

HERMES-01 scaffolded the contract.  HERMES-02 (this phase) fills in
``connect``, ``disconnect``, ``send``, ``send_typing``, and
``get_chat_info`` against the Chatlytics REST gateway using a shared
``httpx.AsyncClient`` instance owned by the adapter.

Future phases:

- HERMES-03 -- embedded aiohttp inbound webhook server inside
  ``connect`` / ``disconnect`` (inbound transport migration)
- HERMES-04 -- media handlers (``send_image``, ``send_voice``,
  ``send_video``, ``send_document``, ``send_animation``,
  ``send_image_file``) and ``_keep_typing`` heartbeat
- HERMES-05 -- full Chatlytics tool surface via ``ctx.register_tool``

The upstream import block is wrapped in ``try/except ImportError`` so
that ``from chatlytics_hermes import register`` works in environments
without ``hermes-agent`` installed (HERMES-01 acceptance criterion 1).
The ``ChatlyticsAdapter`` class only raises when instantiated without
the runtime dependency present.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import mimetypes
import os
from typing import Any, Dict, Optional, Union

import httpx
from aiohttp import web

from .client import ChatlyticsClient, USER_AGENT
from .inbound import make_health_handler, make_webhook_handler


# Chatlytics ``mediaType`` field uses platform-specific tokens that do
# not always match the BasePlatformAdapter handler name.  Keeping this
# table near the handlers documents the mapping in one place:
#
#   voice  -> "voice"  (NOT "audio" -- voice bubbles vs media-player audio)
#   document -> "file" (Chatlytics gateway calls all generic uploads files)
#   animation -> "video" (gif/mp4 animations are delivered as inline video)
_MEDIA_TYPE_MAP: Dict[str, str] = {
    "image": "image",
    "voice": "voice",
    "video": "video",
    "document": "file",
    "animation": "video",
    "image_file": "image",
}


def _guess_content_type(filename: Optional[str]) -> str:
    """Best-effort MIME guess for upload payloads."""
    if not filename:
        return "application/octet-stream"
    guess, _ = mimetypes.guess_type(filename)
    return guess or "application/octet-stream"

try:
    from gateway.platforms.base import BasePlatformAdapter, SendResult
    from gateway.config import Platform, PlatformConfig

    _HERMES_AVAILABLE = True
except ImportError:  # hermes-agent not installed (e.g. acceptance criterion 1)
    BasePlatformAdapter = object  # type: ignore[assignment, misc]
    SendResult = None  # type: ignore[assignment]
    Platform = None  # type: ignore[assignment]
    PlatformConfig = None  # type: ignore[assignment]

    _HERMES_AVAILABLE = False


logger = logging.getLogger("chatlytics_hermes.adapter")


class ChatlyticsConnectError(RuntimeError):
    """Raised when the adapter cannot complete its health check on connect."""


class ChatlyticsAdapter(BasePlatformAdapter):  # type: ignore[misc]
    """Async Chatlytics adapter implementing the ``BasePlatformAdapter`` contract.

    Instantiated by the ``adapter_factory`` passed to
    ``ctx.register_platform`` in :func:`register`.  HERMES-02 wires up
    the outbound text + control surface (connect, disconnect, send,
    send_typing, get_chat_info).  Inbound webhook + media handlers land
    in HERMES-03 / HERMES-04.
    """

    def __init__(self, config: "PlatformConfig", **kwargs: Any) -> None:
        if not _HERMES_AVAILABLE:
            raise RuntimeError(
                "hermes-agent>=0.14,<0.15 must be installed to instantiate "
                "ChatlyticsAdapter. Install with: "
                "pip install 'hermes-agent>=0.14,<0.15'"
            )

        super().__init__(config=config, platform=Platform("chatlytics"))

        extra: Dict[str, Any] = getattr(config, "extra", {}) or {}

        # Connection settings (env vars override config.yaml ``extra`` block).
        self.base_url: str = os.getenv("CHATLYTICS_BASE_URL") or extra.get("base_url", "")
        self.api_key: str = os.getenv("CHATLYTICS_API_KEY") or extra.get("api_key", "")
        self.account_id: Optional[str] = (
            os.getenv("CHATLYTICS_ACCOUNT_ID") or extra.get("account_id")
        )

        # Webhook server settings (HERMES-03).
        self.webhook_host: str = (
            os.getenv("CHATLYTICS_WEBHOOK_HOST") or extra.get("webhook_host", "0.0.0.0")
        )
        try:
            self.webhook_port: int = int(
                os.getenv("CHATLYTICS_WEBHOOK_PORT") or extra.get("webhook_port", 8765)
            )
        except (TypeError, ValueError):
            self.webhook_port = 8765
        self.webhook_path: str = (
            os.getenv("CHATLYTICS_WEBHOOK_PATH") or extra.get("webhook_path", "/webhook")
        )
        self.webhook_secret: Optional[str] = (
            os.getenv("CHATLYTICS_WEBHOOK_SECRET") or extra.get("webhook_secret")
        )

        # Cron / notification default channel (used by HERMES-04).
        self.home_channel: Optional[str] = (
            os.getenv("CHATLYTICS_HOME_CHANNEL") or extra.get("home_channel")
        )

        # HTTP client is constructed lazily on ``connect()`` so that
        # misconfigured adapters (empty base_url/api_key) raise only at
        # connect time, never at registration time.
        self._client: Optional[ChatlyticsClient] = None

        # Embedded aiohttp webhook server -- started in ``connect()``,
        # shut down in ``disconnect()`` via ``runner.cleanup()``.
        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.TCPSite] = None

    @property
    def name(self) -> str:
        return "Chatlytics"

    # --- Lifecycle (HERMES-02) ---------------------------------------------

    async def connect(self) -> bool:
        """Connect to the Chatlytics gateway.

        1. Construct ``self._client`` (httpx).
        2. Issue ``GET {base_url}/health`` against the Chatlytics gateway
           -- non-200 raises :class:`ChatlyticsConnectError`.
        3. Start the embedded aiohttp webhook server bound to
           ``(self.webhook_host, self.webhook_port)`` with routes
           ``POST {webhook_path}`` (default ``/webhook``) and
           ``GET /health`` -- a bind failure raises
           :class:`ChatlyticsConnectError` and tears down the httpx
           client so the next connect() retries from a clean slate.
        """
        if self._client is None:
            self._client = ChatlyticsClient(
                base_url=self.base_url,
                api_key=self.api_key,
            )

        logger.info(
            "Connecting to Chatlytics gateway at %s",
            self._client.base_url,
        )
        try:
            response = await self._client.get("/health")
        except httpx.RequestError as exc:
            # Transport-level error -- close client so the next connect()
            # can retry from a clean slate.
            await self._client.aclose()
            self._client = None
            raise ChatlyticsConnectError(
                f"Chatlytics health check failed: {exc}"
            ) from exc

        if response.status_code != 200:
            await self._client.aclose()
            self._client = None
            raise ChatlyticsConnectError(
                f"Chatlytics health check returned status "
                f"{response.status_code}: {response.text[:200]}"
            )

        # --- Inbound webhook server (HERMES-03) -----------------------
        # Idempotency guard: if a previous connect() already started the
        # aiohttp runner and a caller invokes connect() again without
        # disconnect() in between (e.g. plugin reload, retry shim), skip
        # the rebind to avoid leaking the existing runner under a fresh
        # ``self._runner`` reference (MED-01 from 03-REVIEW).
        if self._runner is not None:
            logger.debug(
                "connect(): webhook server already running on %s:%d; skipping startup",
                self.webhook_host,
                self.webhook_port,
            )
            self._running = True
            return True

        try:
            app = web.Application()
            app.router.add_post(self.webhook_path, make_webhook_handler(self))
            app.router.add_get("/health", make_health_handler())

            self._runner = web.AppRunner(app)
            await self._runner.setup()
            self._site = web.TCPSite(
                self._runner, self.webhook_host, self.webhook_port
            )
            await self._site.start()
            logger.info(
                "Chatlytics webhook server listening on %s:%d%s",
                self.webhook_host,
                self.webhook_port,
                self.webhook_path,
            )
        except OSError as exc:
            # Bind error -- tear down everything cleanly so the next
            # connect() can retry from a known-good state.
            if self._runner is not None:
                try:
                    await self._runner.cleanup()
                except Exception:  # noqa: BLE001 -- never let teardown raise
                    logger.exception("aiohttp runner cleanup raised; continuing")
                self._runner = None
                self._site = None
            await self._client.aclose()
            self._client = None
            raise ChatlyticsConnectError(
                f"Chatlytics webhook server failed to bind "
                f"{self.webhook_host}:{self.webhook_port}: {exc}"
            ) from exc

        self._running = True
        return True

    async def disconnect(self) -> None:
        """Stop the aiohttp webhook server and close the httpx client.  Idempotent.

        Order matters: shut down the aiohttp server first (so no
        in-flight request handlers can issue further outbound calls
        through ``self._client``), then close the shared httpx client.
        """
        if self._runner is not None:
            logger.info("Stopping Chatlytics webhook server")
            try:
                await self._runner.cleanup()
            except Exception:  # noqa: BLE001 -- never let teardown raise
                logger.exception("aiohttp runner cleanup raised; continuing")
            self._runner = None
            self._site = None

        if self._client is not None:
            logger.info("Disconnecting from Chatlytics gateway")
            await self._client.aclose()
            self._client = None

        self._running = False

    # --- Outbound (HERMES-02) ---------------------------------------------

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:  # type: ignore[name-defined]
        """POST /api/v1/send with ``{chatId, text, [accountId], [replyTo], ...}``.

        Returns a ``SendResult`` with ``success=True`` and the gateway's
        reported ``messageId`` on 200, otherwise ``success=False`` with
        the error in ``error`` and the raw status + body in
        ``raw_response``.
        """
        if self._client is None:
            return SendResult(
                success=False,
                error="Adapter not connected: call connect() before send()",
            )

        body: Dict[str, Any] = {
            "chatId": chat_id,
            "text": content,
        }
        if self.account_id:
            body["accountId"] = self.account_id
        if reply_to:
            body["replyTo"] = reply_to
        if metadata:
            # Merge non-conflicting metadata keys into the request body so
            # the gateway can accept platform-specific extras (e.g.
            # quoted-message context, link previews) without the adapter
            # owning a schema for them.  Reserved keys cannot be
            # overridden by the caller.
            for key, value in metadata.items():
                if key not in {"chatId", "text", "accountId", "replyTo"}:
                    body[key] = value

        logger.debug("send -> /api/v1/send chatId=%s len=%d", chat_id, len(content))

        try:
            response = await self._client.post("/api/v1/send", json=body)
        except httpx.RequestError as exc:
            return SendResult(
                success=False,
                error=f"Transport error: {exc}",
                retryable=True,
            )

        # Tolerate non-JSON bodies for diagnostic surface area.
        try:
            payload: Any = response.json()
        except Exception:  # noqa: BLE001 -- json.JSONDecodeError + httpx variants
            payload = {"raw_text": response.text}

        if (
            response.status_code == 200
            and isinstance(payload, dict)
            and payload.get("success", True)
        ):
            return SendResult(
                success=True,
                message_id=payload.get("messageId"),
                raw_response=payload,
            )

        error_msg = (
            payload.get("error") if isinstance(payload, dict) else None
        ) or f"HTTP {response.status_code}"
        return SendResult(
            success=False,
            error=error_msg,
            raw_response=payload,
            retryable=response.status_code >= 500,
        )

    async def send_typing(
        self,
        chat_id: str,
        metadata: Optional[Dict[str, Any]] = None,
        duration: float = 3.0,
    ) -> None:
        """POST /api/v1/typing with ``{chatId, duration}``.

        Compatible with the base class signature (which uses ``metadata``)
        and the Chatlytics-specific ``duration`` knob (default 3.0 s).
        ``metadata`` is accepted for API compatibility; the adapter
        currently uses only ``duration``.  Errors are logged and
        swallowed -- typing is a UX hint, not a critical path.
        """
        if self._client is None:
            return

        try:
            response = await self._client.post(
                "/api/v1/typing",
                json={"chatId": chat_id, "duration": float(duration)},
            )
            if response.status_code != 200:
                logger.warning(
                    "send_typing returned %s for chat %s",
                    response.status_code,
                    chat_id,
                )
        except httpx.RequestError as exc:
            logger.warning("send_typing transport error: %s", exc)

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        """GET /api/v1/chat?chatId={id} and return the JSON body as dict.

        Returns ``{}`` if the adapter is not connected or the gateway
        responds with a non-200 / non-JSON body.  Callers may rely on
        the dict having the keys documented in the Chatlytics gateway
        contract (``name``, ``phone``, ``isGroup``, ...) when status
        was 200 -- the adapter does not validate the schema beyond
        ``isinstance(payload, dict)``.
        """
        if self._client is None:
            return {}

        try:
            response = await self._client.get(
                "/api/v1/chat",
                params={"chatId": chat_id},
            )
        except httpx.RequestError as exc:
            logger.warning("get_chat_info transport error: %s", exc)
            return {}

        if response.status_code != 200:
            logger.warning(
                "get_chat_info returned %s for chat %s",
                response.status_code,
                chat_id,
            )
            return {}

        try:
            payload = response.json()
        except Exception:  # noqa: BLE001
            return {}

        return payload if isinstance(payload, dict) else {}

    # --- Media handlers (HERMES-04) ---------------------------------------

    async def _resolve_media_url(
        self,
        resource: Union[str, bytes, bytearray],
        *,
        upload_filename: Optional[str] = None,
        content_type: Optional[str] = None,
    ) -> str:
        """Resolve a media resource to a remotely-hosted URL.

        Three input shapes are accepted:

        - ``http(s)://...`` string -- returned as-is (URL path)
        - ``bytes`` / ``bytearray`` -- uploaded to ``/api/v1/upload``,
          the returned ``{url}`` is used as ``mediaUrl``
        - any other string -- treated as a local file path; the file is
          read in full and uploaded as above

        Raises :class:`RuntimeError` when the upload endpoint does not
        return a ``url`` field in its JSON body; callers wrap this into
        a ``SendResult(success=False, error=...)``.
        """
        assert self._client is not None  # caller guards

        if isinstance(resource, (bytes, bytearray)):
            name = upload_filename or "upload.bin"
            ctype = content_type or _guess_content_type(name)
            upload_response = await self._client.upload_file(
                filename=name, content=bytes(resource), content_type=ctype
            )
        elif isinstance(resource, str) and resource.startswith(("http://", "https://")):
            return resource
        else:
            # Local file path.
            path = str(resource)
            with open(path, "rb") as fh:
                content = fh.read()
            name = upload_filename or os.path.basename(path) or "upload.bin"
            ctype = content_type or _guess_content_type(name)
            upload_response = await self._client.upload_file(
                filename=name, content=content, content_type=ctype
            )

        if upload_response.status_code != 200:
            raise RuntimeError(
                f"Upload to /api/v1/upload returned HTTP {upload_response.status_code}"
            )

        try:
            payload = upload_response.json()
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"Upload response was not valid JSON: {exc}"
            ) from exc

        if not isinstance(payload, dict) or "url" not in payload:
            raise RuntimeError(
                "Upload response missing 'url' field; "
                f"got keys: {list(payload.keys()) if isinstance(payload, dict) else type(payload).__name__}"
            )
        return payload["url"]

    def _make_send_result(self, response: httpx.Response) -> "SendResult":
        """Convert a Chatlytics send-media response into a SendResult.

        Shared by all 6 media handlers so the success/error mapping
        matches :meth:`send` exactly -- 200 + ``success: True`` (or
        absent) -> success; 4xx / 5xx / ``success: False`` -> failure,
        with ``retryable=True`` on 5xx for the base-class retry shim.
        """
        try:
            payload: Any = response.json()
        except Exception:  # noqa: BLE001
            payload = {"raw_text": response.text}

        if (
            response.status_code == 200
            and isinstance(payload, dict)
            and payload.get("success", True)
        ):
            return SendResult(
                success=True,
                message_id=payload.get("messageId"),
                raw_response=payload,
            )

        error_msg = (
            payload.get("error") if isinstance(payload, dict) else None
        ) or f"HTTP {response.status_code}"
        return SendResult(
            success=False,
            error=error_msg,
            raw_response=payload,
            retryable=response.status_code >= 500,
        )

    async def _send_media_payload(
        self,
        chat_id: str,
        media_kind: str,
        resource: Union[str, bytes, bytearray],
        *,
        caption: Optional[str] = None,
        filename: Optional[str] = None,
        content_type: Optional[str] = None,
    ) -> "SendResult":
        """Resolve resource -> URL, build payload, POST /api/v1/send-media.

        Shared body for the 6 media handlers below.  ``media_kind`` is
        one of the keys in :data:`_MEDIA_TYPE_MAP` and gets translated
        to the Chatlytics ``mediaType`` wire value.
        """
        if self._client is None:
            return SendResult(
                success=False,
                error="Adapter not connected: call connect() before sending media",
            )

        try:
            media_url = await self._resolve_media_url(
                resource,
                upload_filename=filename,
                content_type=content_type,
            )
        except FileNotFoundError as exc:
            return SendResult(success=False, error=f"File not found: {exc}")
        except OSError as exc:
            return SendResult(success=False, error=f"File read error: {exc}")
        except RuntimeError as exc:
            return SendResult(success=False, error=str(exc))
        except httpx.RequestError as exc:
            return SendResult(
                success=False,
                error=f"Upload transport error: {exc}",
                retryable=True,
            )

        body: Dict[str, Any] = {
            "chatId": chat_id,
            "mediaType": _MEDIA_TYPE_MAP[media_kind],
            "mediaUrl": media_url,
        }
        if caption:
            body["caption"] = caption
        if filename and media_kind in {"document", "image_file"}:
            body["filename"] = filename

        logger.debug(
            "send_media (%s) -> /api/v1/send-media chatId=%s",
            media_kind,
            chat_id,
        )

        try:
            response = await self._client.send_media(body)
        except httpx.RequestError as exc:
            return SendResult(
                success=False,
                error=f"send-media transport error: {exc}",
                retryable=True,
            )

        return self._make_send_result(response)

    async def send_image(
        self,
        chat_id: str,
        image_url: Union[str, bytes, bytearray],
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "SendResult":
        """Send an image as a native WhatsApp photo attachment.

        Accepts either an ``http(s)://...`` URL (used as ``mediaUrl``
        directly) or raw ``bytes`` (uploaded to ``/api/v1/upload``
        first).  ``caption`` is optional.  ``reply_to`` / ``metadata``
        are accepted for base-class signature parity but currently
        ignored -- Chatlytics's send-media endpoint does not expose
        per-message reply context (HERMES-05 may revisit).
        """
        return await self._send_media_payload(
            chat_id, "image", image_url, caption=caption
        )

    async def send_animation(
        self,
        chat_id: str,
        animation_url: Union[str, bytes, bytearray],
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "SendResult":
        """Send an animated GIF / short MP4 as inline video.

        Chatlytics's gateway delivers gif/mp4 animations under
        ``mediaType=video`` (the WhatsApp protocol has no native GIF
        primitive; clients render short MP4s in a loop instead).
        """
        return await self._send_media_payload(
            chat_id, "animation", animation_url, caption=caption
        )

    async def send_voice(
        self,
        chat_id: str,
        audio_path: Union[str, bytes, bytearray],
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> "SendResult":
        """Send an audio file as a WhatsApp voice bubble (NOT media-player audio).

        ``mediaType=voice`` deliberately -- the gateway distinguishes
        voice messages (push-to-talk UX, waveform, no controls) from
        ``mediaType=audio`` (full media player, scrubber, title).  This
        handler always emits voice; callers needing the media-player
        experience should use :meth:`send_document` with an
        appropriate filename.
        """
        # caption is accepted for signature parity; voice bubbles
        # technically support it but the gateway hides captions in some
        # clients.  Pass it through anyway so the gateway can decide.
        return await self._send_media_payload(
            chat_id, "voice", audio_path, caption=caption
        )

    async def send_video(
        self,
        chat_id: str,
        video_path: Union[str, bytes, bytearray],
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> "SendResult":
        """Send a video as inline playable media."""
        return await self._send_media_payload(
            chat_id, "video", video_path, caption=caption
        )

    async def send_document(
        self,
        chat_id: str,
        file_path: Union[str, bytes, bytearray],
        caption: Optional[str] = None,
        file_name: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> "SendResult":
        """Send a generic file as a downloadable WhatsApp document.

        ``file_name`` is the displayed attachment name in the recipient's
        chat -- it does NOT have to match the local path's basename.
        When omitted and ``file_path`` is a local path or bytes, we fall
        back to the basename / a generic ``upload.bin``.
        """
        # base signature uses ``file_name``; we ALSO accept ``filename``
        # via kwargs because every other handler in this module uses the
        # shorter spelling.
        if file_name is None and "filename" in kwargs:
            file_name = kwargs.pop("filename")
        return await self._send_media_payload(
            chat_id,
            "document",
            file_path,
            caption=caption,
            filename=file_name,
        )

    async def send_image_file(
        self,
        chat_id: str,
        image_path: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> "SendResult":
        """Send a local image file as a native WhatsApp photo.

        Reads ``image_path`` from disk, uploads the bytes via
        ``/api/v1/upload``, then sends the returned URL via
        ``/api/v1/send-media`` with ``mediaType=image``.
        """
        # Even if the caller hands us a URL by mistake, treat it as a
        # path-or-bytes case -- send_image is the URL-first handler.
        filename = os.path.basename(str(image_path)) or "upload.png"
        return await self._send_media_payload(
            chat_id,
            "image_file",
            image_path,
            caption=caption,
            filename=filename,
        )

    # --- UX polish (HERMES-04) --------------------------------------------

    @contextlib.asynccontextmanager
    async def _keep_typing(self, chat_id: str, interval: float = 30.0):
        """30 s typing-bubble heartbeat for long-running tool handlers.

        Usage::

            async with adapter._keep_typing(chat_id):
                result = await long_running_tool()

        Fires ``send_typing(chat_id, duration=30.0)`` immediately so the
        bubble appears without waiting ``interval`` seconds, then keeps
        a background task alive that re-issues the typing request every
        ``interval`` seconds.  The background task is cancelled cleanly
        on context-manager exit (even if the body raises).

        ``interval`` defaults to 30 s to match the WhatsApp ``typing``
        TTL; long-running handlers (multi-minute LLM calls, image
        generation pipelines) keep the bubble alive without flooding
        the gateway.  Tests override ``interval`` to a small value to
        observe heartbeats without 30 s real-time sleeps.

        Errors from ``send_typing`` are swallowed at debug level --
        typing is a UX hint, not a critical path.  A failed heartbeat
        never aborts the wrapped body.
        """
        async def _beat() -> None:
            try:
                while True:
                    await asyncio.sleep(interval)
                    try:
                        await self.send_typing(chat_id, duration=30.0)
                    except asyncio.CancelledError:
                        raise
                    except Exception:  # noqa: BLE001
                        logger.debug(
                            "send_typing heartbeat raised; continuing",
                            exc_info=True,
                        )
            except asyncio.CancelledError:
                # Normal cancellation on context-manager exit.
                pass

        # Initial fire so the bubble appears immediately -- otherwise
        # the user sees nothing until ``interval`` elapses.
        try:
            await self.send_typing(chat_id, duration=30.0)
        except Exception:  # noqa: BLE001
            logger.debug(
                "send_typing initial fire raised; continuing",
                exc_info=True,
            )

        task = asyncio.create_task(_beat())
        try:
            yield
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001 -- never leak teardown errors
                logger.debug(
                    "_keep_typing teardown raised; continuing",
                    exc_info=True,
                )


# --- Cron support (HERMES-04) ----------------------------------------------


def _env_enablement() -> Optional[Dict[str, Any]]:
    """Seed ``PlatformConfig.extra`` from env vars during gateway config load.

    Returns ``{"home_channel": {...}}`` when ``CHATLYTICS_HOME_CHANNEL``
    is set, so the gateway's ``get_connected_platforms()`` surfaces a
    Chatlytics entry without instantiating the adapter.  Returns
    ``None`` when no home channel is configured (the platform stays
    invisible to ``hermes gateway status`` until a config arrives).

    Mirrors the IRC pattern at
    ``/tmp/hermes-ref-v0.14.0/plugins/platforms/irc/adapter.py:651``.
    """
    home = (os.getenv("CHATLYTICS_HOME_CHANNEL") or "").strip()
    if not home:
        return None
    return {
        "home_channel": {
            "chat_id": home,
            "name": os.getenv("CHATLYTICS_HOME_CHANNEL_NAME", home),
        }
    }


async def _standalone_send(text: str, **kwargs: Any) -> Dict[str, Any]:
    """Out-of-process cron delivery for ``deliver=chatlytics`` jobs.

    Called by Hermes when ``hermes cron`` runs in a separate process
    from ``hermes gateway`` -- without this hook, scheduled deliveries
    fail with "No live adapter for platform chatlytics".

    Opens a fresh ``httpx.AsyncClient`` (no shared state with any live
    adapter), POSTs to ``/api/v1/send`` with ``chatId`` taken from
    ``CHATLYTICS_HOME_CHANNEL``, closes the client.  Returns a dict in
    the shape Hermes's cron pipeline expects:

    - ``{"success": True, "messageId": "...", ...}`` on 200
    - ``{"success": False, "error": "...", "raw_response": ...}`` otherwise
    - ``{"error": "..."}`` when env config is missing (no ``success`` key)

    Accepts arbitrary ``**kwargs`` for forward-compat with Hermes's
    cron call shape (e.g. ``thread_id``, ``media_files``); none are
    currently meaningful for the basic Chatlytics text-send path.
    """
    base_url = (os.getenv("CHATLYTICS_BASE_URL") or "").strip()
    api_key = (os.getenv("CHATLYTICS_API_KEY") or "").strip()
    home_channel = (os.getenv("CHATLYTICS_HOME_CHANNEL") or "").strip()

    if not (base_url and api_key and home_channel):
        return {
            "error": (
                "Chatlytics standalone send: CHATLYTICS_BASE_URL, "
                "CHATLYTICS_API_KEY, and CHATLYTICS_HOME_CHANNEL must all be set"
            ),
        }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }
    try:
        async with httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=30.0,
            headers=headers,
        ) as client:
            response = await client.post(
                "/api/v1/send",
                json={"chatId": home_channel, "text": text},
            )
    except httpx.RequestError as exc:
        return {"success": False, "error": f"Transport error: {exc}"}

    try:
        payload = response.json()
    except Exception:  # noqa: BLE001
        payload = {"raw_text": response.text}

    if (
        response.status_code == 200
        and isinstance(payload, dict)
        and payload.get("success", True)
    ):
        # Spread payload AFTER success=True so a payload-side ``success``
        # value (some endpoints echo) does not override our derived flag.
        result: Dict[str, Any] = {"success": True}
        if isinstance(payload, dict):
            result.update(payload)
            result["success"] = True
        return result

    return {
        "success": False,
        "error": (
            payload.get("error") if isinstance(payload, dict) else None
        ) or f"HTTP {response.status_code}",
        "raw_response": payload,
    }


def register(ctx: Any) -> None:
    """Plugin entry point: discovered by Hermes via the ``hermes_agent.plugins``
    entry point group in ``pyproject.toml``.

    HERMES-01 registers the minimum platform surface required to load
    the plugin.  Subsequent phases extend the ``register_platform`` call
    with additional hooks:

    - HERMES-03 adds ``env_enablement_fn`` and the webhook config bridge
    - HERMES-04 adds ``cron_deliver_env_var`` + ``standalone_sender_fn``
    - HERMES-05 calls ``ctx.register_tool`` for each Chatlytics action
    """
    ctx.register_platform(
        name="chatlytics",
        label="Chatlytics WhatsApp",
        adapter_factory=lambda cfg: ChatlyticsAdapter(cfg),
        required_env=["CHATLYTICS_BASE_URL", "CHATLYTICS_API_KEY"],
        install_hint=(
            "pip install -e git+https://github.com/omernesh/chatlytics-hermes.git"
        ),
        emoji="\U0001f4ac",  # speech bubble -- escape form keeps source ASCII-safe
        platform_hint=(
            "You are chatting via Chatlytics, a WhatsApp gateway. Messages "
            "support WhatsApp-flavored markdown (bold *text*, italic _text_, "
            "strikethrough ~text~, monospace ```text```). Keep responses "
            "conversational and concise; long messages are split by the gateway."
        ),
    )
