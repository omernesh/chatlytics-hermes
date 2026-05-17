"""Chatlytics tool handlers for Hermes (HERMES-05).

This module declares the full Chatlytics tool surface as a tuple of
``(name, schema, handler)`` triples in :data:`TOOLS`.  The plugin
``register()`` (in :mod:`chatlytics_hermes.adapter`) iterates the
tuple and calls ``ctx.register_tool(name=..., toolset='chatlytics',
schema=..., handler=...)`` for each entry, wrapping the bare async
handler in a closure that resolves the live ``ChatlyticsClient`` at
call time (since the adapter instance is constructed by the gateway
``adapter_factory`` *after* ``register()`` runs).

Handler contract:

    async def chatlytics_<action>(
        client: ChatlyticsClient,
        *,
        adapter: ChatlyticsAdapter | None = None,  # media handlers only
        **kwargs,
    ) -> dict

Return shape is **always** ``{"success": bool, ...}``.  ``True``
responses spread the gateway payload into the dict; ``False`` responses
include ``"error"`` (str) and, on HTTP failures, ``"status_code"`` and
``"raw_response"``.

Tool count is locked at **21** for HERMES-05; the registration test
guards against accidental growth or shrinkage.  Sources:
- 8 baseline tools from the Claude Code MCP bundle
  (``chatlytics-mcp.js`` at ``omernesh/chatlytics-claude-code``).
- 10 messaging extensions specified in ROADMAP HERMES-05.
- 5 media tools wrapping HERMES-04 adapter handlers.
- (-2) overlap: ``chatlytics_send`` and ``chatlytics_read`` exist in
  both groups; counted once.

= 8 + 10 + 5 - 2 = 21.
"""

from __future__ import annotations

import inspect
import logging
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple

import httpx

from .adapter import _coerce_success_payload
from .client import ChatlyticsClient

logger = logging.getLogger("chatlytics_hermes.tools")

Handler = Callable[..., Awaitable[Dict[str, Any]]]


# ---------------------------------------------------------------------------
# Response shape helpers
# ---------------------------------------------------------------------------


def _ok(payload: Any) -> Dict[str, Any]:
    """Wrap a successful gateway response as ``{"success": True, ...payload}``.

    If ``payload`` is a dict, its keys are merged into the response
    (``success`` is then re-asserted ``True`` so a payload-side
    ``success: False`` cannot override the derived flag).  Non-dict
    payloads land under the ``"result"`` key.
    """
    if isinstance(payload, dict):
        out: Dict[str, Any] = {"success": True}
        out.update(payload)
        out["success"] = True
        return out
    return {"success": True, "result": payload}


def _err_from_response(response: httpx.Response) -> Dict[str, Any]:
    """Convert a non-2xx response into the canonical failure dict."""
    try:
        payload: Any = response.json()
    except Exception:  # noqa: BLE001
        # HERMES-09 (closes 02-LOW-01): operators tracing a malformed
        # gateway error response can see why raw_text was used.
        logger.debug(
            "_err_from_response JSON decode failed; using raw_text fallback"
        )
        payload = {"raw_text": response.text}
    msg = (
        payload.get("error") if isinstance(payload, dict) else None
    ) or f"HTTP {response.status_code}"
    return {
        "success": False,
        "error": msg,
        "status_code": response.status_code,
        "raw_response": payload,
    }


def _err_from_exception(exc: Exception) -> Dict[str, Any]:
    return {"success": False, "error": f"{type(exc).__name__}: {exc}"}


async def _post(
    client: ChatlyticsClient,
    path: str,
    body: Dict[str, Any],
) -> Dict[str, Any]:
    """POST helper -- enforces the canonical return shape.

    MD-01 fix (HERMES-08): delegates success derivation to
    :func:`chatlytics_hermes.adapter._coerce_success_payload` so this
    helper, ``_make_send_result``, and ``_standalone_send`` all agree
    on the contract.  In particular, a gateway response of
    ``200 {"success": false, "error": "..."}`` now correctly returns
    ``{"success": false, ...}`` instead of being coerced to truthy by
    :func:`_ok`.
    """
    try:
        response = await client.post(path, json=body)
    except httpx.RequestError as exc:
        return _err_from_exception(exc)
    if response.status_code >= 400:
        return _err_from_response(response)
    try:
        payload = response.json()
    except Exception:  # noqa: BLE001
        # HERMES-09 (closes 02-LOW-01): same rationale as
        # _err_from_response -- operators see the decode-failure
        # fallback instead of a mysterious raw_text payload.
        logger.debug("_post JSON decode failed; using raw_text fallback")
        return _ok({"raw_text": response.text})
    success, error_msg = _coerce_success_payload(response.status_code, payload)
    if not success:
        return {
            "success": False,
            "error": error_msg,
            "status_code": response.status_code,
            "raw_response": payload,
        }
    return _ok(payload)


