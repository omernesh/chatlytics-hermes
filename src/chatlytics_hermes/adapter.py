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
  ``send_video``, ``send_document``, ``send_animation``) and
  ``_keep_typing`` heartbeat. HERMES-15 (v3.0 BREAKING) collapsed
  the v2.0 ``send_image`` / ``send_image_file`` split into one
  unified ``send_image(chat_id, resource: str | Path | bytes, ...)``;
  ``send_image_file`` is gone.
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
import hashlib
import json
import logging
import mimetypes
import os
import random
import time
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import httpx
from aiohttp import web

from .client import ChatlyticsClient, USER_AGENT
from .diagnostics import (
    check_hermes_agent_version,
    extract_bot_name,
    map_connect_error,
)
from .inbound import make_health_handler, make_webhook_handler, normalize_payload


# HERMES-V2 (Phase 336): chatlytics v4.0 introduces per-bot bearer tokens
# (``sk_bot_<43-char-base64url>``). The plugin prefers ``CHATLYTICS_BOT_TOKEN``
# over the legacy admin/operator ``CHATLYTICS_API_KEY``. Both share the same
# Bearer header transport — only the token shape differs, so the chatlytics
# gateway can distinguish (``resolveBotFromBearer`` vs ``requirePublicApiAuth``)
# without the plugin needing to know which auth path the gateway picked.
#
# Detection prefix is documented at chatlytics.ai/CLAUDE.md (API Keys table,
# CHATLYTICS_BOT_TOKEN row) — sk_bot_ prefix is the canonical shape.
_BOT_TOKEN_PREFIX: str = "sk_bot_"

# v4.1.5 (telegram-style onboarding): when NO bot token is configured the
# adapter loads in a degraded "no-credential" state instead of hard-failing
# at gateway boot. Every agent-callable DATA tool returns this relayable
# get-a-token prompt so the user is guided to provision a token on first use.
# DO NOT mention BotDaddy — that onboarding route is not live yet.
NO_TOKEN_PROMPT = (
    "⚠️ Chatlytics needs a bot token before it can send or read WhatsApp.\n\n"
    "No CHATLYTICS_BOT_TOKEN is configured yet. Get one (it looks like `sk_bot_…`) either way:\n"
    "  • Web UI — sign in at https://app.chatlytics.ai → Bots → Create Bot, then copy the token (shown only once).\n"
    "  • CLI — `chatlytics bots create --session <your-session-id> --name <bot-name>` (needs an admin API key).\n\n"
    "Then set CHATLYTICS_BOT_TOKEN in your gateway profile config/.env (or run `hermes config`) and restart the gateway."
)

# v4.1 longpoll hold: the chatlytics server HOLDS GET /api/v1/bot/updates for
# up to this many ms before returning an empty {envelopes:[],cursor} batch.
# The longpoll GET's httpx read timeout MUST comfortably exceed this hold or
# every empty poll trips a ReadTimeout (which httpx stringifies to "()", the
# `longpoll GET transport error ()` symptom). The client-level default
# (30s, only ~5s margin) was too tight; the poll uses an explicit per-request
# httpx.Timeout with read = hold + 15s instead. See _poll_loop.
_LONGPOLL_TIMEOUT_MS: int = 25000

# v4.3.0 (chatlytics v5.4 P6): capability advertisement. The gateway adds
# ``caps=control`` as a query parameter on EVERY longpoll GET so the
# chatlytics server knows this gateway understands control envelopes
# (``kind == "control"`` — /new /stop /retry conversation commands typed by
# WhatsApp users). The server records this per-poll and only emits control
# envelopes to caps-advertising gateways; there is no response handshake and
# nothing changes on ack. DO NOT remove this param — without it the server
# silently withholds control envelopes and conversation commands no-op.
_LONGPOLL_CAPS: str = "control"

# v4.3.0: control-envelope action set (chatlytics v5.4 wire contract).
# ``kind=="control"`` envelopes carry one of these in ``action``; anything
# else is logged once and IGNORED (forward-compat — a newer server may emit
# actions this plugin predates, and they must NEVER be dispatched as
# message text to the agent).
#
# v4.5.1 (review-d3 X20): the action set is now DERIVED from this handler
# map, and ``_handle_control_envelope`` dispatches through it — adding a new
# control action means adding exactly one entry here (the frozenset and the
# dispatch can no longer drift apart).
_CONTROL_ACTION_HANDLERS: Dict[str, str] = {
    "new_conversation": "_control_new_conversation",
    "stop": "_control_stop",
    "retry_last": "_control_retry_last",
    # v4.5.0 (chatlytics v5.4 P8): ``question_resolved`` joined the set —
    # the owner's /approve /deny /answer reply to an owner-DM question
    # rides the SAME control cap (``caps=control``); no new capability
    # token is advertised for it.
    "question_resolved": "_control_question_resolved",
}
_CONTROL_ACTIONS: frozenset = frozenset(_CONTROL_ACTION_HANDLERS)

# v4.3.0: bounded per-chat last-message memo for ``retry_last``. Keyed by
# entity_jid, value = the last NORMAL message envelope dispatched for that
# chat (the full envelope dict is the minimal re-dispatch unit — it feeds
# straight back through _dispatch_envelope). LRU-evicted past this cap.
_LAST_MESSAGE_MEMO_MAX: int = 128

# v4.3.0: cap on the warn-once dedup set for unknown envelope kinds /
# control actions, so a misbehaving server emitting unbounded distinct
# unknown values cannot grow adapter memory without limit.
_UNKNOWN_CONTROL_WARN_CAP: int = 64

# v4.4.0 (chatlytics v5.4 P7): progress-bubble edit-in-place bounds.
# ``_progress_bubbles`` maps chat_id -> (waha message id, monotonic ts) for
# the ONE un-consumed "working…" bubble per chat. LRU-evicted past the cap;
# entries older than the TTL are treated as stale (the turn they belonged to
# is long gone — editing a 10-minute-old bubble into a fresh reply would be
# confusing, so the reply falls back to a plain send).
_PROGRESS_BUBBLE_MAX: int = 100
_PROGRESS_BUBBLE_TTL_S: float = 600.0

# v4.5.1 (review-d3 X20): default threshold (seconds) before the ONE
# "working…" progress bubble fires. Was a magic 8.0 inline in __init__.
_STATUS_BUBBLE_AFTER_DEFAULT_S: float = 8.0

# v4.5.0 (chatlytics v5.4 P8): pending owner-DM question registry bounds.
# ``_pending_questions`` maps request_id -> {kind, session_key, clarify_id,
# future, chat_id, created} for every question POSTed to
# ``/api/v1/bot/questions`` that has not yet seen its ``question_resolved``
# control envelope. FIFO-evicted past the cap (WARNING — an evicted
# approval/clarify can no longer be resolved by the owner's reply and will
# time out gateway-side, fail-closed). Entries older than the TTL are
# pruned on insert — the server-side question TTL tops out at 86400 s but
# the gateway-side approval/clarify waits time out far sooner (default
# 600 s), so a 2 h-old registry entry is unresolvable dead weight.
_PENDING_QUESTIONS_MAX: int = 64
_PENDING_QUESTION_TTL_S: float = 7200.0

# v4.5.0 (P8): cap on the ``command`` excerpt embedded in an approval
# question's text. The chatlytics server caps the question ``text`` field
# at 2000 chars; 1500 for the command leaves headroom for the description
# prefix without ever tripping the server-side validation.
_QUESTION_COMMAND_MAX_CHARS: int = 1500

# v4.5.1 (review-d3 X15): question-POST outcome discriminants returned by
# _post_question. CREATED also covers 409 ``duplicate_request_id`` (the
# question already exists server-side and WILL resolve/expire normally —
# keep waiting). UNKNOWN means the POST's fate is unknowable (transport
# error / unexpected raise) — the question MAY have been delivered, so the
# pending-registry entry is KEPT and the caller's wait timeout / registry
# TTL cleans up. FAILED is a definitive server rejection — pop the entry.
_QPOST_CREATED: str = "created"
_QPOST_UNKNOWN: str = "unknown"
_QPOST_FAILED: str = "failed"

# v4.5.1 (review-d3 X15): the hermes runner's gateway-side approval/clarify
# wait defaults to 600 s (gateway run.py exec-approval + clarify timeouts).
# send_exec_approval / send_clarify pass ``ttl_s = wait + 60`` so the
# server-side question outlives the gateway wait by a small margin instead
# of defaulting to the server's much longer TTL (dead questions linger in
# the owner DM long after the wait has fail-closed).
_GATEWAY_QUESTION_WAIT_S: float = 600.0

# Server-side ttl_s validation bounds (chatlytics v5.4 P8 wire contract:
# ``ttl_s?: int 60..86400``). _question_ttl_s clamps into this range.
_QUESTION_TTL_MIN_S: int = 60
_QUESTION_TTL_MAX_S: int = 86400


def _question_ttl_s(wait_s: float) -> int:
    """ttl_s aligned to the caller's wait (wait + 60 s), server-clamped."""
    return max(_QUESTION_TTL_MIN_S, min(_QUESTION_TTL_MAX_S, int(wait_s + 60.0)))

# v4.2.0 (P3 survivability): unmissable load-failure prefix. EVERY partial
# load failure (missing token, unreachable base_url, register() raise) logs
# one ERROR line starting with this string, so an operator grepping the
# gateway log for the "2 platforms instead of 3 / bot silent for days"
# symptom finds the reason + fix in one hit. Keep the prefix stable — it is
# a documented grep target (README "Self-check" section).
_LOAD_FAIL_PREFIX: str = "CHATLYTICS PLUGIN FAILED TO LOAD:"

# v4.2.0 (P3 survivability): bounded longpoll reconnect ladder. Connection
# refused/reset/timeout AND the chatlytics v5.4 graceful-shutdown signal
# (empty 200 + Connection: close) are NORMAL retry events: back off
# 1s → 2s → 5s → 15s → 30s (cap), with jitter, never give up, reset on
# success. State-change logging (healthy↔degraded) lives in _poll_loop.
_BACKOFF_LADDER: Tuple[float, ...] = (1.0, 2.0, 5.0, 15.0, 30.0)
# Multiplicative jitter: delay = base * (1 + uniform(0, frac)). Bounded above
# by base * 1.25 so tests (and operators reading logs) can reason about it.
_BACKOFF_JITTER_FRAC: float = 0.25

# Plugin version for log lines (parsed from the client User-Agent so adapter
# does not import chatlytics_hermes.__init__ — that would be circular).
_PLUGIN_VERSION: str = USER_AGENT.rsplit("/", 1)[-1]


