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

Log level convention (HERMES-09)
--------------------------------

This module (and the rest of the plugin) follows a consistent log-level
convention so operators can tune verbosity without re-reading source:

- **DEBUG**: steady-state telemetry; expected transient hiccups; swallowed-by-design
  exceptions on non-critical paths (typing heartbeats, JSON-decode fallbacks,
  ``ctx`` accessor probes).  Off by default in production logging configs.
- **INFO**: lifecycle events (connect, disconnect, webhook server start/stop).
- **WARNING**: operator-actionable degraded states (``_keep_typing`` initial-fire
  failure, dropped reserved-metadata keys, bind errors, HMAC reject, webhook reject,
  ``get_chat_info`` non-200 lookup).
- **ERROR / EXCEPTION**: dispatch failures and teardown failures.

UX-hint paths (notably ``send_typing``) intentionally log at DEBUG even on
transport / non-200 failures: typing is a cosmetic affordance and a flapping
gateway should not flood operator logs with hundreds of WARNING lines.  The
``_keep_typing`` initial-fire WARNING (in ``_keep_typing``) is the canonical
operator-actionable surface for sustained typing-pipeline failure.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import mimetypes
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

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


# HERMES-10 (03-LOW-01 + PR-MED-01): webhook_path validation at __init__.
# C0 + DEL control range; rejected so adapters fail fast on copy-paste glitches
# and possible injection attempts. Module-level constant so the validator and
# its tests share one source of truth.
_CONTROL_CHARS: str = "".join(chr(i) for i in range(32)) + "\x7f"


def _validate_webhook_path(path: Any) -> None:
    """Validate ``webhook_path`` at adapter ``__init__``.

    Raises :class:`ValueError` with a clear message identifying the
    offending value and the rule violated. Closes:

    - **03-LOW-01** — v2.0 audit: ``webhook_path`` was not validated; a
      missing leading slash silently sent the aiohttp router into a
      route shape it does not honor (``add_post("webhook", ...)``
      registers ``/webhook`` on some aiohttp versions and raises on
      others — non-portable).
    - **PR-review MED-01** — route collision when
      ``CHATLYTICS_WEBHOOK_PATH=/health``. The embedded webhook server
      registers ``GET /health`` for liveness; allowing
      ``POST /health`` to be the inbound webhook path means a curl
      probe and a real inbound delivery share a URL, which silently
      mixes traffic and confuses operators.

    Rules (any violation raises):

    1. Path is a non-empty string AND has no leading/trailing
       whitespace (operators copy-paste paths with stray newlines or
       spaces often enough that silently accepting them defeats the
       validator's fail-fast contract — aiohttp's ``UrlDispatcher``
       does NOT strip route paths, so ``"  /webhook  "`` would either
       404 silently or raise during route compilation depending on
       aiohttp version).
    2. Starts with ``/``.
    3. No C0 or DEL control characters.
    4. No ``..`` segments (path-traversal smell).
    5. No ``?`` or ``#`` (URL queries/fragments belong to the client
       request, not the route registration).
    6. Not equal to ``/health`` (reserved for the health endpoint).

    Fail-fast at ``__init__`` matches Hermes conventions; the adapter
    deliberately does NOT silently rewrite the path. Operators see the
    error immediately at gateway start and can correct config.
    """
    if not isinstance(path, str):
        raise ValueError(
            "CHATLYTICS_WEBHOOK_PATH must be a string; got "
            f"{type(path).__name__}"
        )
    if not path:
        raise ValueError(
            "CHATLYTICS_WEBHOOK_PATH must be a non-empty string"
        )
    # WARNING-01 fix (10-REVIEW): reject whitespace-padded inputs
    # explicitly. Previously the validator stripped before checking and
    # passed inputs like "  /webhook  " through with whitespace intact,
    # which then broke aiohttp route registration silently.
    if path != path.strip():
        raise ValueError(
            "CHATLYTICS_WEBHOOK_PATH must not have leading or trailing "
            f"whitespace; got {path[:80]!r}"
        )
    if not path.strip():
        # Defensive (path was all-whitespace) -- caught by the previous
        # equality check, but kept for symmetry with the original.
        raise ValueError(
            "CHATLYTICS_WEBHOOK_PATH must be a non-empty string"
        )
    if not path.startswith("/"):
        raise ValueError(
            "CHATLYTICS_WEBHOOK_PATH must start with '/'; got "
            f"{path[:80]!r}"
        )
    if any(c in _CONTROL_CHARS for c in path):
        raise ValueError(
            "CHATLYTICS_WEBHOOK_PATH must not contain control characters; "
            f"got {path[:80]!r}"
        )
    if ".." in path:
        raise ValueError(
            "CHATLYTICS_WEBHOOK_PATH must not contain '..' segments; "
            f"got {path[:80]!r}"
        )
    if "?" in path or "#" in path:
        raise ValueError(
            "CHATLYTICS_WEBHOOK_PATH must not contain '?' or '#'; "
            f"got {path[:80]!r}"
        )
    if path == "/health":
        raise ValueError(
            "CHATLYTICS_WEBHOOK_PATH cannot be '/health' (reserved for the "
            "health endpoint route registered by the embedded webhook server)"
        )


