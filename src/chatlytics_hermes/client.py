"""Thin async HTTP client for the Chatlytics REST gateway.

Centralizes auth header injection, base URL, and timeout so the
adapter and (future HERMES-04) media handlers share one transport
surface.  The class is async-context-manager compatible (``__aenter__``
/ ``__aexit__``) so future call-sites can use ``async with`` blocks
when a short-lived client is preferable to the adapter-owned shared
instance.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger("chatlytics_hermes.client")

DEFAULT_TIMEOUT_SECONDS: float = 30.0
# User-Agent tracks the package version — keep in lockstep with
# pyproject.toml / plugin.yaml / __init__.__version__ on EVERY release
# (adapter._PLUGIN_VERSION is parsed from this string). History: stuck at
# 2.0.0 until HERMES-V2 (Phase 336) tied it to the release cycle.
USER_AGENT: str = "chatlytics-hermes/4.5.1"


class ChatlyticsClient:
    """Async wrapper around ``httpx.AsyncClient`` with Bearer auth + timeout."""

    def __init__(
        self,
        base_url: str = "https://node.chatlytics.ai",
        api_key: str = "",
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        user_agent: str = USER_AGENT,
    ) -> None:
        # v4.1.0: base_url defaults to the public DNS name for token-only
        # onboarding. An omitted/None URL falls back to the default; only an
        # explicitly-passed empty string still raises.
        if base_url is None:
            base_url = "https://node.chatlytics.ai"
        if not base_url:
            raise ValueError("ChatlyticsClient requires a non-empty base_url")
        if not api_key:
            raise ValueError("ChatlyticsClient requires a non-empty api_key")

        self.base_url: str = base_url.rstrip("/")
        self._api_key: str = api_key
        self.timeout: float = float(timeout)
        self._headers: Dict[str, str] = {
            "Authorization": f"Bearer {api_key}",
            "User-Agent": user_agent,
            "Accept": "application/json",
        }

        # Loop-aware lazy construction (event-loop binding fix).
        # ``httpx.AsyncClient`` binds an internal connection pool whose locks
        # (and httpcore's anyio primitives) are tied to the event loop that
        # is running when a request is first awaited. The Hermes gateway
        # constructs this client on its OWN loop (connect() / longpoll), but
        # the MCP server later awaits the SAME instance on a DIFFERENT loop —
        # which raised ``RuntimeError: <asyncio.locks.Event ...> is bound to a
        # different event loop`` for every tool call (poll, directory,
        # getChats, getChatMessages, getAllLids, getContacts, ...).
        #
        # Fix: keep ONE shared inner client (built eagerly here, exactly as
        # before — so the single-loop fast path is byte-for-byte unchanged)
        # but record which loop it is bound to and rebuild it transparently in
        # ``_get_client`` ONLY when a later request runs on a DIFFERENT loop.
        # Working tools (send/read/health/login) and the gateway keep reusing
        # the same client on their own loop with zero extra cost; the rebuild
        # fires solely on the cross-loop reuse that triggered the RuntimeError.
        #
        # ``_bound_loop`` captures the loop running at construction time, if
        # any (the gateway builds this client inside connect()/longpoll, i.e.
        # on a loop). Constructed with no running loop (tests / sync caller),
        # it stays None and binds to the first loop that issues a request.
        self._client: httpx.AsyncClient = self._build_client()
        try:
            self._bound_loop: Optional[asyncio.AbstractEventLoop] = (
                asyncio.get_running_loop()
            )
        except RuntimeError:
            self._bound_loop = None

    # --- internal: loop-aware client accessor ------------------------

    def _build_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout,
            headers=dict(self._headers),
        )

    def _get_client(self) -> httpx.AsyncClient:
        """Return the shared ``httpx.AsyncClient``, rebuilt only on loop change.

        The common case (every request on the same loop) returns the
        existing shared client immediately — identical to the pre-fix
        single-client behavior, so concurrent requests still share one pool.

        A rebuild fires only when:

        - the client was closed (defensive; e.g. a prior aclose()), or
        - the current running loop differs from the loop the client is bound
          to AND the client has actually been bound to a real (different)
          loop. This is the gateway-built-on-loop-A, MCP-awaits-on-loop-B
          case that raised the cross-loop ``RuntimeError``.

        When the client was constructed with no running loop (``_bound_loop``
        is None — tests / sync construction), the FIRST request simply
        ADOPTS its loop with no rebuild: the eager client has never touched a
        loop, so it is safe to bind in place and keep the shared-pool fast
        path for concurrent first requests.
        """
        try:
            current = asyncio.get_running_loop()
        except RuntimeError:
            current = None

        # Adopt-in-place: never used on a loop yet → bind to the first one.
        if self._bound_loop is None and not self._client.is_closed:
            self._bound_loop = current
            return self._client

        needs_rebuild = self._client.is_closed or (
            current is not None and self._bound_loop is not current
        )
        if not needs_rebuild:
            return self._client

        stale = self._client
        if (
            not stale.is_closed
            and self._bound_loop is not None
            and self._bound_loop is not current
            and not self._bound_loop.is_closed()
        ):
            # The stale client belongs to another (live) loop — closing it
            # from here would itself cross loops. Schedule aclose() on its OWN
            # loop; never let teardown of the old client raise into the fresh
            # request path.
            def _close_stale(c: httpx.AsyncClient = stale) -> None:
                try:
                    asyncio.ensure_future(c.aclose())
                except Exception:  # noqa: BLE001 -- best-effort only
                    logger.debug(
                        "ChatlyticsClient: stale-client aclose schedule "
                        "failed; relying on GC",
                        exc_info=True,
                    )

            try:
                self._bound_loop.call_soon_threadsafe(_close_stale)
            except Exception:  # noqa: BLE001 -- best-effort cleanup only
                logger.debug(
                    "ChatlyticsClient: could not schedule stale-client close "
                    "on its origin loop; relying on GC",
                    exc_info=True,
                )

        self._client = self._build_client()
        self._bound_loop = current
        return self._client

    # --- lifecycle ---------------------------------------------------

    async def aclose(self) -> None:
        """Close the underlying httpx client (idempotent)."""
        if self._client.is_closed:
            return
        await self._client.aclose()

    async def __aenter__(self) -> "ChatlyticsClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    @property
    def is_closed(self) -> bool:
        return self._client.is_closed

    # --- verbs -------------------------------------------------------

    async def get(
        self,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        timeout: Any = httpx.USE_CLIENT_DEFAULT,
    ) -> httpx.Response:
        """GET ``{base_url}{path}`` with optional query params.

        ``timeout`` defaults to ``httpx.USE_CLIENT_DEFAULT`` so existing
        callers keep the client-level :data:`DEFAULT_TIMEOUT_SECONDS`. The
        v4.1 longpoll consumer passes an explicit ``httpx.Timeout`` whose
        read window MUST exceed the server's long-poll hold (``timeout_ms``)
        — otherwise every empty poll trips a ReadTimeout. See
        ``adapter._poll_loop``.
        """
        return await self._get_client().get(path, params=params, timeout=timeout)

    async def post(
        self,
        path: str,
        *,
        json: Optional[Dict[str, Any]] = None,
        timeout: Any = httpx.USE_CLIENT_DEFAULT,
    ) -> httpx.Response:
        """POST ``{base_url}{path}`` with a JSON body."""
        return await self._get_client().post(path, json=json, timeout=timeout)

    async def post_multipart(
        self,
        path: str,
        *,
        files: Dict[str, Any],
        data: Optional[Dict[str, Any]] = None,
    ) -> httpx.Response:
        """POST ``{base_url}{path}`` as ``multipart/form-data``.

        ``files`` is forwarded to ``httpx.AsyncClient.post`` -- callers
        pass ``{"file": (filename, content_bytes, content_type)}`` for the
        Chatlytics upload endpoint contract.  ``data`` is optional
        non-file form fields.
        """
        return await self._get_client().post(path, files=files, data=data or {})

    # --- HERMES-04 helpers ------------------------------------------------

    async def send_media(self, payload: Dict[str, Any]) -> httpx.Response:
        """POST ``/api/v1/send-media`` with a JSON ``payload``.

        Thin wrapper kept separate from :meth:`post` so future media
        helpers (signed-upload buckets, retry shims) can subclass-override
        without re-implementing the JSON post path.
        """
        return await self._get_client().post("/api/v1/send-media", json=payload)

    async def upload_file(
        self,
        filename: str,
        content: bytes,
        content_type: str = "application/octet-stream",
    ) -> httpx.Response:
        """POST ``/api/v1/upload`` as multipart/form-data; returns the raw response.

        The Chatlytics upload endpoint responds with JSON ``{"url": "..."}``;
        callers reference that URL in subsequent ``send_media`` payloads as
        the ``mediaUrl`` field.  This wrapper does not parse the response --
        the adapter does, so the unit-test surface can stay close to the
        wire-level shape.
        """
        return await self.post_multipart(
            "/api/v1/upload",
            files={"file": (filename, content, content_type)},
        )
