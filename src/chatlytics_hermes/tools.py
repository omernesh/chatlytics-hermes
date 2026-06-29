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

from .adapter import NO_TOKEN_PROMPT, _coerce_success_payload
from .client import ChatlyticsClient

logger = logging.getLogger("chatlytics_hermes.tools")

Handler = Callable[..., Awaitable[Dict[str, Any]]]


# v4.1.5 (telegram-style onboarding): tools that MUST NOT be no-token-guarded.
# These status/health tools should still report the degraded state rather than
# return a get-a-token prompt — an operator running chatlytics_health on a
# token-less gateway wants the health truth, not the onboarding nudge. Every
# OTHER tool in TOOLS is a DATA tool that hits the authed API and is guarded.
_NO_TOKEN_EXEMPT_TOOLS: frozenset = frozenset(
    {"chatlytics_health", "chatlytics_login"}
)


def _no_token_failure() -> Dict[str, Any]:
    """Canonical no-credential failure dict carrying NO_TOKEN_PROMPT.

    v4.1.5 (telegram-style onboarding): returned by every agent-callable
    DATA tool when the adapter loaded in the degraded no-credential state
    (no CHATLYTICS_BOT_TOKEN). Matches the standard tool failure shape
    (``{"success": False, "error": ...}``) so callers/relays treat it like
    any other non-success return — the ``error`` string is the relayable
    get-a-token guidance (Web UI + CLI routes).
    """
    return {"success": False, "error": NO_TOKEN_PROMPT}


def _adapter_lacks_credential(adapter: Any) -> bool:
    """True when ``adapter`` loaded WITHOUT a bot token (degraded state).

    v4.1.5: the per-tool guard short-circuits on this so data tools never
    issue an unauthenticated API call. Checks both the explicit
    ``_no_credential`` flag (set by ``connect()``) and a falsy
    ``_auth_token`` (defensive — covers a never-connected adapter).
    """
    if adapter is None:
        return False
    if getattr(adapter, "_no_credential", False):
        return True
    return not getattr(adapter, "_auth_token", "")


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


# Bug 3 (search ReadTimeout): server-side cold-cache search can exceed the
# client-level 30s default. ``chatlytics_search`` threads this longer read
# window through ``_post`` so ONLY the search call gets the extended timeout;
# every other tool keeps the default. connect/write/pool stay tight so a dead
# connection still fails fast — only the server's processing window is widened.
_SEARCH_TIMEOUT: httpx.Timeout = httpx.Timeout(
    connect=10.0, read=90.0, write=10.0, pool=10.0
)


async def _post(
    client: ChatlyticsClient,
    path: str,
    body: Dict[str, Any],
    *,
    timeout: Any = httpx.USE_CLIENT_DEFAULT,
) -> Dict[str, Any]:
    """POST helper -- enforces the canonical return shape.

    MD-01 fix (HERMES-08): delegates success derivation to
    :func:`chatlytics_hermes.adapter._coerce_success_payload` so this
    helper, ``_make_send_result``, and ``_standalone_send`` all agree
    on the contract.  In particular, a gateway response of
    ``200 {"success": false, "error": "..."}`` now correctly returns
    ``{"success": false, ...}`` instead of being coerced to truthy by
    :func:`_ok`.

    ``timeout`` defaults to the client-level timeout; callers that need a
    longer read window (e.g. ``chatlytics_search`` cold-cache) pass an
    explicit :class:`httpx.Timeout`. Bug 3 fix.
    """
    try:
        response = await client.post(path, json=body, timeout=timeout)
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


# HERMES-14 (v3.0 BREAKING -- see CHANGELOG entry "BREAKING -- strict JID
# regex on chatId schemas"): replaces v2.1's permissive
# ``_CHAT_ID_PATTERN`` (which only rejected empty + control chars) with
# a strict JID-only validator. Matches the sibling JS bundle's canonical
# ``looksLikeJid`` regex at
# ``C:/Users/omern/.claude/plugins/marketplaces/chatlytics-claude-code/servers/chatlytics-mcp.js:58-61``:
#
#     function looksLikeJid(s) {
#       if (typeof s !== "string" || s.length === 0) return false;
#       return /@(c\.us|g\.us|lid|newsletter)$/i.test(s);
#     }
#
# Suffix families (lowercase, per WAHA convention):
#   - ``@c.us``       -- 1:1 contacts
#   - ``@g.us``       -- groups
#   - ``@lid``        -- NOWEB linked-id form
#   - ``@newsletter`` -- channels / newsletters
#
# Phones, display names, and ambiguous strings are now rejected at the
# schema boundary. Callers MUST pre-resolve via ``chatlytics_search``
# before invoking any chatId-bearing tool.
#
# Note on case-sensitivity: JSON Schema ``pattern`` flags are
# implementation-defined; jsonschema's Python validator treats the
# pattern as case-sensitive by default. The JS ``/i`` flag is permissive
# but real-world WAHA JIDs are lowercase, so case-sensitivity matches
# the JS bundle's behavior for every legitimate input.
_JID_PATTERN: str = r"^.+@(c\.us|g\.us|lid|newsletter)$"