async def _get(
    client: ChatlyticsClient,
    path: str,
    params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """GET helper -- enforces the canonical return shape.

    MD-01 fix (HERMES-08): see :func:`_post` docstring.
    """
    try:
        response = await client.get(path, params=params)
    except httpx.RequestError as exc:
        return _err_from_exception(exc)
    if response.status_code >= 400:
        return _err_from_response(response)
    try:
        payload = response.json()
    except Exception:  # noqa: BLE001
        # HERMES-09 (closes 02-LOW-01).
        logger.debug("_get JSON decode failed; using raw_text fallback")
        return _ok({"raw_text": response.text})
    success, error_msg = _coerce_success_payload(response.status_code, payload)
    if not success:
        return {
            "success": False,
            "error": error_msg,
            "status_code": response.status_code,
            "raw_response": payload,
        }
    return _ok(payload)


def _media_result_dict(result: Any) -> Dict[str, Any]:
    """Convert an adapter ``SendResult`` into the tool return shape."""
    success = bool(getattr(result, "success", False))
    out: Dict[str, Any] = {"success": success}
    message_id = getattr(result, "message_id", None)
    if message_id is not None:
        out["messageId"] = message_id
    error = getattr(result, "error", None)
    if error and not success:
        out["error"] = error
    raw = getattr(result, "raw_response", None)
    if raw is not None:
        out["raw_response"] = raw
    return out


# ---------------------------------------------------------------------------
# JSON Schemas (Draft 2020-12)
# ---------------------------------------------------------------------------

_DRAFT = "https://json-schema.org/draft/2020-12/schema"


# HERMES-10 (05-LOW-02 + PR-MED-01): permissive chatId / messageId validation.
#
# Reject only obvious garbage at the schema boundary: empty strings and any C0
# control character (``\x00``-``\x1f``) or DEL (``\x7f``). These almost
# certainly indicate a copy-paste glitch, a stray newline, or an injection
# attempt -- they NEVER represent a real Chatlytics chatId.
#
# We deliberately do NOT enforce strict JID format (``\d+@(c|g|newsletter)\.us``)
# because Chatlytics accepts ALL of the following shapes interchangeably:
#
#   - WhatsApp JIDs (``1234567890@c.us``, ``1234...@g.us``, ``...@newsletter``)
#   - Phone numbers (``+1234567890``, ``1234567890``)
#   - Group display names (some Chatlytics gateway versions resolve these
#     server-side, e.g. ``"My Group Name"``)
#
# Tightening past the permissive pattern would break legitimate user inputs
# typed at ``/chatlytics_send``. Schema-layer validation here is cosmetic --
# better error messages early, NOT a stricter accept-set.
_CHAT_ID_PATTERN: str = r"^[^\x00-\x1f\x7f]+$"


def _chat_id_field(
    description: str = "Chat JID, phone, or group identifier.",
) -> Dict[str, Any]:
    """Reusable schema fragment for ``chatId`` properties.

    Returns a Draft 2020-12 string schema with ``minLength: 1`` and a
    ``pattern`` that rejects empty strings + control characters. Used
    by every chatId-bearing tool schema so the wording stays consistent
    and any future tightening lands in exactly one place.
    """
    return {
        "type": "string",
        "minLength": 1,
        "pattern": _CHAT_ID_PATTERN,
        "description": description,
    }


def _message_id_field(
    description: str = "Target message identifier.",
) -> Dict[str, Any]:
    """Reusable schema fragment for ``messageId`` properties.

    Same permissive validation as :func:`_chat_id_field`. Empty strings
    and control characters are obvious garbage; everything else is
    accepted (Chatlytics gateway versions vary on the exact format).
    """
    return {
        "type": "string",
        "minLength": 1,
        "pattern": _CHAT_ID_PATTERN,
        "description": description,
    }


SEND_SCHEMA: Dict[str, Any] = {
    "$schema": _DRAFT,
    "title": "chatlytics_send",
    "description": "Send a WhatsApp text message via the Chatlytics gateway.",
    "type": "object",
    "properties": {
        "chatId": _chat_id_field(
            "Chat JID (e.g. 12036...@g.us, 9725...@c.us) or phone."
        ),
        "text": {"type": "string", "minLength": 1, "description": "Message text."},
        "replyTo": _message_id_field("Optional message ID to reply to."),
        "accountId": {"type": "string", "description": "Optional Chatlytics account override."},
    },
    "required": ["chatId", "text"],
    "additionalProperties": False,
}


REPLY_SCHEMA: Dict[str, Any] = {
    "$schema": _DRAFT,
    "title": "chatlytics_reply",
    "description": "Reply to a specific WhatsApp message (sets replyTo).",
    "type": "object",
    "properties": {
        "chatId": _chat_id_field(),
        "text": {"type": "string", "minLength": 1},
        "replyTo": _message_id_field("Message ID being replied to."),
        "accountId": {"type": "string"},
    },
    "required": ["chatId", "text", "replyTo"],
    "additionalProperties": False,
}


REACT_SCHEMA: Dict[str, Any] = {
    "$schema": _DRAFT,
    "title": "chatlytics_react",
    "description": "Send an emoji reaction to a message.",
    "type": "object",
    "properties": {
        "messageId": _message_id_field(),
        "emoji": {"type": "string", "minLength": 1, "description": "Single emoji."},
        "chatId": _chat_id_field("Optional chat context."),
    },
    "required": ["messageId", "emoji"],
    "additionalProperties": False,
}


EDIT_SCHEMA: Dict[str, Any] = {
    "$schema": _DRAFT,
    "title": "chatlytics_edit",
    "description": "Edit an already-sent WhatsApp message.",
    "type": "object",
    "properties": {
        "messageId": _message_id_field(),
        "text": {"type": "string", "minLength": 1},
        "chatId": _chat_id_field(),
    },
    "required": ["messageId", "text"],
    "additionalProperties": False,
}


UNSEND_SCHEMA: Dict[str, Any] = {
    "$schema": _DRAFT,
    "title": "chatlytics_unsend",
    "description": "Recall (unsend) a previously sent message.",
    "type": "object",
    "properties": {
        "messageId": _message_id_field(),
        "chatId": _chat_id_field(),
    },
    "required": ["messageId"],
    "additionalProperties": False,
}


PIN_SCHEMA: Dict[str, Any] = {
    "$schema": _DRAFT,
    "title": "chatlytics_pin",
    "description": "Pin a message in a chat.",
    "type": "object",
    "properties": {
        "messageId": _message_id_field(),
        "chatId": _chat_id_field(),
        "duration": {"type": "integer", "minimum": 1, "description": "Pin duration in seconds (optional)."},
    },
    "required": ["messageId"],
    "additionalProperties": False,
}


UNPIN_SCHEMA: Dict[str, Any] = {
    "$schema": _DRAFT,
    "title": "chatlytics_unpin",
    "description": "Unpin a message in a chat.",
    "type": "object",
    "properties": {
        "messageId": _message_id_field(),
        "chatId": _chat_id_field(),
    },
    "required": ["messageId"],
    "additionalProperties": False,
}


READ_SCHEMA: Dict[str, Any] = {
    "$schema": _DRAFT,
    "title": "chatlytics_read",
    "description": "Read recent messages from a chat (paged).",
    "type": "object",
    "properties": {
        "chatId": _chat_id_field(),
        "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 10},
    },
    "required": ["chatId"],
    "additionalProperties": False,
}


DELETE_SCHEMA: Dict[str, Any] = {
    "$schema": _DRAFT,
    "title": "chatlytics_delete",
    "description": "Delete a message (local-only or for everyone, gateway-dependent).",
    "type": "object",
    "properties": {
        "messageId": _message_id_field(),
        "chatId": _chat_id_field(),
        "forEveryone": {"type": "boolean", "default": False},
    },
    "required": ["messageId"],
    "additionalProperties": False,
}


POLL_SCHEMA: Dict[str, Any] = {
    "$schema": _DRAFT,
    "title": "chatlytics_poll",
    "description": "Create a WhatsApp poll in a chat.",
    "type": "object",
    "properties": {
        "chatId": _chat_id_field(),
        "question": {"type": "string", "minLength": 1},
        "options": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "minItems": 2,
            "maxItems": 12,
        },
        "multiple": {"type": "boolean", "default": False, "description": "Allow multiple selections."},
    },
    "required": ["chatId", "question", "options"],
    "additionalProperties": False,
}


