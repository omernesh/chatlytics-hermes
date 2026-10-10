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

import contextlib
import dataclasses
import logging
import os
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

# Leading lookalike marker. Recognized by a hand-written LINEAR scanner
# (_scan_marker) over the folded match view of a line, NOT a regex: the
# previous regex nested quantifiers around the wrapper run and backtracked
# exponentially (28 "*" took 23 s — review BLOCKER on 62a1aa0). DO NOT
# reintroduce a regex here. In the view every opening bracket is "[", every
# closing one "]", letters are NFKD-decomposed, stripped of combining/format
# marks, confusable-folded and lowercased. Grammar (every run is consumed
# greedily, each character is looked at once):
#
#   lead*  "["+  ws*  "owner"  ( sep* "reply" )?  ws*  "]"+  pair*  ws*
#
#   lead = whitespace | one of * _ ~ ` ] (a blockquote ">" folds to "]")
#          | one of - + U+2022 followed by whitespace (list marker)
#   sep  = whitespace | one of _ - . :
#   pair = a trailing * _ ~ ` that PAIRS with one consumed in lead (so
#          "[owner]**important**" keeps the user's "**").
#
# Known false positive (documented): a line OPENING with "(Owner)" loses
# that word, because any bracket pair counts.
# Runs are unbounded but linear, so padding (300 spaces / NBSPs, 300 "[",
# 300 "_") can neither hide a marker nor cost more than one pass.
_WRAPPER_CHARS = frozenset("*_~`")
_LIST_MARKERS = frozenset("-+\u2022")
_SEP_CHARS = frozenset("_-.:")

# Characters that render as blank but are letters/symbols (so neither Cf nor
# a combining mark): braille blank, Hangul fillers. Folded to nothing.
_INVISIBLE_FOLD = frozenset("\u2800\u115f\u1160\u3164\uffa0")

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

class _View:
    """Lazily folded match view of one line, with a map back to the line."""

    __slots__ = ("line", "chars", "src", "folded")

    def __init__(self, line: str) -> None:
        self.line = line
        self.chars: list = []
        self.src: list = []  # src[k] = original index of chars[k]
        self.folded = 0

    def at(self, k: int) -> Optional[str]:
        """View char k, folding just enough of the line (None past the end)."""
        chars = self.chars
        line = self.line
        n = len(line)
        while len(chars) <= k and self.folded < n:
            i = self.folded
            for v in _fold_char(line[i]):
                chars.append(v)
                self.src.append(i)
            self.folded = i + 1
        return chars[k] if k < len(chars) else None


def _skip_ws(view: _View, k: int) -> int:
    c = view.at(k)
    while c is not None and c.isspace():
        k += 1
        c = view.at(k)
    return k


def _skip_word(view: _View, k: int, word: str) -> int:
    """k past ``word`` at k, or -1."""
    for ch in word:
        if view.at(k) != ch:
            return -1
        k += 1
    return k


def _scan_marker(view: _View, k: int) -> int:
    """View index just past ONE marker starting at k, or -1. Linear."""
    lead: dict = {}
    while True:
        c = view.at(k)
        if c is None:
            return -1
        if c.isspace() or c == "]":
            k += 1
        elif c in _WRAPPER_CHARS:
            lead[c] = lead.get(c, 0) + 1
            k += 1
        elif c in _LIST_MARKERS:
            nxt = view.at(k + 1)
            if nxt is None or not nxt.isspace():
                return -1
            k += 2
        else:
            break
    if view.at(k) != "[":
        return -1
    while view.at(k) == "[":
        k += 1
    k = _skip_ws(view, k)
    k = _skip_word(view, k, "owner")
    if k < 0:
        return -1
    j = k
    c = view.at(j)
    while c is not None and (c.isspace() or c in _SEP_CHARS):
        j += 1
        c = view.at(j)
    j = _skip_word(view, j, "reply")
    if j >= 0:
        k = j
    k = _skip_ws(view, k)
    if view.at(k) != "]":
        return -1
    while view.at(k) == "]":
        k += 1
    c = view.at(k)
    while c is not None and lead.get(c, 0) > 0:
        lead[c] -= 1
        k += 1
        c = view.at(k)
    return _skip_ws(view, k)


def _marker_end(line: str) -> int:
    """Length of the leading lookalike marker(s) in ``line`` (0 if none).

    Scans a folded view but returns an index into the ORIGINAL line, so the
    caller cuts only the marker span and every other character (fullwidth
    digits, ZWJ emoji, RLM/LRM) survives byte-for-byte. Stacked markers are
    all consumed. Work is linear in the characters the scan inspects; a line
    that does not start with a marker is rejected after its leading
    whitespace / wrapper run.
    """
    view = _View(line)
    pos = 0
    end = 0
    while True:
        nxt = _scan_marker(view, pos)
        if nxt <= pos:
            break
        pos = nxt
        end = view.src[pos - 1] + 1
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


# --- #5 owner ACCESS pin + refusal visibility --------------------------------
#
# Owner TAGGING (above) only labels a message. Whether the gateway lets it in
# at all is decided by hermes ``_is_user_authorized``, whose allow-all rung
# (``GATEWAY_ALLOW_ALL_USERS``) is read through ``platform_gate_env``. Under
# ``gateway.multiplex_profiles`` that reader returns the PROFILE scope's value
# and never falls back to ``os.environ``, so an allow-all set on the systemd
# unit silently stopped applying and the owner was dropped + asked to pair
# (2026-10-10 incident, #5).
#
# The pin uses the per-message, adapter-verified grant hermes already has:
# ``SessionSource.role_authorized`` (checked BEFORE pairing and the env
# allowlists; ``is True`` only). It is set ONLY for a sender that is an owner
# by the same deterministic rule as tagging (authenticated transport + the
# gateway's own ``allow_admin_from`` / ``group_allow_admin_from``). Every
# other sender is left exactly as hermes would judge it — no widening.
# DO NOT key this off message text, an unauthenticated webhook, or the
# tagging opt-out (that opt-out is about the TEXT prefix, not access).

