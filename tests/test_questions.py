"""v4.5.0 owner-DM question tests (chatlytics v5.4 P8, gateway half).

Covers the LOCKED wire contract:

- ``POST /api/v1/bot/questions`` body shape (type, text, request_id
  charset/length, chat_id) for both ``send_exec_approval`` and
  ``send_clarify``; 201 → pending-registry entry + SendResult(success=True,
  message_id == request_id).
- Non-201 → failure (SendResult(success=False) for approvals; delegation to
  the BASE in-chat text fallback for clarifies). One retry, SAME
  request_id, on 502 ``owner_delivery_failed``.
- ``question_resolved`` control-envelope routing: approval entries map
  approved→"once" / denied→"deny" into
  ``tools.approval.resolve_gateway_approval``; clarify entries resolve via
  ``tools.clarify_gateway.resolve_gateway_clarify`` (answered → the answer;
  denied → the "" no-response sentinel); unknown request_ids warn + no-op.
- ``ask_approval`` / ``ask_clarify`` awaitable primitives (future kind):
  approved→True / answered→text; denied/timeout → False/None (fail-closed).
- Pending-question registry FIFO bound.
- channel_prompt injection in ``_dispatch_envelope`` (envelope value, absent
  → None, config.yaml ``channel_prompts`` fallback).
- ``_LONGPOLL_CAPS`` stays ``"control"`` (contract guard — question_resolved
  rides the existing control cap, no new capability token).

Test strategy clones tests/test_control.py: adapters built without
connecting, a scripted fake client stands in for ``ChatlyticsClient``, and
recorders replace ``handle_message``.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Dict, List, Optional, Tuple

import httpx
import pytest

from chatlytics_hermes.adapter import (
    _LONGPOLL_CAPS,
    _PENDING_QUESTIONS_MAX,
    ChatlyticsAdapter,
)
from tests._fixtures import FakePlatformConfig
from tests.test_longpoll import (
    BASE_URL,
    BOT_TOKEN,
    CHAT_ID,
    SESSION_ID,
    _install_recorders,
    _make_adapter,
)

SENDER = "972544329000@c.us"
OWNER_DM = "972544329000@c.us"
SESSION_KEY = "chatlytics:group:120363100000000000@g.us"
CLARIFY_ID = "clf_test_1"

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


class FakeQuestionsClient:
    """Scripted stand-in for ChatlyticsClient on the questions endpoint.

    ``responses`` is a list of (status_code, json_body) tuples served by
    successive POST calls; once exhausted every further POST returns 201
    with the canonical pending shape echoing the request_id.
    """

    def __init__(self, responses: Optional[List[Tuple[int, Any]]] = None) -> None:
        self._responses = list(responses or [])
        self.post_calls: List[Dict[str, Any]] = []
        self.base_url = BASE_URL

    async def post(
        self, path: str, *, json: Dict[str, Any] | None = None, timeout: Any = None
    ) -> httpx.Response:
        self.post_calls.append({"path": path, "json": dict(json or {})})
        if self._responses:
            status, body = self._responses.pop(0)
        else:
            status, body = 201, {
                "request_id": (json or {}).get("request_id"),
                "short_id": "q1",
                "status": "pending",
                "expires_at": "2026-06-12T00:00:00Z",
            }
        return httpx.Response(
            status, json=body, request=httpx.Request("POST", BASE_URL + path)
        )

    async def aclose(self) -> None:  # pragma: no cover - parity with real client
        return None


def _adapter_with_client(
    responses: Optional[List[Tuple[int, Any]]] = None,
) -> Tuple[ChatlyticsAdapter, FakeQuestionsClient]:
    adapter = _make_adapter()
    fake = FakeQuestionsClient(responses)
    adapter._client = fake  # type: ignore[assignment]
    return adapter, fake


def _resolved_envelope(
    request_id: str, resolution: str, answer: Optional[str] = None
) -> Dict[str, Any]:
    env: Dict[str, Any] = {
        "kind": "control",
        "action": "question_resolved",
        "request_id": request_id,
        "resolution": resolution,
        "bot_token": BOT_TOKEN,
        "session_id": SESSION_ID,
        "chat_type": "dm",
        "entity_jid": OWNER_DM,  # owner DM, not the triggering chat
        "sender_jid": SENDER,
        "ts": 1700000010,
    }
    if answer is not None:
        env["answer"] = answer
    return env


# --- send_exec_approval -------------------------------------------------------


async def test_send_exec_approval_posts_and_registers() -> None:
    adapter, fake = _adapter_with_client()

    result = await adapter.send_exec_approval(
        chat_id=CHAT_ID,
        command="rm -rf /tmp/scratch",
        session_key=SESSION_KEY,
        description="dangerous command",
        metadata=None,
    )

    assert result.success is True
    assert len(fake.post_calls) == 1
    call = fake.post_calls[0]
    assert call["path"] == "/api/v1/bot/questions"
    body = call["json"]
    assert body["type"] == "approval"
    assert body["chat_id"] == CHAT_ID
    assert "rm -rf /tmp/scratch" in body["text"]
    assert "dangerous command" in body["text"]
    rid = body["request_id"]
    assert _REQUEST_ID_RE.fullmatch(rid), (
        "request_id must satisfy the server's 8..64 [A-Za-z0-9_-] rule"
    )
    assert result.message_id == rid
    # Pending entry registered for the question_resolved control envelope.
    entry = adapter._pending_questions[rid]
    assert entry["kind"] == "approval"
    assert entry["session_key"] == SESSION_KEY
    assert entry["chat_id"] == CHAT_ID


async def test_send_exec_approval_truncates_long_command() -> None:
    adapter, fake = _adapter_with_client()

    await adapter.send_exec_approval(
        chat_id=CHAT_ID,
        command="x" * 5000,
        session_key=SESSION_KEY,
        description="dangerous command",
    )

    text = fake.post_calls[0]["json"]["text"]
    # Command capped at 1500 chars + ellipsis; server text cap is 2000.
    assert len(text) <= 2000
    assert text.endswith("…")


async def test_send_exec_approval_post_failure_returns_failed_sendresult(
    caplog: pytest.LogCaptureFixture,
) -> None:
    adapter, fake = _adapter_with_client(
        responses=[(429, {"error": "too_many_pending_questions"})]
    )

    with caplog.at_level("WARNING", logger="chatlytics_hermes.adapter"):
        result = await adapter.send_exec_approval(
            chat_id=CHAT_ID,
            command="rm -rf /",
            session_key=SESSION_KEY,
        )

    assert result.success is False
    assert result.error == "chatlytics question POST failed"
    assert adapter._pending_questions == {}
    # The dead-fallback / fail-closed warning is the operator's only signal.
    assert any("fail-closed" in rec.getMessage() for rec in caplog.records)


async def test_post_question_retries_once_on_502_with_same_request_id() -> None:
    adapter, fake = _adapter_with_client(
        responses=[
            (502, {"error": "owner_delivery_failed"}),
            # Fallthrough default (201) serves the retry.
        ]
    )

    result = await adapter.send_exec_approval(
        chat_id=CHAT_ID,
        command="echo hi",
        session_key=SESSION_KEY,
    )

    assert result.success is True
    assert len(fake.post_calls) == 2
    first_rid = fake.post_calls[0]["json"]["request_id"]
    second_rid = fake.post_calls[1]["json"]["request_id"]
    assert first_rid == second_rid, "502 retry must reuse the SAME request_id"
    assert result.message_id == first_rid


async def test_post_question_double_502_fails_after_one_retry() -> None:
    adapter, fake = _adapter_with_client(
        responses=[
            (502, {"error": "owner_delivery_failed"}),
            (502, {"error": "owner_delivery_failed"}),
        ]
    )

    result = await adapter.send_exec_approval(
        chat_id=CHAT_ID,
        command="echo hi",
        session_key=SESSION_KEY,
    )

    assert result.success is False
    assert len(fake.post_calls) == 2, "exactly ONE retry on 502"


# --- send_clarify ---------------------------------------------------------------


async def test_send_clarify_success_posts_choices_without_mark_awaiting_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter, fake = _adapter_with_client()

    awaiting_calls: List[str] = []
    monkeypatch.setattr(
        "tools.clarify_gateway.mark_awaiting_text",
        lambda cid: awaiting_calls.append(cid) or True,
    )

    result = await adapter.send_clarify(
        chat_id=CHAT_ID,
        question="Which color?",
        choices=["red", "blue"],
        clarify_id=CLARIFY_ID,
        session_key=SESSION_KEY,
    )

    assert result.success is True
    body = fake.post_calls[0]["json"]
    assert body["type"] == "clarify"
    assert body["chat_id"] == CHAT_ID
    assert "Which color?" in body["text"]
    assert "1. red" in body["text"] and "2. blue" in body["text"]
    assert "Answer with the option number or your own text." in body["text"]
    rid = body["request_id"]
    assert result.message_id == rid
    entry = adapter._pending_questions[rid]
    assert entry["kind"] == "clarify"
    assert entry["clarify_id"] == CLARIFY_ID
    # Owner-DM path: the answer arrives via /answer → control envelope, NOT
    # the next chat message — mark_awaiting_text must NOT be armed.
    assert awaiting_calls == []


async def test_send_clarify_failure_delegates_to_base_text_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter, fake = _adapter_with_client(
        responses=[(409, {"error": "gateway_not_control_capable"})]
    )

    awaiting_calls: List[str] = []
    monkeypatch.setattr(
        "tools.clarify_gateway.mark_awaiting_text",
        lambda cid: awaiting_calls.append(cid) or True,
    )

    sends: List[Dict[str, Any]] = []

    async def _fake_send(chat_id: str, content: str, reply_to=None, metadata=None):
        sends.append({"chat_id": chat_id, "content": content})
        from gateway.platforms.base import SendResult

        return SendResult(success=True, message_id="text-fallback")

    monkeypatch.setattr(adapter, "send", _fake_send)

    result = await adapter.send_clarify(
        chat_id=CHAT_ID,
        question="Which color?",
        choices=["red", "blue"],
        clarify_id=CLARIFY_ID,
        session_key=SESSION_KEY,
    )

    # Base fallback ran: numbered text sent in-chat + awaiting-text armed so
    # the gateway text-intercept captures the user's next message.
    assert result.success is True
    assert result.message_id == "text-fallback"
    assert len(sends) == 1
    assert "Which color?" in sends[0]["content"]
    assert awaiting_calls == [CLARIFY_ID]
    # No owner-DM registry entry on the fallback path.
    assert adapter._pending_questions == {}


# --- question_resolved control routing ------------------------------------------


@pytest.mark.parametrize(
    ("resolution", "expected_choice"),
    [("approved", "once"), ("denied", "deny")],
)
async def test_question_resolved_approval_maps_resolution_to_choice(
    monkeypatch: pytest.MonkeyPatch, resolution: str, expected_choice: str
) -> None:
    adapter, _fake = _adapter_with_client()
    await adapter.send_exec_approval(
        chat_id=CHAT_ID, command="echo", session_key=SESSION_KEY
    )
    rid = next(iter(adapter._pending_questions))

    resolved: List[Tuple[str, str]] = []
    monkeypatch.setattr(
        "tools.approval.resolve_gateway_approval",
        lambda session_key, choice, resolve_all=False: resolved.append(
            (session_key, choice)
        )
        or 1,
    )

    await adapter._handle_control_envelope(_resolved_envelope(rid, resolution))

    assert resolved == [(SESSION_KEY, expected_choice)]
    assert rid not in adapter._pending_questions


async def test_question_resolved_clarify_answered_delivers_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter, _fake = _adapter_with_client()
    await adapter.send_clarify(
        chat_id=CHAT_ID,
        question="Which color?",
        choices=None,
        clarify_id=CLARIFY_ID,
        session_key=SESSION_KEY,
    )
    rid = next(iter(adapter._pending_questions))

    resolved: List[Tuple[str, str]] = []
    monkeypatch.setattr(
        "tools.clarify_gateway.resolve_gateway_clarify",
        lambda cid, response: resolved.append((cid, response)) or True,
    )

    await adapter._handle_control_envelope(
        _resolved_envelope(rid, "answered", answer="blue")
    )

    assert resolved == [(CLARIFY_ID, "blue")]
    assert rid not in adapter._pending_questions


async def test_question_resolved_clarify_denied_resolves_with_empty_sentinel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter, _fake = _adapter_with_client()
    await adapter.send_clarify(
        chat_id=CHAT_ID,
        question="Which color?",
        choices=None,
        clarify_id=CLARIFY_ID,
        session_key=SESSION_KEY,
    )
    rid = next(iter(adapter._pending_questions))

    resolved: List[Tuple[str, str]] = []
    monkeypatch.setattr(
        "tools.clarify_gateway.resolve_gateway_clarify",
        lambda cid, response: resolved.append((cid, response)) or True,
    )

    await adapter._handle_control_envelope(_resolved_envelope(rid, "denied"))

    # Denial resolves with "" — the harness's own no-response sentinel
    # (clear_session contract) — so the agent thread unblocks immediately
    # without a fake answer being injected.
    assert resolved == [(CLARIFY_ID, "")]


async def test_question_resolved_unknown_request_id_warns_and_noops(
    caplog: pytest.LogCaptureFixture,
) -> None:
    adapter, _fake = _adapter_with_client()

    with caplog.at_level("WARNING", logger="chatlytics_hermes.adapter"):
        await adapter._handle_control_envelope(
            _resolved_envelope("deadbeefdeadbeef", "approved")
        )

    assert any(
        "unknown/expired request_id" in rec.getMessage() for rec in caplog.records
    )


async def test_question_resolved_missing_request_id_warns_and_noops(
    caplog: pytest.LogCaptureFixture,
) -> None:
    adapter, _fake = _adapter_with_client()
    env = _resolved_envelope("x", "approved")
    del env["request_id"]

    with caplog.at_level("WARNING", logger="chatlytics_hermes.adapter"):
        await adapter._handle_control_envelope(env)

    assert any(
        "without a valid request_id" in rec.getMessage() for rec in caplog.records
    )


# --- ask_approval / ask_clarify primitives ---------------------------------------


async def _wait_for_pending(adapter: ChatlyticsAdapter) -> str:
    for _ in range(50):
        if adapter._pending_questions:
            return next(iter(adapter._pending_questions))
        await asyncio.sleep(0)
    raise AssertionError("pending question never registered")


async def test_ask_approval_approved_returns_true() -> None:
    adapter, _fake = _adapter_with_client()

    task = asyncio.create_task(
        adapter.ask_approval("deploy to prod?", CHAT_ID, timeout_s=5.0)
    )
    rid = await _wait_for_pending(adapter)
    await adapter._handle_control_envelope(_resolved_envelope(rid, "approved"))

    assert await task is True


async def test_ask_approval_denied_returns_false() -> None:
    adapter, _fake = _adapter_with_client()

    task = asyncio.create_task(
        adapter.ask_approval("deploy to prod?", CHAT_ID, timeout_s=5.0)
    )
    rid = await _wait_for_pending(adapter)
    await adapter._handle_control_envelope(_resolved_envelope(rid, "denied"))

    assert await task is False


async def test_ask_approval_timeout_returns_false_and_pops_entry() -> None:
    adapter, _fake = _adapter_with_client()

    result = await adapter.ask_approval("deploy?", CHAT_ID, timeout_s=0.01)

    assert result is False, "timeout must default-DENY (fail-closed)"
    assert adapter._pending_questions == {}, "timeout must pop the registry entry"


async def test_ask_approval_post_failure_returns_false() -> None:
    adapter, _fake = _adapter_with_client(
        responses=[(409, {"error": "owner_unresolved"})]
    )

    assert await adapter.ask_approval("deploy?", CHAT_ID, timeout_s=1.0) is False


async def test_ask_clarify_answered_returns_text() -> None:
    adapter, _fake = _adapter_with_client()

    task = asyncio.create_task(
        adapter.ask_clarify("which env?", CHAT_ID, timeout_s=5.0)
    )
    rid = await _wait_for_pending(adapter)
    await adapter._handle_control_envelope(
        _resolved_envelope(rid, "answered", answer="staging")
    )

    assert await task == "staging"


async def test_ask_clarify_denied_returns_none() -> None:
    adapter, _fake = _adapter_with_client()

    task = asyncio.create_task(
        adapter.ask_clarify("which env?", CHAT_ID, timeout_s=5.0)
    )
    rid = await _wait_for_pending(adapter)
    await adapter._handle_control_envelope(_resolved_envelope(rid, "denied"))

    assert await task is None


async def test_ask_clarify_timeout_returns_none() -> None:
    adapter, _fake = _adapter_with_client()

    assert await adapter.ask_clarify("which env?", CHAT_ID, timeout_s=0.01) is None
    assert adapter._pending_questions == {}


# --- v4.5.1 (review-d3 X15) question robustness -----------------------------------


class RecordingRegistryClient(FakeQuestionsClient):
    """FakeQuestionsClient that snapshots the pending registry AT POST time."""

    def __init__(self, adapter: ChatlyticsAdapter, responses=None) -> None:
        super().__init__(responses)
        self._adapter = adapter
        self.registered_at_post: List[bool] = []

    async def post(self, path: str, *, json=None, timeout=None):
        rid = (json or {}).get("request_id")
        self.registered_at_post.append(rid in self._adapter._pending_questions)
        return await super().post(path, json=json, timeout=timeout)


class RaisingQuestionsClient:
    """Questions client whose POST always raises a transport error."""

    def __init__(self) -> None:
        self.post_calls: List[Dict[str, Any]] = []
        self.base_url = BASE_URL

    async def post(self, path: str, *, json=None, timeout=None):
        self.post_calls.append({"path": path, "json": dict(json or {})})
        raise httpx.ConnectError("connection refused")

    async def aclose(self) -> None:  # pragma: no cover — parity
        return None


async def test_pending_entry_registered_before_post() -> None:
    """The registry entry (and future) must exist BEFORE the POST goes out,
    so a question_resolved envelope racing the POST response can never
    orphan the resolution."""
    adapter = _make_adapter()
    fake = RecordingRegistryClient(adapter)
    adapter._client = fake  # type: ignore[assignment]

    await adapter.send_exec_approval(
        chat_id=CHAT_ID, command="echo", session_key=SESSION_KEY
    )
    result = await adapter.ask_approval("deploy?", CHAT_ID, timeout_s=0.01)

    assert result is False  # timed out — nothing resolved it
    assert fake.registered_at_post == [True, True], (
        "registry entry must be present at POST time for BOTH the "
        "send_exec_approval and ask_approval paths"
    )


async def test_ask_approval_transport_error_keeps_waiting_for_resolution() -> None:
    """Transport error = unknown outcome: the question MAY have been
    delivered, so the future is KEPT and an owner approval still wins."""
    adapter = _make_adapter()
    adapter._client = RaisingQuestionsClient()  # type: ignore[assignment]

    task = asyncio.create_task(
        adapter.ask_approval("deploy?", CHAT_ID, timeout_s=5.0)
    )
    rid = await _wait_for_pending(adapter)
    await adapter._handle_control_envelope(_resolved_envelope(rid, "approved"))

    assert await task is True, (
        "owner approval after a transport-errored POST must still resolve"
    )
    assert adapter._pending_questions == {}


async def test_ask_approval_transport_error_times_out_to_deny() -> None:
    adapter = _make_adapter()
    adapter._client = RaisingQuestionsClient()  # type: ignore[assignment]

    assert await adapter.ask_approval("deploy?", CHAT_ID, timeout_s=0.01) is False
    assert adapter._pending_questions == {}, "timeout must clean the entry"


async def test_send_exec_approval_transport_error_keeps_entry() -> None:
    """Unknown POST outcome on the runner path keeps the registry entry so a
    late owner /approve can still resolve the gateway-side wait."""
    adapter = _make_adapter()
    adapter._client = RaisingQuestionsClient()  # type: ignore[assignment]

    result = await adapter.send_exec_approval(
        chat_id=CHAT_ID, command="echo", session_key=SESSION_KEY
    )

    assert result.success is False
    assert len(adapter._pending_questions) == 1, (
        "transport-error (unknown outcome) must KEEP the pending entry"
    )


async def test_409_duplicate_request_id_treated_as_question_exists() -> None:
    """409 duplicate_request_id means the question row already exists —
    keep waiting (NOT a failure)."""
    adapter, fake = _adapter_with_client(
        responses=[(409, {"error": "duplicate_request_id"})]
    )

    task = asyncio.create_task(
        adapter.ask_approval("deploy?", CHAT_ID, timeout_s=5.0)
    )
    rid = await _wait_for_pending(adapter)
    await adapter._handle_control_envelope(_resolved_envelope(rid, "approved"))

    assert await task is True
    assert len(fake.post_calls) == 1, "no retry on duplicate_request_id"


async def test_send_exec_approval_409_duplicate_is_success() -> None:
    adapter, fake = _adapter_with_client(
        responses=[(409, {"error": "duplicate_request_id"})]
    )

    result = await adapter.send_exec_approval(
        chat_id=CHAT_ID, command="echo", session_key=SESSION_KEY
    )

    assert result.success is True
    assert len(adapter._pending_questions) == 1


async def test_question_posts_carry_ttl_aligned_to_wait() -> None:
    """ttl_s rides every question POST, aligned to the caller's wait + 60 s
    (clamped to the server's 60..86400 contract range)."""
    adapter, fake = _adapter_with_client()

    await adapter.send_exec_approval(
        chat_id=CHAT_ID, command="echo", session_key=SESSION_KEY
    )
    await adapter.send_clarify(
        chat_id=CHAT_ID,
        question="q?",
        choices=None,
        clarify_id=CLARIFY_ID,
        session_key=SESSION_KEY,
    )
    await adapter.ask_approval("deploy?", CHAT_ID, timeout_s=0.01)

    ttls = [c["json"].get("ttl_s") for c in fake.post_calls]
    # send_exec_approval / send_clarify: runner wait 600 s → 660.
    assert ttls[0] == 660 and ttls[1] == 660
    # ask_approval(timeout_s=0.01): clamped to the server minimum 60.
    assert ttls[2] == 60


async def test_ask_clarify_ttl_matches_explicit_timeout() -> None:
    adapter, fake = _adapter_with_client()

    task = asyncio.create_task(
        adapter.ask_clarify("env?", CHAT_ID, timeout_s=300.0)
    )
    rid = await _wait_for_pending(adapter)
    await adapter._handle_control_envelope(
        _resolved_envelope(rid, "answered", answer="staging")
    )

    assert await task == "staging"
    assert fake.post_calls[0]["json"]["ttl_s"] == 360  # 300 + 60


# --- registry bound ---------------------------------------------------------------


async def test_pending_question_registry_is_fifo_bounded() -> None:
    adapter = _make_adapter()

    for i in range(_PENDING_QUESTIONS_MAX + 10):
        adapter._register_pending_question(
            f"rid{i:04d}",
            {
                "kind": "approval",
                "session_key": SESSION_KEY,
                "clarify_id": None,
                "future": None,
                "chat_id": CHAT_ID,
            },
        )

    assert len(adapter._pending_questions) == _PENDING_QUESTIONS_MAX
    # Oldest entries FIFO-evicted first.
    assert "rid0000" not in adapter._pending_questions
    assert f"rid{_PENDING_QUESTIONS_MAX + 9:04d}" in adapter._pending_questions


# --- channel_prompt injection ------------------------------------------------------


def _msg_envelope(text: str = "hello", **overrides: Any) -> Dict[str, Any]:
    env: Dict[str, Any] = {
        "bot_token": BOT_TOKEN,
        "session_id": SESSION_ID,
        "chat_type": "group",
        "entity_jid": CHAT_ID,
        "sender_jid": SENDER,
        "text": text,
        "dispatch": {"reason": "mention", "god_mode": False},
        "ts": 1700000000,
    }
    env.update(overrides)
    return env


async def test_dispatch_envelope_injects_channel_prompt_from_envelope() -> None:
    adapter = _make_adapter()
    events, _ = _install_recorders(adapter)

    await adapter._dispatch_envelope(
        _msg_envelope("hi", channel_prompt="  Be terse.  ")
    )

    assert len(events) == 1
    assert events[0].channel_prompt == "Be terse."


async def test_dispatch_envelope_without_prompt_or_config_leaves_none() -> None:
    adapter = _make_adapter()
    events, _ = _install_recorders(adapter)

    await adapter._dispatch_envelope(_msg_envelope("hi"))

    assert len(events) == 1
    assert events[0].channel_prompt is None


async def test_dispatch_envelope_falls_back_to_config_channel_prompts() -> None:
    extra: Dict[str, Any] = {
        "base_url": BASE_URL,
        "bot_token": BOT_TOKEN,
        "inbound_mode": "longpoll",
        "channel_prompts": {CHAT_ID: "From local config."},
    }
    adapter = ChatlyticsAdapter(FakePlatformConfig(extra=extra))
    events, _ = _install_recorders(adapter)

    await adapter._dispatch_envelope(_msg_envelope("hi"))

    assert len(events) == 1
    assert events[0].channel_prompt == "From local config."


async def test_envelope_channel_prompt_wins_over_config_fallback() -> None:
    extra: Dict[str, Any] = {
        "base_url": BASE_URL,
        "bot_token": BOT_TOKEN,
        "inbound_mode": "longpoll",
        "channel_prompts": {CHAT_ID: "From local config."},
    }
    adapter = ChatlyticsAdapter(FakePlatformConfig(extra=extra))
    events, _ = _install_recorders(adapter)

    await adapter._dispatch_envelope(
        _msg_envelope("hi", channel_prompt="Server wins.")
    )

    assert events[0].channel_prompt == "Server wins."


# --- contract guards ----------------------------------------------------------------


def test_longpoll_caps_advertisement_unchanged() -> None:
    """question_resolved rides the EXISTING control cap — the server refuses
    question POSTs from gateways that didn't advertise ``caps=control``, and
    the P6 serve-time purge protects downgrades. Adding a new token here
    would be a wire-contract change; this test pins it."""
    assert _LONGPOLL_CAPS == "control"
