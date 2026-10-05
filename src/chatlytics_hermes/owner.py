"""Owner tagging — deterministic, code-level operator identification (#3).

Mirrors the official Hermes WhatsApp integration contract
(``plugins/platforms/whatsapp/adapter.py`` + upstream
``tests/gateway/test_whatsapp_from_owner.py``): an owner-originated inbound
message gets

- ``MessageEvent.metadata["whatsapp_from_owner"] = True`` (upstream key, so
  hooks/plugins written against the official integration work unchanged) and
  ``metadata["chatlytics_from_owner"] = True``;
- its text prefixed with the literal ``"[owner reply] "`` — never twice.

WHO the owner is: the gateway's own slash-command admin lists in the platform
``extra`` block (``gateway/slash_access.py``), per scope exactly like Hermes —
``allow_admin_from`` for DMs, ``group_allow_admin_from`` for groups. An admin
in DMs is NOT implicitly an owner in groups.

WHAT is matched: ONLY the sender identity the chatlytics hub delivered on an
authenticated transport (longpoll under the bot bearer, or a webhook whose
HMAC signature verified). Message text never contributes to the decision.

Security properties (DO NOT WEAKEN — each one is pinned by
tests/test_owner_tagging.py, including mutation checks):

- Fail closed: no admin list for the scope, no sender, an unauthenticated
  webhook, a channel/broadcast chat, or any error → NOT owner.
- Spoof resistance: while tagging is active, a typed lookalike marker at the
  start of ANY line (``[owner reply]``, ``[owner]``, any case, fullwidth
  brackets, zero-width padding) is stripped from EVERY sender's text before the
  real marker is applied. A non-owner therefore can never present the marker,
  and an owner's own typed copy collapses into the single real prefix (the
  upstream "no double prefix" behavior).
- Idempotent: neutralize-then-prefix is a fixed point, so retry/replay paths
  that re-run dispatch never double-tag.

Identity canonicalization: ``@c.us`` / ``@s.whatsapp.net`` / bare digits /
``+`` / ``:N`` device suffixes collapse to one phone identity; ``@lid`` is a
SEPARATE namespace (a LID number is not a phone number, and treating them as
one would let a LID collide with an unrelated phone). An owner reachable as
both must list both forms; the hub does not currently send the resolved
phone form of an ``@lid`` sender on the envelope.
"""

from __future__ import annotations

import logging
import os
import re
import unicodedata
from typing import Any, FrozenSet, Optional, Tuple

logger = logging.getLogger("chatlytics_hermes.owner")

#: Upstream Hermes literal (plugins/platforms/whatsapp/adapter.py
#: ``_OWNER_REPLY_PREFIX``). Stable public contract — DO NOT CHANGE.
OWNER_REPLY_PREFIX = "[owner reply] "
#: Upstream metadata key — drop-in parity with the official integration.
WHATSAPP_FROM_OWNER_KEY = "whatsapp_from_owner"
#: chatlytics-specific alias of the same flag.
CHATLYTICS_FROM_OWNER_KEY = "chatlytics_from_owner"

# Leading lookalike marker, matched on an NFKC-normalized, format-char-stripped
# line (NFKC folds fullwidth ［］ to []). Case-insensitive.
_LOOKALIKE_RE = re.compile(r"^\s*\[\s*owner(?:\s+reply)?\s*\]\s*", re.IGNORECASE)

_DM_CHAT_TYPES = frozenset({"dm", "direct", "private", ""})
_GROUP_CHAT_TYPES = frozenset({"group"})

_PHONE_SUFFIXES = ("@c.us", "@s.whatsapp.net")


def canonical_identity(value: Any) -> Optional[Tuple[str, str]]:
    """Canonical ``(namespace, id)`` for a WhatsApp identifier, else None.

    ``namespace`` is ``"pn"`` (phone) or ``"lid"``. Unrecognized shapes
    (group JIDs, newsletters, garbage) return None so they can never match.
    """
    if value is None:
        return None
    s = str(value).strip().lower()
    if not s:
        return None
    namespace = "pn"
    if "@" in s:
        local, domain = s.split("@", 1)
        domain = "@" + domain
        if domain == "@lid":
            namespace = "lid"
        elif domain not in _PHONE_SUFFIXES:
            return None
    else:
        local = s
    local = local.split(":", 1)[0]  # strip ":N" device suffix
    if namespace == "pn" and local.startswith("+"):
        local = local[1:]
    if not local.isdigit() or not local.isascii():
        return None
    return (namespace, local)


