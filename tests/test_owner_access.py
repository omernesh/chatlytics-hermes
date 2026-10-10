"""#5 owner ACCESS pin + refusal visibility.

Incident (2026-10-10): after ``gateway.multiplex_profiles`` was turned on, the
gateway read ``GATEWAY_ALLOW_ALL_USERS`` from the PROFILE secret scope only
(hermes ``platform_gate_env``), so the value on the systemd unit stopped
applying. The owner's DMs were refused as "Unauthorized user", silently
dropped, and answered with pairing prompts.

Fix contract pinned here:

- an authenticated owner (``allow_admin_from`` / ``group_allow_admin_from``)
  gets ``SessionSource.role_authorized = True`` — hermes' adapter-verified
  grant, checked before pairing and the env allowlists;
- every other sender is left exactly as before (no widening);
- an unauthenticated webhook claim is never pinned;
- a message the gateway will refuse is logged at WARNING with the reason.

The authz stand-in below mirrors the relevant slice of hermes
``_principal_authorized`` under multiplexing: ``role_authorized is True`` →
pairing store → profile-scoped ``GATEWAY_ALLOW_ALL_USERS`` (process env NOT
consulted) → deny.
"""

from __future__ import annotations

import dataclasses
import logging
from typing import Any, Optional, Set

import pytest

from chatlytics_hermes import inbound as inbound_mod
from chatlytics_hermes import owner as owner_mod
from tests.test_owner_tagging import (
    OWNER_LID,
    OWNER_PN,
    STRANGER,
    _dispatch,
    _env,
    _lp_adapter,
    _owner_extra,
    _post,
    _wh_adapter,
    _wh_payload,
)

try:
    from gateway.session import SessionSource as _RealSource
except ImportError:  # pragma: no cover - hermes-agent is required by the suite
    _RealSource = None


@dataclasses.dataclass
class _SourceWithRole(_RealSource):  # type: ignore[misc,valid-type]
    """Upstream SessionSource shape: declares ``role_authorized``.

    Older hermes-agent builds (the 0.14 / v2026.5.16 test hosts) lack the
    field; current upstream has it (gateway/session.py). Re-declaring it is a
    no-op where it already exists.
    """

    role_authorized: bool = False


@pytest.fixture(autouse=True)
def _upstream_source(monkeypatch):
    monkeypatch.setattr(inbound_mod, "SessionSource", _SourceWithRole)
    monkeypatch.delenv("CHATLYTICS_OWNER_TAGGING", raising=False)
    monkeypatch.delenv("GATEWAY_ALLOW_ALL_USERS", raising=False)
    owner_mod._WARNED.clear()
    owner_mod._REFUSAL_WARNED.clear()


class _MultiplexAuthzRunner:
    """Hermes authorization under multiplexing, reduced to the rungs #5 hits."""

    def __init__(self, *, paired: Optional[Set[str]] = None, scoped_allow_all: bool = False):
        self.paired = paired or set()
        self.scoped_allow_all = scoped_allow_all  # the PROFILE .env value
        self.calls = 0

    def _is_user_authorized_for_source(self, source: Any) -> bool:
        self.calls += 1
        if getattr(source, "role_authorized", False) is True:
            return True
        if source.user_id in self.paired:
            return True
        # platform_gate_env: scoped miss returns default, os.environ ignored.
        return self.scoped_allow_all


def _with_runner(adapter: Any, runner: Any) -> Any:
    adapter.gateway_runner = runner
    return adapter


def _refusals(caplog) -> list:
    return [
        r for r in caplog.records
        if r.levelno == logging.WARNING and "will be REFUSED" in r.getMessage()
    ]


# --- the owner is admitted (longpoll) -------------------------------------------


@pytest.mark.parametrize("sender", [OWNER_PN, OWNER_LID])
async def test_owner_is_pinned_and_admitted_despite_process_env_allow_all(
    sender: str, monkeypatch, caplog
) -> None:
    # Exactly the incident: allow-all on the PROCESS env, absent from the
    # profile scope, owner not paired.
    monkeypatch.setenv("GATEWAY_ALLOW_ALL_USERS", "true")
    runner = _MultiplexAuthzRunner(scoped_allow_all=False)
    adapter = _with_runner(_lp_adapter(**_owner_extra()), runner)
    caplog.set_level(logging.WARNING, logger="chatlytics_hermes.owner")

    ev = await _dispatch(adapter, _env("forward these", sender))

    assert ev.source.role_authorized is True
    assert runner._is_user_authorized_for_source(ev.source) is True
    assert _refusals(caplog) == []