_ALLOW_ALL_TRUTHY = frozenset({"true", "1", "yes"})


def _source_supports_access_pin(source: Any) -> bool:
    """True when this hermes-agent's SessionSource declares ``role_authorized``."""
    try:
        return dataclasses.is_dataclass(source) and any(
            f.name == "role_authorized" for f in dataclasses.fields(source)
        )
    except Exception:  # noqa: BLE001
        return False


#: warn-once keys (fixed literals only — bounded by construction).
_WARNED: set = set()


def _warn_once(key: str, msg: str, *args: Any) -> None:
    if key in _WARNED:
        logger.debug(msg, *args)
        return
    _WARNED.add(key)
    logger.warning(msg, *args)


def apply_owner_access(event: Any, extra: Any, *, sender_authenticated: bool) -> bool:
    """Pin gateway access for an authenticated owner. Returns True when pinned.

    Never raises and never touches a non-owner's source.
    """
    try:
        if not sender_authenticated:
            return False
        source = getattr(event, "source", None)
        if source is None or not is_owner(
            getattr(source, "user_id", None), getattr(source, "chat_type", None), extra
        ):
            return False
        if not _source_supports_access_pin(source):
            # Older hermes-agent: no multiplexing there either, so the
            # process-env gates still apply. Say so once; set nothing.
            _warn_once(
                "owner_access:unsupported",
                "owner access pin unavailable: this hermes-agent's SessionSource "
                "has no role_authorized field, so owners (allow_admin_from) are "
                "admitted only by the gateway's own allowlist / pairing / "
                "GATEWAY_ALLOW_ALL_USERS (#5)",
            )
            return False
        source.role_authorized = True
        return True
    except Exception:  # noqa: BLE001 -- must never break dispatch
        logger.debug("owner access pin raised", exc_info=True)
        return False


def _refusal_reason(event: Any, extra: Any, *, sender_authenticated: bool, pinned: bool) -> str:
    source = getattr(event, "source", None)
    sender = getattr(source, "user_id", None)
    owner = is_owner(sender, getattr(source, "chat_type", None), extra)
    if owner and pinned:
        return (
            "sender is an owner and was pinned (role_authorized), yet the gateway "
            "still refused — check pre_gateway_dispatch hooks / hermes-agent version"
        )
    if owner and not sender_authenticated:
        return (
            "sender matches allow_admin_from but the webhook is unsigned "
            "(CHATLYTICS_WEBHOOK_SECRET unset), so the owner pin was NOT applied"
        )
    if owner:
        return (
            "sender matches allow_admin_from but this hermes-agent cannot pin "
            "owner access (no SessionSource.role_authorized)"
        )
    if str(os.environ.get("GATEWAY_ALLOW_ALL_USERS", "")).strip().lower() in _ALLOW_ALL_TRUTHY:
        return (
            "GATEWAY_ALLOW_ALL_USERS is set in the gateway PROCESS env but is not "
            "visible to this profile: under gateway.multiplex_profiles the gate is "
            "read from the profile's own .env only (#5). Put it in the profile .env, "
            "list the sender in allow_admin_from, or approve the pairing code"
        )
    return (
        "sender is not an owner (allow_admin_from), not paired and not in any "
        "allowlist — the gateway will pair / decline / ignore per "
        "unauthorized_dm_behavior"
    )


def report_gateway_refusal(
    adapter: Any, event: Any, extra: Any, *, sender_authenticated: bool, pinned: bool
) -> Optional[bool]:
    """Ask the gateway's own authorization check (read-only) whether ``event``
    will be admitted; on a refusal log ONE WARNING with the reason.

    Advisory only: the gateway still makes and enforces the decision. Returns
    the verdict, or None when no runner / check is available.
    """
    try:
        runner = getattr(adapter, "gateway_runner", None)
        if runner is None and callable(getattr(adapter, "_gateway_runner", None)):
            runner = adapter._gateway_runner()  # bound _message_handler.__self__
        check = getattr(runner, "_is_user_authorized_for_source", None)
        if not callable(check):
            check = getattr(runner, "_is_user_authorized", None)
        if not callable(check):
            return None
        source = getattr(event, "source", None)
        if source is None or getattr(source, "user_id", None) is None:
            return None
        canon = getattr(adapter, "_canonicalize", None)
        if callable(canon):  # same identity-first step handle_message runs
            with contextlib.suppress(Exception):
                canon(source)
        verdict = check(source)
        if verdict is not True and verdict is not False:
            return None  # duck-typed / mocked runner: no real verdict
        if verdict is False:
            logger.warning(
                "inbound from %s (chat_type=%s, chat=%s) will be REFUSED by the "
                "gateway's authorization: %s",
                getattr(source, "user_id", None),
                getattr(source, "chat_type", None),
                getattr(source, "chat_id", None),
                _refusal_reason(
                    event, extra, sender_authenticated=sender_authenticated, pinned=pinned
                ),
            )
        return verdict
    except Exception:  # noqa: BLE001 -- advisory; never break dispatch
        logger.debug("gateway authorization preflight raised", exc_info=True)
        return None


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
