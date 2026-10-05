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
- Spoof resistance: the FLAG can only come from the delivered sender id.
  For the TEXT, while tagging is active a typed lookalike marker at the start
  of any line (any Unicode line break; any bracket pair; case, fullwidth,
  common Cyrillic/Greek confusables, combining marks and zero-width padding
  folded) is cut from EVERY sender's text before the real marker is applied.
  This is best-effort hardening, not a proof: an exotic glyph outside the
  fold table can still survive as text, but it can never set the flag and it
  is never the leading token of an owner's message. An owner's own typed copy
  collapses into the single real prefix (upstream "no double prefix").
- On hermes-agent 0.14 the flag is also mirrored onto ``raw_message`` because
  ``metadata`` is not a dataclass field there and a pre_gateway_dispatch
  rewrite hook's ``dataclasses.replace`` drops it.
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

# Leading lookalike marker, matched on the folded MATCH VIEW of a line (see
# _match_view): every opening bracket is "[", every closing one "]", letters
# are NFKD-decomposed, stripped of combining marks, confusable-folded and
# lowercased. "owner" and "reply" may be joined by space / _ - . :
# Leading markdown wrappers (bold/italic/strike/code marks, a blockquote ">"
# which the view folds to "]", "- " / "+ " / bullet list markers) and doubled
# brackets ("[[owner reply]]") are part of the cut span.
# NO "^" ANCHOR: re.match() already anchors. (d0f9051 used "^" together
# with match(view, pos); "^" never matches at pos > 0, so only ONE marker was
# cut per call — review BLOCKER. _marker_end now re-slices the window and
# matches at 0, but keep the pattern anchor-free so a future match(s, pos)
# cannot reintroduce that bug.)
_LOOKALIKE_RE = re.compile(
    r"\s*(?:[*_~`\]]+\s*|[-+\u2022]\s+)*"
    r"\[+\s*owner(?:[\s_\-.:]*reply)?\s*\]+[*_~`]*\s*"
)

# Characters that render as blank but are letters/symbols (so neither Cf nor
# a combining mark): braille blank, Hangul fillers. Folded to nothing.
_INVISIBLE_FOLD = frozenset("\u2800\u115f\u1160\u3164\uffa0")

# Only this many FOLDED characters past the current cut point are examined
# per marker: a marker can only sit at the start of a line, so a huge line
# costs a bounded amount of work instead of a full fold.
_MATCH_WINDOW = 256

# Common Latin lookalikes for the letters of "owner reply" (Cyrillic, Greek,
# Armenian, small capitals, IPA, digit zero). NFKD already folds fullwidth,
# mathematical and circled forms; this covers what it does not.
_CONFUSABLES = {
    # o
    "о": "o", "О": "o", "ο": "o", "Ο": "o", "օ": "o",
    "ᴏ": "o", "೦": "o", "ഠ": "o", "0": "o", "ø": "o",
    # w
    "ԝ": "w", "Ԝ": "w", "ѡ": "w", "ᴡ": "w", "ɯ": "w",
    "ω": "w",
    # n
    "ո": "n", "ɴ": "n", "п": "n", "η": "n",
    # e
    "е": "e", "Е": "e", "Ε": "e", "ᴇ": "e", "є": "e",
    "ҽ": "e", "℮": "e",
    # r
    "г": "r", "ʀ": "r", "ᴦ": "r", "ⲅ": "r",
    # p
    "р": "p", "Р": "p", "ρ": "p", "Ρ": "p", "ᴘ": "p",
    # l
    "ӏ": "l", "І": "l", "і": "l", "Ι": "l", "ʟ": "l",
    "ǀ": "l", "|": "l", "1": "l", "ı": "l",
    # y
    "у": "y", "У": "y", "ү": "y", "γ": "y", "ʏ": "y",
    "Υ": "y",
}
_OPEN_BRACKET_CATS = ("Ps", "Pi")
_CLOSE_BRACKET_CATS = ("Pe", "Pf")
_SKIP_CATS = ("Cf", "Mn", "Me", "Mc")

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


# ASCII fast path for _fold_char: same result as the general path (ASCII is
# NFKD-stable; ()[]{}<> are the ASCII brackets), without the unicodedata
# calls. Built once from the general path itself so the two cannot drift.
_ASCII_FOLD: dict = {}


def _fold_char(ch: str) -> str:
    """Match-view form of ONE original character (may be empty)."""
    hit = _ASCII_FOLD.get(ch)
    if hit is not None:
        return hit
    return _fold_char_slow(ch)


def _fold_char_slow(ch: str) -> str:
    out = []
    for x in unicodedata.normalize("NFKD", ch):
        cat = unicodedata.category(x)
        if cat in _SKIP_CATS or x in _INVISIBLE_FOLD:
            continue
        if x in "<" or cat in _OPEN_BRACKET_CATS:
            out.append("[")
        elif x in ">" or cat in _CLOSE_BRACKET_CATS:
            out.append("]")
        else:
            x = x.lower()
            out.append(_CONFUSABLES.get(x, x))
    return "".join(out)


_ASCII_FOLD.update({chr(c): _fold_char_slow(chr(c)) for c in range(128)})

# View characters that may precede the marker's opening "[" (whitespace and
# the markdown wrappers in _LOOKALIKE_RE). Used for a cheap pre-check so an
# ordinary line is rejected after folding one or two characters.
_LEAD_CHARS = frozenset("*_~`]-+\u2022")


