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
USER_AGENT: str = "chatlytics-hermes/2.0.0"


class ChatlyticsClient:
    """Async wrapper around ``httpx.AsyncClient`` with Bearer auth + timeout."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        user_agent: str = USER_AGENT,
    ) -> None:
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
    ) -> httpx.Response:
        """GET ``{base_url}{path}`` with optional query params."""
        return await self._client.get(path, params=params)

    async def post(
        self,
        path: str,
        *,
        json: Optional[Dict[str, Any]] = None,
    ) -> httpx.Response:
        """POST ``{base_url}{path}`` with a JSON body."""
        return await self._client.post(path, json=json)
