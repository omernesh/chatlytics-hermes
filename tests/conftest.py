"""Shared pytest fixtures for chatlytics-hermes tests.

The HERMES-02 test suite instantiates ``ChatlyticsAdapter`` directly,
which calls ``Platform("chatlytics")`` in ``BasePlatformAdapter.__init__``.
The v0.14.0 ``Platform`` enum accepts unknown values via ``_missing_()``
only when the name is either (a) a bundled plugin under
``plugins/platforms/`` (we are not bundled) or (b) registered in
``gateway.platform_registry.platform_registry``.

We seed the registry once per test session so all ``Platform("chatlytics")``
lookups succeed. HERMES-11 adds idempotent teardown so the registration
does not leak when the suite is embedded in a larger cross-plugin
pytest run (closes 02-MED-02 / IN-03).
"""

from __future__ import annotations

import pytest

# Env vars the adapter resolves via os.getenv() — see ChatlyticsAdapter
# _auth_token precedence and _standalone_send.
_CHATLYTICS_ENV_VARS = (
    "CHATLYTICS_BOT_TOKEN",
    "CHATLYTICS_API_KEY",
    "CHATLYTICS_BASE_URL",
    "CHATLYTICS_HOME_CHANNEL",
    "CHATLYTICS_ACCOUNT_ID",
    "CHATLYTICS_SESSION",
    "CHATLYTICS_WEBHOOK_PORT",
    "CHATLYTICS_WEBHOOK_SECRET",
)


@pytest.fixture(autouse=True)
def _isolate_chatlytics_env(monkeypatch):
    """Hermetic env: clear ambient ``CHATLYTICS_*`` before every test.

    Closes review LOW-01 (test env isolation). Without this, a developer
    running the suite with a real operator credential exported for the live
    MCP (``CHATLYTICS_API_KEY`` / ``CHATLYTICS_BOT_TOKEN``) leaks it into the
    adapter's ``os.getenv``-based ``_auth_token`` resolution — shadowing a
    fixture's ``extra.api_key`` and failing the Bearer-header assertions in
    test_outbound / test_media with the live key instead of the test key.
    Tests that need a credential ``monkeypatch.setenv`` it themselves after
    this autouse fixture has run.
    """
    for var in _CHATLYTICS_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(scope="session", autouse=True)
def _register_chatlytics_platform():
    """Register the chatlytics platform in gateway.platform_registry.

    Required so ``Platform("chatlytics")`` resolves to a pseudo-member
    inside ``BasePlatformAdapter.__init__``. Hermes-side this is done
    by ``PluginContext.register_platform``; we replicate the minimum
    surface here for the unit-test environment.

    Teardown unregisters the entry **only if this fixture registered
    it** (idempotency guard). When embedded in a larger run where
    another fixture already registered chatlytics, leave the existing
    registration untouched.
    """
    try:
        from gateway.platform_registry import platform_registry, PlatformEntry
    except ImportError:
        # hermes-agent not installed -- tests that need it will fail
        # with a clearer error than ImportError on import of the
        # fixture itself.
        yield
        return

    # Idempotency guard: if chatlytics is already registered (embedded
    # run, prior session leak), leave the existing entry alone and
    # skip teardown so we don't yank state another consumer owns.
    if platform_registry.is_registered("chatlytics"):
        yield
        return

    entry = PlatformEntry(
        name="chatlytics",
        label="Chatlytics WhatsApp",
        adapter_factory=lambda cfg: None,  # tests instantiate the adapter directly
        check_fn=lambda: True,
        required_env=["CHATLYTICS_BASE_URL", "CHATLYTICS_API_KEY"],
        install_hint="pip install chatlytics-hermes",
        source="plugin",
    )
    platform_registry.register(entry)
    try:
        yield
    finally:
        try:
            platform_registry.unregister("chatlytics")
        except Exception:
            # Cleanup must never fail the test session. Swallow
            # whatever the registry raises (missing entry, repeated
            # teardown, etc.) and move on.
            pass