# Media tools share a common shape: chatId + (mediaUrl XOR filePath), optional caption.
def _media_schema(title: str, description: str, extra_props: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    base: Dict[str, Any] = {
        "$schema": _DRAFT,
        "title": title,
        "description": description,
        "type": "object",
        "properties": {
            # HERMES-10 (05-LOW-02 + PR-MED-01): permissive chatId validation.
            "chatId": _chat_id_field(),
            "mediaUrl": {"type": "string", "format": "uri", "description": "https:// URL of the media."},
            "filePath": {"type": "string", "description": "Local file path; uploaded to /api/v1/upload."},
            "caption": {"type": "string"},
        },
        "required": ["chatId"],
        "anyOf": [
            {"required": ["mediaUrl"]},
            {"required": ["filePath"]},
        ],
        "additionalProperties": False,
    }
    if extra_props:
        base["properties"].update(extra_props)
    return base


SEND_IMAGE_SCHEMA = _media_schema(
    "chatlytics_send_image",
    "Send an image (URL or local file).",
)
SEND_VOICE_SCHEMA = _media_schema(
    "chatlytics_send_voice",
    "Send an audio file as a WhatsApp voice bubble (push-to-talk UX).",
)
SEND_VIDEO_SCHEMA = _media_schema(
    "chatlytics_send_video",
    "Send a video as inline playable media.",
)
SEND_FILE_SCHEMA = _media_schema(
    "chatlytics_send_file",
    "Send a generic file as a downloadable WhatsApp document.",
    extra_props={"filename": {"type": "string", "description": "Displayed attachment name."}},
)
SEND_ANIMATION_SCHEMA = _media_schema(
    "chatlytics_send_animation",
    "Send an animated GIF or short MP4 (delivered as inline video).",
)


DIRECTORY_SCHEMA: Dict[str, Any] = {
    "$schema": _DRAFT,
    "title": "chatlytics_directory",
    "description": "Browse WhatsApp contacts, groups, and newsletters.",
    "type": "object",
    "properties": {
        "type": {"type": "string", "enum": ["contact", "group", "newsletter"]},
        "search": {"type": "string", "description": "Substring filter."},
        "limit": {"type": "integer", "minimum": 1, "maximum": 1000},
    },
    "required": [],
    "additionalProperties": False,
}


SEARCH_SCHEMA: Dict[str, Any] = {
    "$schema": _DRAFT,
    "title": "chatlytics_search",
    "description": "Search WhatsApp contacts, groups, and channels by name.",
    "type": "object",
    "properties": {
        "query": {"type": "string", "minLength": 1},
    },
    "required": ["query"],
    "additionalProperties": False,
}


ACTIONS_SCHEMA: Dict[str, Any] = {
    "$schema": _DRAFT,
    "title": "chatlytics_actions",
    "description": "List the full Chatlytics action catalog (~100 actions).",
    "type": "object",
    "properties": {},
    "required": [],
    "additionalProperties": False,
}


HEALTH_SCHEMA: Dict[str, Any] = {
    "$schema": _DRAFT,
    "title": "chatlytics_health",
    "description": "Check Chatlytics gateway and WhatsApp connection status.",
    "type": "object",
    "properties": {},
    "required": [],
    "additionalProperties": False,
}


LOGIN_SCHEMA: Dict[str, Any] = {
    "$schema": _DRAFT,
    "title": "chatlytics_login",
    "description": (
        "Validate the configured API key and webhook registration via /health. "
        "Returns success=True only when webhook_registered is True (matches the "
        "Claude Code MCP bundle semantics in chatlytics-mcp.js). On "
        "webhook_registered=False the tool returns success=False with the "
        "webhook_registered + sessions fields populated for diagnostic visibility."
    ),
    "type": "object",
    "properties": {},
    "required": [],
    "additionalProperties": False,
}


DISPATCH_SCHEMA: Dict[str, Any] = {
    "$schema": _DRAFT,
    "title": "chatlytics_dispatch",
    "description": (
        "Dispatch any Chatlytics action by name (POST /api/v1/actions). "
        "Use chatlytics_actions to list the catalog. Use specific tools "
        "(chatlytics_send/react/...) for common operations."
    ),
    "type": "object",
    "properties": {
        "action": {"type": "string", "minLength": 1, "description": "Action name from the catalog."},
        "target": {"type": "string", "description": "Optional chat ID / JID / contact name (action-dependent)."},
        "parameters": {"type": "object", "description": "Action-specific parameters."},
        "session": {"type": "string", "description": "Optional session ID."},
    },
    "required": ["action"],
    "additionalProperties": False,
}


# ---------------------------------------------------------------------------
# Handlers -- Messaging (10)
# ---------------------------------------------------------------------------


async def chatlytics_send(
    client: ChatlyticsClient,
    *,
    chatId: str,
    text: str,
    replyTo: Optional[str] = None,
    accountId: Optional[str] = None,
) -> Dict[str, Any]:
    body: Dict[str, Any] = {"chatId": chatId, "text": text}
    if replyTo:
        body["replyTo"] = replyTo
    if accountId:
        body["accountId"] = accountId
    return await _post(client, "/api/v1/send", body)


async def chatlytics_reply(
    client: ChatlyticsClient,
    *,
    chatId: str,
    text: str,
    replyTo: str,
    accountId: Optional[str] = None,
) -> Dict[str, Any]:
    body: Dict[str, Any] = {"chatId": chatId, "text": text, "replyTo": replyTo}
    if accountId:
        body["accountId"] = accountId
    return await _post(client, "/api/v1/send", body)


async def chatlytics_react(
    client: ChatlyticsClient,
    *,
    messageId: str,
    emoji: str,
    chatId: Optional[str] = None,
) -> Dict[str, Any]:
    body: Dict[str, Any] = {"action": "react", "messageId": messageId, "emoji": emoji}
    if chatId:
        body["chatId"] = chatId
    return await _post(client, "/api/v1/actions", body)


async def chatlytics_edit(
    client: ChatlyticsClient,
    *,
    messageId: str,
    text: str,
    chatId: Optional[str] = None,
) -> Dict[str, Any]:
    body: Dict[str, Any] = {"action": "edit", "messageId": messageId, "text": text}
    if chatId:
        body["chatId"] = chatId
    return await _post(client, "/api/v1/actions", body)


async def chatlytics_unsend(
    client: ChatlyticsClient,
    *,
    messageId: str,
    chatId: Optional[str] = None,
) -> Dict[str, Any]:
    body: Dict[str, Any] = {"action": "unsend", "messageId": messageId}
    if chatId:
        body["chatId"] = chatId
    return await _post(client, "/api/v1/actions", body)


async def chatlytics_pin(
    client: ChatlyticsClient,
    *,
    messageId: str,
    chatId: Optional[str] = None,
    duration: Optional[int] = None,
) -> Dict[str, Any]:
    body: Dict[str, Any] = {"action": "pin", "messageId": messageId}
    if chatId:
        body["chatId"] = chatId
    if duration is not None:
        body["duration"] = duration
    return await _post(client, "/api/v1/actions", body)


async def chatlytics_unpin(
    client: ChatlyticsClient,
    *,
    messageId: str,
    chatId: Optional[str] = None,
) -> Dict[str, Any]:
    body: Dict[str, Any] = {"action": "unpin", "messageId": messageId}
    if chatId:
        body["chatId"] = chatId
    return await _post(client, "/api/v1/actions", body)


async def chatlytics_read(
    client: ChatlyticsClient,
    *,
    chatId: str,
    limit: int = 10,
) -> Dict[str, Any]:
    body = {
        "action": "readMessages",
        "params": {"chatId": chatId, "limit": int(limit)},
    }
    return await _post(client, "/api/v1/actions", body)


async def chatlytics_delete(
    client: ChatlyticsClient,
    *,
    messageId: str,
    chatId: Optional[str] = None,
    forEveryone: bool = False,
) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "action": "delete",
        "messageId": messageId,
        "forEveryone": bool(forEveryone),
    }
    if chatId:
        body["chatId"] = chatId
    return await _post(client, "/api/v1/actions", body)


