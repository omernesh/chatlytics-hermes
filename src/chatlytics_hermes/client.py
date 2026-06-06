"""Thin async HTTP client for the Chatlytics REST gateway.

Centralizes auth header injection, base URL, and timeout so the
adapter and (future HERMES-04) media handlers share one transport
surface.  The class is async-context-manager compatible (``__aenter__``
/ ``__aexit__``) so future call-sites can use ``async with`` blocks
when a short-lived client is preferable to the adapter-owned shared
instance.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger("chatlytics_hermes.client")

DEFAULT_TIMEOUT_SECONDS: float = 30.0
# HERMES-V2 (Phase 336): User-Agent tracks the package version (4.1.2 ships
# DNS-default base_url + optional URL). Previously stuck at 2.0.0 since v2 release.
USER_AGENT: str = "chatlytics-hermes/4.1.4"


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

        self._client: httpx.AsyncClient = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout,
            headers={
                "Authorization": f"Bearer {api_key}",
                "User-Agent": user_agent,
                "Accept": "application/json",
            },
        )

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
        return await self._client.get(path, params=params, timeout=timeout)

    async def post(
        self,
        path: str,
        *,
        json: Optional[Dict[str, Any]] = None,
        timeout: Any = httpx.USE_CLIENT_DEFAULT,
    ) -> httpx.Response:
        """POST ``{base_url}{path}`` with a JSON body."""
        return await self._client.post(path, json=json, timeout=timeout)

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
        return await self._client.post(path, files=files, data=data or {})

    # --- HERMES-04 helpers ------------------------------------------------

    async def send_media(self, payload: Dict[str, Any]) -> httpx.Response:
        """POST ``/api/v1/send-media`` with a JSON ``payload``.

        Thin wrapper kept separate from :meth:`post` so future media
        helpers (signed-upload buckets, retry shims) can subclass-override
        without re-implementing the JSON post path.
        """
        return await self._client.post("/api/v1/send-media", json=payload)

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