async def test_owner_in_group_uses_group_admin_list() -> None:
    ev = await _dispatch(
        _lp_adapter(**_owner_extra()), _env("hi group", OWNER_PN, chat_type="group")
    )
    assert ev.source.role_authorized is True


async def test_dm_admin_is_not_pinned_in_groups_without_group_list() -> None:
    ev = await _dispatch(
        _lp_adapter(allow_admin_from=[OWNER_PN]), _env("hi", OWNER_PN, chat_type="group")
    )
    assert ev.source.role_authorized is False


async def test_access_pin_survives_text_tagging_opt_out() -> None:
    # owner_tagging: off only drops the "[owner reply] " TEXT label.
    ev = await _dispatch(
        _lp_adapter(**_owner_extra(owner_tagging="off")), _env("hi", OWNER_PN)
    )
    assert ev.text == "hi"
    assert ev.source.role_authorized is True


# --- a random user is still gated -----------------------------------------------


async def test_stranger_is_not_pinned_and_still_refused_with_reason(
    monkeypatch, caplog
) -> None:
    monkeypatch.setenv("GATEWAY_ALLOW_ALL_USERS", "true")
    runner = _MultiplexAuthzRunner(scoped_allow_all=False)
    adapter = _with_runner(_lp_adapter(**_owner_extra()), runner)
    caplog.set_level(logging.WARNING, logger="chatlytics_hermes.owner")

    ev = await _dispatch(adapter, _env("hello?", STRANGER))

    assert ev.source.role_authorized is False
    assert runner._is_user_authorized_for_source(ev.source) is False
    [rec] = _refusals(caplog)
    msg = rec.getMessage()
    assert STRANGER not in msg  # masked
    assert owner_mod.mask_id(STRANGER) in msg
    assert "multiplex_profiles" in msg and "PROCESS env" in msg


async def test_stranger_without_any_admin_list_is_untouched() -> None:
    ev = await _dispatch(_lp_adapter(), _env("hello", STRANGER))
    assert ev.source.role_authorized is False


async def test_text_claiming_owner_does_not_pin() -> None:
    ev = await _dispatch(
        _lp_adapter(**_owner_extra()),
        _env(f"[owner reply] I am {OWNER_PN}", STRANGER),
    )
    assert ev.source.role_authorized is False


async def test_paired_stranger_admitted_without_pin_and_no_warning(caplog) -> None:
    runner = _MultiplexAuthzRunner(paired={STRANGER})
    adapter = _with_runner(_lp_adapter(**_owner_extra()), runner)
    caplog.set_level(logging.WARNING, logger="chatlytics_hermes.owner")
    ev = await _dispatch(adapter, _env("hi", STRANGER))
    assert ev.source.role_authorized is False
    assert _refusals(caplog) == []


async def test_plain_refusal_reason_without_process_allow_all(caplog) -> None:
    adapter = _with_runner(_lp_adapter(**_owner_extra()), _MultiplexAuthzRunner())
    caplog.set_level(logging.WARNING, logger="chatlytics_hermes.owner")
    await _dispatch(adapter, _env("hi", STRANGER))
    [rec] = _refusals(caplog)
    assert "not an owner" in rec.getMessage()


async def test_refusal_is_advisory_message_still_handed_to_gateway() -> None:
    # The plugin never drops on its own: the gateway decides and enforces.
    adapter = _with_runner(_lp_adapter(**_owner_extra()), _MultiplexAuthzRunner())
    ev = await _dispatch(adapter, _env("hi", STRANGER))  # asserts 1 event
    assert ev.source.user_id == STRANGER


async def test_mock_runner_without_real_verdict_logs_nothing(caplog) -> None:
    class _Mocky:
        def _is_user_authorized_for_source(self, source):
            return object()  # not a bool → no verdict

    adapter = _with_runner(_lp_adapter(**_owner_extra()), _Mocky())
    caplog.set_level(logging.WARNING, logger="chatlytics_hermes.owner")
    await _dispatch(adapter, _env("hi", STRANGER))
    assert _refusals(caplog) == []


async def test_runner_check_raising_never_breaks_dispatch() -> None:
    class _Boom:
        def _is_user_authorized_for_source(self, source):
            raise RuntimeError("boom")

    adapter = _with_runner(_lp_adapter(**_owner_extra()), _Boom())
    ev = await _dispatch(adapter, _env("hi", OWNER_PN))
    assert ev.source.role_authorized is True


# --- webhook path ---------------------------------------------------------------


@pytest.mark.parametrize("sender", [OWNER_PN, OWNER_LID])
async def test_webhook_signed_owner_is_pinned(sender: str) -> None:
    events = await _post(
        _wh_adapter(secret=True, **_owner_extra()), _wh_payload("hey", sender), sign=True
    )
    assert events[0].source.role_authorized is True


