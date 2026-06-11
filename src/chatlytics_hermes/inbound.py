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

# v4.5.1 (review-d3 H7): warn-once dedup keys for harness-version-drift
# fallbacks (module-level — the drift is per-process, not per-adapter).
# Bounded by construction: only fixed literal keys are ever added.
_WARNED_ONCE: set = set()


def _warn_once(key: str, msg: str, *args: Any) -> None:
    """WARNING the first time ``key`` is seen; DEBUG thereafter."""
    if key in _WARNED_ONCE:
        logger.debug(msg, *args)
        return
    _WARNED_ONCE.add(key)
    logger.warning(msg, *args)

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
            "hermes-agent>=0.14,<1.0 is required for inbound normalization"
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
            "hermes-agent>=0.14,<1.0 is required for inbound normalization"
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

        # P-19 fix (carried forward): thread the WAHA `session` from the
        # inbound webhook to the outbound /api/v1/send. The Chatlytics
        # gateway requires `session` in the send body; without it every
        # reply 400s with "chatId and session are required". We read it
        # from the top-level payload (or raw_message.* aliases) and record
        # it via register_chat_session so send() can resolve it.
        try:
            inbound_session = (
                payload.get("session")
                or payload.get("sessionName")
                or payload.get("waha_session")
            )
            if isinstance(inbound_session, str) and inbound_session:
                register = getattr(adapter, "register_chat_session", None)
                if callable(register):
                    register(event.source.chat_id, inbound_session)
        except Exception:  # noqa: BLE001 -- never let session bookkeeping break dispatch
            logger.debug("inbound session bookkeeping raised; continuing")

        # v4.5.0 (chatlytics v5.4 P8): per-channel prompt injection — same
        # pattern as adapter._dispatch_envelope (keep the two in sync).
        # ``MessageEvent.channel_prompt`` is the harness's native per-turn
        # ephemeral system-prompt channel (applied at API call time, never
        # persisted to the transcript). Webhook envelopes POST the full
        # envelope dict, so the server's bot_module_config "channel-prompts"
        # value rides ``payload["channel_prompt"]``; the local config.yaml
        # ``channel_prompts`` map is the fallback.
        try:
            cp = payload.get("channel_prompt")
            if not (isinstance(cp, str) and cp.strip()):
                try:
                    from gateway.platforms.base import resolve_channel_prompt

                    cp = resolve_channel_prompt(
                        getattr(getattr(adapter, "config", None), "extra", None)
                        or {},
                        event.source.chat_id,
                    )
                except Exception as exc:  # noqa: BLE001 -- harness drift
                    # v4.5.1 (review-d3 H7): version drift is operator-
                    # relevant — local channel_prompts config silently
                    # never applies. WARNING once, DEBUG thereafter.
                    _warn_once(
                        "channel_prompt:resolver_unavailable",
                        "channel-prompt config fallback unavailable: "
                        "gateway.platforms.base.resolve_channel_prompt "
                        "missing or raised (%s: %s) — hermes-agent version "
                        "drift; local channel_prompts config will NOT apply",
                        type(exc).__name__,
                        exc,
                    )
                    cp = None
            if isinstance(cp, str) and cp.strip():
                if hasattr(event, "channel_prompt"):
                    event.channel_prompt = cp.strip()
                else:
                    # v4.5.1 (review-d3 H7): configured prompt DROPPED —
                    # name the missing attribute.
                    _warn_once(
                        "channel_prompt:event_attr_missing",
                        "MessageEvent has no channel_prompt attribute "
                        "(hermes-agent too old for per-channel prompts) — "
                        "the configured channel prompt was dropped for "
                        "chat %s",
                        event.source.chat_id,
                    )
        except Exception:  # noqa: BLE001 -- prompt injection must never break dispatch
            logger.debug("channel_prompt injection raised; continuing")

        try:
            await adapter.handle_message(event)
        except Exception:  # noqa: BLE001 -- never let dispatch errors crash the server
            # v4.5.1 (review-d3 M7): deliberately ack 200 + log at ERROR
            # rather than returning 500. The chatlytics server's
            # webhook-forwarder (src/webhook-forwarder.ts) retries 5xx /
            # 408 / network errors up to 3 more times before dead-lettering
            # — but a handle_message raise here is almost always
            # DETERMINISTIC (payload/harness bug), and worse, the raise may
            # land AFTER the agent turn was partially dispatched, so a 500
            # would make the server re-POST the same message and trigger
            # duplicate agent turns. 200 + the unmissable traceback below
            # is the lesser evil; non-deterministic transport-level
            # failures never reach this handler in the first place.
            logger.exception("handle_message raised; webhook still acked")

        return web.json_response({"status": "accepted"}, status=200)

    return _handle


def make_health_handler() -> Callable[[web.Request], Any]:
    """Return a trivial GET handler that responds with ``{"status": "ok"}``."""

    async def _handle(_request: web.Request) -> web.Response:
        return web.json_response({"status": "ok"}, status=200)

    return _handle