async def chatlytics_poll(
    client: ChatlyticsClient,
    *,
    chatId: str,
    question: str,
    options: list,
    multiple: bool = False,
) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "action": "poll",
        "chatId": chatId,
        "question": question,
        "options": list(options),
        "multiple": bool(multiple),
    }
    return await _post(client, "/api/v1/actions", body)


# ---------------------------------------------------------------------------
# Handlers -- Media (5) -- wrap HERMES-04 adapter methods.
# ---------------------------------------------------------------------------


def _resolve_resource(*, mediaUrl: Optional[str], filePath: Optional[str]) -> Any:
    """Pick the resource argument for adapter.send_<media> from tool kwargs.

    mediaUrl wins when both are present (an explicit URL is cheaper than
    re-uploading a local file).  Returning ``None`` is a programming error
    (the schema's ``anyOf`` already enforces presence of at least one),
    but defensively returning empty bytes here would mask that bug, so we
    let the adapter call fail with a clear ``FileNotFoundError``.
    """
    if mediaUrl:
        return mediaUrl
    return filePath


async def chatlytics_send_image(
    client: ChatlyticsClient,
    *,
    adapter: Any = None,
    chatId: str,
    mediaUrl: Optional[str] = None,
    filePath: Optional[str] = None,
    caption: Optional[str] = None,
) -> Dict[str, Any]:
    """Send an image via the Chatlytics gateway.

    HERMES-10 (PR-review LOW-06): dispatches to the appropriate
    adapter method based on which input is set:

    - ``mediaUrl`` -> ``adapter.send_image(chatId, mediaUrl, ...)``
      (URL-first variant)
    - ``filePath`` -> ``adapter.send_image_file(chatId, filePath, ...)``
      (local-path variant)

    The adapter retains the URL-vs-path split for v2.0 surface
    backwards-compat; this tool offers a single unified entry point so
    MCP / Claude Code users see one consistent ``chatlytics_send_image``
    call shape regardless of source. The other four media tools
    (``send_voice/video/file/animation``) use a single adapter method
    that internally dispatches on input shape -- only the image surface
    is split historically.
    """
    if adapter is None:
        return {"success": False, "error": "Media tools require a live adapter; ensure register() ran."}
    resource = _resolve_resource(mediaUrl=mediaUrl, filePath=filePath)
    if not resource:
        return {"success": False, "error": "Either mediaUrl or filePath is required."}
    if mediaUrl:
        result = await adapter.send_image(chatId, mediaUrl, caption=caption)
    else:
        result = await adapter.send_image_file(chatId, filePath, caption=caption)
    return _media_result_dict(result)


