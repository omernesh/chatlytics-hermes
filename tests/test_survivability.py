"""v4.2.0 P3 plugin-survivability tests.

Covers the four survivability surfaces added in v4.2.0:

1. **No-downgrade guard** — ``diagnostics.check_hermes_agent_version`` flags
   a hermes-agent OLDER than the plugin floor (the v4.1.1 ``==0.14.0`` pin
   once downgraded production 0.15.1 → 0.14.0 via a plain pip install) and
   ``register()`` logs it at ERROR without breaking the load.
2. **base_url error mapping** — ``diagnostics.map_connect_error`` classifies
   the recurring misconfigurations (dead Tailscale-style IP → timeout, wrong
   host/port → refused, rotated token → 401, Cloudflare tunnel longpoll
   limit → 502) into actionable messages.
3. **Longpoll reconnect resilience** — bounded jittered backoff ladder
   (1→2→5→15→30s cap), state-change-only logging (one WARNING on
   healthy→degraded, one INFO on recovery), and the chatlytics v5.4
   graceful-shutdown signal (empty 200 + Connection: close) treated as a
   normal retry event. Healthy-path behavior (empty JSON batch loops
   immediately, no ack) is asserted unchanged by ``test_longpoll.py``.
4. **Doctor self-check + boot loud-failure** — ``chatlytics_hermes.doctor``
   PASS/FAIL checks against a respx-mocked gateway, and the unmissable
   ``CHATLYTICS PLUGIN FAILED TO LOAD:`` ERROR lines from ``register()`` /
   ``connect()``.

No network calls — gateway responses are scripted (FakeClient) or mocked
(respx); backoff sleeps are intercepted via a recording ``asyncio.sleep``.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Dict, List, Tuple

import httpx
import pytest
import respx

from chatlytics_hermes import diagnostics
from chatlytics_hermes import doctor as doctor_mod
from chatlytics_hermes.adapter import (
    _BACKOFF_JITTER_FRAC,
    _BACKOFF_LADDER,
    _LOAD_FAIL_PREFIX,
    ChatlyticsAdapter,
    ChatlyticsConnectError,
    _backoff_delay,
    register,
)
from chatlytics_hermes.diagnostics import (
    check_hermes_agent_version,
    extract_bot_name,
    map_connect_error,
    parse_version,
)
from tests._fixtures import FakePlatformConfig

BASE_URL = "https://gateway.test.chatlytics.ai"
BOT_TOKEN = "sk_bot_" + "A" * 43


# --- 1. No-downgrade guard ------------------------------------------------


def test_parse_version_shapes() -> None:
    assert parse_version("0.15.1") == (0, 15, 1)
    assert parse_version("0.14.0") == (0, 14, 0)
    assert parse_version("0.15.1rc1") == (0, 15, 1)
    assert parse_version("1.0") == (1, 0)
    assert parse_version("weird") == ()
    assert parse_version("") == ()


def test_version_guard_flags_downgrade() -> None:
    msg = check_hermes_agent_version("0.13.9")
    assert msg is not None
    # The message must carry the operator fix: --no-deps install discipline.
    assert "--no-deps" in msg
    assert "DOWNGRADED" in msg
    assert "0.13.9" in msg


def test_version_guard_passes_floor_and_above() -> None:
    assert check_hermes_agent_version("0.14.0") is None
    assert check_hermes_agent_version("0.14") is None
    assert check_hermes_agent_version("0.15.1") is None
    assert check_hermes_agent_version("1.0.0") is None


def test_version_guard_tolerates_unknown_version() -> None:
    # Unknown / undeterminable versions must NEVER flag (no false alarms
    # that scare an otherwise-working load).
    assert check_hermes_agent_version("") is None
    assert check_hermes_agent_version("garbage") is None


def test_version_guard_tolerates_missing_dist(monkeypatch) -> None:
    monkeypatch.setattr(
        diagnostics, "installed_hermes_agent_version", lambda: None
    )
    assert check_hermes_agent_version() is None


def test_register_logs_downgrade_error_but_still_registers(
    monkeypatch, caplog
) -> None:
    """A downgraded hermes-agent logs ERROR yet the platform still registers."""
    monkeypatch.setattr(
        diagnostics, "installed_hermes_agent_version", lambda: "0.13.0"
    )

    class MockCtx:
        def __init__(self) -> None:
            self.platforms: List[Dict[str, Any]] = []

        def register_platform(self, **kwargs: Any) -> None:
            self.platforms.append(kwargs)

    ctx = MockCtx()
    with caplog.at_level(logging.ERROR, logger="chatlytics_hermes.adapter"):
        register(ctx)

    assert len(ctx.platforms) == 1, "downgrade guard must not block the load"
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert any("DOWNGRADED" in r.getMessage() for r in errors)


# --- 2. base_url error mapping ---------------------------------------------


def test_map_timeout_points_at_dead_tailscale_ip() -> None:
    msg = map_connect_error(exc=httpx.ConnectTimeout("connect timed out after 30s"))
    assert "Tailscale" in msg
    assert "http://192.168.1.133:8050" in msg
    assert "https://node.chatlytics.ai" in msg


def test_map_refused_points_at_wrong_host_port() -> None:
    msg = map_connect_error(exc=httpx.ConnectError("[Errno 111] Connection refused"))
    assert "host/port" in msg


def test_map_reset_is_transient_restart() -> None:
    msg = map_connect_error(exc=httpx.ReadError("Connection reset by peer"))
    assert "restart" in msg.lower()


def test_map_401_points_at_token_rotation() -> None:
    msg = map_connect_error(status_code=401)
    assert "401" in msg
    assert "Rotate" in msg or "rotate" in msg
    assert "CHATLYTICS_BOT_TOKEN" in msg


def test_map_502_points_at_cloudflare_longpoll_limit() -> None:
    msg = map_connect_error(status_code=502)
    assert "Cloudflare" in msg
    assert "http://192.168.1.133:8050" in msg


def test_extract_bot_name_shapes() -> None:
    assert extract_bot_name({"name": "Sammie"}) == "Sammie"
    assert extract_bot_name({"bot": {"name": "BotDaddy"}}) == "BotDaddy"
    assert extract_bot_name({"bot_name": "Henry "}) == "Henry"
    assert extract_bot_name({"success": True}) is None
    assert extract_bot_name(None) is None
    assert extract_bot_name([1, 2]) is None


# --- 3. Longpoll reconnect resilience ---------------------------------------


def test_backoff_ladder_sequence_and_jitter_bounds() -> None:
    assert _BACKOFF_LADDER == (1.0, 2.0, 5.0, 15.0, 30.0)
    for attempt, base in enumerate(_BACKOFF_LADDER):
        for _ in range(200):
            delay = _backoff_delay(attempt)
            assert base <= delay <= base * (1.0 + _BACKOFF_JITTER_FRAC), (
                f"attempt {attempt}: delay {delay} outside "
                f"[{base}, {base * (1.0 + _BACKOFF_JITTER_FRAC)}]"
            )
    # Past the ladder end: capped at 30s base.
    for _ in range(200):
        delay = _backoff_delay(99)
        assert 30.0 <= delay <= 30.0 * (1.0 + _BACKOFF_JITTER_FRAC)


def _make_adapter() -> ChatlyticsAdapter:
    extra: Dict[str, Any] = {
        "base_url": BASE_URL,
        "bot_token": BOT_TOKEN,
        "inbound_mode": "longpoll",
    }
    return ChatlyticsAdapter(FakePlatformConfig(extra=extra))


class ScriptedClient:
    """Longpoll fake serving a scripted sequence of results per GET.

    Each script entry is one of:
      - ``("raise", exc)``           — raise the transport exception
      - ``("response", status, body)`` — JSON response
      - ``("empty200",)``            — empty-body 200 (graceful shutdown)
    When the script is exhausted, flips ``adapter._running`` False after
    serving a final empty JSON batch so the loop exits deterministically.
    """

    def __init__(self, adapter: ChatlyticsAdapter, script: List[Tuple[Any, ...]]) -> None:
        self._adapter = adapter
        self._script = list(script)
        self.get_count = 0
        self.post_calls: List[Dict[str, Any]] = []

    async def get(self, path: str, *, params: Dict[str, Any] | None = None, timeout: Any = None) -> httpx.Response:
        self.get_count += 1
        request = httpx.Request("GET", BASE_URL + path)
        if not self._script:
            self._adapter._running = False
            return httpx.Response(200, json={"envelopes": [], "cursor": ""}, request=request)
        step = self._script.pop(0)
        if not self._script:
            # Last scripted step: stop the loop after this iteration. Failure
            # steps sleep (intercepted) and re-enter the while-guard, so the
            # loop still exits without another GET.
            self._adapter._running = False
        if step[0] == "raise":
            raise step[1]
        if step[0] == "empty200":
            return httpx.Response(200, content=b"", request=request)
        _, status, body = step
        return httpx.Response(status, json=body, request=request)

    async def post(self, path: str, *, json: Dict[str, Any] | None = None, timeout: Any = None) -> httpx.Response:
        self.post_calls.append({"path": path, "json": json or {}})
        return httpx.Response(
            200, json={"acked": 1}, request=httpx.Request("POST", BASE_URL + path)
        )

    async def aclose(self) -> None:  # pragma: no cover — parity
        return None


@pytest.fixture()
def sleep_recorder(monkeypatch):
    """Intercept asyncio.sleep so backoff waits are recorded, not awaited."""
    recorded: List[float] = []
    real_sleep = asyncio.sleep

    async def _fake_sleep(delay: float, *args: Any, **kwargs: Any) -> None:
        recorded.append(delay)
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)
    return recorded


async def test_poll_loop_backoff_ladder_and_state_change_logging(
    sleep_recorder, caplog
) -> None:
    """3 consecutive transport errors → 1 WARNING + ladder backoff; recovery
    on the next 200 → 1 INFO + backoff reset."""
    adapter = _make_adapter()
    envelope = {
        "session_id": "3cf11776_logan",
        "chat_type": "dm",
        "entity_jid": "972544329000@c.us",
        "sender_jid": "972544329000@c.us",
        "text": "back online",
        "ts": 1,
    }
    fake = ScriptedClient(
        adapter,
        script=[
            ("raise", httpx.ConnectError("[Errno 111] Connection refused")),
            ("raise", httpx.ReadError("Connection reset by peer")),
            ("raise", httpx.ConnectTimeout("connect timed out")),
            ("response", 200, {"envelopes": [envelope], "cursor": "c1"}),
        ],
    )
    events: List[Any] = []

    async def _recorder(event: Any) -> None:
        events.append(event)

    adapter.handle_message = _recorder  # type: ignore[assignment]
    adapter._client = fake  # type: ignore[assignment]
    adapter._running = True

    with caplog.at_level(logging.DEBUG, logger="chatlytics_hermes.adapter"):
        await adapter._poll_loop()

    # Ladder: 1s, 2s, 5s bases (with bounded jitter), one per failure.
    assert len(sleep_recorder) == 3
    for delay, base in zip(sleep_recorder, _BACKOFF_LADDER):
        assert base <= delay <= base * (1.0 + _BACKOFF_JITTER_FRAC)

    # Exactly ONE healthy→degraded WARNING (not one per attempt) ...
    degraded_warnings = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "longpoll degraded" in r.getMessage()
    ]
    assert len(degraded_warnings) == 1
    # ... carrying the actionable hint for the FIRST symptom (refused).
    assert "host/port" in degraded_warnings[0].getMessage()

    # Exactly ONE degraded→healthy INFO on recovery.
    recoveries = [
        r
        for r in caplog.records
        if r.levelno == logging.INFO and "longpoll recovered" in r.getMessage()
    ]
    assert len(recoveries) == 1

    # The healthy path then dispatched + acked the envelope as before.
    assert len(events) == 1
    assert events[0].text == "back online"
    assert fake.post_calls and fake.post_calls[0]["json"]["cursor"] == "c1"


async def test_poll_loop_treats_empty_200_as_graceful_shutdown(
    sleep_recorder, caplog
) -> None:
    """chatlytics v5.4 restart signal (empty 200 + Connection: close) is a
    normal retry event: backoff + reconnect, no dispatch, no crash."""
    adapter = _make_adapter()
    envelope = {
        "session_id": "3cf11776_logan",
        "chat_type": "dm",
        "entity_jid": "972544329000@c.us",
        "sender_jid": "972544329000@c.us",
        "text": "after restart",
        "ts": 2,
    }
    fake = ScriptedClient(
        adapter,
        script=[
            ("empty200",),
            ("response", 200, {"envelopes": [envelope], "cursor": "c2"}),
        ],
    )
    events: List[Any] = []

    async def _recorder(event: Any) -> None:
        events.append(event)

    adapter.handle_message = _recorder  # type: ignore[assignment]
    adapter._client = fake  # type: ignore[assignment]
    adapter._running = True

    with caplog.at_level(logging.DEBUG, logger="chatlytics_hermes.adapter"):
        await adapter._poll_loop()

    # One backoff sleep at the ladder base.
    assert len(sleep_recorder) == 1
    assert _BACKOFF_LADDER[0] <= sleep_recorder[0] <= _BACKOFF_LADDER[0] * (
        1.0 + _BACKOFF_JITTER_FRAC
    )
    shutdown_logs = [
        r
        for r in caplog.records
        if "longpoll degraded" in r.getMessage()
        and "graceful shutdown" in r.getMessage()
    ]
    assert len(shutdown_logs) == 1
    # Loop survived the restart window and dispatched the next batch.
    assert len(events) == 1
    assert events[0].text == "after restart"


async def test_poll_loop_empty_json_batch_stays_immediate(sleep_recorder) -> None:
    """Healthy-path preservation: a normal empty JSON batch must loop again
    WITHOUT backoff (no sleep) and without acking — exactly the pre-v4.2.0
    behavior."""
    adapter = _make_adapter()
    fake = ScriptedClient(
        adapter,
        script=[("response", 200, {"envelopes": [], "cursor": "same"})],
    )
    adapter._client = fake  # type: ignore[assignment]
    adapter._running = True

    await adapter._poll_loop()

    assert sleep_recorder == [], "empty JSON batch must not back off"
    assert fake.post_calls == [], "empty batch must not ack"


async def test_poll_loop_401_logs_error_once_and_backs_off_at_cap(
    sleep_recorder, caplog
) -> None:
    adapter = _make_adapter()
    fake = ScriptedClient(
        adapter,
        script=[
            ("response", 401, {"error": "bot_token_required"}),
            ("response", 401, {"error": "bot_token_required"}),
        ],
    )
    adapter._client = fake  # type: ignore[assignment]
    adapter._running = True

    with caplog.at_level(logging.DEBUG, logger="chatlytics_hermes.adapter"):
        await adapter._poll_loop()

    # Both waits at the 30s cap (token problems don't self-heal fast).
    assert len(sleep_recorder) == 2
    for delay in sleep_recorder:
        assert delay >= _BACKOFF_LADDER[-1]
    # ONE ERROR on the state change, not one per attempt.
    errors = [
        r
        for r in caplog.records
        if r.levelno == logging.ERROR and "longpoll degraded" in r.getMessage()
    ]
    assert len(errors) == 1
    assert "CHATLYTICS_BOT_TOKEN" in errors[0].getMessage()


# --- 4. Boot loud-failure + doctor ------------------------------------------


def _adapter_with_token() -> ChatlyticsAdapter:
    return ChatlyticsAdapter(
        FakePlatformConfig(extra={"base_url": BASE_URL, "bot_token": BOT_TOKEN})
    )


async def test_connect_logs_identity_info_line(caplog) -> None:
    adapter = _adapter_with_token()
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        router.get("/health").mock(return_value=httpx.Response(200, json={}))
        router.get("/api/v1/bot/me").mock(
            return_value=httpx.Response(200, json={"bot": {"name": "Sammie"}})
        )
        with caplog.at_level(logging.INFO, logger="chatlytics_hermes.adapter"):
            assert await adapter.connect() is True
        await adapter.disconnect()

    identity = [
        r
        for r in caplog.records
        if "registered, authenticated as Sammie" in r.getMessage()
    ]
    assert len(identity) == 1


async def test_connect_identity_probe_401_logs_unmissable_error(caplog) -> None:
    adapter = _adapter_with_token()
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        router.get("/health").mock(return_value=httpx.Response(200, json={}))
        router.get("/api/v1/bot/me").mock(
            return_value=httpx.Response(401, json={"error": "bot_token_required"})
        )
        with caplog.at_level(logging.ERROR, logger="chatlytics_hermes.adapter"):
            # Probe failure must NOT fail connect() (control flow unchanged).
            assert await adapter.connect() is True
        await adapter.disconnect()

    errors = [r for r in caplog.records if _LOAD_FAIL_PREFIX in r.getMessage()]
    assert len(errors) == 1
    assert "rotate" in errors[0].getMessage().lower()


async def test_connect_identity_probe_failure_is_silent_besteffort(caplog) -> None:
    """A gateway without /api/v1/bot/me (legacy server) keeps the exact
    pre-v4.2.0 connect() behavior — no ERROR, connect succeeds."""
    adapter = _adapter_with_token()
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        router.get("/health").mock(return_value=httpx.Response(200, json={}))
        router.get("/api/v1/bot/me").mock(
            return_value=httpx.Response(404, json={"error": "not_found"})
        )
        with caplog.at_level(logging.ERROR, logger="chatlytics_hermes.adapter"):
            assert await adapter.connect() is True
        await adapter.disconnect()

    assert not [r for r in caplog.records if _LOAD_FAIL_PREFIX in r.getMessage()]


async def test_connect_health_failure_logs_unmissable_error_with_hint(caplog) -> None:
    adapter = _adapter_with_token()
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        router.get("/health").mock(
            side_effect=httpx.ConnectTimeout("connect timed out after 30s")
        )
        with caplog.at_level(logging.ERROR, logger="chatlytics_hermes.adapter"):
            with pytest.raises(ChatlyticsConnectError):
                await adapter.connect()

    errors = [r for r in caplog.records if _LOAD_FAIL_PREFIX in r.getMessage()]
    assert len(errors) == 1
    assert "Tailscale" in errors[0].getMessage()


async def test_connect_no_token_logs_unmissable_error(caplog) -> None:
    adapter = ChatlyticsAdapter(FakePlatformConfig(extra={"base_url": BASE_URL}))
    with caplog.at_level(logging.WARNING, logger="chatlytics_hermes.adapter"):
        assert await adapter.connect() is True
    await adapter.disconnect()

    errors = [r for r in caplog.records if _LOAD_FAIL_PREFIX in r.getMessage()]
    assert len(errors) == 1
    assert "CHATLYTICS_BOT_TOKEN" in errors[0].getMessage()


def test_register_failure_logs_unmissable_error_and_reraises(caplog) -> None:
    class BoomCtx:
        def register_platform(self, **kwargs: Any) -> None:
            raise RuntimeError("boom: registry exploded")

    with caplog.at_level(logging.ERROR, logger="chatlytics_hermes.adapter"):
        with pytest.raises(RuntimeError, match="registry exploded"):
            register(BoomCtx())

    errors = [r for r in caplog.records if _LOAD_FAIL_PREFIX in r.getMessage()]
    assert len(errors) == 1
    assert "2 platforms" in errors[0].getMessage()
    assert "doctor" in errors[0].getMessage()


def test_register_success_logs_confirmation_info(caplog) -> None:
    class MockCtx:
        def register_platform(self, **kwargs: Any) -> None:
            pass

    with caplog.at_level(logging.INFO, logger="chatlytics_hermes.adapter"):
        register(MockCtx())

    confirmations = [
        r for r in caplog.records if "chatlytics plugin registered" in r.getMessage()
    ]
    assert len(confirmations) == 1


# --- Doctor ------------------------------------------------------------------


def _plugin_dir(tmp_path: Path) -> Path:
    d = tmp_path / "plugins" / "chatlytics"
    d.mkdir(parents=True)
    (d / "__init__.py").write_text("# shim\n", encoding="utf-8")
    return d


def test_doctor_all_pass(tmp_path) -> None:
    lines: List[str] = []
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        router.get("/health").mock(return_value=httpx.Response(200, json={}))
        router.get("/api/v1/bot/me").mock(
            return_value=httpx.Response(200, json={"name": "Sammie"})
        )
        router.get("/api/v1/bot/updates").mock(
            return_value=httpx.Response(200, json={"envelopes": [], "cursor": ""})
        )
        code = doctor_mod.doctor(
            base_url=BASE_URL,
            token=BOT_TOKEN,
            plugin_dir=_plugin_dir(tmp_path),
            print_fn=lines.append,
        )

    assert code == 0, "\n".join(lines)
    report = "\n".join(lines)
    assert "authenticated as Sammie" in report
    assert "FAIL" not in report
    assert "all checks passed" in report


def test_doctor_401_token_fails_with_rotate_hint(tmp_path) -> None:
    lines: List[str] = []
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        router.get("/health").mock(return_value=httpx.Response(200, json={}))
        router.get("/api/v1/bot/me").mock(
            return_value=httpx.Response(401, json={"error": "bot_token_required"})
        )
        router.get("/api/v1/bot/updates").mock(
            return_value=httpx.Response(401, json={"error": "bot_token_required"})
        )
        code = doctor_mod.doctor(
            base_url=BASE_URL,
            token=BOT_TOKEN,
            plugin_dir=_plugin_dir(tmp_path),
            print_fn=lines.append,
        )

    assert code == 1
    report = "\n".join(lines)
    assert "FAIL — bot-me" in report
    assert "rotate" in report.lower()


def test_doctor_unreachable_base_url_maps_symptom(tmp_path) -> None:
    lines: List[str] = []
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        router.get("/health").mock(
            side_effect=httpx.ConnectError("[Errno 111] Connection refused")
        )
        router.get("/api/v1/bot/me").mock(
            side_effect=httpx.ConnectError("[Errno 111] Connection refused")
        )
        router.get("/api/v1/bot/updates").mock(
            side_effect=httpx.ConnectError("[Errno 111] Connection refused")
        )
        code = doctor_mod.doctor(
            base_url=BASE_URL,
            token=BOT_TOKEN,
            plugin_dir=_plugin_dir(tmp_path),
            print_fn=lines.append,
        )

    assert code == 1
    report = "\n".join(lines)
    assert "FAIL — health" in report
    assert "host/port" in report


def test_doctor_no_token_fails_config_and_skips_authed_checks(tmp_path) -> None:
    lines: List[str] = []
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        router.get("/health").mock(return_value=httpx.Response(200, json={}))
        code = doctor_mod.doctor(
            base_url=BASE_URL,
            token="",
            plugin_dir=_plugin_dir(tmp_path),
            print_fn=lines.append,
        )

    assert code == 1
    report = "\n".join(lines)
    assert "FAIL — config" in report
    assert "CHATLYTICS_BOT_TOKEN" in report
    assert "FAIL — bot-me: skipped" in report
    assert "FAIL — longpoll: skipped" in report
    # Unauthed reachability check still ran.
    assert "PASS — health" in report


def test_doctor_missing_plugin_dir_fails_without_pip_fallback(
    tmp_path, monkeypatch
) -> None:
    # Force the pip fallback to miss so the check is deterministic even in
    # venvs where chatlytics-hermes happens to be pip-installed.
    import importlib.metadata as _ilm

    def _no_dist(name: str) -> str:
        raise _ilm.PackageNotFoundError(name)

    monkeypatch.setattr(_ilm, "version", _no_dist)

    ok, message = doctor_mod.check_plugin_dir(tmp_path / "nope" / "chatlytics")
    assert ok is False
    assert "hermes plugins install" in message


def test_doctor_longpoll_404_flags_old_server(tmp_path) -> None:
    lines: List[str] = []
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        router.get("/health").mock(return_value=httpx.Response(200, json={}))
        router.get("/api/v1/bot/me").mock(
            return_value=httpx.Response(200, json={"name": "Sammie"})
        )
        router.get("/api/v1/bot/updates").mock(
            return_value=httpx.Response(404, json={"error": "not_found"})
        )
        code = doctor_mod.doctor(
            base_url=BASE_URL,
            token=BOT_TOKEN,
            plugin_dir=_plugin_dir(tmp_path),
            print_fn=lines.append,
        )

    assert code == 1
    report = "\n".join(lines)
    assert "FAIL — longpoll" in report
    assert "predates" in report