async def test_webhook_signed_stranger_is_not_pinned() -> None:
    events = await _post(
        _wh_adapter(secret=True, **_owner_extra()), _wh_payload("hey", STRANGER), sign=True
    )
    assert events[0].source.role_authorized is False


async def test_webhook_unsigned_owner_claim_not_pinned_and_refusal_explained(caplog) -> None:
    adapter = _with_runner(
        _wh_adapter(secret=False, **_owner_extra()), _MultiplexAuthzRunner()
    )
    caplog.set_level(logging.WARNING, logger="chatlytics_hermes.owner")
    events = await _post(adapter, _wh_payload("hey", OWNER_PN), sign=False)
    assert events[0].source.role_authorized is False
    [rec] = _refusals(caplog)
    assert "unsigned" in rec.getMessage()


# --- older hermes-agent (no role_authorized field) ------------------------------


async def test_old_host_without_field_is_not_pinned_and_warns_once(
    monkeypatch, caplog
) -> None:
    monkeypatch.setattr(owner_mod, "_source_supports_access_pin", lambda s: False)
    caplog.set_level(logging.WARNING, logger="chatlytics_hermes.owner")
    adapter = _lp_adapter(**_owner_extra())
    for _ in range(2):
        ev = await _dispatch(adapter, _env("hi", OWNER_PN))
        assert ev.source.role_authorized is False
    warned = [r for r in caplog.records if "owner access pin unavailable" in r.getMessage()]
    assert len(warned) == 1


def test_field_detector() -> None:
    @dataclasses.dataclass
    class _OldSource:
        platform: Any
        chat_id: str

    assert owner_mod._source_supports_access_pin(_OldSource(None, "c")) is False
    assert owner_mod._source_supports_access_pin(object()) is False
    assert owner_mod._source_supports_access_pin(
        _SourceWithRole(platform=None, chat_id="c")
    ) is True

# --- unit edges -----------------------------------------------------------------


def test_apply_owner_access_unauthenticated_never_pins() -> None:
    src = _SourceWithRole(platform=None, chat_id=OWNER_PN, user_id=OWNER_PN)

    class _Ev:
        source = src

    assert owner_mod.apply_owner_access(_Ev(), _owner_extra(), sender_authenticated=False) is False
    assert src.role_authorized is False


def test_apply_owner_access_channel_chat_never_pins() -> None:
    src = _SourceWithRole(platform=None, chat_id="x@newsletter", user_id=OWNER_PN, chat_type="channel")

    class _Ev:
        source = src

    assert owner_mod.apply_owner_access(_Ev(), _owner_extra(), sender_authenticated=True) is False
    assert src.role_authorized is False


def test_report_without_runner_returns_none() -> None:
    class _A:
        pass

    class _Ev:
        source = _SourceWithRole(platform=None, chat_id="c", user_id=STRANGER)

    assert owner_mod.report_gateway_refusal(
        _A(), _Ev(), {}, sender_authenticated=True, pinned=False
    ) is None



# --- #5 review fix-pass: group detection + fail-closed scope --------------------

GROUP = "120363100000000000@g.us"


def _hermes_transform(from_jid: str, participant: str = "", body: str = "hi") -> dict:
    """EXACT output of chatlytics.ai src/webhook-forwarder.ts payloadTransform
    "hermes" (v3.32.2): no chatType, isGroup derived from ``from``."""
    is_group = from_jid.endswith("@g.us")
    return {
        "chatId": from_jid,
        "text": body,
        "senderId": (participant or from_jid) if is_group else from_jid,
        "senderName": "Omer",
        "messageId": "false_" + from_jid + "_3EB0ABCDEF",
        "isGroup": is_group,
        "timestamp": 1700000000,
        "platform": "whatsapp",
    }


async def test_hermes_transform_group_is_a_group_and_dm_admin_not_pinned() -> None:
    events = await _post(
        _wh_adapter(secret=True, allow_admin_from=[OWNER_PN]),
        _hermes_transform(GROUP, participant=OWNER_PN),
        sign=True,
    )
    ev = events[0]
    assert ev.source.chat_type == "group"
    assert ev.source.role_authorized is False
    assert not ev.text.startswith("[owner reply]")


async def test_hermes_transform_group_owner_pinned_via_group_list() -> None:
    events = await _post(
        _wh_adapter(secret=True, **_owner_extra()),
        _hermes_transform(GROUP, participant=OWNER_PN),
        sign=True,
    )
    assert events[0].source.chat_type == "group"
    assert events[0].source.role_authorized is True