# ``_message_id_field`` stays on the v2.1 permissive validator (empty +
# control-char rejection only). The JS canonical bundle does NOT regex-
# validate WhatsApp messageIds -- they are treated as opaque strings --
# so the Python plugin matches. Renamed from ``_CHAT_ID_PATTERN`` to
# make the dual intent explicit; the messageId helper still uses it.
_PERMISSIVE_ID_PATTERN: str = r"^[^\x00-\x1f\x7f]+$"


def _chat_id_field(
    description: str = (
        "WhatsApp JID. Format: <id>@<suffix> where suffix is one of "
        "c.us (1:1), g.us (groups), lid (NOWEB linked-id), "
        "newsletter (channels). Phones and display names are rejected -- "
        "use chatlytics_search first to resolve them to a JID."
    ),
) -> Dict[str, Any]:
    """Reusable schema fragment for ``chatId`` properties (strict JID).

    HERMES-14 (v3.0 BREAKING): emits a Draft 2020-12 string schema with
    ``minLength: 1`` and a ``pattern`` enforcing the JID suffix families
    (c.us / g.us / lid / newsletter). Inputs that lack a valid suffix --
    bare phones, display names, ambiguous strings -- are rejected at
    validation time.

    Callers needing a permissive identifier (e.g. ``messageId``) should
    use :func:`_message_id_field` instead.
    """
    return {
        "type": "string",
        # HERMES-18 (closes Phase 10 LOW-02): ``minLength: 1`` is
        # deliberately redundant with the pattern's ``+`` quantifier
        # (``+`` already rejects empty strings). The two-layer guard
        # is intentional defense-in-depth: a future maintainer who
        # changes the pattern to ``*`` (zero-or-more) will not
        # accidentally allow empty chatIds through validation.
        "minLength": 1,
        "pattern": _JID_PATTERN,
        "description": description,
    }


def _message_id_field(
    description: str = "Target message identifier.",
) -> Dict[str, Any]:
    """Reusable schema fragment for ``messageId`` properties.

    HERMES-14: stays permissive (empty + control-char rejection only).
    The sibling JS bundle (``looksLikeJid`` at
    ``servers/chatlytics-mcp.js``) does NOT regex-validate WhatsApp
    messageIds -- they are treated as opaque strings. Matching that
    behavior here keeps the Python plugin and JS bundle in lockstep.
    """
    return {
        "type": "string",
        # HERMES-18 (closes Phase 10 LOW-02): ``minLength: 1`` is
        # deliberately redundant with the pattern's ``+`` quantifier
        # (``+`` already rejects empty strings). The two-layer guard
        # is intentional defense-in-depth -- see the same rationale
        # on :func:`_chat_id_field`.
        "minLength": 1,
        "pattern": _PERMISSIVE_ID_PATTERN,
        "description": description,
    }


