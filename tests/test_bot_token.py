"""HERMES-V2 (Phase 336) — bot_token precedence + back-compat tests.

Covers the auth-token resolution matrix introduced in chatlytics-hermes
v4.0.0 where ``CHATLYTICS_BOT_TOKEN`` (per-bot bearer, ``sk_bot_<43-char>``)
becomes the preferred credential and ``CHATLYTICS_API_KEY`` (legacy
operator/admin bearer) is retained as a one-minor-cycle fallback.

Precedence (highest first):
    1. ``CHATLYTICS_BOT_TOKEN`` env var
    2. ``extra.bot_token`` (PlatformConfig.extra block from config.yaml)
    3. ``CHATLYTICS_API_KEY`` env var (legacy)
    4. ``extra.api_key`` (legacy config.yaml fallback)

Failure mode: when none of (1)-(4) is set, ``ChatlyticsAdapter.connect()``
raises ``ChatlyticsConnectError`` with a clear message naming both env
vars. We deliberately do NOT raise at ``__init__`` time so registration-
phase env-loading races don't crash adapter instantiation in a Hermes
gateway that populates env asynchronously.

These five tests bring the suite to 125/125 (120 pre-existing + 5 new).
"""
from __future__ import annotations

import httpx
import pytest
import respx

from chatlytics_hermes.adapter import ChatlyticsAdapter, ChatlyticsConnectError
from tests._fixtures import FakePlatformConfig

BASE_URL = "https://gateway.test.chatlytics.ai"
BOT_TOKEN_ENV = "sk_bot_envenvenvenvenvenvenvenvenvenvenvenvenvenvenve"
BOT_TOKEN_EXTRA = "sk_bot_extraextraextraextraextraextraextraextraext"
API_KEY_ENV = "legacy-api-key-from-env"
API_KEY_EXTRA = "legacy-api-key-from-extra"


# Shared env teardown — pytest's monkeypatch fixture handles each test.
@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """Strip CHATLYTICS_* auth env vars so each test starts from a known slate."""
    for var in ("CHATLYTICS_BOT_TOKEN", "CHATLYTICS_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


# --- Test 1: env CHATLYTICS_BOT_TOKEN beats env CHATLYTICS_API_KEY -----

def test_bot_token_env_takes_precedence_over_api_key_env(
    clean_env: pytest.MonkeyPatch,
) -> None:
    """When both env vars are set, BOT_TOKEN wins."""
    clean_env.setenv("CHATLYTICS_BOT_TOKEN", BOT_TOKEN_ENV)
    clean_env.setenv("CHATLYTICS_API_KEY", API_KEY_ENV)

    adapter = ChatlyticsAdapter(
        FakePlatformConfig(extra={"base_url": BASE_URL})
    )

    assert adapter.bot_token == BOT_TOKEN_ENV
    assert adapter.api_key == API_KEY_ENV
    assert adapter._auth_token == BOT_TOKEN_ENV
    assert adapter.is_bot_token is True


# --- Test 2: extra.bot_token beats extra.api_key when env is empty -----

def test_bot_token_extra_overrides_api_key_extra(
    clean_env: pytest.MonkeyPatch,
) -> None:
    """Within config.yaml's extra block, bot_token beats api_key."""
    # No env vars set — fall through to extra block.
    adapter = ChatlyticsAdapter(
        FakePlatformConfig(
            extra={
                "base_url": BASE_URL,
                "bot_token": BOT_TOKEN_EXTRA,
                "api_key": API_KEY_EXTRA,
            }
        )
    )

    assert adapter.bot_token == BOT_TOKEN_EXTRA
    assert adapter.api_key == API_KEY_EXTRA
    assert adapter._auth_token == BOT_TOKEN_EXTRA
    assert adapter.is_bot_token is True


# --- Test 3: env beats extra for bot_token ------------------------------

def test_env_overrides_extra_for_bot_token(
    clean_env: pytest.MonkeyPatch,
) -> None:
    """CHATLYTICS_BOT_TOKEN env var wins even when extra.bot_token is also set."""
    clean_env.setenv("CHATLYTICS_BOT_TOKEN", BOT_TOKEN_ENV)

    adapter = ChatlyticsAdapter(
        FakePlatformConfig(
            extra={
                "base_url": BASE_URL,
                "bot_token": BOT_TOKEN_EXTRA,  # should be shadowed by env
            }
        )
    )

    assert adapter.bot_token == BOT_TOKEN_ENV
    # is_bot_token is True via the sk_bot_ prefix detection too.
    assert adapter.is_bot_token is True


# --- Test 4: api_key fallback when bot_token absent ---------------------

@pytest.mark.asyncio
async def test_api_key_fallback_when_no_bot_token(
    clean_env: pytest.MonkeyPatch,
) -> None:
    """Legacy CHATLYTICS_API_KEY-only deployments continue to work.

    Drives connect() through respx to confirm the Bearer header carries
    the legacy api_key when bot_token is unset — back-compat is the
    explicit one-minor-cycle promise of v4.0.
    """
    clean_env.setenv("CHATLYTICS_API_KEY", API_KEY_ENV)

    adapter = ChatlyticsAdapter(
        FakePlatformConfig(extra={"base_url": BASE_URL, "webhook_port": 0})
    )

    # Pre-connect assertions on resolution.
    assert adapter.bot_token == ""
    assert adapter.api_key == API_KEY_ENV
    assert adapter._auth_token == API_KEY_ENV
    assert adapter.is_bot_token is False

    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        health_route = router.get("/health").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        await adapter.connect()
        # Bearer header carries the legacy api_key — not bot_token.
        assert (
            health_route.calls.last.request.headers.get("authorization")
            == f"Bearer {API_KEY_ENV}"
        )
        await adapter.disconnect()


# --- Test 5: connect() loads degraded when neither token is present ----
# v4.1.5 (telegram-style onboarding) BEHAVIOR CHANGE: connect() previously
# RAISED ChatlyticsConnectError when no token was set. It now TOLERATES a
# missing token — loads in a degraded "no-credential" state (sets
# _no_credential, warns, returns True) so the platform registers and tools
# can prompt the user for a token on first use. The hard-fail contract is
# intentionally gone; data tools surface NO_TOKEN_PROMPT instead.

@pytest.mark.asyncio
async def test_connect_loads_degraded_when_neither_token_present(
    clean_env: pytest.MonkeyPatch,
) -> None:
    """No auth at all — adapter instantiates and connect() loads degraded."""
    # Both env vars cleared by the clean_env fixture; extra has no token.
    adapter = ChatlyticsAdapter(
        FakePlatformConfig(extra={"base_url": BASE_URL})
    )

    assert adapter._auth_token == ""
    assert adapter.is_bot_token is False
    assert adapter._no_credential is False  # not set until connect()

    # v4.1.5: connect() does NOT raise; it loads degraded.
    result = await adapter.connect()
    assert result is True
    assert adapter._no_credential is True
    assert adapter._client is None  # no authed client built
    assert adapter.is_connected is True  # loaded-but-degraded, no crash

    await adapter.disconnect()
