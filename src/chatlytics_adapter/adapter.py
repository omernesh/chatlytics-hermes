"""Hermes Agent platform adapter for Chatlytics WhatsApp.

Bridges Hermes Agent to Chatlytics by implementing the 5 required
BasePlatformAdapter methods: connect, disconnect, send, send_typing,
get_chat_info.  Uses httpx for async HTTP calls to the Chatlytics API
and Flask for the inbound webhook server.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any, Callable

import httpx
from flask import Flask, request as flask_request

logger = logging.getLogger(__name__)


class ChatlyticsAdapter:
    """Hermes Agent platform adapter for Chatlytics WhatsApp.

    Implements the 5 required methods: connect, disconnect, send,
    send_typing, get_chat_info.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        account_id: str | None = None,
        webhook_port: int = 9090,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.account_id = account_id
        self.webhook_port = webhook_port
        self._client: httpx.AsyncClient | None = None
        self._webhook_server: threading.Thread | None = None
        self._message_handler: Callable[..., Any] | None = None

    # -- lifecycle -------------------------------------------------------------

    async def connect(self) -> None:
        """Connect to Chatlytics and verify health."""
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )
        resp = await self._client.get("/health")
        resp.raise_for_status()
        logger.info("Connected to Chatlytics at %s", self.base_url)

    async def disconnect(self) -> None:
        """Disconnect and stop webhook server."""
        if self._client:
            await self._client.aclose()
            self._client = None
        # Flask dev server doesn't expose a clean shutdown from another thread,
        # but the daemon flag ensures it dies with the process.
        self._webhook_server = None
        logger.info("Disconnected from Chatlytics")

    # -- outbound --------------------------------------------------------------

    async def send(self, chat_id: str, message: str, **kwargs: Any) -> dict:
        """Send a message via Chatlytics.

        Parameters
        ----------
        chat_id:
            WhatsApp JID (e.g. ``972544329000@c.us`` or group JID).
        message:
            Text body to send.
        **kwargs:
            Extra fields forwarded to the Chatlytics ``/api/v1/send`` payload
            (e.g. ``media_url``, ``reply_to``).
        """
        self._ensure_connected()
        payload: dict[str, Any] = {"chatId": chat_id, "text": message, **kwargs}
        if self.account_id:
            payload["accountId"] = self.account_id

        resp = await self._client.post("/api/v1/send", json=payload)  # type: ignore[union-attr]
        resp.raise_for_status()
        data: dict = resp.json()
        logger.debug("Sent message to %s: %s", chat_id, data)
        return data

    async def send_typing(self, chat_id: str, duration: float = 3.0) -> None:
        """Send typing indicator.

        Parameters
        ----------
        chat_id:
            Target chat JID.
        duration:
            How long to show the indicator (seconds).
        """
        self._ensure_connected()
        payload: dict[str, Any] = {"chatId": chat_id, "duration": duration}
        if self.account_id:
            payload["accountId"] = self.account_id

        resp = await self._client.post("/api/v1/typing", json=payload)  # type: ignore[union-attr]
        resp.raise_for_status()

    # -- queries ---------------------------------------------------------------

    async def get_chat_info(self, chat_id: str) -> dict:
        """Get chat/contact information.

        Parameters
        ----------
        chat_id:
            WhatsApp JID to look up.

        Returns
        -------
        dict
            Chat metadata (name, participants for groups, etc.).
        """
        self._ensure_connected()
        params: dict[str, str] = {"chatId": chat_id}
        if self.account_id:
            params["accountId"] = self.account_id

        resp = await self._client.get("/api/v1/chat", params=params)  # type: ignore[union-attr]
        resp.raise_for_status()
        return resp.json()

    # -- inbound ---------------------------------------------------------------

    def on_message(self, handler: Callable[..., Any]) -> None:
        """Register handler for inbound messages.

        The handler will be called with the parsed webhook JSON payload
        whenever Chatlytics forwards an incoming WhatsApp message.
        """
        self._message_handler = handler

    def start_webhook_server(self) -> None:
        """Start Flask webhook server for inbound messages.

        Runs in a daemon thread so it won't block the async event loop.
        Configure Chatlytics to POST webhooks to
        ``http://<host>:<webhook_port>/webhook``.
        """
        app = Flask(__name__)

        @app.post("/webhook")
        def _webhook() -> tuple[dict, int]:
            payload = flask_request.get_json(silent=True) or {}
            if self._message_handler:
                try:
                    self._message_handler(payload)
                except Exception:
                    logger.exception("Message handler error")
            return {"ok": True}, 200

        @app.get("/health")
        def _health() -> tuple[dict, int]:
            return {"status": "ok"}, 200

        self._webhook_server = threading.Thread(
            target=lambda: app.run(
                host="0.0.0.0",
                port=self.webhook_port,
                use_reloader=False,
            ),
            daemon=True,
        )
        self._webhook_server.start()
        logger.info("Webhook server listening on port %d", self.webhook_port)

    # -- internal --------------------------------------------------------------

    def _ensure_connected(self) -> None:
        if self._client is None:
            raise RuntimeError(
                "Not connected — call await adapter.connect() first"
            )