def _coerce_ids(raw: Any) -> FrozenSet[Tuple[str, str]]:
    """Admin list (list / comma string / scalar) → canonical identity set."""
    if raw is None:
        return frozenset()
    if isinstance(raw, (list, tuple, set, frozenset)):
        items = list(raw)
    elif isinstance(raw, str):
        items = raw.split(",")
    else:
        items = [raw]
    out = set()
    for item in items:
        ident = canonical_identity(item)
        if ident is not None:
            out.add(ident)
    return frozenset(out)


def owner_ids_for_chat_type(extra: Any, chat_type: Any) -> FrozenSet[Tuple[str, str]]:
    """The owner set for one scope. Channels/broadcasts have no owners."""
    if not isinstance(extra, dict):
        return frozenset()
    ct = str(chat_type or "").strip().lower()
    if ct in _DM_CHAT_TYPES:
        return _coerce_ids(extra.get("allow_admin_from"))
    if ct in _GROUP_CHAT_TYPES:
        return _coerce_ids(extra.get("group_allow_admin_from"))
    return frozenset()


def tagging_enabled(extra: Any) -> bool:
    """Active when ANY owner list is configured and not explicitly disabled.

    Explicit opt-out: env ``CHATLYTICS_OWNER_TAGGING`` or
    ``extra.owner_tagging`` set to ``0``/``false``/``no``/``off`` (env wins).
    """
    if not isinstance(extra, dict):
        return False
    raw = os.getenv("CHATLYTICS_OWNER_TAGGING")
    if raw is None:
        raw = extra.get("owner_tagging")
    if raw is not None and str(raw).strip().lower() in ("0", "false", "no", "off"):
        return False
    return bool(
        _coerce_ids(extra.get("allow_admin_from"))
        or _coerce_ids(extra.get("group_allow_admin_from"))
    )


def is_owner(sender_id: Any, chat_type: Any, extra: Any) -> bool:
    """Deterministic owner decision from the delivered sender identity."""
    ident = canonical_identity(sender_id)
    if ident is None:
        return False
    return ident in owner_ids_for_chat_type(extra, chat_type)


def _strip_format_chars(s: str) -> str:
    return "".join(ch for ch in s if unicodedata.category(ch) != "Cf")


def neutralize_owner_markers(text: str) -> str:
    """Remove typed lookalike markers from the start of every line."""
    if not text:
        return text
    out = []
    for line in text.split("\n"):
        norm = unicodedata.normalize("NFKC", _strip_format_chars(line))
        if _LOOKALIKE_RE.match(norm):
            while True:
                m = _LOOKALIKE_RE.match(norm)
                if not m:
                    break
                norm = norm[m.end():]
            line = norm
        out.append(line)
    return "\n".join(out)


def _set_text(event: Any, text: str) -> Any:
    try:
        event.text = text
        return event
    except Exception:  # frozen MessageEvent — rebuild a copy
        import dataclasses

        return dataclasses.replace(event, text=text)


def _set_owner_metadata(event: Any) -> None:
    md = getattr(event, "metadata", None)
    if not isinstance(md, dict):
        # hermes-agent 0.14's MessageEvent has no ``metadata`` field; upstream
        # added it. Attach one so hooks reading ``event.metadata`` see the flag
        # on either version (the text marker is the version-independent signal).
        md = {}
        try:
            event.metadata = md
        except Exception:  # noqa: BLE001 -- frozen/slotted event
            return
    md[WHATSAPP_FROM_OWNER_KEY] = True
    md[CHATLYTICS_FROM_OWNER_KEY] = True


def apply_owner_tagging(event: Any, extra: Any, *, sender_authenticated: bool) -> Any:
    """Neutralize typed markers, then tag the event if its sender is an owner.

    Returns the (possibly rebuilt) event. Never raises: on any error the event
    is returned untagged (fail closed) with the text it had.
    """
    try:
        if not tagging_enabled(extra):
            return event
        text = getattr(event, "text", "") or ""
        text = neutralize_owner_markers(text)
        source = getattr(event, "source", None)
        owner = bool(sender_authenticated) and is_owner(
            getattr(source, "user_id", None),
            getattr(source, "chat_type", None),
            extra,
        )
        # Slash commands stay commands: Hermes detects them by a leading "/",
        # and owners are exactly the users allowed to run them
        # (slash_access). They still get the metadata flag.
        if owner and not text.startswith("/"):
            text = OWNER_REPLY_PREFIX + text
        event = _set_text(event, text)
        if owner:
            _set_owner_metadata(event)
        return event
    except Exception:  # noqa: BLE001 -- tagging must never break dispatch
        logger.debug("owner tagging raised; dispatching untagged", exc_info=True)
        return event