async def chatlytics_send_voice(
    client: ChatlyticsClient,
    *,
    adapter: Any = None,
    chatId: str,
    mediaUrl: Optional[str] = None,
    filePath: Optional[str] = None,
    caption: Optional[str] = None,
) -> Dict[str, Any]:
    if adapter is None:
        return {"success": False, "error": "Media tools require a live adapter; ensure register() ran."}
    resource = _resolve_resource(mediaUrl=mediaUrl, filePath=filePath)
    if not resource:
        return {"success": False, "error": "Either mediaUrl or filePath is required."}
    result = await adapter.send_voice(chatId, resource, caption=caption)
    return _media_result_dict(result)


async def chatlytics_send_video(
    client: ChatlyticsClient,
    *,
    adapter: Any = None,
    chatId: str,
    mediaUrl: Optional[str] = None,
    filePath: Optional[str] = None,
    caption: Optional[str] = None,
) -> Dict[str, Any]:
    if adapter is None:
        return {"success": False, "error": "Media tools require a live adapter; ensure register() ran."}
    resource = _resolve_resource(mediaUrl=mediaUrl, filePath=filePath)
    if not resource:
        return {"success": False, "error": "Either mediaUrl or filePath is required."}
    result = await adapter.send_video(chatId, resource, caption=caption)
    return _media_result_dict(result)