SEND_SCHEMA: Dict[str, Any] = {
    "$schema": _DRAFT,
    "title": "chatlytics_send",
    "description": "Send a WhatsApp text message via the Chatlytics gateway.",
    "type": "object",
    "properties": {
        "chatId": _chat_id_field(
            "Chat JID (e.g. 12036...@g.us, 9725...@c.us). "
            "Use chatlytics_search to resolve names/phones to a JID."
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


# Media tools share a common shape: chatId + file (URL or local path; mediaUrl/filePath aliases), optional caption.
def _media_schema(title: str, description: str, extra_props: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    base: Dict[str, Any] = {
        "$schema": _DRAFT,
        "title": title,
        "description": description,
        "type": "object",
        "properties": {
            # HERMES-14 (v3.0 BREAKING): strict JID validation via _chat_id_field().
            "chatId": _chat_id_field(),
            "file": {"type": "string", "description": "Media to send: an https:// URL or a local file path (preferred — URL vs path auto-detected)."},
            "mediaUrl": {"type": "string", "format": "uri", "description": "https:// URL of the media (alias of file)."},
            "filePath": {"type": "string", "description": "Local file path (alias of file)."},
            "caption": {"type": "string"},
        },
        "required": ["chatId"],
        "anyOf": [
            {"required": ["file"]},
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


def _resolve_resource(*, file: Optional[str] = None, mediaUrl: Optional[str] = None, filePath: Optional[str] = None) -> Any:
    """Pick the resource argument for adapter.send_<media> from tool kwargs.

    ``file`` is the canonical param — a URL or a local path; the adapter
    auto-detects URL vs local-path vs bytes in
    :meth:`ChatlyticsAdapter._resolve_media_url`. ``mediaUrl``/``filePath``
    are back-compat aliases. Precedence: file > mediaUrl > filePath.
    Returning ``None`` is a programming error (the schema's ``anyOf``
    enforces presence of at least one); the adapter call then fails with a
    clear error rather than masking the bug.
    """
    if file:
        return file
    if mediaUrl:
        return mediaUrl
    return filePath


async def chatlytics_send_image(
    client: ChatlyticsClient,
    *,
    adapter: Any = None,
    chatId: str,
    file: Optional[str] = None,
    mediaUrl: Optional[str] = None,
    filePath: Optional[str] = None,
    caption: Optional[str] = None,
) -> Dict[str, Any]:
    """Send an image via the Chatlytics gateway.

    HERMES-15 (v3.0 BREAKING — library API; tool surface unchanged):
    dispatches to the unified ``adapter.send_image(chatId, resource,
    ...)`` method. Either ``mediaUrl`` or ``filePath`` is required —
    ``_resolve_resource`` picks one of them and hands it to the
    adapter, which auto-detects URL vs local-path vs bytes in
    :meth:`ChatlyticsAdapter._resolve_media_url`.

    The v2.0/v2.1 split (``adapter.send_image`` vs
    ``adapter.send_image_file``) is gone at the adapter layer. The
    tool layer has always exposed one face; only the internal
    dispatch simplified.

    Tool surface stays at 21 tools — this is an internal simplification
    only. MCP / Hermes callers see no behavior change.
    """
    if adapter is None:
        return {"success": False, "error": "Media tools require a live adapter; ensure register() ran."}
    # v4.1.5 (telegram-style onboarding): no-credential degraded state.
    if _adapter_lacks_credential(adapter):
        return _no_token_failure()
    resource = _resolve_resource(file=file, mediaUrl=mediaUrl, filePath=filePath)
    if not resource:
        return {"success": False, "error": "A media `file` (an https:// URL or a local path) is required."}
    result = await adapter.send_image(chatId, resource, caption=caption)
    return _media_result_dict(result)


async def chatlytics_send_voice(
    client: ChatlyticsClient,
    *,
    adapter: Any = None,
    chatId: str,
    file: Optional[str] = None,
    mediaUrl: Optional[str] = None,
    filePath: Optional[str] = None,
    caption: Optional[str] = None,
) -> Dict[str, Any]:
    if adapter is None:
        return {"success": False, "error": "Media tools require a live adapter; ensure register() ran."}
    # v4.1.5 (telegram-style onboarding): no-credential degraded state.
    if _adapter_lacks_credential(adapter):
        return _no_token_failure()
    resource = _resolve_resource(file=file, mediaUrl=mediaUrl, filePath=filePath)
    if not resource:
        return {"success": False, "error": "A media `file` (an https:// URL or a local path) is required."}
    result = await adapter.send_voice(chatId, resource, caption=caption)
    return _media_result_dict(result)


async def chatlytics_send_video(
    client: ChatlyticsClient,
    *,
    adapter: Any = None,
    chatId: str,
    file: Optional[str] = None,
    mediaUrl: Optional[str] = None,
    filePath: Optional[str] = None,
    caption: Optional[str] = None,
) -> Dict[str, Any]:
    if adapter is None:
        return {"success": False, "error": "Media tools require a live adapter; ensure register() ran."}
    # v4.1.5 (telegram-style onboarding): no-credential degraded state.
    if _adapter_lacks_credential(adapter):
        return _no_token_failure()
    resource = _resolve_resource(file=file, mediaUrl=mediaUrl, filePath=filePath)
    if not resource:
        return {"success": False, "error": "A media `file` (an https:// URL or a local path) is required."}
    result = await adapter.send_video(chatId, resource, caption=caption)
    return _media_result_dict(result)


async def chatlytics_send_file(
    client: ChatlyticsClient,
    *,
    adapter: Any = None,
    chatId: str,
    file: Optional[str] = None,
    mediaUrl: Optional[str] = None,
    filePath: Optional[str] = None,
    caption: Optional[str] = None,
    filename: Optional[str] = None,
) -> Dict[str, Any]:
    if adapter is None:
        return {"success": False, "error": "Media tools require a live adapter; ensure register() ran."}
    # v4.1.5 (telegram-style onboarding): no-credential degraded state.
    if _adapter_lacks_credential(adapter):
        return _no_token_failure()
    resource = _resolve_resource(file=file, mediaUrl=mediaUrl, filePath=filePath)
    if not resource:
        return {"success": False, "error": "A media `file` (an https:// URL or a local path) is required."}
    result = await adapter.send_document(
        chatId, resource, caption=caption, file_name=filename
    )
    return _media_result_dict(result)


async def chatlytics_send_animation(
    client: ChatlyticsClient,
    *,
    adapter: Any = None,
    chatId: str,
    file: Optional[str] = None,
    mediaUrl: Optional[str] = None,
    filePath: Optional[str] = None,
    caption: Optional[str] = None,
) -> Dict[str, Any]:
    if adapter is None:
        return {"success": False, "error": "Media tools require a live adapter; ensure register() ran."}
    # v4.1.5 (telegram-style onboarding): no-credential degraded state.
    if _adapter_lacks_credential(adapter):
        return _no_token_failure()
    resource = _resolve_resource(file=file, mediaUrl=mediaUrl, filePath=filePath)
    if not resource:
        return {"success": False, "error": "A media `file` (an https:// URL or a local path) is required."}
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
    # Bug 3: server cold-cache search can exceed the 30s client default and
    # trip a ReadTimeout. Use the extended read window for THIS call only.
    body = {"action": "search", "params": {"query": query}}
    return await _post(client, "/api/v1/actions", body, timeout=_SEARCH_TIMEOUT)


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
# Tool-layer wrapper for adapter.get_chat_info (HERMES-13)
# ---------------------------------------------------------------------------
#
# Not registered in the TOOLS tuple — scope-locked at 21 tools for HERMES-13;
# only the SEMANTICS of get_chat_info break in this phase, not the tool count.
# Exposed as a module-level coroutine so in-plugin callers and tests can use
# the canonical {"success": bool, ...} shape with the v3.0 ``_error`` machine
# code. A future v3.1 minor MAY add this wrapper to TOOLS if the operator
# decides the tool surface needs an explicit get_chat_info entry.


async def chatlytics_get_chat_info(
    client: ChatlyticsClient,
    *,
    adapter: Any = None,
    chatId: str,
) -> Dict[str, Any]:
    """Tool-layer wrapper for :meth:`ChatlyticsAdapter.get_chat_info`.

    Translates the new v3.0 three-way adapter contract
    (``dict | None | raises ChatlyticsLookupError``) into the canonical
    tool-shape:

    - ``{"success": True,  "chat": {...}}``    — chat found
    - ``{"success": True,  "chat": None}``     — legitimate empty
    - ``{"success": False, "error": "...",     — any error
       "_error": "<code>"}``                     code per ChatlyticsLookupError

    Codes (lowercase snake_case): ``transport_error``, ``auth_error``,
    ``server_error``, ``validation_error``, ``unknown_error``.

    ``client`` is accepted for signature parity with the other handlers
    in this module (the adapter-level lookup uses ``adapter.client``
    internally; the parameter is unused here but keeps the call shape
    uniform).
    """
    # Local import keeps the module-load dependency direction consistent
    # with the existing pattern (tools.py already imports the lower-level
    # helper _coerce_success_payload from adapter at module top).
    from .adapter import ChatlyticsLookupError

    if adapter is None:
        return {
            "success": False,
            "error": "chatlytics_get_chat_info requires a live adapter",
            "_error": "unknown_error",
        }
    # v4.1.5 (telegram-style onboarding): no-credential degraded state.
    if _adapter_lacks_credential(adapter):
        return _no_token_failure()
    try:
        chat = await adapter.get_chat_info(chatId)
    except ChatlyticsLookupError as exc:
        return {
            "success": False,
            "error": exc.message,
            "_error": exc.code,
        }
    return {"success": True, "chat": chat}


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
