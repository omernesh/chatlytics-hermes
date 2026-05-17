"""HERMES-10 tests: input validation + UX alignment.

Covers Phase 10 acceptance criteria:

- ``__init__`` validation of ``webhook_path`` (8 tests; 03-LOW-01 + PR-MED-01)
- Tool schema validation of ``chatId`` / ``messageId`` (6 tests; 05-LOW-02 + PR-MED-01)
- ``chatlytics_login`` MCP-aligned semantics (5 tests; 05-LOW-03 + PR-LOW-03)

Total: 19 tests. All other Phase 10 deliverables are docstring-only
(``get_chat_info`` empty-vs-error semantics in adapter.py docstring;
``send_image`` / ``send_image_file`` cross-reference; tool-layer
``chatlytics_send_image`` docstring) and verified by code review.
"""

from __future__ import annotations

from typing import Any, Dict

import httpx
import jsonschema
import pytest
import respx

from chatlytics_hermes.adapter import ChatlyticsAdapter
from chatlytics_hermes.client import ChatlyticsClient
from chatlytics_hermes.tools import (
    REACT_SCHEMA,
    SEND_IMAGE_SCHEMA,
    SEND_SCHEMA,
    chatlytics_login,
)
from tests._fixtures import FakePlatformConfig

# Module-level marker omitted: section 1 + 2 tests are sync; section 3
# login-semantics tests are async and individually marked.


BASE_URL = "https://gateway.test.chatlytics.ai"
API_KEY = "test-api-key-validation"


def _make_config(**overrides: Any) -> FakePlatformConfig:
    """Build a FakePlatformConfig with the standard base_url/api_key
    pre-populated; tests override ``webhook_path`` etc.

    Phase 11 (HERMES-11) consolidated the previously copy-pasted
    ``_FakePlatformConfig`` shim into ``tests/_fixtures.FakePlatformConfig``.
    """
    extra: Dict[str, Any] = {
        "base_url": BASE_URL,
        "api_key": API_KEY,
    }
    extra.update(overrides)
    return FakePlatformConfig(extra=extra)


# ---------------------------------------------------------------------------
# Section 1: webhook_path validation at __init__ (8 tests)
# ---------------------------------------------------------------------------


def test_init_rejects_empty_webhook_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """03-LOW-01: empty webhook_path raises ValueError at __init__."""
    monkeypatch.delenv("CHATLYTICS_WEBHOOK_PATH", raising=False)
    cfg = _make_config(webhook_path="")
    with pytest.raises(ValueError, match="non-empty"):
        ChatlyticsAdapter(cfg)


def test_init_rejects_webhook_path_without_leading_slash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """03-LOW-01: webhook_path missing leading slash raises ValueError."""
    monkeypatch.delenv("CHATLYTICS_WEBHOOK_PATH", raising=False)
    cfg = _make_config(webhook_path="webhook")
    with pytest.raises(ValueError, match="must start with '/'"):
        ChatlyticsAdapter(cfg)