async def chatlytics_send_file(
    client: ChatlyticsClient,
    *,
    adapter: Any = None,
    chatId: str,
    mediaUrl: Optional[str] = None,
    filePath: Optional[str] = None,
    caption: Optional[str] = None,
    filename: Optional[str] = None,
) -> Dict[str, Any]:
    if adapter is None:
        return {"success": False, "error": "Media tools require a live adapter; ensure register() ran."}
    resource = _resolve_resource(mediaUrl=mediaUrl, filePath=filePath)
    if not resource:
        return {"success": False, "error": "Either mediaUrl or filePath is required."}
    result = await adapter.send_document(
        chatId, resource, caption=caption, file_name=filename
    )
    return _media_result_dict(result)


async def chatlytics_send_animation(
    client: ChatlyticsClient,
    *,
    adapter: Any = None,
    chatId: str,
    mediaUrl: Optional[str] = None,
    filePath: Optional[str] = None,
    caption: Optional[str] = None,
) -> Dict[str, Any]:
    if adapter is None:
        return {"success": False, "error": "Media tools require a live adapter; ensure register() ran."}
    resource = _resolve_resource(mediaUrl=mediaUrl, filePath=filePath)
    if not resource:
        return {"success": False, "error": "Either mediaUrl or filePath is required."}
    result = await adapter.send_animation(chatId, resource, caption=caption)
    return _media_result_dict(result)


