"""HERMES-01 acceptance tests for the chatlytics-hermes plugin contract.

These tests cover ROADMAP Phase 1 acceptance criteria 1, 2, 3, 5 in
pytest form.  Acceptance criterion 4 (clean-venv install with
hermes-agent installed) runs out-of-process via the dockerized smoke
documented in PLAN.md Task 3 and is exercised in HERMES-06 CI.

The tests deliberately avoid invoking ``adapter_factory`` so that the
suite runs without ``hermes-agent`` installed -- the import shim in
``chatlytics_hermes.adapter`` lets the bare-import smoke (criterion 1)
pass in any env.  HERMES-02 will add the first hermes-required
integration test once ``connect()`` is implemented.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any, Dict, List

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]


class MockCtx:
    """Minimal stand-in for the Hermes plugin registration context.

    HERMES-01 only exercises ``register_platform``; later phases extend
    this mock as additional ``ctx`` methods (``register_tool``,
    ``register_route``, etc.) become required.
    """

    def __init__(self) -> None:
        self.platforms: List[Dict[str, Any]] = []

    def register_platform(self, **kwargs: Any) -> None:
        self.platforms.append(kwargs)


def test_register_is_callable() -> None:
    """Acceptance criterion 1: the package exposes a callable ``register``."""
    from chatlytics_hermes import register

    assert callable(register)
    assert register.__name__ == "register"


def test_register_adds_chatlytics_platform() -> None:
    """Acceptance criterion 2 (the ROADMAP-named test)."""
    from chatlytics_hermes import register

    ctx = MockCtx()
    register(ctx)

    assert len(ctx.platforms) == 1, (
        "register() must call ctx.register_platform exactly once"
    )

    entry = ctx.platforms[0]
    assert entry["name"] == "chatlytics"
    assert entry["label"] == "Chatlytics WhatsApp"
    assert callable(entry["adapter_factory"])
    # v4.1.0: base_url is optional (DNS default) and the auth token is a
    # "one-of" (BOT_TOKEN or API_KEY) enforced at connect() time, so
    # required_env is empty. See adapter._auth_token guard.
    assert entry["required_env"] == []
    assert entry["install_hint"].startswith("pip install")
    assert "platform_hint" in entry
    hint = entry["platform_hint"]
    assert "Chatlytics" in hint or "WhatsApp" in hint


def test_register_declares_hermes_04_hooks() -> None:
    """HERMES-04 register() must declare the cron + env-enablement hooks.

    Originally written for HERMES-01 as a scope-discipline guard ("no
    deferred hooks present yet").  HERMES-04 now legitimately registers
    ``cron_deliver_env_var``, ``standalone_sender_fn``,
    ``env_enablement_fn``, and ``check_fn`` -- so this test flips
    polarity and asserts they ARE present.

    Hooks still owned by later phases (HERMES-05 tool surface) must
    remain absent.
    """
    from chatlytics_hermes import register

    ctx = MockCtx()
    register(ctx)
    entry = ctx.platforms[0]

    # HERMES-04 hooks MUST be present.
    assert entry.get("cron_deliver_env_var") == "CHATLYTICS_HOME_CHANNEL"
    assert callable(entry.get("standalone_sender_fn"))
    assert callable(entry.get("env_enablement_fn"))
    assert callable(entry.get("check_fn"))
    # check_fn should be safe to call and return True (deps are pre-imported).
    assert entry["check_fn"]() is True

    # Hooks owned by later phases (HERMES-05 / HERMES-06) MUST stay absent.
    deferred_hooks = {
        "apply_yaml_config_fn",
        "validate_config",
        "is_connected",
        "setup_fn",
        "allowed_users_env",
        "allow_all_env",
    }
    leaked = deferred_hooks & set(entry)
    assert not leaked, f"HERMES-04 leaked hooks owned by later phases: {leaked}"


def test_plugin_yaml_is_valid() -> None:
    """Acceptance criterion 3: ``plugin.yaml`` is valid YAML with the right shape.

    HERMES-V2 (Phase 336): in plugin v4.0 the required-env set changed —
    ``CHATLYTICS_BOT_TOKEN`` (per-bot bearer) is the new required field;
    ``CHATLYTICS_API_KEY`` moved to optional_env as a deprecated fallback.

    v4.1.2: ``CHATLYTICS_BASE_URL`` moved to optional_env (defaults to
    https://node.chatlytics.ai) — ``CHATLYTICS_BOT_TOKEN`` is now the SOLE
    required field (token-only onboarding).
    """
    manifest = yaml.safe_load((REPO_ROOT / "plugin.yaml").read_text(encoding="utf-8"))

    assert manifest["name"] == "chatlytics"
    assert manifest["kind"] == "platform"
    assert manifest["version"] == "4.1.5"

    required = {entry["name"] for entry in manifest["requires_env"]}
    assert required == {"CHATLYTICS_BOT_TOKEN"}

    # Optional env should include the webhook + cron knobs that later
    # phases consume.  Asserting the set keeps the manifest honest.
    # HERMES-V2: CHATLYTICS_API_KEY is now optional (deprecated fallback).
    # v4.1.0: CHATLYTICS_BASE_URL is now optional (DNS default).
    optional = {entry["name"] for entry in manifest["optional_env"]}
    assert {
        "CHATLYTICS_BASE_URL",
        "CHATLYTICS_API_KEY",
        "CHATLYTICS_ACCOUNT_ID",
        "CHATLYTICS_WEBHOOK_PORT",
        "CHATLYTICS_WEBHOOK_SECRET",
        "CHATLYTICS_HOME_CHANNEL",
    } <= optional

    # Bot token + api key + webhook secret must be marked as password fields so the
    # ``hermes config`` wizard masks them on entry.
    by_name = {entry["name"]: entry for entry in (
        manifest["requires_env"] + manifest["optional_env"]
    )}
    assert by_name["CHATLYTICS_BOT_TOKEN"]["password"] is True
    assert by_name["CHATLYTICS_API_KEY"]["password"] is True
    assert by_name["CHATLYTICS_WEBHOOK_SECRET"]["password"] is True


def test_pyproject_declares_hermes_entry_point() -> None:
    """Acceptance criterion 5 + dependency-pin sanity in test form."""
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    entry_points = data["project"]["entry-points"]["hermes_agent.plugins"]
    # v4.1: bare-module entry point (Hermes-compat fix #1 carried forward
    # from hpg6) — NOT the "chatlytics_hermes:register" colon form, which
    # made Hermes' PluginManager fail to load the plugin.
    assert entry_points["chatlytics"] == "chatlytics_hermes"

    project = data["project"]
    assert project["version"] == "4.1.5"  # v4.1.5 — telegram-style no-token onboarding prompt.
    assert project["name"] == "chatlytics-hermes"

    deps = project["dependencies"]
    hermes_dep = next(dep for dep in deps if dep.startswith("hermes-agent"))
    assert hermes_dep.startswith("hermes-agent>=0.14"), (
        "hermes-agent must be pinned to >=0.14"
    )
    # v4.1.0 pin-downgrade fix: the upper bound MUST NOT exclude the live
    # 0.15.x host. The old `<0.15` bound silently downgraded Hermes on
    # install — guard against any regression to a sub-1.0 ceiling.
    assert "<0.15" not in hermes_dep, (
        "hermes-agent upper bound must not be <0.15 (excludes the live "
        "0.15.x host and downgrades it on install)"
    )
    assert "<1.0" in hermes_dep
    assert any(dep.startswith("httpx>=0.27") for dep in deps)
    assert any(dep.startswith("aiohttp>=3.9") for dep in deps)
    assert all(not dep.startswith("flask") for dep in deps), (
        "flask must be removed; HERMES-03 uses aiohttp for the webhook server"
    )
