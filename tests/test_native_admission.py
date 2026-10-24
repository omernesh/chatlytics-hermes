"""hermes-agent 0.22.5 native admission: the owner flag must not refuse the message.

Regression (2026-10-25): hermes-agent v0.22.5 admits every inbound message
through a durable native envelope (``gateway/session_envelope.py``). Its
``_native_metadata`` refuses ANY non-empty ``MessageEvent.metadata`` unless the
platform is ``whatsapp`` and every key is one the bundled Baileys adapter
stamps. The owner flag (``whatsapp_from_owner`` + ``chatlytics_from_owner``)
therefore turned every owner message into
``Native admission refused: invalid_params (metadata)``.

The test host carries an older hermes-agent with no ``gateway.session_envelope``,
so the check is VENDORED below verbatim (hermes-agent v0.22.5,
``gateway/session_envelope.py`` ``_native_metadata`` + the ``metadata`` clause of
``_event_clause``) and installed as that module for the feature-detected path.
"""

from __future__ import annotations

import json
import sys
import types
from types import SimpleNamespace
from typing import Any

import pytest

from chatlytics_hermes import owner as owner_mod
from chatlytics_hermes.owner import (
    CHATLYTICS_FROM_OWNER_KEY,
    OWNER_REPLY_PREFIX,
    WHATSAPP_FROM_OWNER_KEY,
)
from tests.test_longpoll import SESSION_ID
from tests.test_owner_tagging import (
    OWNER_LID,
    OWNER_PN,
    STRANGER,
    _dispatch,
    _env,
    _lp_adapter,
    _md,
    _owner_extra,
    _post,
    _wh_adapter,
    _wh_payload,
)

# --- vendored: hermes-agent v0.22.5 gateway/session_envelope.py -----------------

_WHATSAPP_METADATA = frozenset({'whatsapp_native_type', 'whatsapp_native', 'whatsapp_from_owner'})
_WHATSAPP_NATIVE_KEYS = frozenset({'video', 'audio', 'sticker', 'location', 'contact', 'contacts',
                                   'reaction', 'poll', 'pollUpdate'})
_WHATSAPP_NATIVE_MAX_BYTES = 16 * 1024


def _native_metadata(event):
    metadata = event.metadata
    if not metadata:
        return {}
    if (not isinstance(metadata, dict) or event.source.platform.value != 'whatsapp'
            or not set(metadata) <= _WHATSAPP_METADATA):
        return None
    kind, native = metadata.get('whatsapp_native_type'), metadata.get('whatsapp_native')
    if (kind is not None and not (isinstance(kind, str) and 0 < len(kind) <= 64)
            or metadata.get('whatsapp_from_owner') not in (None, True)
            or native is not None and not (isinstance(native, dict) and set(native) <= _WHATSAPP_NATIVE_KEYS)):
        return None
    projected = {key: metadata[key] for key in ('whatsapp_native_type', 'whatsapp_from_owner') if key in metadata}
    if native is not None:
        try:
            encoded = json.dumps(native, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError):
            return None
        if len(encoded.encode()) <= _WHATSAPP_NATIVE_MAX_BYTES:
            projected['whatsapp_native'] = json.loads(encoded)
    return projected


def _metadata_clause(event):
    """The ``metadata`` rung of v0.22.5 ``_event_clause`` (``invalid_params``).

    v0.22.5's MessageEvent declares ``metadata`` (default ``{}``); the 0.14 test
    host's does not, so an event without the attribute is read as the default.
    """
    event = SimpleNamespace(metadata=getattr(event, "metadata", {}), source=event.source)
    if _native_metadata(event) is None:
        return 'metadata'
    return None


# --- fixtures --------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.delenv("CHATLYTICS_OWNER_TAGGING", raising=False)
    owner_mod._METADATA_SKIP_LOGGED.clear()


@pytest.fixture
def hermes_0225(monkeypatch):
    """Make ``gateway.session_envelope`` resolve to the vendored v0.22.5 check."""
    mod = types.ModuleType("gateway.session_envelope")
    mod._native_metadata = _native_metadata
    monkeypatch.setitem(sys.modules, "gateway.session_envelope", mod)
    return mod


@pytest.fixture
def hermes_pre_native(monkeypatch):
    """A hermes-agent without native admission (module absent → ImportError)."""
    monkeypatch.setitem(sys.modules, "gateway.session_envelope", None)


def _source(platform: str) -> Any:
    return SimpleNamespace(platform=SimpleNamespace(value=platform))


# --- the vendored check bites (negative control) ---------------------------------


def test_vendored_check_refuses_the_pre_fix_event_shape() -> None:
    pre_fix = SimpleNamespace(
        metadata={WHATSAPP_FROM_OWNER_KEY: True, CHATLYTICS_FROM_OWNER_KEY: True},
        source=_source("chatlytics"),
    )
    assert _metadata_clause(pre_fix) == "metadata"
    # Even the upstream key alone is refused off the whatsapp platform.
    pre_fix.metadata = {WHATSAPP_FROM_OWNER_KEY: True}
    assert _metadata_clause(pre_fix) == "metadata"