def test_init_rejects_webhook_path_equal_to_health(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PR-MED-01: webhook_path=/health collides with health route -> ValueError."""
    monkeypatch.delenv("CHATLYTICS_WEBHOOK_PATH", raising=False)
    cfg = _make_config(webhook_path="/health")
    with pytest.raises(ValueError, match="/health"):
        ChatlyticsAdapter(cfg)


def test_init_rejects_webhook_path_with_control_chars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """03-LOW-01: control characters in webhook_path raise ValueError."""
    monkeypatch.delenv("CHATLYTICS_WEBHOOK_PATH", raising=False)
    cfg = _make_config(webhook_path="/web\nhook")
    with pytest.raises(ValueError, match="control characters"):
        ChatlyticsAdapter(cfg)


def test_init_rejects_webhook_path_with_traversal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """03-LOW-01: '..' segments in webhook_path raise ValueError."""
    monkeypatch.delenv("CHATLYTICS_WEBHOOK_PATH", raising=False)
    cfg = _make_config(webhook_path="/../etc")
    with pytest.raises(ValueError, match=r"'\.\.'"):
        ChatlyticsAdapter(cfg)


def test_init_rejects_webhook_path_with_query_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """03-LOW-01: '?' or '#' in webhook_path raise ValueError."""
    monkeypatch.delenv("CHATLYTICS_WEBHOOK_PATH", raising=False)
    cfg = _make_config(webhook_path="/webhook?x=1")
    with pytest.raises(ValueError, match=r"'\?' or '#'"):
        ChatlyticsAdapter(cfg)


def test_init_accepts_valid_webhook_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    """03-LOW-01: valid paths pass validation without raising."""
    monkeypatch.delenv("CHATLYTICS_WEBHOOK_PATH", raising=False)
    for valid in ("/webhook", "/api/webhook", "/v1/inbound", "/chatlytics/in"):
        cfg = _make_config(webhook_path=valid)
        adapter = ChatlyticsAdapter(cfg)
        assert adapter.webhook_path == valid


def test_init_accepts_default_webhook_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """03-LOW-01: default '/webhook' (no override) passes validation."""
    monkeypatch.delenv("CHATLYTICS_WEBHOOK_PATH", raising=False)
    cfg = _make_config()  # no webhook_path -> defaults to /webhook
    adapter = ChatlyticsAdapter(cfg)
    assert adapter.webhook_path == "/webhook"


def test_init_rejects_webhook_path_with_whitespace_padding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WARNING-01 (10-REVIEW): whitespace-padded paths must be rejected.

    Previously the validator stripped before checking, which let inputs
    like "  /webhook  " pass while the unstripped value broke aiohttp
    route registration silently. The fix rejects leading/trailing
    whitespace explicitly.
    """
    monkeypatch.delenv("CHATLYTICS_WEBHOOK_PATH", raising=False)
    for padded in ("  /webhook", "/webhook  ", "  /webhook  ", "\t/webhook", "/webhook\n"):
        cfg = _make_config(webhook_path=padded)
        with pytest.raises(ValueError):
            ChatlyticsAdapter(cfg)


# ---------------------------------------------------------------------------
# Section 2: tool schema validation (6 tests)
# ---------------------------------------------------------------------------


def test_media_chat_id_rejects_empty_string() -> None:
    """05-LOW-02: media-tool chatId schema rejects ''."""
    validator = jsonschema.Draft202012Validator(SEND_IMAGE_SCHEMA)
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(
            {"chatId": "", "mediaUrl": "https://example.com/a.png"}
        )


def test_media_chat_id_rejects_control_chars() -> None:
    """05-LOW-02: media-tool chatId schema rejects control characters."""
    validator = jsonschema.Draft202012Validator(SEND_IMAGE_SCHEMA)
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(
            {"chatId": "abc\x00def", "mediaUrl": "https://example.com/a.png"}
        )


def test_media_chat_id_accepts_jid_format() -> None:
    """05-LOW-02: standard WhatsApp JID passes the permissive validator."""
    validator = jsonschema.Draft202012Validator(SEND_IMAGE_SCHEMA)
    validator.validate(
        {"chatId": "1234567890@c.us", "mediaUrl": "https://example.com/a.png"}
    )


def test_media_chat_id_accepts_phone_number() -> None:
    """05-LOW-02: bare phone number passes (Chatlytics resolves these)."""
    validator = jsonschema.Draft202012Validator(SEND_IMAGE_SCHEMA)
    validator.validate(
        {"chatId": "+1234567890", "mediaUrl": "https://example.com/a.png"}
    )


def test_media_chat_id_accepts_group_name() -> None:
    """05-LOW-02: display-name strings pass (permissive accept-set)."""
    validator = jsonschema.Draft202012Validator(SEND_IMAGE_SCHEMA)
    validator.validate(
        {"chatId": "My Group Name", "mediaUrl": "https://example.com/a.png"}
    )


def test_messaging_chat_id_rejects_empty_string() -> None:
    """05-LOW-02: same chatId tightening on non-media schemas (SEND, REACT)."""
    send_validator = jsonschema.Draft202012Validator(SEND_SCHEMA)
    with pytest.raises(jsonschema.ValidationError):
        send_validator.validate({"chatId": "", "text": "hello"})

    react_validator = jsonschema.Draft202012Validator(REACT_SCHEMA)
    # REACT's messageId is required; chatId is optional but still validated when present.
    with pytest.raises(jsonschema.ValidationError):
        react_validator.validate(
            {"messageId": "msg-1", "emoji": ":smile:", "chatId": ""}
        )


# ---------------------------------------------------------------------------
# Section 3: chatlytics_login MCP-aligned semantics (5 tests)
# ---------------------------------------------------------------------------


@pytest.fixture
async def login_client():
    """Fresh ChatlyticsClient pointed at BASE_URL for each test."""
    client = ChatlyticsClient(base_url=BASE_URL, api_key=API_KEY)
    try:
        yield client
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_chatlytics_login_returns_false_when_webhook_not_registered(
    login_client: ChatlyticsClient,
) -> None:
    """05-LOW-03 + PR-LOW-03: webhook_registered=False -> success=False.

    Matches the Claude Code MCP bundle's behavior (isError: true).
    """
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        router.get("/health").mock(
            return_value=httpx.Response(
                200,
                json={"webhook_registered": False, "sessions": []},
            )
        )
        result = await chatlytics_login(login_client)
    assert result["success"] is False
    assert "webhook_registered" in result["error"]
    # Diagnostic fields are populated so operators can see the state.
    assert result["webhook_registered"] is False
    assert result["sessions"] == 0


@pytest.mark.asyncio
async def test_chatlytics_login_returns_true_when_webhook_registered(
    login_client: ChatlyticsClient,
) -> None:
    """05-LOW-03: webhook_registered=True + 200 -> success=True with sessions count."""
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        router.get("/health").mock(
            return_value=httpx.Response(
                200,
                json={
                    "webhook_registered": True,
                    "sessions": [{"id": "s1"}, {"id": "s2"}],
                },
            )
        )
        result = await chatlytics_login(login_client)
    assert result["success"] is True
    assert result["webhook_registered"] is True
    assert result["sessions"] == 2


@pytest.mark.asyncio
async def test_chatlytics_login_session_count_from_int(
    login_client: ChatlyticsClient,
) -> None:
    """05-LOW-03: sessions: int payload -> int session count."""
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        router.get("/health").mock(
            return_value=httpx.Response(
                200,
                json={"webhook_registered": True, "sessions": 4},
            )
        )
        result = await chatlytics_login(login_client)
    assert result["success"] is True
    assert result["sessions"] == 4


@pytest.mark.asyncio
async def test_chatlytics_login_session_count_unknown_when_missing(
    login_client: ChatlyticsClient,
) -> None:
    """05-LOW-03: missing 'sessions' field -> 'unknown'."""
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        router.get("/health").mock(
            return_value=httpx.Response(
                200,
                json={"webhook_registered": True},
            )
        )
        result = await chatlytics_login(login_client)
    assert result["success"] is True
    assert result["sessions"] == "unknown"


@pytest.mark.asyncio
async def test_chatlytics_login_session_count_unknown_when_bool(
    login_client: ChatlyticsClient,
) -> None:
    """LOW-01 fix (10-REVIEW): bool ``sessions`` -> 'unknown', NOT coerced to 0/1.

    Python's ``bool`` is a subclass of ``int``; without the explicit
    bool exclusion the session-count branch would assign True/False to
    ``session_count`` directly. The MCP bundle's
    ``typeof === "number"`` excludes booleans, so this Python
    implementation must too.
    """
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        router.get("/health").mock(
            return_value=httpx.Response(
                200,
                json={"webhook_registered": True, "sessions": True},
            )
        )
        result = await chatlytics_login(login_client)
    assert result["success"] is True
    assert result["sessions"] == "unknown"


@pytest.mark.asyncio
async def test_chatlytics_login_passes_through_get_failure(
    login_client: ChatlyticsClient,
) -> None:
    """05-LOW-03: /health non-200 -> _get failure propagates unchanged."""
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        router.get("/health").mock(
            return_value=httpx.Response(503, text="upstream busy")
        )
        result = await chatlytics_login(login_client)
    assert result["success"] is False
    # The underlying _get path populates an error message; we don't pin
    # the exact wording here so that tools._get can evolve.
    assert "error" in result
