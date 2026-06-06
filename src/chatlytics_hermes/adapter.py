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
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import httpx
from aiohttp import web

from .client import ChatlyticsClient, USER_AGENT
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

# v4.1 longpoll hold: the chatlytics server HOLDS GET /api/v1/bot/updates for
# up to this many ms before returning an empty {envelopes:[],cursor} batch.
# The longpoll GET's httpx read timeout MUST comfortably exceed this hold or
# every empty poll trips a ReadTimeout (which httpx stringifies to "()", the
# `longpoll GET transport error ()` symptom). The client-level default
# (30s, only ~5s margin) was too tight; the poll uses an explicit per-request
# httpx.Timeout with read = hold + 15s instead. See _poll_loop.
_LONGPOLL_TIMEOUT_MS: int = 25000


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
_RESERVED_BODY_KEYS: frozenset = frozenset({"chatId", "text", "accountId", "replyTo", "session"})


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

        Error discipline (never let the loop die silently):
          - httpx transport error / non-200: WARNING + exponential backoff
            (1s -> cap 30s), then retry.
          - 400 invalid_cursor: reset cursor to "" and continue.
          - 401 bot_token_required: ERROR (token bad/revoked) + 30s backoff.
          - asyncio.CancelledError: exit cleanly (disconnect()).
        """
        cursor: str = ""
        backoff: float = 1.0
        backoff_max: float = 30.0
        logger.info(
            "chatlytics inbound: longpoll loop started (polling /api/v1/bot/updates)"
        )
        while self._running:
            client = self._client
            if client is None:
                # connect() always sets _client before starting the task,
                # but guard defensively against a teardown race.
                return
            try:
                resp = await client.get(
                    "/api/v1/bot/updates",
                    params={"cursor": cursor, "timeout_ms": _LONGPOLL_TIMEOUT_MS},
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
                logger.warning(
                    "longpoll GET transport error (%s); backing off %.1fs",
                    exc,
                    backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, backoff_max)
                continue

            if resp.status_code == 400:
                # Bad/expired cursor — reset and re-poll from the tail.
                logger.warning(
                    "longpoll GET returned 400 (invalid_cursor); resetting cursor"
                )
                cursor = ""
                continue
            if resp.status_code == 401:
                logger.error(
                    "longpoll GET returned 401 (bot_token_required); the bot "
                    "token is missing/invalid/revoked. Backing off 30s."
                )
                await asyncio.sleep(backoff_max)
                continue
            if resp.status_code != 200:
                logger.warning(
                    "longpoll GET returned HTTP %d; backing off %.1fs",
                    resp.status_code,
                    backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, backoff_max)
                continue

            # Successful poll — reset backoff.
            backoff = 1.0
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
                await client.post(
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
        """
        body = {
            "chatId": env["entity_jid"],          # required by normalize_payload
            "text": env.get("text", ""),
            "senderId": env.get("sender_jid"),
            "chatType": (
                "channel"
                if env.get("chat_type") == "newsletter"
                else env.get("chat_type") or "dm"
            ),
            "session": env.get("session_id"),
        }
        # Thread the WAHA session BEFORE dispatch so the reply path resolves
        # it. InboundEnvelope always carries session_id under longpoll.
        self.register_chat_session(body["chatId"], body["session"])
        event = normalize_payload(body, self.platform)
        await self.handle_message(event)

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
        # HERMES-V2 (Phase 336): assert we have SOME auth token before
        # attempting transport. Deferred from __init__ so registration-time
        # env loading races don't crash adapter instantiation; failing here
        # gives the operator a single clear error at gateway start.
        if not self._auth_token:
            raise ChatlyticsConnectError(
                "ChatlyticsAdapter requires either CHATLYTICS_BOT_TOKEN "
                "(preferred, chatlytics v4.0+) or CHATLYTICS_API_KEY "
                "(legacy). Neither is set in env or PlatformConfig.extra; "
                "the adapter has no Bearer credential to send."
            )

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
        if self._client is None:
            return SendResult(
                success=False,
                error="Adapter not connected: call connect() before send()",
            )

        body: Dict[str, Any] = {
            "chatId": chat_id,
            "text": content,
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
            # Fail loudly with an operator-actionable error instead of
            # letting chatlytics return its generic 400.
            return SendResult(
                success=False,
                error=(
                    "Chatlytics adapter missing WAHA session for chat "
                    f"{chat_id!r}: set CHATLYTICS_SESSION env var "
                    "(e.g. 3cf11776_logan) or pass session= in the "
                    "platform extra block. Inbound-derived session "
                    "mapping is empty for this chat."
                ),
            )
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
    # v4.1.0: base_url defaults to the public DNS name (token-only onboarding).
    base_url = (os.getenv("CHATLYTICS_BASE_URL") or "").strip() or "https://node.chatlytics.ai"
    api_key = (os.getenv("CHATLYTICS_API_KEY") or "").strip()
    home_channel = (os.getenv("CHATLYTICS_HOME_CHANNEL") or "").strip()

    if not (api_key and home_channel):
        return {
            "error": (
                "Chatlytics standalone send: CHATLYTICS_API_KEY and "
                "CHATLYTICS_HOME_CHANNEL must both be set"
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
        client = getattr(adapter_inst, "client", None) if adapter_inst else None
        if client is None:
            result: Any = {
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