# --- the fix: owner events built by the adapter pass admission --------------------


@pytest.mark.parametrize("sender", [OWNER_PN, OWNER_LID])
async def test_longpoll_owner_event_passes_native_admission(hermes_0225, sender) -> None:
    adapter = _lp_adapter(**_owner_extra())
    ev = await _dispatch(adapter, _env("forward these", sender))

    assert _metadata_clause(ev) is None
    assert WHATSAPP_FROM_OWNER_KEY not in _md(ev)
    assert CHATLYTICS_FROM_OWNER_KEY not in _md(ev)
    # The owner signal is still carried: text prefix + raw_message mirror.
    assert ev.text == OWNER_REPLY_PREFIX + "forward these"
    assert ev.raw_message[WHATSAPP_FROM_OWNER_KEY] is True
    assert ev.raw_message[CHATLYTICS_FROM_OWNER_KEY] is True
    # Reply routing: the chat's WAHA session and the source survive unchanged.
    assert adapter._resolve_session_for_chat(sender) == SESSION_ID
    assert ev.source.chat_id == sender
    assert ev.source.user_id == sender


async def test_longpoll_group_owner_event_passes_native_admission(hermes_0225) -> None:
    ev = await _dispatch(_lp_adapter(**_owner_extra()), _env("hi group", OWNER_PN, chat_type="group"))
    assert _metadata_clause(ev) is None
    assert ev.text.startswith(OWNER_REPLY_PREFIX)


async def test_non_owner_event_passes_native_admission(hermes_0225) -> None:
    ev = await _dispatch(_lp_adapter(**_owner_extra()), _env("plain hello", STRANGER))
    assert _metadata_clause(ev) is None
    assert ev.text == "plain hello"


async def test_webhook_owner_event_passes_and_keeps_reply_fields(hermes_0225) -> None:
    payload = _wh_payload("hey", OWNER_PN)
    payload.update(messageId="MSG-1", replyTo="MSG-0", session="sess-a")
    adapter = _wh_adapter(secret=True, **_owner_extra())
    events = await _post(adapter, payload, sign=True)

    ev = events[0]
    assert _metadata_clause(ev) is None
    assert ev.text == "[owner reply] hey"
    assert ev.message_id == "MSG-1"
    assert ev.reply_to_message_id == "MSG-0"
    assert ev.raw_message[WHATSAPP_FROM_OWNER_KEY] is True
    assert adapter._resolve_session_for_chat(OWNER_PN) == "sess-a"


# --- feature detection follows the running gateway --------------------------------


def test_detection_mirrors_upstream_whatsapp_allowance(hermes_0225) -> None:
    # Exactly upstream: the whatsapp platform may carry whatsapp_from_owner,
    # nothing else may carry anything.
    assert owner_mod._native_admission_refuses(
        SimpleNamespace(source=_source("whatsapp")), {WHATSAPP_FROM_OWNER_KEY: True}
    ) is False
    assert owner_mod._native_admission_refuses(
        SimpleNamespace(source=_source("chatlytics")), {WHATSAPP_FROM_OWNER_KEY: True}
    ) is True


async def test_older_hermes_keeps_the_metadata_flag(hermes_pre_native) -> None:
    ev = await _dispatch(_lp_adapter(**_owner_extra()), _env("hi", OWNER_PN))
    assert _md(ev).get(WHATSAPP_FROM_OWNER_KEY) is True
    assert _md(ev).get(CHATLYTICS_FROM_OWNER_KEY) is True


async def test_unrecognized_native_contract_fails_safe(monkeypatch) -> None:
    # Module present, check renamed: never risk a refusal — drop the flag.
    monkeypatch.setitem(
        sys.modules, "gateway.session_envelope", types.ModuleType("gateway.session_envelope")
    )
    ev = await _dispatch(_lp_adapter(**_owner_extra()), _env("hi", OWNER_PN))
    assert WHATSAPP_FROM_OWNER_KEY not in _md(ev)
    assert ev.text == "[owner reply] hi"


# --- role reauthorization hook (session_ingress_context.reauthorize_roles) ---------


async def _reauth(adapter: Any, source: Any) -> Any:
    # Same call shape as hermes: getattr(type(adapter), ...)(adapter, source).
    check = getattr(type(adapter), "reauthorize_native_roles", None)
    assert check is not None
    return await check(adapter, source)


async def test_reauthorize_native_roles_confirms_owner_only() -> None:
    adapter = _lp_adapter(**_owner_extra())
    owner_ev = await _dispatch(adapter, _env("x", OWNER_PN))
    stranger_ev = await _dispatch(adapter, _env("x", STRANGER))
    assert await _reauth(adapter, owner_ev.source) is True
    assert await _reauth(adapter, stranger_ev.source) is False


async def test_reauthorize_reads_current_admin_list() -> None:
    adapter = _lp_adapter(**_owner_extra())
    ev = await _dispatch(adapter, _env("x", OWNER_PN))
    adapter.config.extra["allow_admin_from"] = []
    assert await _reauth(adapter, ev.source) is False