def _marker_end(line: str) -> int:
    """Length of the leading lookalike marker(s) in ``line`` (0 if none).

    Matches on a folded view but returns an index into the ORIGINAL line, so
    the caller cuts only the marker span and every other character
    (fullwidth digits, ZWJ emoji, RLM/LRM) survives byte-for-byte.
    """
    view: list = []
    owner_idx: list = []  # owner_idx[k] = original index of view[k]
    folded = 0  # original characters folded so far (lazy, incremental)
    n = len(line)
    end = 0
    pos = 0
    def _ensure(k: int) -> None:
        nonlocal folded
        while len(view) < k and folded < n:
            for v in _fold_char(line[folded]):
                view.append(v)
                owner_idx.append(folded)
            folded += 1

    while True:
        # Cheap pre-check: skip whitespace / wrapper chars; the next view
        # char must be "[" or there is no marker here.
        k = pos
        limit = pos + _MATCH_WINDOW
        while k < limit:
            _ensure(k + 1)
            if k >= len(view) or not (view[k].isspace() or view[k] in _LEAD_CHARS):
                break
            k += 1
        if k >= len(view) or view[k] != "[":
            break
        _ensure(pos + _MATCH_WINDOW)
        m = _LOOKALIKE_RE.match("".join(view[pos:pos + _MATCH_WINDOW]))
        if not m or m.end() == 0:
            break
        pos += m.end()
        end = owner_idx[pos - 1] + 1
    # Invisible characters AFTER the marker are left alone: they belong to
    # the user's text (e.g. an RLM opening a Hebrew line).
    return end


# Visible stand-in used when cutting a marker would turn the text into a
# slash command the sender never typed ("[owner reply] /approve").
_DEFANGED = "[marker removed] "


def neutralize_owner_markers(text: str) -> str:
    """Remove typed lookalike markers from the start of every line.

    Every Unicode line boundary counts (str.splitlines: \r, \u2028, \x85,
    ...), not just \n. Only the marker span is cut from the original line.
    """
    if not text:
        return text
    out = []
    for piece in text.splitlines(keepends=True):
        body = piece.splitlines()[0] if piece.splitlines() else ""
        ending = piece[len(body):]
        end = _marker_end(body)
        if end:
            rest = body[end:]
            # Never manufacture a leading slash command out of a message
            # whose original text did not start with "/".
            if not out and rest.lstrip().startswith("/"):
                rest = _DEFANGED + rest
            body = rest
        out.append(body + ending)
    return "".join(out)


def _set_text(event: Any, text: str) -> Any:
    try:
        event.text = text
        return event
    except Exception:  # frozen MessageEvent — rebuild a copy
        import dataclasses

        return dataclasses.replace(event, text=text)


def _mirror_raw_flag(event: Any, owner: bool) -> None:
    """Mirror the flag onto ``raw_message`` (a real dataclass field).

    On hermes-agent 0.14 ``metadata`` is NOT a MessageEvent field, so a
    ``pre_gateway_dispatch`` "rewrite" hook (gateway/run.py
    ``dataclasses.replace(event, text=...)``) drops it; ``raw_message`` is
    carried over. Non-owner events get the keys REMOVED, so a payload that
    arrived already claiming them can never pass them through.

    The hub envelope / webhook payload is NEVER mutated in place: the event
    gets a shallow copy (the memoized retry_last envelope and any other holder
    of the original dict stay untouched).
    """
    raw = getattr(event, "raw_message", None)
    if not isinstance(raw, dict):
        return
    keys = (WHATSAPP_FROM_OWNER_KEY, CHATLYTICS_FROM_OWNER_KEY)
    if not owner and not any(k in raw for k in keys):
        return
    new = {k: v for k, v in raw.items() if k not in keys}
    if owner:
        for k in keys:
            new[k] = True
    try:
        event.raw_message = new
    except Exception:  # noqa: BLE001 -- frozen event: leave as is
        logger.debug("could not replace raw_message", exc_info=True)


def neutralize_event_text(event: Any, extra: Any) -> Any:
    """Strip typed markers from the RAW text, before any plugin prefix.

    Callers that add their own leading prefix (the ``[<sender id>]`` identity
    bridge) must run this first: once a prefix sits in front, a typed marker is
    no longer at the start of its line and the anchored match cannot see it.
    """
    try:
        if not tagging_enabled(extra):
            return event
        text = getattr(event, "text", "") or ""
        new = neutralize_owner_markers(text)
        return _set_text(event, new) if new != text else event
    except Exception:  # noqa: BLE001 -- must never break dispatch
        logger.debug("owner marker neutralization raised", exc_info=True)
        return event


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
    is returned untagged (fail closed) with the text it had. Payload-supplied
    ``*_from_owner`` keys on ``raw_message`` are stripped on EVERY path —
    tagging off, non-owner, or error — and set only for a completed owner tag.
    """
    tagged = False
    try:
        event, tagged = _tag(event, extra, sender_authenticated)
    except Exception:  # noqa: BLE001 -- tagging must never break dispatch
        logger.debug("owner tagging raised; dispatching untagged", exc_info=True)
    try:
        _mirror_raw_flag(event, tagged)
    except Exception:  # noqa: BLE001
        logger.debug("raw_message flag scrub raised", exc_info=True)
    return event


def _tag(event: Any, extra: Any, sender_authenticated: bool) -> Tuple[Any, bool]:
    """apply_owner_tagging's body; returns (event, owner_tag_completed)."""
    if not tagging_enabled(extra):
        return event, False
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
    return event, owner