def _coerce_success_payload(
    status_code: int,
    payload: Any,
) -> Tuple[bool, Optional[str]]:
    """Single source of truth for Chatlytics gateway response success derivation.

    Returns ``(success, error_msg)`` where ``error_msg`` is non-None
    only when ``success`` is False.

    Coercion rules (resolves MD-01 from the v2.0 milestone code review):

    - HTTP 4xx/5xx -> ``(False, payload.error or "HTTP {status}")``
    - HTTP 2xx + ``payload.get("success") is False`` ->
      ``(False, payload.error or "gateway reported success=false")``
    - HTTP 2xx + ``payload.success`` truthy/absent / non-dict payload ->
      ``(True, None)``

    Used by :meth:`ChatlyticsAdapter._make_send_result`,
    :func:`_standalone_send`, and the tool-layer ``_post``/``_get``
    helpers (via :mod:`chatlytics_hermes.tools`) so all three sites
    agree on the contract.  Eliminates the 200+``success:false``
    divergence flagged in the milestone code review (MD-01).
    """
    if status_code >= 400:
        err = (
            payload.get("error") if isinstance(payload, dict) else None
        ) or f"HTTP {status_code}"
        return False, err
    if isinstance(payload, dict) and payload.get("success") is False:
        err = payload.get("error") or "gateway reported success=false"
        return False, err
    return True, None

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
        # HERMES-10 (03-LOW-01 + PR-MED-01): validate the resolved value so
        # invalid configs raise at gateway startup instead of failing silently
        # at the aiohttp route registration in connect().
        _validate_webhook_path(self.webhook_path)
        self.webhook_secret: Optional[str] = (
            os.getenv("CHATLYTICS_WEBHOOK_SECRET") or extra.get("webhook_secret")
        )

        # Cron / notification default channel (used by HERMES-04).
        self.home_channel: Optional[str] = (
            os.getenv("CHATLYTICS_HOME_CHANNEL") or extra.get("home_channel")
        )

        # HI-01 fix (HERMES-08): path allowlist for ``filePath`` uploads.
        # Default-deny — when unset, ALL local-file uploads are rejected.
        # Use OS pathsep separator (``:`` on POSIX, ``;`` on Windows).
        _roots_raw: str = (
            os.getenv("CHATLYTICS_UPLOAD_ALLOWED_ROOTS")
            or str(extra.get("upload_allowed_roots") or "")
        )
        self.upload_allowed_roots: List[Path] = []
        if _roots_raw:
            for entry in _roots_raw.split(os.pathsep):
                entry = entry.strip()
                if not entry:
                    continue
                try:
                    self.upload_allowed_roots.append(
                        Path(entry).expanduser().resolve()
                    )
                except (OSError, RuntimeError) as exc:
                    logger.warning(
                        "CHATLYTICS_UPLOAD_ALLOWED_ROOTS entry %r could not be "
                        "resolved (%s); skipping",
                        entry,
                        exc,
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

    @property
    def client(self) -> Optional[ChatlyticsClient]:
        """Public access to the shared httpx wrapper.

        HERMES-05 tool handlers (in :mod:`chatlytics_hermes.tools`) call
        through this property to reuse the adapter's authenticated
        ``ChatlyticsClient`` instead of opening a fresh httpx.AsyncClient
        per call.  Returns ``None`` when the adapter has not yet been
        connected.
        """
        return self._client

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
            #
            # HERMES-09 (closes 02-LOW-01): dropped reserved keys now emit
            # a WARNING so the caller learns their intent was unrealized
            # instead of silently mismatching the body shape.
            _reserved = {"chatId", "text", "accountId", "replyTo"}
            for key, value in metadata.items():
                if key in _reserved:
                    logger.warning(
                        "send() ignoring reserved metadata key %r "
                        "(would shadow body field)",
                        key,
                    )
                    continue
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
            # HERMES-09 (closes 02-LOW-01): operators tracing a
            # malformed gateway response can now see why raw_text was
            # used instead of the parsed payload.
            logger.debug(
                "send() response was not JSON; using raw_text fallback"
            )
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
        ``metadata`` is accepted for base-class signature parity; the
        Chatlytics typing endpoint does not currently consume it.
        Errors are logged at DEBUG and swallowed -- typing is a UX
        hint, not a critical path.

        Operator-actionable surface area for sustained failures is in
        :meth:`_keep_typing`'s first-fire WARNING (HERMES-08 06-LOW-02),
        which uses :meth:`_send_typing_once` internally so it can detect
        degraded sends without forcing :meth:`send_typing` to raise.
        """
        await self._send_typing_once(chat_id, duration)

    async def _send_typing_once(
        self,
        chat_id: str,
        duration: float = 3.0,
    ) -> bool:
        """POST one typing request; return True on success, False on degraded.

        Internal helper introduced in HERMES-09 (closes 02-LOW-02 +
        LO-11): :meth:`send_typing` must stay quiet (DEBUG) on transport
        and non-200 failures so a flapping gateway does not flood logs,
        but :meth:`_keep_typing` still needs to know whether the FIRST
        fire failed so it can emit a WARNING (06-LOW-02 fix).  Returning
        a status bool lets both call sites share one request path.
        """
        if self._client is None:
            return False

        try:
            response = await self._client.post(
                "/api/v1/typing",
                json={"chatId": chat_id, "duration": float(duration)},
            )
        except httpx.RequestError as exc:
            # HERMES-09 (closes 02-LOW-02 + LO-11): UX-hint endpoint;
            # DEBUG prevents log flood on a flapping gateway.
            logger.debug("send_typing transport error: %s", exc)
            return False

        if response.status_code != 200:
            logger.debug(
                "send_typing returned %s for chat %s",
                response.status_code,
                chat_id,
            )
            return False

        return True

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        """GET /api/v1/chat?chatId={id} and return the JSON body as a dict.

        HERMES-10 (02-LOW-03): empty-vs-error semantics.

        Returns ``{}`` in four distinct paths -- callers cannot
        distinguish from the return value alone; consult logs for the
        cause:

        1. Adapter not connected (``self._client is None``) -- no
           log; this indicates a programmer error (called before
           ``connect()``).
        2. Transport error (``httpx.RequestError``) -- WARNING log
           ``"get_chat_info transport error: ..."``.
        3. Non-200 response from the gateway -- WARNING log
           ``"get_chat_info returned <status> for chat <id>"``.
        4. Malformed JSON body or non-dict payload -- DEBUG log
           ``"get_chat_info JSON decode failed; returning {}"``.

        When the gateway returns 200 with a valid JSON object, the
        dict is returned as-is and is expected to contain ``name``,
        ``phone``, ``isGroup``, ... per the Chatlytics gateway
        contract. The adapter does NOT validate the schema beyond
        ``isinstance(payload, dict)``.

        Future v2.2+ may introduce a richer return shape
        (``{"success": bool, "chat": dict | None, "error": str |
        None}``) to distinguish these paths without log inspection;
        that is a breaking change and is out of scope for v2.1.
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
            # HERMES-09 (closes 02-LOW-01): distinguish "malformed body"
            # from "empty success" in the operator log -- the bare {}
            # return is otherwise ambiguous to callers debugging.
            logger.debug("get_chat_info JSON decode failed; returning {}")
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
            # Local file path. The blocking ``open()+read()`` runs in a
            # worker thread via ``asyncio.to_thread`` so concurrent media
            # tool invocations do not stall the event loop while a
            # multi-MB file is read off disk (fix for 04-MED-02 / surfaced
            # by HERMES-05 tool layer in 05-MED-02).
            path = str(resource)

            # HI-01 fix (HERMES-08): reject paths outside the configured
            # allowlist BEFORE opening the file. Default-deny — when the
            # env var ``CHATLYTICS_UPLOAD_ALLOWED_ROOTS`` is unset, the
            # allowlist is empty and EVERY local upload is refused. This
            # closes the prompt-injection / LLM-manipulation path to
            # ``open("/etc/passwd")``.
            try:
                resolved = Path(path).expanduser().resolve()
            except (OSError, RuntimeError) as exc:
                raise PermissionError(
                    f"Cannot resolve upload path {path!r}: {exc}"
                ) from exc
            if not self.upload_allowed_roots:
                raise PermissionError(
                    "Local file uploads are disabled: set "
                    "CHATLYTICS_UPLOAD_ALLOWED_ROOTS to an allowlist of "
                    "absolute paths (OS-pathsep separated) to enable "
                    "filePath uploads."
                )
            allowed = False
            for root in self.upload_allowed_roots:
                # Path.is_relative_to landed in 3.9; we pin >=3.10 in
                # pyproject so this is safe. Equality also matches
                # uploading the root itself when it's a regular file.
                try:
                    if resolved == root or resolved.is_relative_to(root):
                        allowed = True
                        break
                except AttributeError:
                    # Defensive fallback for 3.8 hosts if a downstream
                    # consumer ever loosens the pin.
                    rs = str(resolved)
                    rr = str(root)
                    if rs == rr or rs.startswith(rr + os.sep):
                        allowed = True
                        break
            if not allowed:
                raise PermissionError(
                    f"Refusing upload outside CHATLYTICS_UPLOAD_ALLOWED_ROOTS: "
                    f"{resolved}"
                )

            def _read_file() -> tuple[bytes, str]:
                with open(str(resolved), "rb") as fh:
                    return fh.read(), os.path.basename(str(resolved)) or "upload.bin"

            content, basename = await asyncio.to_thread(_read_file)
            name = upload_filename or basename
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
        matches :meth:`send` exactly.  Delegates success derivation to
        :func:`_coerce_success_payload` (MD-01 dedup — adapter,
        standalone-send, and tool layer all use the same predicate).
        """
        try:
            payload: Any = response.json()
        except Exception:  # noqa: BLE001
            payload = {"raw_text": response.text}

        success, error_msg = _coerce_success_payload(response.status_code, payload)
        if success:
            return SendResult(
                success=True,
                message_id=(
                    payload.get("messageId") if isinstance(payload, dict) else None
                ),
                raw_response=payload,
            )

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
        except PermissionError as exc:
            # HI-01 surface: path is outside CHATLYTICS_UPLOAD_ALLOWED_ROOTS
            # OR the allowlist is empty (default-deny). Distinct from a
            # generic OSError so the tool layer / operator can tell why.
            return SendResult(success=False, error=f"Permission denied: {exc}")
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
        **kwargs: Any,
    ) -> "SendResult":
        """Send an image as a native WhatsApp photo attachment.

        Accepts either an ``http(s)://...`` URL (used as ``mediaUrl``
        directly) or raw ``bytes`` (uploaded to ``/api/v1/upload``
        first).  ``caption`` is optional.  ``reply_to`` / ``metadata``
        are accepted for base-class signature parity but currently
        ignored -- Chatlytics's send-media endpoint does not expose
        per-message reply context (HERMES-05 may revisit).

        ``**kwargs`` is swallowed for forward-compat with upstream base
        signature evolution (HI-03 fix from HERMES-08): future Hermes
        versions may add new kwargs (``priority``, ``force_native``,
        etc.) that this override should not trip on.

        HERMES-10 (PR-review LOW-06) -- API-shape cross-reference:
        companion method :meth:`send_image_file` exists for backwards
        compat with the v1.x API surface that exposed a path-only
        variant; v2.0 preserved both. Both methods accept
        ``Union[str, bytes, bytearray]`` internally and route through
        the same :meth:`_send_media_payload` so behavior is identical
        given equivalent inputs. New code should prefer
        :meth:`send_image` (URL-or-bytes-or-path Union shape) and
        treat :meth:`send_image_file` as a legacy alias.
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
        **kwargs: Any,
    ) -> "SendResult":
        """Send an animated GIF / short MP4 as inline video.

        Chatlytics's gateway delivers gif/mp4 animations under
        ``mediaType=video`` (the WhatsApp protocol has no native GIF
        primitive; clients render short MP4s in a loop instead).

        ``**kwargs`` is swallowed for forward-compat with upstream base
        signature evolution (HI-03 fix from HERMES-08).
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

        HERMES-10 (PR-review LOW-06) -- API-shape cross-reference:
        companion to :meth:`send_image`. v2.0 retained both methods
        for v1.x API-shape backwards-compat; new code may prefer
        :meth:`send_image` since it accepts the same
        ``Union[str, bytes, bytearray]`` shape. Both methods share
        the same :meth:`_send_media_payload` body, so they return
        identical ``SendResult`` shapes given equivalent inputs.
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

    # --- UX polish (HERMES-04 + HERMES-08 BL-01 fix) -----------------------

    async def _keep_typing(
        self,
        chat_id: str,
        interval: float = 30.0,
        metadata: Optional[Dict[str, Any]] = None,
        stop_event: Optional[asyncio.Event] = None,
    ) -> None:
        """Continuously refresh the typing bubble until cancelled.

        BL-01 fix (HERMES-08): this method is now a plain coroutine that
        matches the upstream ``BasePlatformAdapter._keep_typing`` call
        site signature.  The base class invokes::

            asyncio.create_task(
                self._keep_typing(chat_id, metadata=..., stop_event=...)
            )

        on every inbound message (see
        ``gateway/platforms/base.py:1787-1792``).  In v2.0 this method
        was decorated ``@asynccontextmanager`` and crashed with
        ``TypeError: a coroutine was expected, got
        _AsyncGeneratorContextManager`` AND ``unexpected keyword
        argument 'metadata'``.  The async-cm flavor has been moved to
        :meth:`_typing_scope` (preserved for in-plugin tool handlers).

        Behavior:

        - Fires ``send_typing(chat_id, duration=30.0)`` immediately so
          the bubble appears without waiting ``interval`` seconds.
        - Initial-fire failure logs at WARNING (operator-actionable;
          06-LOW-02 fix).  Subsequent heartbeat failures stay at DEBUG
          to prevent log flood on a flapping gateway.
        - Reissues the typing request every ``interval`` seconds.
        - Respects ``stop_event.is_set()`` between sleeps.
        - Respects ``asyncio.CancelledError`` at any point.
        - ``metadata`` is accepted for base-class signature parity;
          Chatlytics's typing endpoint does not consume it currently.

        ``interval`` defaults to 30 s to match the WhatsApp typing TTL;
        long-running handlers (multi-minute LLM calls, image generation
        pipelines) keep the bubble alive without flooding the gateway.
        Tests override ``interval`` to a small value to observe
        heartbeats without 30 s real-time sleeps.
        """
        # Initial fire so the bubble appears immediately.  04-LOW-03
        # fix: the initial fire now happens INSIDE the coroutine, so
        # any wrapper (e.g. _typing_scope) returns control to the body
        # immediately and does not block on the first round-trip.
        #
        # HERMES-09 (06-LOW-02 + LO-11): use the internal
        # ``_send_typing_once`` helper so degraded sends (non-200 or
        # transport error) surface as WARNING here even though
        # :meth:`send_typing` itself stays quiet (DEBUG).
        #
        # HERMES-09 WARNING-01 fix: use try/except/else so the exception
        # path emits exactly ONE WARNING (with traceback) and the
        # degraded-status path emits exactly ONE WARNING (without
        # traceback).  The previous flow logged twice on the exception
        # branch because the post-try `if not initial_ok` block re-fired.
        try:
            initial_ok = await self._send_typing_once(chat_id, duration=30.0)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.warning(
                "send_typing initial fire raised for chat %s; continuing heartbeat",
                chat_id,
                exc_info=True,
            )
        else:
            if not initial_ok:
                # 06-LOW-02: first-fire failure is operator-actionable --
                # WARNING level so it surfaces in default logging configs.
                # The DEBUG record from _send_typing_once carries the
                # "why"; this WARNING carries the "who".
                logger.warning(
                    "send_typing initial fire failed for chat %s; continuing heartbeat",
                    chat_id,
                )

        while True:
            if stop_event is not None and stop_event.is_set():
                return
            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                return
            if stop_event is not None and stop_event.is_set():
                return
            try:
                await self.send_typing(chat_id, duration=30.0)
            except asyncio.CancelledError:
                return
            except Exception:  # noqa: BLE001
                logger.debug(
                    "send_typing heartbeat raised; continuing",
                    exc_info=True,
                )

    @contextlib.asynccontextmanager
    async def _typing_scope(self, chat_id: str, interval: float = 30.0):
        """In-plugin convenience wrapper around :meth:`_keep_typing`.

        Usage::

            async with adapter._typing_scope(chat_id):
                result = await long_running_tool()

        Spawns a background task running :meth:`_keep_typing` with an
        internal ``stop_event``.  On context-manager exit, sets the
        stop event, cancels the task, and awaits cancellation.  Errors
        from the typing path never abort the wrapped body.

        Renamed from ``_keep_typing`` in HERMES-08 (BL-01 fix) so the
        public :meth:`_keep_typing` can honor the upstream coroutine
        contract.  Existing tool-handler call sites that used
        ``async with adapter._keep_typing(...)`` should switch to
        ``async with adapter._typing_scope(...)``.
        """
        stop = asyncio.Event()
        task = asyncio.create_task(
            self._keep_typing(chat_id, interval=interval, stop_event=stop)
        )
        try:
            yield
        finally:
            stop.set()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001 -- never leak teardown errors
                logger.debug(
                    "_typing_scope teardown raised; continuing",
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
        # HERMES-09 (closes 02-LOW-01): cron deliveries blind-spot --
        # operators tracing a failed scheduled send can now see why
        # raw_text was used.
        logger.debug(
            "_standalone_send response was not JSON; using raw_text fallback"
        )
        payload = {"raw_text": response.text}

    # MD-01 fix (HERMES-08): use the canonical success-shape helper so
    # _standalone_send, _make_send_result, and tools._post/_get agree on
    # the predicate.  Eliminates the v2.0 divergence where a gateway
    # response of ``200 {"success": false}`` would be coerced to truthy
    # at one site and falsy at another.
    success, error_msg = _coerce_success_payload(response.status_code, payload)
    if success:
        # Spread payload AFTER success=True so a payload-side ``success``
        # value (some endpoints echo) does not override our derived flag.
        result: Dict[str, Any] = {"success": True}
        if isinstance(payload, dict):
            result.update(payload)
            result["success"] = True
        return result

    return {
        "success": False,
        "error": error_msg,
        "raw_response": payload,
    }


def _make_tool_handler(ctx: Any, name: str, handler: Any) -> Any:
    """Build a register-time wrapper that resolves the live adapter at call time.

    ``ctx.register_tool`` runs at plugin-load time -- before the gateway
    calls ``adapter_factory(config)``.  Tool handlers therefore cannot
    capture an adapter instance at registration: they must look one up
    via ``ctx.get_platform("chatlytics")`` (or its equivalent on the
    runtime ``PluginContext``) on each invocation.

    The wrapper:

    - Resolves the adapter through any of the well-known accessor names
      gateways have exposed historically (``get_platform``,
      ``platform`` mapping, ``platforms`` dict).
    - Returns a structured ``{"success": False, "error": ...}`` payload
      when no live adapter / client is available, instead of raising --
      tool callers expect every code path to honor the contract.
    - Forwards the live ``ChatlyticsClient`` (plus, for media handlers,
      the adapter instance) into the bare async handler.
    """
    # Imported lazily so registration order matches HERMES-01: importing
    # ``tools`` triggers schema construction; we keep that out of import
    # path until ``register()`` is actually called.
    from .tools import handler_takes_adapter

    needs_adapter = handler_takes_adapter(handler)

    def _lookup_adapter() -> Optional["ChatlyticsAdapter"]:
        # ``ctx.get_platform("chatlytics")`` is the v0.14 public accessor;
        # some test harnesses (and older PluginContexts) expose the
        # adapter via ``ctx.platforms[name].adapter`` instead.  Try both.
        entry = None
        get_platform = getattr(ctx, "get_platform", None)
        if callable(get_platform):
            try:
                entry = get_platform("chatlytics")
            except Exception as exc:  # noqa: BLE001
                # HERMES-09 (closes 05-LOW-01): the fallback chain is
                # by design (older PluginContexts expose ``platforms``
                # dict instead), but operators deep-debugging a missing
                # adapter should see why we did not use get_platform.
                logger.debug(
                    "_make_tool_handler ctx.get_platform raised: %s; "
                    "falling back to ctx.platforms",
                    exc,
                )
                entry = None
        if entry is None:
            platforms_attr = getattr(ctx, "platforms", None)
            if isinstance(platforms_attr, dict):
                entry = platforms_attr.get("chatlytics")
        if entry is None:
            return None
        # Both attribute and dict accessors used by various harnesses.
        adapter_inst = getattr(entry, "adapter", None)
        if adapter_inst is None and isinstance(entry, dict):
            adapter_inst = entry.get("adapter")
        return adapter_inst

    async def _bound(**kwargs: Any) -> Dict[str, Any]:
        adapter_inst = _lookup_adapter()
        client = getattr(adapter_inst, "client", None) if adapter_inst else None
        if client is None:
            return {
                "success": False,
                "error": (
                    f"Chatlytics tool '{name}' invoked but the adapter is "
                    "not connected; ensure 'hermes gateway start' has run."
                ),
            }
        if needs_adapter:
            return await handler(client, adapter=adapter_inst, **kwargs)
        return await handler(client, **kwargs)

    _bound.__name__ = f"_bound_{name}"
    _bound.__qualname__ = f"_bound_{name}"
    return _bound


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
        # HERMES-04 forward-fix for 02-REVIEW MED-01: real PluginContext
        # requires check_fn. Adapter deps (httpx, aiohttp, PyYAML) are
        # all declared in pyproject; by the time register() runs, the
        # import path has succeeded so returning True is honest.
        check_fn=lambda: True,
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
        # HERMES-04: env-driven enablement so `hermes gateway status`
        # surfaces a Chatlytics entry when CHATLYTICS_HOME_CHANNEL is
        # set, without instantiating the adapter.
        env_enablement_fn=_env_enablement,
        # HERMES-04: cron home-channel routing. ``deliver=chatlytics``
        # cron jobs use this env var to find the target chat.
        cron_deliver_env_var="CHATLYTICS_HOME_CHANNEL",
        # HERMES-04: out-of-process cron delivery hook so ``hermes
        # cron`` running in a separate process from ``hermes gateway``
        # can fire deliveries without an active adapter.
        standalone_sender_fn=_standalone_send,
    )

    # --- HERMES-05: tool surface -------------------------------------
    # Iterate the locked-21 TOOLS registry and register each as a Hermes
    # tool under the ``chatlytics`` toolset.  ``ctx`` from the v0.14
    # PluginContext exposes ``register_tool(name=, toolset=, schema=,
    # handler=, ...)`` (see ``plugins/spotify/__init__.py`` for the
    # canonical signature).  We feature-detect via ``hasattr`` so the
    # HERMES-01 MockCtx (platform-only) still works for the existing
    # ``test_register_*`` suite.
    register_tool = getattr(ctx, "register_tool", None)
    if callable(register_tool):
        from .tools import TOOLS

        for tool_name, tool_schema, tool_handler in TOOLS:
            register_tool(
                name=tool_name,
                toolset="chatlytics",
                schema=tool_schema,
                handler=_make_tool_handler(ctx, tool_name, tool_handler),
            )