# ---------------------------------------------------------------------------
# Handlers -- Directory / search (3)
# ---------------------------------------------------------------------------


async def chatlytics_directory(
    client: ChatlyticsClient,
    *,
    type: Optional[str] = None,  # noqa: A002 -- matches MCP bundle field name
    search: Optional[str] = None,
    limit: Optional[int] = None,
) -> Dict[str, Any]:
    params: Dict[str, Any] = {}
    if type:
        params["type"] = type
    if search:
        params["search"] = search
    if limit is not None:
        params["limit"] = int(limit)
    return await _get(client, "/api/v1/directory", params=params or None)


async def chatlytics_search(
    client: ChatlyticsClient,
    *,
    query: str,
) -> Dict[str, Any]:
    body = {"action": "search", "params": {"query": query}}
    return await _post(client, "/api/v1/actions", body)


async def chatlytics_actions(client: ChatlyticsClient) -> Dict[str, Any]:
    """List the Chatlytics action catalog."""
    return await _get(client, "/api/v1/actions")


# ---------------------------------------------------------------------------
# Handlers -- Sessions / health (3)
# ---------------------------------------------------------------------------


async def chatlytics_health(client: ChatlyticsClient) -> Dict[str, Any]:
    return await _get(client, "/health")


async def chatlytics_login(client: ChatlyticsClient) -> Dict[str, Any]:
    """Validate API key + webhook registration via /health.

    HERMES-10 (05-LOW-03 + PR-LOW-03): aligns with the Claude Code MCP
    bundle's semantics (``chatlytics-mcp.js`` :240-303).

    Behavior:

    - When ``/health`` itself fails (transport error or non-200): pass
      through the failure dict returned by :func:`_get` (already
      populated with ``success=False`` + ``error``).
    - When ``/health`` returns 200 but ``webhook_registered`` is not
      literally ``True``: return ``success=False`` with an error message
      indicating the webhook is unregistered. WhatsApp inbound is
      effectively down even though the gateway responds, so a "login
      OK" return would mislead the operator.
    - When ``/health`` returns 200 AND ``webhook_registered`` is
      ``True``: return ``success=True`` with ``webhook_registered`` and
      ``sessions`` (derived count) populated.

    ``sessions`` derivation matches the MCP bundle:

    - ``list`` payload -> ``len(list)``
    - ``int`` payload -> ``int``
    - anything else (missing, ``None``, string) -> ``"unknown"``
    """
    result = await _get(client, "/health")
    if not result.get("success"):
        # Transport error or non-200 -- _get already populated success=False.
        return result

    webhook_value = result.get("webhook_registered")
    webhook_ok = webhook_value is True

    sessions = result.get("sessions")
    # LOW-01 fix (10-REVIEW): ``bool`` is a subclass of ``int`` in
    # Python, so a bare ``isinstance(sessions, int)`` matches True/False
    # too. The MCP bundle's ``typeof === "number"`` does NOT match JS
    # booleans, so without the explicit bool exclusion this branch
    # would diverge from the reference implementation under a
    # degenerate ``{"sessions": true}`` response.
    if isinstance(sessions, list):
        session_count: Any = len(sessions)
    elif isinstance(sessions, int) and not isinstance(sessions, bool):
        session_count = sessions
    else:
        session_count = "unknown"

    raw_response = {k: v for k, v in result.items() if k != "success"}

    if not webhook_ok:
        # MCP-aligned: gateway reachable but webhook unregistered ->
        # surface as a clear failure so operators do not think the
        # plugin is healthy when inbound is silently dropped.
        return {
            "success": False,
            "error": (
                f"webhook_registered is not true (got {webhook_value!r}); "
                "WhatsApp inbound may be down"
            ),
            "webhook_registered": webhook_value,
            "sessions": session_count,
            "raw_response": raw_response,
        }

    return {
        "success": True,
        "webhook_registered": True,
        "sessions": session_count,
        "raw_response": raw_response,
    }


