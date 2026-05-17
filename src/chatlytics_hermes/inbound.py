"""Inbound webhook normalization + aiohttp handler factory for chatlytics-hermes.

HERMES-03 moves the v1.x Flask-in-a-thread inbound transport onto an
aiohttp ``web.Application`` started inside ``ChatlyticsAdapter.connect()``.
This module owns two pieces:

1. ``normalize_payload(body: dict, platform) -> MessageEvent`` --
   translate a Chatlytics webhook JSON body into a Hermes v0.14
   ``MessageEvent`` (including ``SessionSource`` construction and
   ``MessageType`` derivation from the ``mediaType`` field).
2. ``make_webhook_handler(adapter) -> async def`` -- aiohttp request
   handler that reads the body, optionally verifies HMAC against
   ``adapter.webhook_secret``, normalizes via (1), and dispatches via
   ``await adapter.handle_message(event)``.

``make_health_handler()`` returns a trivial GET handler that responds
with ``{"status": "ok"}`` (Chatlytics polls this to confirm the
webhook is reachable).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any, Callable, Dict, Optional

from aiohttp import web

try:
    from gateway.platforms.base import MessageEvent, MessageType
    from gateway.session import SessionSource

    _HERMES_AVAILABLE = True
except ImportError:  # hermes-agent missing -- module still imports cleanly
    MessageEvent = None  # type: ignore[assignment]
    MessageType = None  # type: ignore[assignment]
    SessionSource = None  # type: ignore[assignment]
    _HERMES_AVAILABLE = False


logger = logging.getLogger("chatlytics_hermes.inbound")

# Map Chatlytics-side ``mediaType`` strings -> Hermes ``MessageType``
# members.  ``"image"`` is an alias for ``PHOTO`` because the Chatlytics
# gateway speaks WhatsApp-flavored payloads ("image") while Hermes calls
# the canonical message type ``PHOTO``.
_MEDIA_TYPE_MAP: Dict[str, str] = {
    "image": "PHOTO",
    "photo": "PHOTO",
    "audio": "AUDIO",
    "voice": "VOICE",
    "video": "VIDEO",
    "document": "DOCUMENT",
    "sticker": "STICKER",
}


def _derive_message_type(payload: Dict[str, Any]) -> "MessageType":
    """Map the payload's ``mediaType`` to the Hermes ``MessageType`` enum.

    Falls back to ``MessageType.TEXT`` for missing / unknown values --
    the Hermes runtime can still process the event as plain text and
    any text content remains accessible via ``event.text``.
    """
    if MessageType is None:
        raise RuntimeError(
            "hermes-agent>=0.14,<0.15 is required for inbound normalization"
        )
    media_type = (payload.get("mediaType") or "").strip().lower()
    member_name = _MEDIA_TYPE_MAP.get(media_type)
    if member_name and hasattr(MessageType, member_name):
        return getattr(MessageType, member_name)
    return MessageType.TEXT


def normalize_payload(
    body: Dict[str, Any],
    platform: Any,
) -> "MessageEvent":
    """Translate a Chatlytics webhook JSON body into a ``MessageEvent``.

    Schema (subset of the Chatlytics gateway documented contract)::

        {
          "chatId":     str,           # required -- destination/origin chat
          "text":       str,           # optional -- caption or message body
          "senderId":   str,           # optional -- chat-relative user id
          "messageId":  str,           # optional -- platform message id
          "timestamp":  int|float|str, # optional -- ignored (Hermes stamps locally)
          "replyTo":    str,           # optional -- in-reply-to message id
          "mediaType":  str,           # optional -- "image"/"audio"/"video"/...
          "mediaUrl":   str,           # optional -- direct URL when media is attached
          "chatType":   str,           # optional -- "dm"/"group"/"channel"/"thread"
        }

    Anything else in the body is preserved on ``event.raw_message`` for
    downstream consumers.
    """
    if not _HERMES_AVAILABLE:
        raise RuntimeError(
            "hermes-agent>=0.14,<0.15 is required for inbound normalization"
        )

    chat_id = str(body.get("chatId") or "")
    if not chat_id:
        raise ValueError("webhook payload missing required 'chatId'")

    sender_id = body.get("senderId")
    message_id = body.get("messageId")
    reply_to = body.get("replyTo")
    text = body.get("text") or ""

    message_type = _derive_message_type(body)

    media_urls: list[str] = []
    media_types: list[str] = []
    media_url = body.get("mediaUrl")
    if media_url:
        media_urls.append(str(media_url))
        media_types.append((body.get("mediaType") or "").lower() or "unknown")

    source = SessionSource(
        platform=platform,
        chat_id=chat_id,
        user_id=str(sender_id) if sender_id is not None else None,
        chat_type=str(body.get("chatType") or "dm"),
        message_id=str(message_id) if message_id is not None else None,
    )

    return MessageEvent(
        text=text,
        message_type=message_type,
        source=source,
        raw_message=body,
        message_id=str(message_id) if message_id is not None else None,
        media_urls=media_urls,
        media_types=media_types,
        reply_to_message_id=str(reply_to) if reply_to is not None else None,
    )


def verify_hmac(
    secret: str,
    body: bytes,
    provided_signature: Optional[str],
) -> bool:
    """Verify ``X-Chatlytics-Signature`` against HMAC-SHA256 of ``body``.

    ``hmac.compare_digest`` makes the comparison constant-time, so a
    mismatched signature does not leak per-byte timing information.
    Returns False on any malformed input (missing signature, wrong
    length, non-hex characters).
    """
    if not provided_signature:
        return False
    expected = hmac.new(
        secret.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()
    try:
        return hmac.compare_digest(expected, provided_signature.strip().lower())
    except (TypeError, ValueError):
        return False


def make_webhook_handler(adapter: Any) -> Callable[[web.Request], Any]:
    """Return an aiohttp request handler bound to ``adapter``.

    The closure pattern keeps ``adapter`` out of the module's global
    state so the same module can serve multiple adapter instances
    (e.g. multi-tenant runtimes).
    """

    async def _handle(request: web.Request) -> web.Response:
        body_bytes = await request.read()

        # Optional HMAC verification.
        secret = getattr(adapter, "webhook_secret", None)
        if secret:
            provided = request.headers.get("X-Chatlytics-Signature")
            if not verify_hmac(secret, body_bytes, provided):
                logger.warning(
                    "Rejecting webhook: HMAC signature mismatch from %s",
                    request.remote,
                )
                return web.json_response(
                    {"error": "invalid signature"}, status=401
                )

        try:
            payload = await request.json()
        except Exception as exc:  # noqa: BLE001 -- JSONDecodeError + aiohttp variants
            logger.warning("Rejecting webhook: invalid JSON: %s", exc)
            return web.json_response({"error": "invalid json"}, status=400)

        if not isinstance(payload, dict):
            return web.json_response(
                {"error": "payload must be a JSON object"}, status=400
            )

        try:
            event = normalize_payload(payload, adapter.platform)
        except ValueError as exc:
            logger.warning("Rejecting webhook: %s", exc)
            return web.json_response({"error": str(exc)}, status=400)

        try:
            await adapter.handle_message(event)
        except Exception:  # noqa: BLE001 -- never let dispatch errors crash the server
            logger.exception("handle_message raised; webhook still acked")

        return web.json_response({"status": "accepted"}, status=200)

    return _handle


def make_health_handler() -> Callable[[web.Request], Any]:
    """Return a trivial GET handler that responds with ``{"status": "ok"}``."""

    async def _handle(_request: web.Request) -> web.Response:
        return web.json_response({"status": "ok"}, status=200)

    return _handle
