"""Meta-tests verifying the conftest session-fixture teardown contract.

HERMES-11 / 02-MED-02 / IN-03 fix verification. The session-autouse
``_register_chatlytics_platform`` fixture in ``tests/conftest.py``:

1. Registers the chatlytics platform entry on session start (so
   ``Platform("chatlytics")`` resolves inside ``BasePlatformAdapter.__init__``).
2. Skips registration when the entry is already present (idempotency
   guard for embedded cross-plugin pytest runs).
3. Unregisters on session teardown IFF it registered the entry itself.

These tests do not directly drive the teardown half (that requires
spawning a subprocess pytest invocation, which is out of scope). They
DO verify the registration is active during a normal run AND that the
entry shape matches what conftest seeds -- if either drifts, the
session-fixture teardown promise is structurally broken.
"""

from __future__ import annotations

import pytest


def test_chatlytics_platform_is_registered_during_session() -> None:
    """During any normal pytest run, chatlytics is in the registry."""
    try:
        from gateway.platform_registry import platform_registry
    except ImportError:
        pytest.skip("hermes-agent not installed")

    assert platform_registry.is_registered("chatlytics"), (
        "conftest session-autouse fixture should have registered chatlytics"
    )


def test_registry_entry_has_expected_shape() -> None:
    """Sanity-check the registered entry matches what conftest seeds."""
    try:
        from gateway.platform_registry import platform_registry
    except ImportError:
        pytest.skip("hermes-agent not installed")

    entry = platform_registry.get("chatlytics")
    assert entry is not None, "chatlytics entry missing despite is_registered=True"
    assert entry.name == "chatlytics"
    assert entry.label == "Chatlytics WhatsApp"
    assert entry.source == "plugin"
    assert "CHATLYTICS_BASE_URL" in entry.required_env
    assert "CHATLYTICS_API_KEY" in entry.required_env
    # adapter_factory is a no-op lambda in the test seed; tests
    # instantiate the adapter directly. Just confirm it's callable.
    assert callable(entry.adapter_factory)
    assert callable(entry.check_fn)
    assert entry.check_fn() is True