async def chatlytics_dispatch(
    client: ChatlyticsClient,
    *,
    action: str,
    target: Optional[str] = None,
    parameters: Optional[Dict[str, Any]] = None,
    session: Optional[str] = None,
) -> Dict[str, Any]:
    body: Dict[str, Any] = {"action": action}
    if target is not None:
        body["target"] = target
    if parameters is not None:
        body["params"] = parameters
    if session is not None:
        body["session"] = session
    return await _post(client, "/api/v1/actions", body)


# ---------------------------------------------------------------------------
# TOOLS registry -- iterated by chatlytics_hermes.adapter.register()
# ---------------------------------------------------------------------------


TOOLS: Tuple[Tuple[str, Dict[str, Any], Handler], ...] = (
    # Messaging (10)
    ("chatlytics_send",         SEND_SCHEMA,         chatlytics_send),
    ("chatlytics_reply",        REPLY_SCHEMA,        chatlytics_reply),
    ("chatlytics_react",        REACT_SCHEMA,        chatlytics_react),
    ("chatlytics_edit",         EDIT_SCHEMA,         chatlytics_edit),
    ("chatlytics_unsend",       UNSEND_SCHEMA,       chatlytics_unsend),
    ("chatlytics_pin",          PIN_SCHEMA,          chatlytics_pin),
    ("chatlytics_unpin",        UNPIN_SCHEMA,        chatlytics_unpin),
    ("chatlytics_read",         READ_SCHEMA,         chatlytics_read),
    ("chatlytics_delete",       DELETE_SCHEMA,       chatlytics_delete),
    ("chatlytics_poll",         POLL_SCHEMA,         chatlytics_poll),
    # Media (5) -- wrap HERMES-04 adapter methods (handler accepts adapter kwarg)
    ("chatlytics_send_image",      SEND_IMAGE_SCHEMA,      chatlytics_send_image),
    ("chatlytics_send_voice",      SEND_VOICE_SCHEMA,      chatlytics_send_voice),
    ("chatlytics_send_video",      SEND_VIDEO_SCHEMA,      chatlytics_send_video),
    ("chatlytics_send_file",       SEND_FILE_SCHEMA,       chatlytics_send_file),
    ("chatlytics_send_animation",  SEND_ANIMATION_SCHEMA,  chatlytics_send_animation),
    # Directory / search (3)
    ("chatlytics_directory",    DIRECTORY_SCHEMA,    chatlytics_directory),
    ("chatlytics_search",       SEARCH_SCHEMA,       chatlytics_search),
    ("chatlytics_actions",      ACTIONS_SCHEMA,      chatlytics_actions),
    # Sessions / health (3)
    ("chatlytics_health",       HEALTH_SCHEMA,       chatlytics_health),
    ("chatlytics_login",        LOGIN_SCHEMA,        chatlytics_login),
    ("chatlytics_dispatch",     DISPATCH_SCHEMA,     chatlytics_dispatch),
)

# Locked count for HERMES-05.  The registration test fails loudly if this
# drifts -- adding tools is a design decision, not a typo.
assert len(TOOLS) == 21, (
    f"Chatlytics tool surface drift: expected 21 tools, got {len(TOOLS)}"
)


def handler_takes_adapter(handler: Handler) -> bool:
    """Return True when ``handler`` accepts an ``adapter`` keyword.

    Used by the register-time wrapper in :mod:`chatlytics_hermes.adapter`
    to decide whether to inject the live adapter instance alongside the
    httpx client.  Cached via ``inspect.signature`` introspection so the
    wrapper does not pay the cost on every call.
    """
    try:
        sig = inspect.signature(handler)
    except (TypeError, ValueError):
        return False
    return "adapter" in sig.parameters