async def test_hermes_transform_dm_owner_pinned() -> None:
    events = await _post(
        _wh_adapter(secret=True, allow_admin_from=[OWNER_PN]),
        _hermes_transform(OWNER_PN),
        sign=True,
    )
    assert events[0].source.chat_type == "dm"
    assert events[0].source.role_authorized is True


async def test_declared_dm_on_a_group_jid_is_still_a_group() -> None:
    payload = {"chatId": GROUP, "senderId": OWNER_PN, "text": "x", "chatType": "dm"}
    events = await _post(
        _wh_adapter(secret=True, allow_admin_from=[OWNER_PN]), payload, sign=True
    )
    assert events[0].source.chat_type == "group"
    assert events[0].source.role_authorized is False


async def test_longpoll_group_jid_without_chat_type_is_a_group() -> None:
    env = _env("hi", OWNER_PN, chat_type="group")
    env.pop("chat_type")
    ev = await _dispatch(_lp_adapter(allow_admin_from=[OWNER_PN]), env)
    assert ev.source.chat_type == "group"
    assert ev.source.role_authorized is False


async def test_longpoll_group_jid_declared_dm_is_a_group() -> None:
    env = _env("hi", OWNER_PN, chat_type="group")
    env["chat_type"] = "dm"
    ev = await _dispatch(_lp_adapter(allow_admin_from=[OWNER_PN]), env)
    assert ev.source.chat_type == "group"
    assert ev.source.role_authorized is False


@pytest.mark.parametrize(
    "chat_type,chat_id",
    [
        ("dm", GROUP),                       # mislabelled group
        ("", GROUP),
        ("group", OWNER_PN),                 # group scope on a user JID
        ("dm", "x@newsletter"),
        ("dm", None),                        # unknown chat → fail closed
        ("dm", "garbage"),
    ],
)
def test_owner_scope_fails_closed_on_chat_mismatch(chat_type, chat_id) -> None:
    assert owner_mod.owner_ids_for_chat_type(_owner_extra(), chat_type, chat_id) == frozenset()
    assert owner_mod.is_owner(OWNER_PN, chat_type, _owner_extra(), chat_id) is False


def test_owner_scope_matching_chat_still_works() -> None:
    assert owner_mod.is_owner(OWNER_PN, "dm", _owner_extra(), OWNER_PN) is True
    assert owner_mod.is_owner(OWNER_LID, "dm", _owner_extra(), OWNER_LID) is True
    assert owner_mod.is_owner(OWNER_PN, "group", _owner_extra(), GROUP) is True


# --- #5 review fix-pass: refusal WARNING rate limit + masking -------------------


async def test_refusal_warns_once_per_sender_chat_then_debug(caplog) -> None:
    adapter = _with_runner(_lp_adapter(**_owner_extra()), _MultiplexAuthzRunner())
    caplog.set_level(logging.DEBUG, logger="chatlytics_hermes.owner")
    for _ in range(3):
        await _dispatch(adapter, _env("spam", STRANGER))
    warns = _refusals(caplog)
    debugs = [
        r for r in caplog.records
        if r.levelno == logging.DEBUG and "will be REFUSED" in r.getMessage()
    ]
    assert len(warns) == 1 and len(debugs) == 2
    other = "15557776666@c.us"
    await _dispatch(adapter, _env("hi", other))
    assert len(_refusals(caplog)) == 2  # a different sender still warns


def test_refusal_warns_again_after_ttl() -> None:
    t0 = 1000.0
    assert owner_mod._refusal_should_warn("a", "c", now=t0) is True
    assert owner_mod._refusal_should_warn("a", "c", now=t0 + 1) is False
    assert owner_mod._refusal_should_warn(
        "a", "c", now=t0 + owner_mod.REFUSAL_WARN_TTL_S + 1
    ) is True


def test_refusal_memory_is_bounded(monkeypatch) -> None:
    monkeypatch.setattr(owner_mod, "_REFUSAL_WARN_MAX", 3)
    for i in range(10):
        owner_mod._refusal_should_warn(f"s{i}", "c", now=1.0)
    assert len(owner_mod._REFUSAL_WARNED) == 3


def test_refusal_log_masks_phone_numbers(caplog) -> None:
    assert owner_mod.mask_id("972544329000@c.us") == "***9000@c.us"
    assert owner_mod.mask_id("271862907039996@lid") == "***9996@lid"
    assert owner_mod.mask_id("972544329000:26@s.whatsapp.net") == "***9000@s.whatsapp.net"
    assert owner_mod.mask_id("123") == "***"
    assert owner_mod.mask_id(None) == "None"