def _coerce_flag(value: Any, default: bool) -> bool:
    """Defensively coerce an env-var / extra-block value to a bool.

    v4.4.0 (P7): config-knob parsing for ``status_edit_in_place``. Accepts
    the usual textual spellings; anything unrecognized (including ``None``)
    falls back to ``default`` — a typo'd env var must never flip a
    default-ON feature into a surprising state, and must never raise at
    adapter ``__init__`` (gateway boot path).
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("0", "false", "no", "off"):
        return False
    return default


def _backoff_delay(attempt: int) -> float:
    """Jittered delay for the Nth consecutive longpoll failure (0-based).

    Climbs the :data:`_BACKOFF_LADDER` and stays at the 30s cap once past the
    end. Jitter spreads simultaneous reconnects from multiple gateways so a
    chatlytics restart does not get a synchronized thundering herd.
    """
    base = _BACKOFF_LADDER[min(attempt, len(_BACKOFF_LADDER) - 1)]
    return base * (1.0 + random.uniform(0.0, _BACKOFF_JITTER_FRAC))


def _token_fingerprint(token: str, length: int = 8) -> str:
    """8-char SHA256 fingerprint of an auth token for safe log lines.

    INV-02 (chatlytics v4.0 invariant — token plaintext discipline): bot
    tokens MUST NEVER appear in logs. This helper produces the same 8-char
    fingerprint shape that the chatlytics-side ``tokenFingerprint`` helper
    emits, so an operator grepping logs across plugin + gateway sees
    matching fingerprints for the same token.
    """
    if not token:
        return "<empty>"
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:length]


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


def _read_file_sync(path: str) -> Tuple[bytes, str]:
    """Synchronous file read for ``asyncio.to_thread`` offload.

    Returns ``(content_bytes, basename)``. Used by both the
    explicit-``Path`` and string-path-that-exists branches in
    :meth:`ChatlyticsAdapter._resolve_media_url` (HERMES-15).
    Module-level so the two branches share one implementation site
    instead of defining identical inner functions (LOW-01 fix from
    Phase 15 code review).
    """
    with open(path, "rb") as fh:
        return fh.read(), os.path.basename(path) or "upload.bin"


class _RemovedMethod:
    """Descriptor that raises ``AttributeError`` on access.

    Shadows an inherited base-class method so v3.0 BREAKING removals
    surface as a clear migration error instead of silently inheriting
    the base implementation. Used by :class:`ChatlyticsAdapter` to
    block access to the removed ``send_image_file`` method (HERMES-15).

    Compared to overriding ``__getattribute__``, the descriptor pays
    its cost only at the removed-method's access site — all other
    attribute lookups go through the normal C-level slot without
    paying a per-access Python comparison.
    """

    def __init__(self, message: str) -> None:
        self._message = message

    def __get__(self, obj: Any, objtype: Any = None) -> Any:
        raise AttributeError(self._message)

    def __set_name__(self, owner: type, name: str) -> None:
        # Stash the attribute name for nicer repr / introspection.
        self._name = name


# HERMES-10 (03-LOW-01 + PR-MED-01): webhook_path validation at __init__.
# C0 + DEL control range; rejected so adapters fail fast on copy-paste glitches
# and possible injection attempts. Module-level constant so the validator and
# its tests share one source of truth.
_CONTROL_CHARS: str = "".join(chr(i) for i in range(32)) + "\x7f"


# HERMES-18 (closes v2.1 Phase 9 INFO-02): module-level constant for the
# reserved top-level keys of the /api/v1/send request body. ``send()``
# assigns these keys unconditionally (chatId/text) or conditionally
# (accountId/replyTo) above the metadata-merge block and must NOT let a
# caller-passed metadata kwarg shadow them. Lifting the set here ensures
# a future contributor who adds a new top-level body field updates the
# reserved-key check in lockstep instead of silently regressing the
# 02-LOW-01 WARNING contract.
# v4.4.0 (P7): ``edit_message_id`` and ``progress`` joined the reserved set —
# they are server-interpreted /api/v1/send body fields (progress-bubble
# edit-in-place) that the adapter assigns itself; caller metadata must not
# be able to inject them.
_RESERVED_BODY_KEYS: frozenset = frozenset(
    {"chatId", "text", "accountId", "replyTo", "session", "edit_message_id", "progress"}
)


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


def _envelope_to_body(
    env: Dict[str, Any], *, text: Optional[str] = None
) -> Dict[str, Any]:
    """Translate one longpoll InboundEnvelope into a webhook-shaped body.

    v4.5.1 (review-d3 X20): single implementation shared by
    ``_dispatch_envelope`` (message dispatch) and ``_control_event``
    (control-envelope SessionSource derivation) — previously two hand-kept
    copies with a "keep in sync!" comment. The shape feeds
    :func:`inbound.normalize_payload`, so the SessionSource (and therefore
    the hermes session key) is identical on both paths by construction.

    ``text`` overrides the envelope's text (control events pass a cosmetic
    "/new" / "/stop" label; message dispatch uses the envelope's own text).
    """
    return {
        "chatId": env["entity_jid"],          # required by normalize_payload
        "text": env.get("text", "") if text is None else text,
        "senderId": env.get("sender_jid"),
        "chatType": (
            "channel"
            if env.get("chat_type") == "newsletter"
            else env.get("chat_type") or "dm"
        ),
        "session": env.get("session_id"),
    }


try:
    from gateway.platforms.base import BasePlatformAdapter, SendResult
    from gateway.config import Platform, PlatformConfig
    from gateway.session import build_session_key

    _HERMES_AVAILABLE = True
except ImportError:  # hermes-agent not installed (e.g. acceptance criterion 1)
    BasePlatformAdapter = object  # type: ignore[assignment, misc]
    SendResult = None  # type: ignore[assignment]
    Platform = None  # type: ignore[assignment]
    PlatformConfig = None  # type: ignore[assignment]
    build_session_key = None  # type: ignore[assignment]

    _HERMES_AVAILABLE = False


logger = logging.getLogger("chatlytics_hermes.adapter")


class ChatlyticsConnectError(RuntimeError):
    """Raised when the adapter cannot complete its health check on connect."""


class ChatlyticsLookupError(RuntimeError):
    """Raised by :meth:`ChatlyticsAdapter.get_chat_info` on lookup failures.

    Carries a machine-readable ``code`` so the tool-layer wrapper
    (:func:`chatlytics_hermes.tools.chatlytics_get_chat_info`) can
    translate the exception into the v3.0 canonical failure dict
    (``{"success": False, "error": str, "_error": code}``) without
    re-classifying the underlying cause.

    Codes (lowercase snake_case):

    - ``transport_error``  — :class:`httpx.RequestError`
      (network / timeout / DNS / connection-refused).
    - ``auth_error``       — HTTP 401 / 403.
    - ``server_error``     — HTTP 5xx.
    - ``validation_error`` — HTTP 4xx other than 401/403. **404 from
      the gateway for an unknown chatId is `validation_error`** (the
      JID was malformed or unknown — NOT a "chat-not-found legitimate
      empty"; the empty branch is reserved for HTTP 200 + falsy body).
    - ``unknown_error``    — non-JSON body on 2xx, unexpected raise,
      or adapter-not-connected.

    Introduced in HERMES-13 (v3.0 BREAKING). The v2.1 bare-``{}``
    return on lookup errors is gone. See the v3.0 CHANGELOG entry
    "BREAKING — get_chat_info return shape" for migration guidance.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


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
                "hermes-agent>=0.14,<1.0 must be installed to instantiate "
                "ChatlyticsAdapter. Install with: "
                "pip install 'hermes-agent>=0.14,<1.0'"
            )

        super().__init__(config=config, platform=Platform("chatlytics"))

        extra: Dict[str, Any] = getattr(config, "extra", {}) or {}

        # Connection settings (env vars override config.yaml ``extra`` block).
        # v4.1.0: base_url defaults to the public DNS name so token-only
        # onboarding works with no URL set. Env var > config.yaml extra >
        # DNS default. An explicit empty value in either source still falls
        # through to the default.
        self.base_url: str = (
            os.getenv("CHATLYTICS_BASE_URL")
            or extra.get("base_url")
            or "https://node.chatlytics.ai"
        )
        # HERMES-V2 (Phase 336): bot_token is the PREFERRED auth mechanism in
        # chatlytics v4.0. Falls back to api_key (legacy) when bot_token is
        # absent. Resolution precedence (highest first):
        #   1. CHATLYTICS_BOT_TOKEN env var
        #   2. extra.bot_token   (config.yaml chatlytics.extra block)
        #   3. CHATLYTICS_API_KEY env var (legacy)
        #   4. extra.api_key     (legacy config.yaml fallback)
        # The empty-string sentinel from the first three falsy results lets
        # the next branch take over; final empty string defers the error to
        # connect() time so registration phase doesn't crash on partial env.
        self.bot_token: str = (
            os.getenv("CHATLYTICS_BOT_TOKEN") or extra.get("bot_token", "") or ""
        )
        self.api_key: str = os.getenv("CHATLYTICS_API_KEY") or extra.get("api_key", "")
        # _auth_token: the actual Bearer the client uses. Both shapes flow
        # through the SAME header; the chatlytics gateway distinguishes by
        # token shape (sk_bot_ prefix → resolveBotFromBearer; otherwise →
        # requirePublicApiAuth). Plugin stays agnostic to the gateway path.
        self._auth_token: str = self.bot_token or self.api_key
        # v4.1.5 (telegram-style onboarding): degraded "no-credential" state.
        # Set True by connect() when no auth token is configured. When True the
        # adapter has loaded (platform registered, tools callable) but has NO
        # authed client — every data tool short-circuits with NO_TOKEN_PROMPT
        # instead of hitting the gateway. False on every token'd deployment, so
        # the existing path is byte-for-byte unchanged when a token IS present.
        self._no_credential: bool = False
        self.account_id: Optional[str] = (
            os.getenv("CHATLYTICS_ACCOUNT_ID") or extra.get("account_id")
        )
        # P-19 fix (carried forward from hpg6's running tree): Chatlytics
        # gateway's /api/v1/send REQUIRES `session` (the WAHA session name,
        # e.g. "3cf11776_logan"). Without it the server returns 400
        # "chatId and session are required" and every reply fails.
        # Resolution order at send() time:
        #   1. per-chat session recorded on inbound (webhook handler OR
        #      longpoll consumer) via register_chat_session() — preferred,
        #      works multi-tenant. The v4.1 longpoll InboundEnvelope ALWAYS
        #      carries session_id, so longpoll deployments populate this map
        #      on every inbound message.
        #   2. self.session_name (env var / extra fallback for single-tenant
        #      operator config; required for webhook deployments where the
        #      chatlytics-server hermes transform does not forward `session`)
        self.session_name: Optional[str] = (
            os.getenv("CHATLYTICS_SESSION") or extra.get("session")
        )
        # Per-chat session map populated by inbound (webhook / longpoll).
        # Bounded growth: each chat_id is a WhatsApp JID (under ~50 chars),
        # collection naturally tracks active conversations only.
        self._chat_session_map: Dict[str, str] = {}

        # v4.3.0 (control envelopes): bounded LRU of the last NORMAL message
        # envelope dispatched per chat (entity_jid -> envelope dict), consumed
        # by the ``retry_last`` control action. See _remember_last_message.
        self._last_message_memo: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        # v4.3.0: warn-once dedup for unknown envelope kinds / control
        # actions so a stream of unknown envelopes does not spam the log
        # per envelope. Bounded by _UNKNOWN_CONTROL_WARN_CAP.
        self._unknown_control_warned: set = set()

        # v4.1 longpoll consumer: asyncio.Task handle for the inbound poll
        # loop, started in connect() when inbound_mode == "longpoll" and
        # cancelled in disconnect(). None when in webhook mode (default).
        self._poll_task: Optional[asyncio.Task] = None
        # Inbound transport mode: "webhook" (default — PRESERVES the
        # existing aiohttp webhook-server behavior for all deployments) or
        # "longpoll" (v4.1 — PULL inbound via GET /api/v1/bot/updates).
        self.inbound_mode: str = (
            os.getenv("CHATLYTICS_INBOUND_MODE")
            or extra.get("inbound_mode")
            or "webhook"
        ).strip().lower()

        # v4.4.0 (chatlytics v5.4 P7): progress-bubble edit-in-place knobs.
        # Default ON — zero behavior change for fast turns (no bubble is ever
        # sent before the threshold elapses, so no edit happens either).
        # Flag False disables BOTH the bubble and the edit entirely →
        # byte-identical to v4.3.0. Env wins over the extra block (matches
        # the inbound_mode precedence pattern above); env values like
        # "false"/"0"/"off" disable.
        _seip_raw: Any = os.getenv("CHATLYTICS_STATUS_EDIT_IN_PLACE")
        if _seip_raw is None:
            _seip_raw = extra.get("status_edit_in_place")
        self.status_edit_in_place: bool = _coerce_flag(_seip_raw, default=True)
        # Threshold (seconds) before the ONE "working…" bubble fires; <= 0
        # disables the bubble. Parsed defensively — a bad value falls back
        # to _STATUS_BUBBLE_AFTER_DEFAULT_S instead of raising at gateway boot.
        _bubble_after_raw: Any = os.getenv("CHATLYTICS_STATUS_BUBBLE_AFTER_S")
        if _bubble_after_raw is None:
            _bubble_after_raw = extra.get("status_bubble_after_s")
        try:
            self.status_bubble_after_s: float = (
                float(_bubble_after_raw)
                if _bubble_after_raw is not None
                else _STATUS_BUBBLE_AFTER_DEFAULT_S
            )
        except (TypeError, ValueError):
            self.status_bubble_after_s = _STATUS_BUBBLE_AFTER_DEFAULT_S
        self.status_bubble_text: str = str(
            os.getenv("CHATLYTICS_STATUS_BUBBLE_TEXT")
            or extra.get("status_bubble_text")
            or "⏳ working…"
        )
        # chat_id -> (waha message id of the un-consumed bubble, monotonic
        # timestamp). Bounded LRU (cap _PROGRESS_BUBBLE_MAX); entries older
        # than _PROGRESS_BUBBLE_TTL_S are stale and ignored on pop. Exactly
        # ONE pending bubble per chat (turn) — see _send_progress_bubble.
        self._progress_bubbles: "OrderedDict[str, Tuple[str, float]]" = OrderedDict()

        # v4.5.0 (chatlytics v5.4 P8): pending owner-DM questions. request_id
        # -> {"kind": "approval"|"clarify"|"future", "session_key": str|None,
        #     "clarify_id": str|None, "future": asyncio.Future|None,
        #     "chat_id": str, "created": float (monotonic)}. Bounded FIFO
        # (cap _PENDING_QUESTIONS_MAX, warn on evict) + stale pruning on
        # insert (_PENDING_QUESTION_TTL_S). Resolved entries are popped by
        # _control_question_resolved when the owner's /approve /deny /answer
        # reply arrives as a question_resolved control envelope.
        self._pending_questions: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()

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

    # --- Session threading (P-19, carried forward) ------------------------

    def register_chat_session(self, chat_id: str, session: str) -> None:
        """Record the WAHA session name observed for ``chat_id`` on inbound.

        Called by the inbound webhook handler (when a payload carries a
        top-level ``session``) AND by the v4.1 longpoll consumer (every
        InboundEnvelope carries ``session_id``). The mapping is read back in
        :meth:`_resolve_session_for_chat` so the outbound ``/api/v1/send``
        body includes the same WAHA session the message originated on
        (required by the Chatlytics gateway since v3.x — without it the
        server returns 400 "chatId and session are required").
        """
        if not chat_id or not session:
            return
        self._chat_session_map[chat_id] = session

    def _resolve_session_for_chat(self, chat_id: str) -> Optional[str]:
        """Return the best-known WAHA session name for ``chat_id``.

        Order:
            1. per-chat map populated by inbound (webhook / longpoll)
            2. ``self.session_name`` (env var / extra config fallback)

        Returns ``None`` if neither is available — the caller surfaces a
        clear error so misconfigured adapters fail loudly at send() time
        instead of silently dropping replies.
        """
        mapped = self._chat_session_map.get(chat_id)
        if mapped:
            return mapped
        return self.session_name

    def _build_send_body(
        self, chat_id: str, text: str
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """Build the base /api/v1/send body shared by send() and the bubble.

        v4.4.0 (P7): factored out of :meth:`send` so the progress-bubble
        emitter reuses the EXACT same chatId/text/session/accountId
        resolution instead of duplicating the P-19 session logic. Returns
        ``(body, None)`` on success or ``(None, error_message)`` when no
        WAHA session can be resolved for ``chat_id`` (the caller decides
        whether that is fail-loud — send() — or skip-quietly — bubble).
        """
        body: Dict[str, Any] = {
            "chatId": chat_id,
            "text": text,
        }
        # P-19 fix (carried forward): Chatlytics gateway's /api/v1/send
        # REQUIRES `session` (the WAHA session name). Without it the server
        # returns 400 "chatId and session are required" and the reply
        # silently dies. Resolution: per-chat map populated by inbound
        # (longpoll envelopes ALWAYS carry session_id; webhook payloads carry
        # it when chatlytics forwards it) falling back to CHATLYTICS_SESSION.
        session_name = self._resolve_session_for_chat(chat_id)
        if session_name:
            body["session"] = session_name
        else:
            return None, (
                "Chatlytics adapter missing WAHA session for chat "
                f"{chat_id!r}: set CHATLYTICS_SESSION env var "
                "(e.g. 3cf11776_logan) or pass session= in the "
                "platform extra block. Inbound-derived session "
                "mapping is empty for this chat."
            )
        if self.account_id:
            body["accountId"] = self.account_id
        return body, None

    # --- Progress bubbles (v4.4.0 — chatlytics v5.4 P7) ---------------------

    def _store_progress_bubble(self, chat_id: str, message_id: str) -> None:
        """Record the un-consumed bubble id for ``chat_id`` (LRU-bounded)."""
        self._progress_bubbles[chat_id] = (message_id, time.monotonic())
        self._progress_bubbles.move_to_end(chat_id)
        while len(self._progress_bubbles) > _PROGRESS_BUBBLE_MAX:
            self._progress_bubbles.popitem(last=False)

    def _pop_progress_bubble(self, chat_id: str) -> Optional[str]:
        """Pop and return the fresh pending bubble id for ``chat_id``.

        Returns ``None`` when no bubble is pending OR the entry is older
        than :data:`_PROGRESS_BUBBLE_TTL_S` (stale — the turn it belonged
        to is long gone; the entry is dropped either way so it can never
        be consumed twice).
        """
        entry = self._progress_bubbles.pop(chat_id, None)
        if entry is None:
            return None
        message_id, ts = entry
        if time.monotonic() - ts > _PROGRESS_BUBBLE_TTL_S:
            logger.debug(
                "progress bubble for chat %s is stale; dropping", chat_id
            )
            return None
        return message_id

    def _has_pending_progress_bubble(self, chat_id: str) -> bool:
        """True when a fresh un-consumed bubble exists for ``chat_id``.

        One-bubble-per-turn guard for :meth:`_send_progress_bubble`. Stale
        entries are evicted on inspection (and report False).
        """
        entry = self._progress_bubbles.get(chat_id)
        if entry is None:
            return False
        if time.monotonic() - entry[1] > _PROGRESS_BUBBLE_TTL_S:
            self._progress_bubbles.pop(chat_id, None)
            return False
        return True

    @staticmethod
    def _extract_message_id(payload: Any) -> Optional[str]:
        """Dig the normalized WAHA message id out of a /api/v1/send response.

        chatlytics v5.4 P7 servers return top-level ``message_id``; older /
        intermediate shapes used ``messageId``; defensive fallbacks dig the
        raw WAHA echo (``waha.id._serialized`` / ``waha.id`` / ``waha.key.id``)
        so the edit-in-place feature degrades to "no edit" rather than
        crashing on a response-shape drift.
        """
        if not isinstance(payload, dict):
            return None
        for key in ("message_id", "messageId"):
            mid = payload.get(key)
            if isinstance(mid, str) and mid:
                return mid
        waha = payload.get("waha")
        if isinstance(waha, dict):
            wid = waha.get("id")
            if isinstance(wid, dict):
                for key in ("_serialized", "id"):
                    ser = wid.get(key)
                    if isinstance(ser, str) and ser:
                        return ser
            elif isinstance(wid, str) and wid:
                return wid
            wkey = waha.get("key")
            if isinstance(wkey, dict):
                kid = wkey.get("id")
                if isinstance(kid, str) and kid:
                    return kid
        return None

    async def _send_progress_bubble(self, chat_id: str) -> None:
        """POST the ONE "working…" bubble for ``chat_id`` and memo its id.

        Failure philosophy mirrors :meth:`_send_typing_once`: the bubble is
        a UX affordance — every failure path logs at DEBUG and returns
        without raising, so a flapping gateway can never affect the agent
        turn it decorates. The bubble body carries ``progress: true`` so the
        server suppresses reaction-feedback correlation for it.
        """
        if self._has_pending_progress_bubble(chat_id):
            logger.debug(
                "progress bubble already pending for chat %s; not sending another",
                chat_id,
            )
            return
        if self._client is None or self._no_credential or not self._auth_token:
            logger.debug(
                "progress bubble skipped for chat %s: adapter not connected "
                "or no credential",
                chat_id,
            )
            return
        body, err = self._build_send_body(chat_id, self.status_bubble_text)
        if body is None:
            logger.debug("progress bubble skipped: %s", err)
            return
        body["progress"] = True
        try:
            response = await self._client.post("/api/v1/send", json=body)
        except httpx.RequestError as exc:
            logger.debug(
                "progress bubble transport error for chat %s: %s", chat_id, exc
            )
            return
        if response.status_code != 200:
            logger.debug(
                "progress bubble send returned HTTP %d for chat %s",
                response.status_code,
                chat_id,
            )
            return
        try:
            payload: Any = response.json()
        except Exception:  # noqa: BLE001
            payload = None
        message_id = self._extract_message_id(payload)
        if not message_id:
            logger.debug(
                "progress bubble response carried no message id for chat %s; "
                "edit-in-place unavailable for this turn",
                chat_id,
            )
            return
        self._store_progress_bubble(chat_id, message_id)
        logger.debug(
            "progress bubble sent for chat %s (message_id=%s)",
            chat_id,
            message_id,
        )

    async def _progress_bubble_timer(
        self, chat_id: str, stop_event: Optional[asyncio.Event]
    ) -> None:
        """Wait ``status_bubble_after_s``; if the turn is still running, bubble.

        Spawned by :meth:`_keep_typing` (the only in-turn hook the adapter
        has). When ``stop_event`` fires before the threshold (the common
        fast-turn case) NO bubble is sent and the request stream is
        byte-identical to v4.3.0. Errors never propagate (DEBUG only).
        """
        try:
            if stop_event is not None:
                try:
                    await asyncio.wait_for(
                        stop_event.wait(), timeout=self.status_bubble_after_s
                    )
                    return  # turn finished before the threshold — no bubble
                except asyncio.TimeoutError:
                    pass
            else:
                await asyncio.sleep(self.status_bubble_after_s)
            await self._send_progress_bubble(chat_id)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 -- UX affordance, never propagate
            logger.debug(
                "progress bubble timer raised for chat %s; continuing",
                chat_id,
                exc_info=True,
            )

    # --- Longpoll inbound consumer (v4.1) ---------------------------------

    async def _poll_loop(self) -> None:
        """PULL inbound messages from chatlytics via long-poll.

        Replaces the webhook PUSH transport when ``inbound_mode ==
        "longpoll"``. Implements the chatlytics v4.0 bot-updates contract:

        - ``GET  /api/v1/bot/updates?cursor=<opaque>&timeout_ms=25000``
          long-polls (server clamps timeout <= 60000), returning
          ``{envelopes: InboundEnvelope[], cursor: str}``.
        - ``POST /api/v1/bot/updates/ack {cursor}`` advances the per-bot
          read pointer. The GET does NOT advance it — envelopes re-deliver
          until acked, so we ack AFTER processing every non-empty batch.

        Error discipline (never let the loop die silently — reworked in
        v4.2.0 P3 survivability):
          - httpx transport error (refused/reset/timeout) / non-200 /
            empty-200 graceful shutdown: NORMAL retry events. Bounded
            jittered backoff via the 1→2→5→15→30s :data:`_BACKOFF_LADDER`
            (never gives up), reset on success.
          - State-change logging ONLY: one WARNING on healthy→degraded
            (with the :func:`map_connect_error` actionable hint), one INFO
            on degraded→healthy. Per-attempt records stay at DEBUG so a
            long chatlytics outage cannot flood operator logs.
          - empty 200 + Connection: close — chatlytics v5.4 longpoll
            graceful shutdown (server restarting). Detected as a 200 with
            an EMPTY body (a normal empty batch is a JSON object); treated
            as a retry event, NOT an error, so restarts don't busy-spin.
            A normal empty JSON batch still loops immediately (healthy
            path byte-for-byte preserved).
          - 400 invalid_cursor: reset cursor to "" and continue.
          - 401 bot_token_required: ERROR on state change (token
            bad/revoked, with rotate guidance) + capped backoff.
          - asyncio.CancelledError: exit cleanly (disconnect()).
        """
        cursor: str = ""
        # Consecutive-failure count (indexes _BACKOFF_LADDER) and the
        # degraded-state label (None == healthy). Both reset on success.
        failures: int = 0
        degraded: Optional[str] = None

        def _note_degraded(
            reason: str,
            hint: str,
            *,
            level: int = logging.WARNING,
            reason_class: Optional[str] = None,
        ) -> float:
            """Record one failure; log ONCE per healthy→degraded transition.

            v4.5.1 (review-d3 H9): a degraded-reason CLASS change (e.g.
            transport error → HTTP 401) also logs at ``level`` — that is
            operator-relevant signal, not a repeat. ``reason_class`` is the
            STABLE comparison key (defaults to ``reason``); transport
            errors pass an exc-detail-free class so a flapping network that
            alternates refused/reset/timeout details still logs exactly one
            WARNING per degraded episode (the P3 no-log-flood contract).

            Returns the jittered backoff delay for this attempt.
            """
            nonlocal failures, degraded
            cls_key = reason_class or reason
            delay = _backoff_delay(failures)
            failures += 1
            if degraded is None:
                logger.log(
                    level,
                    "longpoll degraded: %s — %s (retrying with bounded "
                    "backoff, %.1fs now, 30s cap; will log on recovery)",
                    reason,
                    hint,
                    delay,
                )
            elif degraded != cls_key:
                logger.log(
                    level,
                    "longpoll degraded reason changed: %s -> %s — %s "
                    "(attempt %d, backing off %.1fs)",
                    degraded,
                    reason,
                    hint,
                    failures,
                    delay,
                )
            else:
                logger.debug(
                    "longpoll still degraded (%s); attempt %d, backing off %.1fs",
                    reason,
                    failures,
                    delay,
                )
            degraded = cls_key
            return delay

        def _note_healthy() -> None:
            nonlocal failures, degraded
            if degraded is not None:
                logger.info(
                    "longpoll recovered (healthy again after: %s)", degraded
                )
            degraded = None
            failures = 0

        logger.info(
            "chatlytics inbound: longpoll loop started (polling /api/v1/bot/updates)"
        )
        # v4.5.1 (review-d3 X1): error classes already reported at ERROR by
        # the catch-all below. The full traceback logs ONCE per distinct
        # class (then DEBUG) so a hot unexpected error cannot flood the log
        # while still being unmissable the first time.
        unexpected_seen: set = set()

        while self._running:
            client = self._client
            if client is None:
                # connect() always sets _client before starting the task,
                # but guard defensively against a teardown race.
                return
            # v4.5.1 (review-d3 X1 CRITICAL): the ENTIRE iteration is wrapped
            # so NO exception other than CancelledError can ever kill the
            # poll task — a dead poll task is a silently dead bot (no done-
            # callback fired pre-v4.5.1, reference held, never surfaced).
            # Known transport / HTTP / shutdown signals are still handled by
            # the inner branches; anything they miss lands in the catch-all
            # at the bottom and takes the SAME bounded-backoff retry path.
            try:
                try:
                    resp = await client.get(
                        "/api/v1/bot/updates",
                        # v4.3.0: ``caps=control`` advertises control-envelope
                        # support on EVERY poll (chatlytics v5.4 capability
                        # negotiation — server-side, per-poll, no handshake).
                        # This is the ONLY change to the longpoll request shape;
                        # cursor/timeout semantics are untouched.
                        params={
                            "cursor": cursor,
                            "timeout_ms": _LONGPOLL_TIMEOUT_MS,
                            "caps": _LONGPOLL_CAPS,
                        },
                        # Read timeout MUST exceed the server hold (timeout_ms) or
                        # every empty poll times out. read = hold + 15s buffer.
                        timeout=httpx.Timeout(
                            connect=10.0,
                            read=(_LONGPOLL_TIMEOUT_MS / 1000) + 15.0,
                            write=10.0,
                            pool=10.0,
                        ),
                    )
                except asyncio.CancelledError:
                    raise
                except httpx.RequestError as exc:
                    # Connection refused/reset/timeout: normal retry events
                    # (chatlytics restart, transient network). Backoff + retry.
                    delay = _note_degraded(
                        f"transport error ({exc or type(exc).__name__})",
                        map_connect_error(exc=exc),
                        # H9: stable class — refused/reset/timeout detail
                        # variations within one episode stay at DEBUG.
                        reason_class="transport error",
                    )
                    await asyncio.sleep(delay)
                    continue

                if resp.status_code == 400:
                    # Bad/expired cursor — reset and re-poll from the tail.
                    logger.warning(
                        "longpoll GET returned 400 (invalid_cursor); resetting cursor"
                    )
                    cursor = ""
                    continue
                if resp.status_code == 401:
                    delay = _note_degraded(
                        "HTTP 401 (bot_token_required)",
                        map_connect_error(status_code=401),
                        level=logging.ERROR,
                    )
                    # Token problems don't self-heal fast — wait at the cap.
                    await asyncio.sleep(max(delay, _BACKOFF_LADDER[-1]))
                    continue
                if resp.status_code != 200:
                    delay = _note_degraded(
                        f"HTTP {resp.status_code}",
                        map_connect_error(status_code=resp.status_code),
                    )
                    await asyncio.sleep(delay)
                    continue

                # v4.2.0 (P3): chatlytics v5.4 longpoll graceful shutdown —
                # parked longpolls get an EMPTY 200 + Connection: close when the
                # server restarts. A normal empty batch is a JSON object
                # ({"envelopes": [], ...}), so an empty BODY unambiguously means
                # "server going down; reconnect later". Back off instead of
                # busy-spinning into the connection-refused window.
                if not (resp.content or b"").strip():
                    delay = _note_degraded(
                        "server closed poll (graceful shutdown — chatlytics restarting)",
                        "the plugin reconnects automatically with backoff",
                    )
                    await asyncio.sleep(delay)
                    continue

                # Successful poll — reset backoff / degraded state.
                _note_healthy()
                try:
                    data = resp.json()
                except Exception:  # noqa: BLE001 -- JSONDecodeError + httpx variants
                    logger.warning("longpoll GET returned non-JSON body; skipping")
                    continue
                if not isinstance(data, dict):
                    logger.warning("longpoll GET body was not a JSON object; skipping")
                    continue

                envelopes = data.get("envelopes") or []
                next_cursor = data.get("cursor", cursor)

                if not envelopes:
                    # Timeout / empty batch: advance cursor to the returned
                    # value and re-poll. No ack needed for an empty batch.
                    cursor = next_cursor if isinstance(next_cursor, str) else cursor
                    continue

                for env in envelopes:
                    try:
                        await self._dispatch_envelope(env)
                    except asyncio.CancelledError:
                        raise
                    except Exception:  # noqa: BLE001 -- one bad envelope must not
                        # kill the loop or block the ack for the rest of the batch.
                        logger.exception(
                            "longpoll: failed to dispatch one envelope; continuing"
                        )

                # Advance + persist the read pointer. Ack AFTER processing so an
                # in-flight crash re-delivers unprocessed envelopes.
                if isinstance(next_cursor, str):
                    cursor = next_cursor
                try:
                    ack_resp = await client.post(
                        "/api/v1/bot/updates/ack", json={"cursor": cursor}
                    )
                except asyncio.CancelledError:
                    raise
                except httpx.RequestError as exc:
                    # Ack failed — envelopes will re-deliver on the next GET.
                    # Don't advance past them: keep cursor as-is and retry.
                    logger.warning(
                        "longpoll ack POST transport error (%s); envelopes will "
                        "re-deliver on next poll",
                        exc,
                    )
                else:
                    # v4.5.1 (review-d3 H8): a non-200 ack was previously
                    # IGNORED — the read pointer silently failed to advance
                    # and envelopes re-delivered forever with no log trail.
                    if ack_resp.status_code == 401:
                        logger.error(
                            "longpoll ack POST rejected with HTTP 401 — bot "
                            "token rejected (rotated/revoked?); %s",
                            map_connect_error(status_code=401),
                        )
                    elif ack_resp.status_code != 200:
                        logger.warning(
                            "longpoll ack POST returned HTTP %d; the read "
                            "pointer did not advance and envelopes will "
                            "re-deliver on the next poll",
                            ack_resp.status_code,
                        )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 -- X1: loop must NEVER die
                cls = type(exc).__name__
                if cls not in unexpected_seen:
                    unexpected_seen.add(cls)
                    logger.exception(
                        "longpoll loop hit an unexpected %s — the loop "
                        "continues with bounded backoff (traceback logged "
                        "once per error class)",
                        cls,
                    )
                else:
                    logger.debug(
                        "longpoll loop: repeat unexpected %s (%s); backing off",
                        cls,
                        exc,
                    )
                delay = _note_degraded(
                    f"unexpected {cls}",
                    "internal error — see the traceback logged above",
                    level=logging.ERROR,
                )
                await asyncio.sleep(delay)

    async def _dispatch_envelope(self, env: Dict[str, Any]) -> None:
        """Translate one InboundEnvelope -> MessageEvent and dispatch it.

        InboundEnvelope shape (chatlytics v4.0 bot-updates contract)::

            { bot_token, session_id, chat_type: "dm"|"group"|"newsletter",
              entity_jid, sender_jid, text, dispatch:{reason,god_mode}, ts }

        We reuse the existing :func:`inbound.normalize_payload` by building
        the webhook-shaped ``body`` it already understands, then thread the
        WAHA session via :meth:`register_chat_session` (every envelope
        carries ``session_id``) so the outbound reply resolves the correct
        session.

        v4.3.0 (chatlytics v5.4 P6 — control envelopes): each envelope MAY
        now carry a ``kind`` discriminator. Routing rules (wire contract,
        FIXED — keep in lockstep with the chatlytics server):

        - absent ``kind`` OR ``kind == "message"`` → the existing message
          path below, byte-for-byte (plus the retry_last memo record).
        - ``kind == "control"``      → :meth:`_handle_control_envelope`.
        - any other ``kind``         → log once + IGNORE (forward-compat).
          NEVER dispatched as message text to the agent.

        Control envelopes ride the same seq space and are acked exactly
        like message envelopes — the batch-level ack in :meth:`_poll_loop`
        is unchanged.
        """
        kind = env.get("kind")
        if kind is not None and kind != "message":
            if kind == "control":
                await self._handle_control_envelope(env)
            else:
                self._warn_unknown_control(
                    f"kind={kind!r}",
                    "longpoll envelope with unknown kind %r — ignoring "
                    "(forward-compat; NOT dispatched as message text)",
                    kind,
                )
            return

        # v4.3.0: record the last normal message per chat so a subsequent
        # ``retry_last`` control envelope can re-dispatch it. Recorded
        # BEFORE dispatch on purpose: a turn that crashes mid-dispatch is
        # exactly what /retry exists to re-run.
        self._remember_last_message(env)

        body = _envelope_to_body(env)
        # Thread the WAHA session BEFORE dispatch so the reply path resolves
        # it. InboundEnvelope always carries session_id under longpoll.
        self.register_chat_session(body["chatId"], body["session"])
        event = normalize_payload(body, self.platform)

        # v4.5.0 (chatlytics v5.4 P8): per-channel prompt injection.
        # ``MessageEvent.channel_prompt`` is the harness's NATIVE per-turn
        # ephemeral system-prompt channel — the runner combines it into the
        # per-turn system prompt at API call time and NEVER persists it to
        # the transcript. The chatlytics server's bot_module_config
        # "channel-prompts" module is the source of ``env["channel_prompt"]``
        # (an additive MESSAGE-envelope field, absent when none configured);
        # the local config.yaml ``channel_prompts`` map (resolved via the
        # harness's resolve_channel_prompt) is the fallback. retry_last
        # replays stay correct automatically: the memoized envelope carries
        # the field, so a re-dispatch re-runs this exact block.
        cp = env.get("channel_prompt")
        if not (isinstance(cp, str) and cp.strip()):
            try:
                from gateway.platforms.base import resolve_channel_prompt

                cp = resolve_channel_prompt(
                    getattr(self.config, "extra", None) or {}, body["chatId"]
                )
            except Exception as exc:  # noqa: BLE001 -- harness-drift tolerance
                # v4.5.1 (review-d3 H7): version drift is operator-relevant —
                # the local channel_prompts config silently never applies.
                # WARNING once (warn-once dedup), DEBUG thereafter.
                self._warn_unknown_control(
                    "channel_prompt:resolver_unavailable",
                    "channel-prompt config fallback unavailable: "
                    "gateway.platforms.base.resolve_channel_prompt missing or "
                    "raised (%s: %s) — hermes-agent version drift; local "
                    "channel_prompts config will NOT apply",
                    type(exc).__name__,
                    exc,
                )
                cp = None
        # hasattr guard = harness-drift tolerance (older MessageEvent
        # dataclasses without the field must not grow a stray attribute).
        if isinstance(cp, str) and cp.strip():
            if hasattr(event, "channel_prompt"):
                event.channel_prompt = cp.strip()
            else:
                # v4.5.1 (review-d3 H7): a configured prompt was DROPPED —
                # name the missing attribute so the operator knows why.
                self._warn_unknown_control(
                    "channel_prompt:event_attr_missing",
                    "MessageEvent has no channel_prompt attribute "
                    "(hermes-agent too old for per-channel prompts) — the "
                    "configured channel prompt was dropped for chat %s",
                    body["chatId"],
                )

        await self.handle_message(event)

    # --- Control envelopes (v4.3.0 — chatlytics v5.4 P6) -------------------

    def _remember_last_message(self, env: Dict[str, Any]) -> None:
        """Record ``env`` as the last normal message for its chat (LRU-bounded).

        Keyed by ``entity_jid`` (the chat key per the v5.4 control
        contract). The stored value is a shallow copy of the envelope —
        the minimal unit ``retry_last`` needs, because re-dispatch feeds
        it straight back through :meth:`_dispatch_envelope` (same
        normalization, session-threading, and handle_message path as the
        original delivery). Bounded at :data:`_LAST_MESSAGE_MEMO_MAX`
        chats, one (the last) message per chat.
        """
        chat_id = env.get("entity_jid")
        if not isinstance(chat_id, str) or not chat_id:
            return
        self._last_message_memo[chat_id] = dict(env)
        self._last_message_memo.move_to_end(chat_id)
        while len(self._last_message_memo) > _LAST_MESSAGE_MEMO_MAX:
            self._last_message_memo.popitem(last=False)

    def _warn_unknown_control(self, dedup_key: str, msg: str, *args: Any) -> None:
        """WARNING once per distinct unknown kind/action; DEBUG thereafter.

        Keeps the forward-compat ignore path from spamming one WARNING per
        envelope when a newer chatlytics server emits values this plugin
        predates. The dedup set is capped (``_UNKNOWN_CONTROL_WARN_CAP``)
        so unbounded distinct unknown values cannot grow memory; past the
        cap every occurrence logs WARNING again (noisy beats silent).
        """
        if dedup_key in self._unknown_control_warned:
            logger.debug(msg, *args)
            return
        if len(self._unknown_control_warned) < _UNKNOWN_CONTROL_WARN_CAP:
            self._unknown_control_warned.add(dedup_key)
        logger.warning(msg, *args)

    def _control_event(self, env: Dict[str, Any], text: str) -> Any:
        """Build a MessageEvent (for its SessionSource) from a control envelope.

        Shares :func:`_envelope_to_body` with :meth:`_dispatch_envelope`
        (v4.5.1 review-d3 X20 dedup — previously two hand-kept copies) so
        the SessionSource — and therefore the session key — derived for a
        control envelope is IDENTICAL to the one the chat's normal messages
        produce. ``text`` is cosmetic (the event is never dispatched to the
        agent; it only feeds harness APIs that take an event/source).
        """
        return normalize_payload(_envelope_to_body(env, text=text), self.platform)

    def _control_session_key(self, source: Any) -> str:
        """Hermes session key for ``source`` — same derivation as inbound.

        Mirrors ``BasePlatformAdapter.handle_message`` exactly (same
        ``build_session_key`` call, same config extras) so control actions
        target the SAME per-chat conversation that normal messages use.
        """
        extra = getattr(self.config, "extra", None) or {}
        return build_session_key(
            source,
            group_sessions_per_user=extra.get("group_sessions_per_user", True),
            thread_sessions_per_user=extra.get("thread_sessions_per_user", False),
        )

    def _gateway_runner(self) -> Any:
        """Best-effort handle on the HermesGateway runner instance.

        The gateway wires ``adapter.set_message_handler(self._handle_message)``
        at startup (gateway/run.py), so the bound method's ``__self__`` IS
        the runner. Used (behind ``hasattr`` feature-detection, never
        required) to reach the runner's own /new and /stop machinery —
        the ONLY code that can fully reset a conversation (session-store
        rotation alone leaves the runner's cached AIAgent, which holds the
        in-memory conversation history, alive). Returns ``None`` when no
        handler is set (tests, partial wiring) — callers degrade gracefully.
        """
        return getattr(getattr(self, "_message_handler", None), "__self__", None)

    async def _handle_control_envelope(self, env: Dict[str, Any]) -> None:
        """Route one ``kind=="control"`` envelope to its action handler.

        Control envelope shape (chatlytics v5.4 wire contract)::

            { kind: "control",
              action: "new_conversation" | "stop" | "retry_last",
              bot_token, session_id, chat_type, entity_jid, sender_jid, ts }

        The chat key is ``entity_jid``. Unknown actions are logged once and
        IGNORED (forward-compat) — never dispatched as message text.
        """
        chat_id = env.get("entity_jid")
        if not isinstance(chat_id, str) or not chat_id:
            logger.warning(
                "control envelope missing entity_jid; ignoring (action=%r)",
                env.get("action"),
            )
            return
        # Keep the WAHA session map fresh — control envelopes carry
        # session_id like message envelopes do.
        session_id = env.get("session_id")
        if isinstance(session_id, str) and session_id:
            self.register_chat_session(chat_id, session_id)

        # v4.5.1 (review-d3 X20): dispatch through _CONTROL_ACTION_HANDLERS
        # (the same map _CONTROL_ACTIONS is derived from) so the supported
        # action set and the routing can never drift apart.
        action = env.get("action")
        handler_name = (
            _CONTROL_ACTION_HANDLERS.get(action) if isinstance(action, str) else None
        )
        if handler_name is None:
            self._warn_unknown_control(
                f"action={action!r}",
                "control envelope with unknown action %r for chat %s — "
                "ignoring (forward-compat; NOT dispatched as message text)",
                action,
                chat_id,
            )
            return
        await getattr(self, handler_name)(env)

    async def _control_new_conversation(self, env: Dict[str, Any]) -> None:
        """``/new`` from WhatsApp: reset the chat's hermes conversation.

        Resolution ladder (most → least complete; each step is
        feature-detected so harness version drift degrades instead of
        breaking):

        1. Runner ``_interrupt_and_clear_session`` + ``_handle_reset_command``
           — the gateway's OWN /new machinery (same calls run.py makes for a
           typed mid-run /new): interrupts an in-flight agent turn, clears
           queued/pending work, EVICTS the cached AIAgent (which holds the
           in-memory conversation), rotates the session-store entry, clears
           session-scoped overrides, fires session-boundary hooks. The
           destructive-slash confirm gate is deliberately NOT involved —
           the chatlytics server owns the user-facing command UX and has
           already accepted the command.
        2. ``self._session_store.reset_session(session_key)`` — the harness
           session-reset API the gateway hands every adapter via
           ``set_session_store``. Rotates the session id so the next turn
           starts a fresh transcript (partial: cannot evict a runner-cached
           agent — logged).
        3. Neither available → WARNING + ignore (nothing to reset against).

        The handler's reply text (step 1) is discarded — the chatlytics
        server acks the command to the WhatsApp user itself.
        """
        event = self._control_event(env, text="/new")
        source = event.source
        session_key = self._control_session_key(source)
        gw = self._gateway_runner()

        # Step 1a: cancel any in-flight turn + clear queued work first, so
        # the old conversation cannot keep streaming into the new one.
        if gw is not None and hasattr(gw, "_interrupt_and_clear_session"):
            try:
                await gw._interrupt_and_clear_session(
                    session_key,
                    source,
                    interrupt_reason="chatlytics control: new_conversation",
                    invalidation_reason="control_new_conversation",
                )
            except Exception:  # noqa: BLE001 — best-effort; reset still proceeds
                logger.exception(
                    "control new_conversation: interrupt-and-clear raised; "
                    "continuing with reset"
                )
        else:
            # v4.5.1 (review-d3 H6): no silent suppress — a failed adapter-
            # side interrupt is operator-relevant (the old turn may keep
            # streaming into the "new" conversation).
            try:
                await self.interrupt_session_activity(session_key, source.chat_id)
            except Exception as exc:  # noqa: BLE001 — best-effort; reset proceeds
                logger.warning(
                    "control new_conversation: adapter-side "
                    "interrupt_session_activity raised for %s (%s: %s) — "
                    "an in-flight turn may not have been interrupted; "
                    "continuing with reset",
                    session_key,
                    type(exc).__name__,
                    exc,
                    exc_info=True,
                )
            getattr(self, "_pending_messages", {}).pop(session_key, None)

        # Step 1b: full reset via the runner's /new handler.
        if gw is not None and hasattr(gw, "_handle_reset_command"):
            try:
                await gw._handle_reset_command(event)
                logger.info(
                    "control new_conversation: session %s reset via gateway "
                    "/new handler",
                    session_key,
                )
                return
            except Exception:  # noqa: BLE001
                logger.exception(
                    "control new_conversation: gateway reset handler raised; "
                    "falling back to session-store reset"
                )

        # Step 2: session-store rotation (partial reset).
        store = getattr(self, "_session_store", None)
        if store is not None and hasattr(store, "reset_session"):
            try:
                store.reset_session(session_key)
                logger.info(
                    "control new_conversation: session %s rotated via "
                    "session_store.reset_session (gateway /new handler "
                    "unavailable — a runner-cached agent, if any, was only "
                    "interrupted, not evicted)",
                    session_key,
                )
                return
            except Exception:  # noqa: BLE001
                logger.exception(
                    "control new_conversation: session_store.reset_session raised"
                )

        # Step 3: nothing to reset against.
        logger.warning(
            "control new_conversation: no session-reset API available for "
            "%s (no gateway runner, no session store); ignoring",
            session_key,
        )

    async def _control_stop(self, env: Dict[str, Any]) -> None:
        """``/stop`` from WhatsApp: best-effort cancel the in-flight turn.

        The hermes harness DOES expose real cancellation: the runner's
        ``_interrupt_and_clear_session`` calls ``running_agent.interrupt()``
        on the live AIAgent, invalidates the run generation, signals the
        adapter's per-session interrupt event, stops typing, and drops any
        adapter-side pending/queued message for the chat — the same path a
        typed /stop takes. When the runner is unreachable we degrade to the
        adapter-local surface (interrupt event + pending-queue drop) and
        log clearly what was NOT cancellable.
        """
        event = self._control_event(env, text="/stop")
        source = event.source
        session_key = self._control_session_key(source)
        gw = self._gateway_runner()

        if gw is not None and hasattr(gw, "_interrupt_and_clear_session"):
            try:
                await gw._interrupt_and_clear_session(
                    session_key,
                    source,
                    interrupt_reason="chatlytics control: stop",
                    invalidation_reason="control_stop",
                )
                logger.info(
                    "control stop: interrupted in-flight run + cleared queued "
                    "work for %s via gateway cancellation",
                    session_key,
                )
                return
            except Exception:  # noqa: BLE001
                logger.exception(
                    "control stop: gateway cancellation raised; falling back "
                    "to adapter-side interrupt"
                )

        # Fallback: adapter-local best effort. The per-session interrupt
        # event (when an active handler exists) makes the processing task
        # wind down; dropping the pending slot prevents a queued follow-up
        # from re-firing the turn we just tried to stop.
        had_active = session_key in (getattr(self, "_active_sessions", None) or {})
        # v4.5.1 (review-d3 H6): no silent suppress, and the summary line
        # below only claims the interrupt was signalled when it actually was.
        interrupt_signalled = False
        try:
            await self.interrupt_session_activity(session_key, source.chat_id)
            interrupt_signalled = True
        except Exception as exc:  # noqa: BLE001 — best-effort fallback
            logger.warning(
                "control stop: adapter-side interrupt_session_activity "
                "raised for %s (%s: %s)",
                session_key,
                type(exc).__name__,
                exc,
                exc_info=True,
            )
        dropped = (
            getattr(self, "_pending_messages", {}).pop(session_key, None)
            is not None
        )
        logger.warning(
            "control stop: gateway cancellation API unavailable for %s — "
            "%s adapter interrupt event (active handler: %s), "
            "dropped pending queued message: %s; an agent turn already "
            "executing inside the runner could NOT be cancelled from the "
            "adapter",
            session_key,
            "signalled" if interrupt_signalled else "FAILED to signal",
            had_active,
            dropped,
        )

    async def _control_retry_last(self, env: Dict[str, Any]) -> None:
        """``/retry`` from WhatsApp: re-dispatch the chat's last user message.

        Replays the memoized last NORMAL message envelope through the
        regular inbound dispatch path (:meth:`_dispatch_envelope` — same
        normalization, session threading, and handle_message flow as the
        original delivery). No memo for the chat → log + ignore.
        """
        chat_id = env["entity_jid"]
        memo = self._last_message_memo.get(chat_id)
        if memo is None:
            logger.info(
                "control retry_last: no memoized last message for chat %s; "
                "ignoring",
                chat_id,
            )
            return
        logger.info(
            "control retry_last: re-dispatching last user message for chat %s",
            chat_id,
        )
        # dict() copy so the re-dispatch (which re-records the memo) never
        # aliases the stored entry.
        await self._dispatch_envelope(dict(memo))

    # --- Owner-DM questions (v4.5.0 — chatlytics v5.4 P8) -------------------
    #
    # The chatlytics server delivers approval / clarify questions to the
    # bot's paired OWNER DM (never the triggering chat); the owner replies
    # /approve <id> | /deny <id> | /answer <id> <text> there and the
    # resolution arrives back as a ``question_resolved`` control envelope
    # on the existing longpoll. Wire contract (chatlytics v5.4 P8, LOCKED):
    #
    #   POST /api/v1/bot/questions
    #     { type: "approval"|"clarify", text: str (<=2000), request_id:
    #       str 8..64 [A-Za-z0-9_-], chat_id: str, ttl_s?: int 60..86400 }
    #     201 -> {request_id, short_id, status:"pending", expires_at}
    #     409 gateway_not_control_capable / owner_unresolved /
    #         duplicate_request_id; 429 too_many_pending_questions;
    #     502 owner_delivery_failed (row rolled back — safe to retry once).
    #
    #   control envelope: { kind:"control", action:"question_resolved",
    #     request_id, resolution:"approved"|"denied"|"answered",
    #     answer?: str, ... entity_jid (owner DM), session_id, ts }
    #
    # No new longpoll capability token: question_resolved rides the
    # existing ``caps=control`` advertisement (the server refuses question
    # POSTs from gateways that didn't advertise it).

    def _register_pending_question(
        self, request_id: str, entry: Dict[str, Any]
    ) -> None:
        """Insert ``entry`` into the bounded pending-question registry.

        Stale entries (older than :data:`_PENDING_QUESTION_TTL_S`) are
        pruned on every insert; past :data:`_PENDING_QUESTIONS_MAX` the
        oldest entry is FIFO-evicted with a WARNING — an evicted
        approval/clarify can no longer be resolved by the owner's reply
        and times out gateway-side (fail-closed deny / unanswered).
        """
        now = time.monotonic()
        entry.setdefault("created", now)
        stale = [
            rid
            for rid, e in self._pending_questions.items()
            if now - e.get("created", now) > _PENDING_QUESTION_TTL_S
        ]
        for rid in stale:
            self._pending_questions.pop(rid, None)
            logger.debug(
                "pending question %s older than %.0fs; pruned as stale",
                rid,
                _PENDING_QUESTION_TTL_S,
            )
        self._pending_questions[request_id] = entry
        self._pending_questions.move_to_end(request_id)
        while len(self._pending_questions) > _PENDING_QUESTIONS_MAX:
            old_rid, old_entry = self._pending_questions.popitem(last=False)
            logger.warning(
                "pending-question registry full (cap %d); evicting oldest "
                "request_id %s (kind=%s) — its owner reply can no longer "
                "resolve it and the gateway-side wait will time out",
                _PENDING_QUESTIONS_MAX,
                old_rid,
                old_entry.get("kind"),
            )

    async def _post_question(
        self,
        qtype: str,
        text: str,
        chat_id: str,
        *,
        request_id: str,
        ttl_s: Optional[int] = None,
    ) -> str:
        """POST one question to ``/api/v1/bot/questions``; return an outcome.

        v4.5.1 (review-d3 X15): the caller now generates ``request_id``
        (uuid4().hex — satisfies the server's 8..64 ``[A-Za-z0-9_-]`` rule)
        and registers its pending-question entry BEFORE calling this, so a
        ``question_resolved`` envelope racing the POST response can never
        orphan the resolution. Return value is one of:

        - :data:`_QPOST_CREATED` — 201, OR 409 ``duplicate_request_id``
          (the question already exists server-side — e.g. the 502 retry
          raced a row that was NOT rolled back; it will resolve/expire
          normally, so the caller keeps waiting).
        - :data:`_QPOST_UNKNOWN` — transport error / unexpected raise. The
          POST's fate is unknowable: it MAY have been delivered and the
          owner MAY still resolve it, so the caller KEEPS its registry
          entry (the wait timeout / registry TTL cleans up).
        - :data:`_QPOST_FAILED` — definitive server rejection (any other
          non-201) or no credential. The caller pops its entry.

        Retries exactly ONCE, with the SAME request_id, on 502
        ``owner_delivery_failed`` (the server rolls the row back before
        returning 502, so the same id cannot trip ``duplicate_request_id``;
        if it does anyway, the 409 branch above absorbs it). Never raises.
        """
        if self._client is None or self._no_credential or not self._auth_token:
            logger.warning(
                "question POST skipped (type=%s): adapter not connected or "
                "no credential",
                qtype,
            )
            return _QPOST_FAILED
        body: Dict[str, Any] = {
            "type": qtype,
            "text": text,
            "request_id": request_id,
            "chat_id": chat_id,
        }
        if ttl_s is not None:
            body["ttl_s"] = int(ttl_s)
        for attempt in (0, 1):
            try:
                response = await self._client.post(
                    "/api/v1/bot/questions", json=body
                )
            except asyncio.CancelledError:
                raise
            except httpx.RequestError as exc:
                logger.warning(
                    "question POST transport error (type=%s, request_id=%s): "
                    "%s — outcome UNKNOWN (the question may have been "
                    "delivered; keeping the pending entry so an owner reply "
                    "can still resolve it)",
                    qtype,
                    request_id,
                    exc,
                )
                return _QPOST_UNKNOWN
            except Exception:  # noqa: BLE001 -- never-raises contract
                # v4.5.1 (review-d3 X15): full traceback — an unexpected
                # raise here is a bug, not a network blip.
                logger.exception(
                    "question POST raised unexpectedly (type=%s, "
                    "request_id=%s) — outcome UNKNOWN",
                    qtype,
                    request_id,
                )
                return _QPOST_UNKNOWN
            if response.status_code == 201:
                return _QPOST_CREATED
            err: Optional[str] = None
            try:
                payload = response.json()
                if isinstance(payload, dict):
                    err = payload.get("error")
            except Exception:  # noqa: BLE001 -- diagnostic only
                err = None
            if response.status_code == 409 and err == "duplicate_request_id":
                # The question row already exists for this request_id — it
                # WILL be resolved or expire normally. Keep waiting on it.
                logger.warning(
                    "question POST got 409 duplicate_request_id (type=%s, "
                    "request_id=%s) — question already exists server-side; "
                    "keeping the pending wait",
                    qtype,
                    request_id,
                )
                return _QPOST_CREATED
            if response.status_code == 502 and attempt == 0:
                # owner_delivery_failed: the server rolled the question row
                # back, so retrying with the SAME request_id is safe (and
                # keeps the registry key stable for the caller).
                logger.warning(
                    "question POST got HTTP 502 (%s); retrying once with the "
                    "same request_id",
                    err or "owner_delivery_failed",
                )
                continue
            # Treat ANY other non-201 as definitive failure (LOCKED wire
            # contract).
            logger.warning(
                "question POST failed (type=%s): HTTP %d%s",
                qtype,
                response.status_code,
                f" ({err})" if err else "",
            )
            return _QPOST_FAILED
        return _QPOST_FAILED

    async def send_exec_approval(
        self,
        chat_id: str,
        command: str,
        session_key: str,
        description: str = "dangerous command",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:  # type: ignore[name-defined]
        """Route a dangerous-command approval to the chatlytics owner-DM flow.

        The hermes runner feature-detects this method ON THE CLASS
        (``getattr(type(adapter), "send_exec_approval", None)`` in
        gateway/run.py) and calls it with exactly these kwargs when a
        dangerous command is pending. Returning ``SendResult(success=True)``
        makes the runner wait; the agent thread unblocks when
        ``tools.approval.resolve_gateway_approval(session_key, choice)``
        fires — which :meth:`_control_question_resolved` does when the
        owner's /approve or /deny reply arrives.

        On POST failure we return ``SendResult(success=False)`` so the
        runner attempts its plain-text fallback — but note that under
        chatlytics v5.4 P8 the typed ``/approve`` reply to that fallback is
        intercepted SERVER-SIDE by the slash floor and never reaches the
        runner, so an unresolved approval times out to deny (fail-closed).
        The WARNING below documents this for operators.
        """
        cmd = command or ""
        if len(cmd) > _QUESTION_COMMAND_MAX_CHARS:
            cmd = cmd[:_QUESTION_COMMAND_MAX_CHARS] + "…"
        text = f"{description}:\n{cmd}"
        # v4.5.1 (review-d3 X15): register BEFORE the POST so a
        # question_resolved envelope racing the POST response can never
        # orphan the owner's reply.
        request_id = uuid.uuid4().hex
        self._register_pending_question(
            request_id,
            {
                "kind": "approval",
                "session_key": session_key,
                "clarify_id": None,
                "future": None,
                "chat_id": chat_id,
                "created": time.monotonic(),
            },
        )
        outcome = await self._post_question(
            "approval",
            text,
            chat_id,
            request_id=request_id,
            # ttl aligned to the runner's gateway-side approval wait (+60 s
            # margin) instead of the server's much longer default.
            ttl_s=_question_ttl_s(_GATEWAY_QUESTION_WAIT_S),
        )
        if outcome != _QPOST_CREATED:
            if outcome == _QPOST_FAILED:
                # Definitive rejection — the question does NOT exist
                # server-side; the entry can never resolve. Pop it.
                self._pending_questions.pop(request_id, None)
            # _QPOST_UNKNOWN keeps the entry: the POST may have landed and
            # the owner may still /approve — resolve_gateway_approval would
            # then unblock the runner's wait. Registry TTL/cap cleans up.
            # v4.5.1 (review-d3 X15): question LOSS on the exec-approval
            # path is an ERROR — the operator's command silently dies.
            logger.error(
                "send_exec_approval: question POST %s for chat %s — the "
                "runner will fall back to a typed-/approve text prompt, but "
                "under chatlytics the slash floor intercepts /approve "
                "server-side, so the fallback is effectively dead and an "
                "unresolved approval times out to DENY (fail-closed)",
                "failed" if outcome == _QPOST_FAILED else "outcome unknown",
                chat_id,
            )
            return SendResult(
                success=False, error="chatlytics question POST failed"
            )
        logger.info(
            "send_exec_approval: approval question %s routed to owner DM "
            "(chat %s)",
            request_id,
            chat_id,
        )
        return SendResult(success=True, message_id=request_id)

    async def send_clarify(
        self,
        chat_id: str,
        question: str,
        choices: Optional[list],
        clarify_id: str,
        session_key: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:  # type: ignore[name-defined]
        """Route a clarify prompt to the chatlytics owner-DM question flow.

        Success path: the answer arrives via the owner DM ``/answer`` →
        ``question_resolved`` control envelope → ``resolve_gateway_clarify``
        — NOT via the next message in the triggering chat, so this path
        deliberately does NOT call ``mark_awaiting_text`` (doing so would
        capture an unrelated next chat message as the answer).

        Failure path: delegate to ``super().send_clarify(...)`` — the base
        numbered-text fallback DOES ``mark_awaiting_text`` and resolves off
        the next chat message. Plain text is not slash-intercepted by
        chatlytics, so unlike the approval fallback this degradation
        actually works.
        """
        lines = [question]
        if choices:
            lines.append("")
            for i, choice in enumerate(choices, start=1):
                lines.append(f"  {i}. {choice}")
            lines.append("")
            lines.append("Answer with the option number or your own text.")
        text = "\n".join(lines)
        # v4.5.1 (review-d3 X15): register BEFORE the POST (orphan fix —
        # see send_exec_approval).
        request_id = uuid.uuid4().hex
        self._register_pending_question(
            request_id,
            {
                "kind": "clarify",
                "session_key": session_key,
                "clarify_id": clarify_id,
                "future": None,
                "chat_id": chat_id,
                "created": time.monotonic(),
            },
        )
        outcome = await self._post_question(
            "clarify",
            text,
            chat_id,
            request_id=request_id,
            ttl_s=_question_ttl_s(_GATEWAY_QUESTION_WAIT_S),
        )
        if outcome != _QPOST_CREATED:
            if outcome == _QPOST_FAILED:
                # Definitive rejection — no server-side question; pop.
                self._pending_questions.pop(request_id, None)
            # _QPOST_UNKNOWN keeps the entry: if the POST actually landed,
            # the owner's /answer still resolves via resolve_gateway_clarify
            # (first resolution wins; the loser is a logged no-op).
            logger.warning(
                "send_clarify: question POST %s for chat %s; falling "
                "back to the base in-chat numbered-text prompt",
                "failed" if outcome == _QPOST_FAILED else "outcome unknown",
                chat_id,
            )
            return await super().send_clarify(
                chat_id, question, choices, clarify_id, session_key, metadata
            )
        logger.info(
            "send_clarify: clarify question %s routed to owner DM (chat %s)",
            request_id,
            chat_id,
        )
        return SendResult(success=True, message_id=request_id)

    async def _ask_question(
        self, qtype: str, text: str, chat_id: str, timeout_s: float
    ) -> Optional[Tuple[Any, Any]]:
        """Shared core of :meth:`ask_approval` / :meth:`ask_clarify`.

        v4.5.1 (review-d3 X15 + X20): POSTs one ``qtype`` question and
        awaits the owner's resolution future. Returns ``(resolution,
        answer)`` or ``None`` on definitive POST failure / timeout.

        Robustness contract:

        - The future is registered BEFORE the POST (keyed by request_id) so
          a ``question_resolved`` envelope racing the POST response can
          never orphan the resolution.
        - On transport-error / unknown POST outcome the future is KEPT and
          awaited anyway — the question may have been delivered and the
          owner may still resolve it; the ``timeout_s`` wait cleans up.
        - Only a definitive server rejection (:data:`_QPOST_FAILED`)
          returns early.
        - The registry entry is popped in ``finally`` — resolved entries
          were already popped by ``_control_question_resolved`` (no-op),
          and timeout AND cancellation both clean up deterministically.
        """
        request_id = uuid.uuid4().hex
        fut: "asyncio.Future[Tuple[Any, Any]]" = (
            asyncio.get_running_loop().create_future()
        )
        self._register_pending_question(
            request_id,
            {
                "kind": "future",
                "session_key": None,
                "clarify_id": None,
                "future": fut,
                "chat_id": chat_id,
                "created": time.monotonic(),
            },
        )
        try:
            outcome = await self._post_question(
                qtype,
                text,
                chat_id,
                request_id=request_id,
                ttl_s=_question_ttl_s(timeout_s),
            )
            if outcome == _QPOST_FAILED:
                return None
            try:
                return await asyncio.wait_for(fut, timeout_s)
            except asyncio.TimeoutError:
                logger.info(
                    "ask_%s %s timed out after %.0fs",
                    qtype,
                    request_id,
                    timeout_s,
                )
                return None
        finally:
            # Cancellation-safe cleanup (review-d3 X15): whether resolved
            # (already popped — no-op), timed out, POST-failed, or the
            # awaiting task was cancelled, the entry never lingers.
            self._pending_questions.pop(request_id, None)

    async def ask_approval(
        self, text: str, chat_id: str, timeout_s: float = 300.0
    ) -> bool:
        """Awaitable adapter-level approval primitive (tooling / public API).

        POSTs an approval question and awaits the owner's resolution as an
        asyncio future. ``True`` ONLY on resolution ``"approved"``; denied,
        timeout, and POST failure all return ``False`` (default DENY,
        fail-closed).
        """
        result = await self._ask_question("approval", text, chat_id, timeout_s)
        if result is None:
            return False  # fail-closed DENY
        resolution, _answer = result
        return resolution == "approved"

    async def ask_clarify(
        self, text: str, chat_id: str, timeout_s: float = 300.0
    ) -> Optional[str]:
        """Awaitable adapter-level clarify primitive (tooling / public API).

        Returns the owner's answer string on resolution ``"answered"``;
        denied, timeout, and POST failure all return ``None``.
        """
        result = await self._ask_question("clarify", text, chat_id, timeout_s)
        if result is None:
            return None
        resolution, answer = result
        if resolution == "answered" and isinstance(answer, str):
            return answer
        return None

    async def _control_question_resolved(self, env: Dict[str, Any]) -> None:
        """Handle a ``question_resolved`` control envelope from the owner DM.

        Pops the matching pending-question entry and unblocks whichever
        wait primitive registered it:

        - ``"future"``   — :meth:`ask_approval` / :meth:`ask_clarify`:
          resolve the asyncio future with ``(resolution, answer)``.
        - ``"approval"`` — the runner's exec-approval wait: map
          approved→"once" / anything-else→"deny" into
          ``tools.approval.resolve_gateway_approval(session_key, choice)``.
        - ``"clarify"``  — the runner's clarify wait: answered→answer via
          ``tools.clarify_gateway.resolve_gateway_clarify``; denied→resolve
          with ``""`` (see inline comment).

        Never raises — wrapped defensively like the other control handlers.
        """
        try:
            request_id = env.get("request_id")
            if not isinstance(request_id, str) or not request_id:
                self._warn_unknown_control(
                    "question_resolved:missing_request_id",
                    "control question_resolved without a valid request_id — "
                    "ignoring",
                )
                return
            entry = self._pending_questions.pop(request_id, None)
            if entry is None:
                self._warn_unknown_control(
                    f"question_resolved:unknown:{request_id}",
                    "question_resolved for unknown/expired request_id %s — "
                    "ignoring (already resolved, timed out, or evicted)",
                    request_id,
                )
                return
            resolution = env.get("resolution")
            answer = env.get("answer")
            kind = entry.get("kind")

            if kind == "future":
                fut = entry.get("future")
                if fut is not None and not fut.done():
                    fut.set_result((resolution, answer))
                return

            if kind == "approval":
                # Owner DM has no "session"/"always" verb surface — map the
                # binary approved/denied onto the one-shot choices.
                choice = "once" if resolution == "approved" else "deny"
                try:
                    # Lazy import: tolerate harness drift (an older/newer
                    # hermes-agent without tools.approval must not break
                    # control routing for the other actions).
                    from tools.approval import resolve_gateway_approval
                except ImportError:
                    logger.warning(
                        "question_resolved %s: tools.approval unavailable in "
                        "this hermes-agent — cannot resolve the pending "
                        "approval (it will time out to deny)",
                        request_id,
                    )
                    return
                resolved_count = resolve_gateway_approval(
                    entry.get("session_key") or "", choice
                )
                if resolved_count == 0:
                    logger.warning(
                        "question_resolved %s: resolution %r mapped to %r but "
                        "resolved_count=0 — the gateway-side approval already "
                        "timed out",
                        request_id,
                        resolution,
                        choice,
                    )
                else:
                    logger.info(
                        "question_resolved %s: approval %s by owner "
                        "(choice=%s, resolved_count=%d)",
                        request_id,
                        resolution,
                        choice,
                        resolved_count,
                    )
                return

            if kind == "clarify":
                clarify_id = entry.get("clarify_id") or ""
                try:
                    from tools.clarify_gateway import resolve_gateway_clarify
                except ImportError:
                    logger.warning(
                        "question_resolved %s: tools.clarify_gateway "
                        "unavailable in this hermes-agent — cannot resolve "
                        "the pending clarify (it will time out on its own)",
                        request_id,
                    )
                    return
                if resolution == "answered" and isinstance(answer, str) and answer:
                    ok = resolve_gateway_clarify(clarify_id, answer)
                    logger.info(
                        "question_resolved %s: clarify answered by owner "
                        "(clarify_id=%s, delivered=%s)",
                        request_id,
                        clarify_id,
                        ok,
                    )
                else:
                    # Denied (or answered with an empty answer): resolve with
                    # the empty string. This is the harness's OWN cancellation
                    # sentinel — tools/clarify_gateway.py clear_session() sets
                    # entry.response = "" with the documented contract "most
                    # callers just treat any falsy result as 'user did not
                    # respond'". Resolving (rather than letting it time out)
                    # unblocks the agent thread immediately instead of pinning
                    # it for the full clarify_timeout (default 600 s), and ""
                    # is NOT a fake answer — it is the established
                    # no-response signal.
                    ok = resolve_gateway_clarify(clarify_id, "")
                    logger.info(
                        "question_resolved %s: clarify denied by owner — "
                        "resolved clarify_id=%s with the empty-string "
                        "no-response sentinel (delivered=%s)",
                        request_id,
                        clarify_id,
                        ok,
                    )
                return

            logger.warning(
                "question_resolved %s: pending entry has unknown kind %r — "
                "dropped",
                request_id,
                kind,
            )
        except Exception:  # noqa: BLE001 -- control handling must never raise
            logger.exception(
                "question_resolved handling raised; envelope dropped"
            )

    @property
    def is_bot_token(self) -> bool:
        """True when the adapter authenticates as a chatlytics v4.0 bot.

        HERMES-V2 (Phase 336): operators inspecting an adapter instance
        in REPL / debugger / log lines need a fast predicate to confirm
        which auth path the plugin took. True iff EITHER:

        - ``self.bot_token`` is non-empty (explicit bot-token branch),
          OR
        - the resolved ``self._auth_token`` carries the canonical
          ``sk_bot_`` prefix (defensive — covers the case where an
          operator pasted a bot token into the legacy ``api_key`` slot
          and we still want to acknowledge it as a bot token).
        """
        if self.bot_token:
            return True
        return bool(self._auth_token) and self._auth_token.startswith(
            _BOT_TOKEN_PREFIX
        )

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
        # v4.1.5 (telegram-style onboarding): TOLERATE a missing auth token.
        # Previously (HERMES-V2 / Phase 336) connect() hard-raised
        # ChatlyticsConnectError when no token was set, which failed the whole
        # gateway boot. Now we load in a degraded "no-credential" state: the
        # platform registers and tools can run, but every data tool prompts the
        # user to provision a token on first use (see NO_TOKEN_PROMPT + the
        # tools.py per-tool guard + the send() guard below). When a token IS
        # present this branch is skipped and the existing connect() path runs
        # byte-for-byte unchanged.
        if not self._auth_token:
            self._no_credential = True
            # Do NOT build/validate the authed client or start any inbound
            # transport — there is no credential to authenticate with. Mark the
            # adapter as "loaded" so is_connected reflects loaded-but-degraded
            # (it does NOT crash) and the platform stays registered.
            self._running = True
            logger.warning(
                "chatlytics loaded WITHOUT a bot token — agent tools will "
                "prompt the user for one on first use; set CHATLYTICS_BOT_TOKEN "
                "to enable sending"
            )
            # v4.2.0 (P3 survivability): missing token is a partial load —
            # the platform registers but cannot send/receive. Emit the
            # unmissable grep-target line so a silent bot is diagnosable
            # from the gateway log alone. (The WARNING above is the
            # onboarding-friendly wording; this ERROR is the loud one.)
            logger.error(
                "%s no auth token configured (degraded no-credential load). "
                "Fix: set CHATLYTICS_BOT_TOKEN (sk_bot_...) in the gateway "
                "profile config/.env and restart. Run "
                "`python -m chatlytics_hermes.doctor` to verify.",
                _LOAD_FAIL_PREFIX,
            )
            return True

        # A token IS present: clear any prior degraded flag (defensive against
        # a reconnect after the operator set the token) and proceed normally.
        self._no_credential = False

        if self._client is None:
            # ChatlyticsClient takes the resolved _auth_token regardless of
            # which branch (bot vs operator) populated it; the chatlytics
            # gateway distinguishes by token-shape on the receive side.
            self._client = ChatlyticsClient(
                base_url=self.base_url,
                api_key=self._auth_token,
            )

        # HERMES-V2 (Phase 336): log which auth identity the plugin uses
        # so operators can confirm bot vs legacy at gateway start. Token
        # plaintext NEVER appears — only the 8-char SHA256 fingerprint.
        if self.is_bot_token:
            logger.info(
                "chatlytics adapter authenticated as bot (fp=%s)",
                _token_fingerprint(self._auth_token, 8),
            )
        else:
            logger.info(
                "chatlytics adapter authenticated as operator (legacy api_key, fp=%s)",
                _token_fingerprint(self._auth_token, 8),
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
            # v4.2.0 (P3): unmissable + actionable. map_connect_error turns
            # the symptom (timeout vs refused vs reset) into the known fix
            # (dead Tailscale-style IP → LAN/DNS URL, wrong host/port, ...).
            logger.error(
                "%s base_url %s unreachable (%s) — %s",
                _LOAD_FAIL_PREFIX,
                self.base_url,
                exc,
                map_connect_error(exc=exc),
            )
            raise ChatlyticsConnectError(
                f"Chatlytics health check failed: {exc}"
            ) from exc

        if response.status_code != 200:
            await self._client.aclose()
            self._client = None
            logger.error(
                "%s GET %s/health returned HTTP %d — %s",
                _LOAD_FAIL_PREFIX,
                self.base_url,
                response.status_code,
                map_connect_error(status_code=response.status_code),
            )
            raise ChatlyticsConnectError(
                f"Chatlytics health check returned status "
                f"{response.status_code}: {response.text[:200]}"
            )

        # v4.2.0 (P3 survivability): boot identity self-check. One INFO line
        # confirming WHO we are ("registered, authenticated as <bot>") or one
        # unmissable ERROR when the token is rejected. Best-effort — the probe
        # NEVER fails connect() (legacy servers without /api/v1/bot/me and
        # operator-api_key deployments must keep loading exactly as before).
        await self._log_boot_identity()

        # --- Inbound transport selection (v4.1) -----------------------
        # longpoll mode (chatlytics v4.0 bot-updates): do NOT start the
        # aiohttp webhook server; PULL inbound via GET /api/v1/bot/updates
        # instead. webhook mode (default) keeps the existing PUSH server
        # untouched so non-longpoll deployments are unaffected.
        if self.inbound_mode == "longpoll":
            self._running = True
            # Idempotency: don't spawn a second poll task on re-connect.
            if self._poll_task is None or self._poll_task.done():
                self._poll_task = asyncio.create_task(self._poll_loop())
                # v4.5.1 (review-d3 X1): belt + suspenders for the loop's
                # never-exit contract — if the task ever completes without
                # being cancelled, that is a dead inbound transport and MUST
                # be unmissable in the log.
                self._poll_task.add_done_callback(self._on_poll_task_done)
            logger.info(
                "chatlytics inbound: longpoll mode (polling /api/v1/bot/updates)"
            )
            return True

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
            logger.error(
                "%s webhook server failed to bind %s:%d (%s) — pick a free "
                "port via CHATLYTICS_WEBHOOK_PORT or stop the conflicting "
                "process.",
                _LOAD_FAIL_PREFIX,
                self.webhook_host,
                self.webhook_port,
                exc,
            )
            raise ChatlyticsConnectError(
                f"Chatlytics webhook server failed to bind "
                f"{self.webhook_host}:{self.webhook_port}: {exc}"
            ) from exc

        self._running = True
        return True

    def _on_poll_task_done(self, task: "asyncio.Task") -> None:
        """Done-callback for the longpoll task (v4.5.1 — review-d3 X1).

        ``_poll_loop`` is contractually exit-proof (every exception except
        CancelledError is caught inside the loop), so this callback firing
        for anything other than a cancellation means the contract was
        somehow violated — inbound is DEAD until reconnect, which is the
        exact "silent bot" failure mode X1 exists to make unmissable.
        """
        if task.cancelled():
            return  # disconnect() — clean shutdown.
        exc = task.exception()
        if exc is not None:
            logger.error(
                "chatlytics longpoll task EXITED with %r — inbound is DEAD "
                "until the gateway reconnects (this should be impossible; "
                "the loop's catch-all was bypassed)",
                exc,
                exc_info=exc,
            )
        elif self._running:
            # Completed normally while we were still supposed to be running
            # (the client-is-None teardown-race guard is the only normal
            # return, and disconnect() flips _running first).
            logger.error(
                "chatlytics longpoll task EXITED (returned while running) — "
                "inbound is DEAD until the gateway reconnects"
            )

    async def _log_boot_identity(self) -> None:
        """Best-effort GET /api/v1/bot/me after a successful health check.

        v4.2.0 (P3 survivability): closes the "gateway boots with 2 platforms
        instead of 3 and nobody notices" gap from the LOG side — when the
        plugin DOES load, the gateway log carries exactly one INFO line
        confirming the platform is live and which bot it authenticated as,
        so its ABSENCE is diagnostic. A 401/403 (rotated/revoked token) logs
        the unmissable :data:`_LOAD_FAIL_PREFIX` ERROR with rotate guidance.

        NEVER raises and NEVER fails connect(): legacy chatlytics servers
        without /api/v1/bot/me, operator-api_key deployments (the endpoint is
        bot-token-scoped), and offline test harnesses must keep the exact
        pre-v4.2.0 connect() behavior. Any non-auth failure logs at DEBUG.
        """
        if self._client is None:
            return
        try:
            resp = await self._client.get("/api/v1/bot/me")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — probe is strictly best-effort
            logger.debug("boot identity probe failed: %s", exc)
            return
        if resp.status_code == 200:
            try:
                payload: Any = resp.json()
            except Exception:  # noqa: BLE001
                payload = None
            bot_name = extract_bot_name(payload) or "<unnamed bot>"
            logger.info(
                "chatlytics platform registered, authenticated as %s (fp=%s)",
                bot_name,
                _token_fingerprint(self._auth_token, 8),
            )
        elif resp.status_code in (401, 403):
            logger.error(
                "%s bot token rejected (HTTP %d on /api/v1/bot/me, fp=%s) — %s",
                _LOAD_FAIL_PREFIX,
                resp.status_code,
                _token_fingerprint(self._auth_token, 8),
                map_connect_error(status_code=401),
            )
        else:
            logger.debug(
                "boot identity probe returned HTTP %d; skipping", resp.status_code
            )

    async def disconnect(self) -> None:
        """Stop the aiohttp webhook server and close the httpx client.  Idempotent.

        Order matters: shut down the aiohttp server first (so no
        in-flight request handlers can issue further outbound calls
        through ``self._client``), then close the shared httpx client.
        """
        # Stop accepting new inbound work before tearing down transports so
        # the longpoll loop sees ``_running == False`` and exits its while.
        self._running = False

        # v4.1 longpoll: cancel the poll task first so no in-flight GET/ack
        # uses ``self._client`` after we close it below.
        if self._poll_task is not None:
            logger.info("Stopping Chatlytics longpoll consumer")
            self._poll_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._poll_task
            self._poll_task = None

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
        # v4.1.5 (telegram-style onboarding): degraded no-credential state.
        # Surface the relayable get-a-token prompt instead of the generic
        # "Adapter not connected" message so the user learns HOW to fix it.
        if self._no_credential or not self._auth_token:
            return SendResult(
                success=False,
                error=NO_TOKEN_PROMPT,
            )
        if self._client is None:
            return SendResult(
                success=False,
                error="Adapter not connected: call connect() before send()",
            )

        # P-19 session resolution + chatId/text/accountId base body — shared
        # with the v4.4.0 progress-bubble path via _build_send_body. send()
        # keeps the fail-loud contract: no resolvable WAHA session is an
        # operator-actionable error instead of chatlytics's generic 400.
        body_opt, session_err = self._build_send_body(chat_id, content)
        if body_opt is None:
            return SendResult(
                success=False,
                error=session_err,
            )
        body: Dict[str, Any] = body_opt
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
            # HERMES-18 (closes Phase 9 INFO-02): reserved set lifted to
            # the module-level ``_RESERVED_BODY_KEYS`` constant so the
            # set stays in lockstep with the body-field assignments above.
            for key, value in metadata.items():
                if key in _RESERVED_BODY_KEYS:
                    logger.warning(
                        "send() ignoring reserved metadata key %r "
                        "(would shadow body field)",
                        key,
                    )
                    continue
                body[key] = value

        # v4.4.0 (P7): progress-bubble edit-in-place. When a fresh bubble is
        # pending for this chat, POP it (consume exactly once) and ask the
        # server to EDIT that bubble into this reply instead of stacking a
        # second message. Server-side the edit is ownership-enforced and
        # falls back to a normal send automatically — a 200 always means
        # the text was delivered.
        edit_message_id: Optional[str] = None
        if self.status_edit_in_place:
            edit_message_id = self._pop_progress_bubble(chat_id)
            if edit_message_id:
                body["edit_message_id"] = edit_message_id

        logger.debug("send -> /api/v1/send chatId=%s len=%d", chat_id, len(content))

        try:
            response = await self._client.post("/api/v1/send", json=body)
        except httpx.RequestError as exc:
            if edit_message_id:
                # Never lose the reply over the edit decoration: retry ONCE
                # as a plain send without edit_message_id. The bubble stays
                # behind as an acceptable residual.
                #
                # At-least-once tradeoff (review-d3 X16/M1): the failed
                # first attempt may have actually reached the server (e.g.
                # the response was lost in transit after delivery), so this
                # retry can DOUBLE-DELIVER the reply. Accepted deliberately:
                # a duplicated reply is recoverable UX noise; a silently
                # lost reply is a dead bot.
                logger.debug(
                    "edit-tagged send transport error for chat %s (%s); "
                    "retrying once as plain send",
                    chat_id,
                    exc,
                )
                body.pop("edit_message_id", None)
                edit_message_id = None  # retried plain — no edit outcome to log
                try:
                    response = await self._client.post("/api/v1/send", json=body)
                except httpx.RequestError as exc2:
                    return SendResult(
                        success=False,
                        error=f"Transport error: {exc2}",
                        retryable=True,
                    )
            else:
                return SendResult(
                    success=False,
                    error=f"Transport error: {exc}",
                    retryable=True,
                )

        # v4.5.1 (review-d3 X16/M1): edit-tagged send that came back NON-200
        # also retries once as a plain send — previously only the transport-
        # error path retried, so a server that 500s on the edit decoration
        # lost the reply entirely. Same at-least-once double-deliver
        # tradeoff as the transport-error retry above.
        if edit_message_id and response.status_code != 200:
            logger.warning(
                "edit-tagged send returned HTTP %d for chat %s; retrying "
                "once as plain send",
                response.status_code,
                chat_id,
            )
            body.pop("edit_message_id", None)
            edit_message_id = None
            try:
                response = await self._client.post("/api/v1/send", json=body)
            except httpx.RequestError as exc2:
                return SendResult(
                    success=False,
                    error=f"Transport error: {exc2}",
                    retryable=True,
                )

        # v4.5.1 (review-d3 X16/M6): a 200 with a NON-JSON body is a broken
        # contract, not a success — /api/v1/send always answers JSON, so a
        # raw HTML error page (proxy, captive portal, crashed middleware)
        # must not be reported as "delivered".
        try:
            payload: Any = response.json()
        except Exception:  # noqa: BLE001 -- json.JSONDecodeError + httpx variants
            snippet = (response.text or "")[:120]
            logger.warning(
                "send() got HTTP %d with a non-JSON body for chat %s — "
                "treating as failure (body[:120]=%r)",
                response.status_code,
                chat_id,
                snippet,
            )
            return SendResult(
                success=False,
                error=f"non-JSON response (HTTP {response.status_code})",
                raw_response={"raw_text": response.text},
                retryable=response.status_code >= 500,
            )

        # v4.5.1 (review-d3 X20): success derivation + message-id extraction
        # now reuse the shared helpers (_coerce_success_payload /
        # _extract_message_id) so send(), the media handlers, and the tool
        # layer agree on the contract (and the messageId/message_id
        # fallback ORDER no longer drifts between send() and the P7 bubble
        # path).
        success, error_msg = _coerce_success_payload(response.status_code, payload)
        if success:
            # v4.4.0 (P7): surface the server's edit outcome at DEBUG. A 200
            # means the text was delivered either way (the server falls back
            # to a plain send internally on edit failure / unknown ownership).
            if edit_message_id and isinstance(payload, dict):
                if payload.get("edited"):
                    logger.debug(
                        "send edited progress bubble %s in place for chat %s",
                        edit_message_id,
                        chat_id,
                    )
                elif payload.get("edit_fallback"):
                    logger.debug(
                        "send edit fell back to plain send for chat %s "
                        "(edit_fallback=%s)",
                        chat_id,
                        payload.get("edit_fallback"),
                    )
            return SendResult(
                success=True,
                message_id=self._extract_message_id(payload),
                raw_response=payload,
            )

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
        await self._send_typing_once(chat_id, duration, metadata=metadata)

    async def _send_typing_once(
        self,
        chat_id: str,
        duration: float = 3.0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """POST one typing request; return True on success, False on degraded.

        Internal helper introduced in HERMES-09 (closes 02-LOW-02 +
        LO-11): :meth:`send_typing` must stay quiet (DEBUG) on transport
        and non-200 failures so a flapping gateway does not flood logs,
        but :meth:`_keep_typing` still needs to know whether the FIRST
        fire failed so it can emit a WARNING (06-LOW-02 fix).  Returning
        a status bool lets both call sites share one request path.

        ``metadata`` is accepted for signature parity with
        :meth:`send_typing` (HERMES-18 LOW-01); the Chatlytics typing
        endpoint does not currently consume it, so the kwarg is
        silently dropped at the request layer. Forwarding it here
        keeps the helper future-proof if upstream ever evolves a
        metadata-aware typing endpoint.

        Not-connected branch (HERMES-18 INFO-04 doc clarification):
        when ``self._client is None`` (adapter never connected, or
        already disconnected), the helper returns ``False`` without
        attempting a request and without logging. The False is
        deliberately indistinguishable from a "gateway returned non-200"
        degraded result so callers like :meth:`_keep_typing` can apply
        one uniform "degraded first-fire" code path. Production callers
        always invoke this after ``connect()`` populates ``_client``;
        the branch exists to keep defensive callers (and pre-connect
        unit tests) safe rather than crash on attribute access.
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

    async def get_chat_info(self, chat_id: str) -> Optional[Dict[str, Any]]:
        """GET /api/v1/chat?chatId={id} with v3.0 three-way return contract.

        HERMES-13 (v3.0 BREAKING — see CHANGELOG entry
        "BREAKING — get_chat_info return shape"): replaces the v2.1
        ambiguous bare-``{}`` return with explicit semantics.

        Returns:

        - ``dict`` — chat-found. Gateway responded 200 with a JSON
          object payload. Returned as-is; the adapter does NOT
          validate the schema beyond ``isinstance(payload, dict)``.
        - ``None`` — chat-not-found legitimate empty. Gateway
          responded 200 with a falsy / non-dict body (``None``,
          ``{}``, ``[]``). Currently unreachable for known gateway
          versions but the code path is defined for forward-compat.

        Raises:

        - :class:`ChatlyticsLookupError` — on transport / auth /
          server / validation errors. ``exc.code`` is one of:

          - ``transport_error``  (``httpx.RequestError``)
          - ``auth_error``       (HTTP 401 / 403)
          - ``server_error``     (HTTP 5xx)
          - ``validation_error`` (HTTP 4xx other than 401/403, incl.
            **404 — unknown JID, NOT a legitimate empty**)
          - ``unknown_error``    (non-JSON body on 2xx, non-dict
            2xx payload, or adapter not connected)

        The 404-disambiguation rule is the trickiest case: a 404
        from the gateway means the JID was malformed or unknown —
        ``validation_error``, not legitimate empty. The empty branch
        (``None``) is reserved for HTTP 200 with a falsy body.
        """
        if self._client is None:
            raise ChatlyticsLookupError(
                "unknown_error",
                "Adapter not connected: call connect() before get_chat_info()",
            )

        try:
            response = await self._client.get(
                "/api/v1/chat",
                params={"chatId": chat_id},
            )
        except httpx.RequestError as exc:
            logger.warning("get_chat_info transport error: %s", exc)
            raise ChatlyticsLookupError(
                "transport_error", f"Transport error: {exc}"
            ) from exc

        status = response.status_code
        if status in (401, 403):
            logger.warning(
                "get_chat_info auth error %s for chat %s", status, chat_id
            )
            raise ChatlyticsLookupError(
                "auth_error", f"Authentication error: HTTP {status}"
            )
        if 500 <= status < 600:
            logger.warning(
                "get_chat_info server error %s for chat %s", status, chat_id
            )
            raise ChatlyticsLookupError(
                "server_error", f"Server error: HTTP {status}"
            )
        if 400 <= status < 500:
            # 404 from gateway for an unknown chatId is validation_error per
            # the v3.0 contract (NOT a legitimate empty).
            logger.warning(
                "get_chat_info validation error %s for chat %s",
                status,
                chat_id,
            )
            raise ChatlyticsLookupError(
                "validation_error", f"Validation error: HTTP {status}"
            )
        # 2xx path.
        try:
            payload = response.json()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "get_chat_info JSON decode failed on 2xx response: %s", exc
            )
            raise ChatlyticsLookupError(
                "unknown_error", f"Malformed JSON in 2xx response: {exc}"
            ) from exc

        if not payload:
            # Legitimate empty: gateway 200 with falsy body (None, {}, []).
            return None
        if not isinstance(payload, dict):
            # 2xx with non-dict body — treat as malformed.
            raise ChatlyticsLookupError(
                "unknown_error",
                f"Expected dict payload, got {type(payload).__name__}",
            )
        return payload

    # --- Media handlers (HERMES-04 / HERMES-15) ---------------------------

    def _enforce_upload_allowlist(self, candidate: Path) -> Path:
        """Resolve + allowlist-check a local upload path. HI-01 fix preserved.

        Returns the resolved ``Path`` on success. Raises
        :class:`PermissionError` when the allowlist is empty
        (default-deny) or the path is outside every configured root.

        Used by :meth:`_resolve_media_url` from BOTH the explicit
        ``Path`` object branch and the implicit ``str`` + ``exists()``
        branch (HERMES-15). Pulled out so the security check has exactly
        one canonical implementation site — drift here would silently
        weaken HI-01.
        """
        try:
            resolved = candidate.expanduser().resolve()
        except (OSError, RuntimeError) as exc:
            raise PermissionError(
                f"Cannot resolve upload path {str(candidate)!r}: {exc}"
            ) from exc
        if not self.upload_allowed_roots:
            raise PermissionError(
                "Local file uploads are disabled: set "
                "CHATLYTICS_UPLOAD_ALLOWED_ROOTS to an allowlist of "
                "absolute paths (OS-pathsep separated) to enable "
                "local-file uploads."
            )
        for root in self.upload_allowed_roots:
            # Path.is_relative_to landed in 3.9; we pin >=3.10 in
            # pyproject so this is safe. Equality also matches
            # uploading the root itself when it's a regular file.
            try:
                if resolved == root or resolved.is_relative_to(root):
                    return resolved
            except AttributeError:
                # Defensive fallback for 3.8 hosts if a downstream
                # consumer ever loosens the >=3.10 pin in pyproject.
                rs = str(resolved)
                rr = str(root)
                if rs == rr or rs.startswith(rr + os.sep):
                    return resolved
        raise PermissionError(
            f"Refusing upload outside CHATLYTICS_UPLOAD_ALLOWED_ROOTS: "
            f"{resolved}"
        )

    async def _resolve_media_url(
        self,
        resource: Union[str, Path, bytes, bytearray],
        *,
        upload_filename: Optional[str] = None,
        content_type: Optional[str] = None,
    ) -> str:
        """Resolve a media resource to a remotely-hosted URL.

        HERMES-15 (v3.0 BREAKING — library API): unified resolver with
        explicit ``Path`` support and an unambiguous failure mode.

        Branches (evaluated IN ORDER — order matters for correctness):

        1. ``bytes`` / ``bytearray`` — uploaded to ``/api/v1/upload``;
           returned ``{url}`` becomes the media URL. Preserved from
           HERMES-04 — caller-supplied raw bytes is a legitimate path.
        2. ``Path`` object — resolved + allowlist-checked + read +
           uploaded. New in HERMES-15 for explicit ergonomics.
        3. ``str`` starting with ``http://`` or ``https://`` — returned
           as-is (URL passthrough, no upload).
        4. ``str`` whose ``Path(s).expanduser().exists()`` is true —
           treated as a local path; resolved + allowlist-checked + read
           + uploaded.
        5. Anything else (typically a malformed ``str`` that is neither
           a URL nor an existing path) — raises :class:`ValueError`.

        The blocking ``open()+read()`` for local files runs in a worker
        thread via :func:`asyncio.to_thread` so concurrent media tool
        invocations do not stall the event loop while a multi-MB file
        is read off disk (fix for 04-MED-02 / surfaced by HERMES-05
        tool layer in 05-MED-02). Both file branches share this wrap.

        Raises:

        - :class:`PermissionError` — path is outside
          ``CHATLYTICS_UPLOAD_ALLOWED_ROOTS`` (HI-01 default-deny
          preserved).
        - :class:`ValueError` — input is not bytes / Path / URL /
          existing string path. Caught by :meth:`_send_media_payload`
          and surfaced as ``SendResult(success=False, error=...)``.
        - :class:`RuntimeError` — upload endpoint did not return a
          ``url`` field in its JSON body.

        Callers wrap exceptions into
        ``SendResult(success=False, error=...)`` via
        :meth:`_send_media_payload`.
        """
        assert self._client is not None  # caller guards

        # Branch 1: raw bytes — upload as-is.
        if isinstance(resource, (bytes, bytearray)):
            name = upload_filename or "upload.bin"
            ctype = content_type or _guess_content_type(name)
            upload_response = await self._client.upload_file(
                filename=name, content=bytes(resource), content_type=ctype
            )

        # Branch 2: explicit Path object — local file, allowlist-enforced.
        elif isinstance(resource, Path):
            resolved = self._enforce_upload_allowlist(resource)
            content, basename = await asyncio.to_thread(
                _read_file_sync, str(resolved)
            )
            name = upload_filename or basename
            ctype = content_type or _guess_content_type(name)
            upload_response = await self._client.upload_file(
                filename=name, content=content, content_type=ctype
            )

        # Branch 3: URL string — passthrough.
        elif isinstance(resource, str) and resource.startswith(("http://", "https://")):
            return resource

        # Branch 4: string path that exists on disk — local file.
        elif isinstance(resource, str) and Path(resource).expanduser().exists():
            resolved = self._enforce_upload_allowlist(Path(resource))
            content, basename = await asyncio.to_thread(
                _read_file_sync, str(resolved)
            )
            name = upload_filename or basename
            ctype = content_type or _guess_content_type(name)
            upload_response = await self._client.upload_file(
                filename=name, content=content, content_type=ctype
            )

        # Branch 5: unresolvable input — clean ValueError.
        else:
            raise ValueError(
                "resource must be a URL (http://, https://) or a local "
                f"file path that exists; got {type(resource).__name__}="
                f"{resource!r}"
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
                # v4.5.1 (review-d3 X20): shared extractor — same
                # message_id/messageId/waha-echo fallback order as send()
                # and the P7 bubble path.
                message_id=self._extract_message_id(payload),
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
        except ValueError as exc:
            # HERMES-15: resource was neither a URL, a Path, nor an
            # existing string path. _resolve_media_url raised cleanly;
            # surface as a regular SendResult failure for the caller.
            return SendResult(success=False, error=f"Invalid resource: {exc}")
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
        resource: Union[str, Path, bytes, bytearray],
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> "SendResult":
        """Send an image as a native WhatsApp photo attachment.

        HERMES-15 (v3.0 BREAKING — library API): unified resource shape.
        The v2.0/v2.1 ``send_image_file`` companion is GONE. Callers
        pass ``resource`` in any of four forms; the adapter auto-detects
        which branch applies in :meth:`_resolve_media_url`:

        - ``http(s)://...`` ``str`` — used as ``mediaUrl`` directly.
        - ``Path`` object — local file, uploaded via multipart.
        - ``str`` whose ``Path(s).exists()`` is true — local file (the
          adapter resolves + uploads the same as the ``Path`` branch).
        - ``bytes`` / ``bytearray`` — uploaded as raw bytes (preserved
          from HERMES-04).
        - Anything else — :class:`ValueError`, surfaced by
          :meth:`_send_media_payload` as
          ``SendResult(success=False, ...)``.

        ``caption`` is optional. ``reply_to`` / ``metadata`` are
        accepted for base-class signature parity but currently ignored
        — the Chatlytics send-media endpoint does not expose
        per-message reply context.

        ``**kwargs`` is swallowed for forward-compat with upstream base
        signature evolution (HI-03 fix from HERMES-08): future Hermes
        versions may add new kwargs (``priority``, ``force_native``,
        etc.) that this override should not trip on.

        See CHANGELOG entry "BREAKING — adapter send_* unified resource
        shape" for migration guidance from v2.x callers.
        """
        return await self._send_media_payload(
            chat_id, "image", resource, caption=caption
        )

    async def send_animation(
        self,
        chat_id: str,
        resource: Union[str, Path, bytes, bytearray],
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

        HERMES-15: ``resource`` accepts URL str, Path, string path, or
        bytes (auto-detected by :meth:`_resolve_media_url`).
        """
        return await self._send_media_payload(
            chat_id, "animation", resource, caption=caption
        )

    async def send_voice(
        self,
        chat_id: str,
        resource: Union[str, Path, bytes, bytearray],
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

        HERMES-15: ``resource`` accepts URL str, Path, string path, or
        bytes (auto-detected by :meth:`_resolve_media_url`).
        """
        # caption is accepted for signature parity; voice bubbles
        # technically support it but the gateway hides captions in some
        # clients.  Pass it through anyway so the gateway can decide.
        return await self._send_media_payload(
            chat_id, "voice", resource, caption=caption
        )

    async def send_video(
        self,
        chat_id: str,
        resource: Union[str, Path, bytes, bytearray],
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> "SendResult":
        """Send a video as inline playable media.

        HERMES-15: ``resource`` accepts URL str, Path, string path, or
        bytes (auto-detected by :meth:`_resolve_media_url`).
        """
        return await self._send_media_payload(
            chat_id, "video", resource, caption=caption
        )

    async def send_document(
        self,
        chat_id: str,
        resource: Union[str, Path, bytes, bytearray],
        caption: Optional[str] = None,
        file_name: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> "SendResult":
        """Send a generic file as a downloadable WhatsApp document.

        ``file_name`` is the displayed attachment name in the recipient's
        chat -- it does NOT have to match the local path's basename.
        When omitted and ``resource`` is a local path or bytes, we fall
        back to the basename / a generic ``upload.bin``.

        HERMES-15: ``resource`` accepts URL str, Path, string path, or
        bytes (auto-detected by :meth:`_resolve_media_url`).
        """
        # base signature uses ``file_name``; we ALSO accept ``filename``
        # via kwargs because every other handler in this module uses the
        # shorter spelling.
        if file_name is None and "filename" in kwargs:
            file_name = kwargs.pop("filename")
        return await self._send_media_payload(
            chat_id,
            "document",
            resource,
            caption=caption,
            filename=file_name,
        )

    # HERMES-15 (v3.0 BREAKING): ``send_image_file`` was DELETED from
    # the Chatlytics adapter. The unified :meth:`send_image` now
    # accepts ``str | Path | bytes`` and auto-detects which branch to
    # use via :meth:`_resolve_media_url`. No deprecation alias — clean
    # break per operator preference.
    #
    # The base class ``BasePlatformAdapter.send_image_file`` provides
    # a generic text-fallback default. To honor the "clean break"
    # intent (no silent degradation to a text bubble), we shadow the
    # inherited method with the ``_RemovedMethod`` descriptor below so
    # v2.x callers see a clear ``AttributeError`` on upgrade with
    # migration guidance, instead of an unexpected text-message side
    # effect. Using a descriptor (rather than ``__getattribute__``)
    # keeps the cost paid only at the removed-method's access site —
    # all other attribute lookups continue through the C-level slot
    # without paying a per-access Python comparison.
    send_image_file = _RemovedMethod(
        "ChatlyticsAdapter.send_image_file was removed in v3.0 "
        "(HERMES-15). Use adapter.send_image(chat_id, resource: "
        "str | Path | bytes) — the unified method auto-detects "
        "URL vs local-file vs raw-bytes inputs. See the v3.0 "
        "CHANGELOG entry 'BREAKING — adapter send_* unified "
        "resource shape' for migration guidance."
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
        #
        # v4.4.0 (P7): _keep_typing is the only in-turn hook the adapter
        # has (the base class spawns it per inbound message and stops it
        # when the turn finishes), so it doubles as the progress-bubble
        # anchor: a sibling timer task waits ``status_bubble_after_s`` on
        # the SAME stop_event and posts the ONE "working…" bubble only if
        # the turn outlives the threshold. Fast turns ⇒ timer exits on
        # stop_event ⇒ zero new requests (byte-identical to v4.3.0).
        progress_task: Optional[asyncio.Task] = None
        if self.status_edit_in_place and self.status_bubble_after_s > 0:
            progress_task = asyncio.create_task(
                self._progress_bubble_timer(chat_id, stop_event)
            )
        try:
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
        finally:
            # v4.4.0 (P7): the timer normally exits on its own (stop_event
            # fired, or bubble already sent). The cancel covers the path
            # where _keep_typing is cancelled WITHOUT stop_event being set
            # so the timer can never outlive its turn.
            if progress_task is not None and not progress_task.done():
                progress_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await progress_task

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
    # v4.1.0: base_url defaults to the public DNS name (token-only onboarding).
    base_url = (os.getenv("CHATLYTICS_BASE_URL") or "").strip() or "https://node.chatlytics.ai"
    # v4.1.3 (MD-01 close): prefer the per-bot CHATLYTICS_BOT_TOKEN (sk_bot_*)
    # over the legacy operator CHATLYTICS_API_KEY — mirrors ChatlyticsAdapter's
    # _auth_token precedence (HERMES-V2 / Phase 336). Without this, token-only
    # cron deployments silently no-sent: the gateway's /api/v1/send rejects an
    # absent/empty Bearer, and the env-config guard below tripped on a missing
    # api_key even when a valid bot token was present.
    auth_token = (
        (os.getenv("CHATLYTICS_BOT_TOKEN") or "").strip()
        or (os.getenv("CHATLYTICS_API_KEY") or "").strip()
    )
    home_channel = (os.getenv("CHATLYTICS_HOME_CHANNEL") or "").strip()

    if not (auth_token and home_channel):
        return {
            "error": (
                "Chatlytics standalone send: a credential "
                "(CHATLYTICS_BOT_TOKEN preferred, or legacy CHATLYTICS_API_KEY) "
                "and CHATLYTICS_HOME_CHANNEL must both be set"
            ),
        }

    headers = {
        "Authorization": f"Bearer {auth_token}",
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
    from .tools import (
        _NO_TOKEN_EXEMPT_TOOLS,
        _adapter_lacks_credential,
        _no_token_failure,
        handler_takes_adapter,
    )

    needs_adapter = handler_takes_adapter(handler)
    # v4.1.5 (telegram-style onboarding): data tools get the no-token guard;
    # status/health tools (chatlytics_health / chatlytics_login) are exempt so
    # they still report the degraded state rather than the get-a-token prompt.
    no_token_guarded = name not in _NO_TOKEN_EXEMPT_TOOLS

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

    async def _bound(args: Optional[Dict[str, Any]] = None, **kwargs: Any) -> str:
        # HERMES-compat fix #2 (carried forward from hpg6's running tree):
        # Hermes' tools.registry.dispatch calls ``entry.handler(args, **kwargs)``
        # where ``args`` is the JSON args dict from the tool call. Merge it
        # into kwargs so bare async handlers see the named params they
        # expect. Fixes TypeError: _bound_chatlytics_*() takes 0
        # positional arguments but 1 was given.
        if args:
            for _k, _v in args.items():
                kwargs.setdefault(_k, _v)
        adapter_inst = _lookup_adapter()
        # v4.1.5 (telegram-style onboarding): if the adapter loaded WITHOUT a
        # bot token (degraded no-credential state), every DATA tool returns the
        # relayable get-a-token prompt instead of a generic "not connected"
        # error. Status tools (health/login) are exempt and fall through to the
        # normal not-connected handling so they still report the degraded state.
        if no_token_guarded and _adapter_lacks_credential(adapter_inst):
            result: Any = _no_token_failure()
            return json.dumps(result, ensure_ascii=False, default=str)
        client = getattr(adapter_inst, "client", None) if adapter_inst else None
        if client is None:
            result = {
                "success": False,
                "error": (
                    f"Chatlytics tool '{name}' invoked but the adapter is "
                    "not connected; ensure 'hermes gateway start' has run."
                ),
            }
        elif needs_adapter:
            result = await handler(client, adapter=adapter_inst, **kwargs)
        else:
            result = await handler(client, **kwargs)

        # DeepSeek (and other strict OpenAI-compatible providers) reject a
        # role:tool message whose ``content`` is a raw object, erroring
        # ``messages[N]: content should be a string or a list``. OpenAI
        # tolerates a dict; DeepSeek does not. Hermes' tool_executor passes
        # our return value straight into the tool-result message content
        # (and its own _detect_tool_failure does safe_json_loads() on it,
        # i.e. it already EXPECTS a JSON string), so serialize the canonical
        # dict here. Strings pass through untouched; dict/list/other get
        # json.dumps'd so content is always a valid type.
        if isinstance(result, str):
            return result
        try:
            return json.dumps(result, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            # Never hand back a non-string and re-trigger the 400.
            return str(result)

    _bound.__name__ = f"_bound_{name}"
    _bound.__qualname__ = f"_bound_{name}"
    return _bound


def _register_impl(ctx: Any) -> None:
    """Actual registration body — see :func:`register` (the public entry
    point, which wraps this in the v4.2.0 P3 loud-failure guard).

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
        # v4.1.0: base_url is optional (defaults to https://node.chatlytics.ai),
        # so it is no longer in required_env. An auth token IS still required,
        # but it can be CHATLYTICS_BOT_TOKEN (preferred) OR CHATLYTICS_API_KEY
        # (legacy) -- a "one-of" that required_env can't express -- so the
        # token check is enforced at connect() time (see _auth_token guard).
        required_env=[],
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
                # Every TOOLS handler (and the _bound closure wrapping it) is
                # `async def`. Without is_async=True the Hermes tools.registry
                # treats the handler as sync, never awaits it, and
                # tool_executor.py does len(coroutine) -> "TypeError: object
                # of type coroutine has no len()" on the first tool call.
                is_async=True,
            )


def register(ctx: Any) -> None:
    """Plugin entry point: discovered by Hermes via the ``hermes_agent.plugins``
    entry point group in ``pyproject.toml`` AND via the directory-plugin shim
    (repo-root ``__init__.py`` → ``hermes_plugins.chatlytics``).

    v4.2.0 (P3 survivability) wraps :func:`_register_impl` with:

    1. **No-downgrade guard** — if the installed hermes-agent is OLDER than
       the plugin floor (>=0.14), log a clear ERROR telling the operator the
       environment was downgraded (the v4.1.1 ``==0.14.0`` pin once dragged
       production 0.15.1 → 0.14.0 via a plain pip install) and how to fix it
       (reinstall + ``--no-deps``). The guard never blocks an
       otherwise-working load — it only makes the downgrade visible.
    2. **Loud failure** — if registration raises for ANY reason, log one
       unmissable :data:`_LOAD_FAIL_PREFIX` ERROR (the "gateway boots with 2
       platforms instead of 3" symptom now has a grep-able cause + fix in
       the log) and re-raise so Hermes' PluginManager sees the real error.
    3. **Boot confirmation** — one INFO line on success, so the line's
       ABSENCE in a gateway boot log is itself diagnostic. The
       "authenticated as <bot>" identity line follows at connect() time
       (:meth:`ChatlyticsAdapter._log_boot_identity`).
    """
    downgrade_msg = check_hermes_agent_version()
    if downgrade_msg:
        # ERROR, not raise: a floor-violating hermes-agent USUALLY still
        # imports this plugin fine (the API the plugin needs may predate the
        # floor bump) — breaking the load would turn a visible degradation
        # into the silent "2 platforms" outage we are trying to kill.
        logger.error("chatlytics plugin environment problem: %s", downgrade_msg)

    try:
        _register_impl(ctx)
    except Exception as exc:
        logger.error(
            "%s register(ctx) raised %s: %s — the gateway will boot WITHOUT "
            "the chatlytics platform (\"2 platforms\" symptom; bot goes "
            "silent). Run `python -m chatlytics_hermes.doctor` to diagnose.",
            _LOAD_FAIL_PREFIX,
            type(exc).__name__,
            exc,
        )
        raise

    logger.info(
        "chatlytics plugin registered (chatlytics-hermes v%s): platform + "
        "tool surface loaded",
        _PLUGIN_VERSION,
    )
