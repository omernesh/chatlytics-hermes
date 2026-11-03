"""The host-import guard must heal when the adapter is created.

Each scenario runs in its own interpreter. It reloads the plugin modules, and the rest of the
suite holds references to the originals, so isolating it in-process would be fragile.

The obstruction is a stand-in for ``gateway.platforms.base`` flagged as initializing, the state
another thread's unfinished import leaves in ``sys.modules``. CPython itself then produces the
"partially initialized module" ImportError, so the guard sees the genuine exception.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

SRC_DIR = Path(__file__).resolve().parents[1] / "src"

PRELUDE = r"""
import asyncio, importlib, importlib.machinery, json, logging, sys, threading, types

BASE = "gateway.platforms.base"
CFG = types.SimpleNamespace(
    extra={"base_url": "https://gateway.test", "api_key": "test-key", "webhook_port": 0},
    enabled=True, name="chatlytics", token=None, api_key="test-key", home_channel=None,
)


def obstruct():
    partial = types.ModuleType(BASE)
    partial.__spec__ = importlib.machinery.ModuleSpec(BASE, None)
    partial.__spec__._initializing = True
    sys.modules[BASE] = partial


def clear_obstruction():
    del sys.modules[BASE]


class Records(logging.Handler):
    def __init__(self):
        super().__init__()
        self.seen = []

    def emit(self, record):
        self.seen.append(record)

    def warnings(self):
        return [
            r.getMessage() for r in self.seen
            if r.levelno == logging.WARNING and r.name == "chatlytics_hermes.adapter"
        ]


records = Records()
logging.getLogger("chatlytics_hermes").addHandler(records)


class Recorder:
    def __init__(self):
        self.platform = None
        self.tools = {}

    def register_platform(self, **kwargs):
        self.platform = kwargs

    def register_tool(self, *, name, handler, **_):
        self.tools[name] = handler


def register_host_platform():
    # What the host's PluginContext does; Platform("chatlytics") needs it. Importing the
    # registry waits for the obstruction to go, like the host finishing its own imports.
    from gateway.platform_registry import PlatformEntry, platform_registry

    platform_registry.register(PlatformEntry(
        name="chatlytics", label="Chatlytics WhatsApp", adapter_factory=lambda cfg: None,
        check_fn=lambda: True, required_env=[], install_hint="", source="plugin",
    ))


reloads = []
real_reload = importlib.reload


def counting_reload(module):
    reloads.append(module.__name__)
    return real_reload(module)


importlib.reload = counting_reload


def expect_runtime_error(call):
    try:
        call()
    except RuntimeError as exc:
        return exc
    raise AssertionError("expected RuntimeError")


def import_degraded():
    obstruct()
    from chatlytics_hermes import adapter, inbound

    assert adapter._HERMES_AVAILABLE is False
    assert inbound._HERMES_AVAILABLE is False
    assert adapter.BasePlatformAdapter is object
    return adapter, inbound


def assert_partial_import_error(exc):
    text = str(exc)
    assert "hermes-agent>=0.14 must be installed" in text, text
    assert f"partially initialized module '{BASE}'" in text, text
    assert "circular import" in text, text
    assert "different problem from a missing install" in text, text
    assert isinstance(exc.__cause__, ImportError), repr(exc.__cause__)


def assert_healed(adapter_mod, inbound_mod, built):
    from gateway.platforms.base import BasePlatformAdapter, MessageEvent

    assert isinstance(built, BasePlatformAdapter), type(built).__mro__
    assert type(built) is adapter_mod.ChatlyticsAdapter
    assert adapter_mod._HERMES_AVAILABLE is True
    assert adapter_mod._HERMES_IMPORT_ERROR is None
    assert inbound_mod._HERMES_AVAILABLE is True
    assert inbound_mod._HERMES_IMPORT_ERROR is None
    # The name adapter.py imported from inbound, which the running adapter calls.
    event = adapter_mod.normalize_payload(
        {"chatId": "123@g.us", "text": "hi", "senderId": "1@c.us", "messageId": "m1"},
        built.platform,
    )
    assert isinstance(event, MessageEvent)
    assert event.text == "hi"
"""


def _run(body: str, tmp_path: Path) -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith("CHATLYTICS_")}
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(SRC_DIR), env.get("PYTHONPATH")]))
    env["PYTHONIOENCODING"] = "utf-8"
    done = subprocess.run(
        [sys.executable, "-c", PRELUDE + body],
        capture_output=True, encoding="utf-8", errors="replace", timeout=180, env=env, cwd=tmp_path,
    )
    assert done.returncode == 0, f"scenario failed\n--- stdout\n{done.stdout}\n--- stderr\n{done.stderr}"
    assert "SCENARIO OK" in done.stdout


@pytest.fixture
def real_host() -> None:
    pytest.importorskip("gateway.platform_registry")


def test_degraded_load_registers_then_the_factory_heals(real_host, tmp_path) -> None:
    _run(
        r"""
adapter, inbound = import_degraded()

warned = records.warnings()
assert len(warned) == 1, warned
assert f"partially initialized module '{BASE}'" in warned[0], warned
assert "retried when the adapter is created" in warned[0], warned

ctx = Recorder()
adapter.register(ctx)
assert ctx.platform["name"] == "chatlytics"
assert len(ctx.tools) == 24

assert_partial_import_error(expect_runtime_error(lambda: adapter.ChatlyticsAdapter(CFG)))

clear_obstruction()
register_host_platform()
built = ctx.platform["adapter_factory"](CFG)
assert_healed(adapter, inbound, built)
assert reloads == ["chatlytics_hermes.inbound", "chatlytics_hermes.adapter"], reloads

# Direct construction works too now, and a later factory call does not reload again.
assert isinstance(adapter.ChatlyticsAdapter(CFG), adapter.BasePlatformAdapter)
ctx.platform["adapter_factory"](CFG)
assert len(reloads) == 2, reloads
print("SCENARIO OK")
""",
        tmp_path,
    )


def test_closures_registered_while_degraded_see_the_healed_module(real_host, tmp_path) -> None:
    _run(
        r"""
adapter, inbound = import_degraded()
from chatlytics_hermes import tools

ctx = Recorder()
adapter.register(ctx)
health = ctx.tools["chatlytics_health"]
old_lookup_error = adapter.ChatlyticsLookupError
assert "not connected" in json.loads(asyncio.run(health({})))["error"]

clear_obstruction()
register_host_platform()
built = ctx.platform["adapter_factory"](CFG)


async def gateway_says_ok(client, path):
    return {"success": True}


# The handler the host has held since before the heal finds the healed adapter.
tools._get = gateway_says_ok
built._client = object()
adapter._register_live_adapter(built)
result = json.loads(asyncio.run(health({})))
assert result["success"] is True, result
assert "inbound" in result, result


# The reload redefined the class. Raised the way the adapter raises it, it must still match the
# `except ChatlyticsLookupError` in tools.py.
assert adapter.ChatlyticsLookupError is not old_lookup_error


async def lookup_fails(chat_id):
    raise adapter.ChatlyticsLookupError("auth_error", "denied")


built.get_chat_info = lookup_fails
result = asyncio.run(tools.chatlytics_get_chat_info(None, adapter=built, chatId="123@g.us"))
assert result == {"success": False, "error": "denied", "_error": "auth_error"}, result
print("SCENARIO OK")
""",
        tmp_path,
    )


def test_factory_reports_the_cause_until_the_obstruction_clears(real_host, tmp_path) -> None:
    _run(
        r"""
adapter, inbound = import_degraded()
ctx = Recorder()
adapter.register(ctx)
factory = ctx.platform["adapter_factory"]

assert_partial_import_error(expect_runtime_error(lambda: factory(CFG)))
assert_partial_import_error(expect_runtime_error(lambda: factory(CFG)))
assert adapter._HERMES_AVAILABLE is False

clear_obstruction()
register_host_platform()
assert_healed(adapter, inbound, factory(CFG))
print("SCENARIO OK")
""",
        tmp_path,
    )


def test_concurrent_factory_calls_all_succeed_and_reload_once(real_host, tmp_path) -> None:
    _run(
        r"""
adapter, inbound = import_degraded()
ctx = Recorder()
adapter.register(ctx)
factory = ctx.platform["adapter_factory"]

clear_obstruction()
register_host_platform()

THREADS = 8
barrier = threading.Barrier(THREADS)
built, failed = [], []


def worker():
    barrier.wait()
    try:
        built.append(factory(CFG))
    except BaseException as exc:
        failed.append(repr(exc))


threads = [threading.Thread(target=worker) for _ in range(THREADS)]
for t in threads:
    t.start()
for t in threads:
    t.join(120)

assert not failed, failed
assert len(built) == THREADS
for one in built:
    assert_healed(adapter, inbound, one)
assert reloads == ["chatlytics_hermes.inbound", "chatlytics_hermes.adapter"], reloads
print("SCENARIO OK")
""",
        tmp_path,
    )


def test_inbound_tripped_alone_heals_without_replacing_the_adapter_class(real_host, tmp_path) -> None:
    # adapter.py imports inbound long before its own guard runs, so inbound can see the partial
    # module while the adapter's guard passes. normalize_payload would then refuse every message.
    _run(
        r"""


class ClearAfterInbound:
    # adapter.py imports .stream right after .inbound, so the host finishes its import here.
    def find_spec(self, name, path=None, target=None):
        if name == "chatlytics_hermes.stream":
            sys.meta_path.remove(self)
            clear_obstruction()
        return None


obstruct()
sys.meta_path.insert(0, ClearAfterInbound())
from chatlytics_hermes import adapter, inbound

assert inbound._HERMES_AVAILABLE is False
assert adapter._HERMES_AVAILABLE is True
warned = records.warnings()
assert len(warned) == 1 and "retried when the adapter is created" in warned[0], warned

ctx = Recorder()
adapter.register(ctx)
register_host_platform()
class_before = adapter.ChatlyticsAdapter
built = ctx.platform["adapter_factory"](CFG)
assert_healed(adapter, inbound, built)
assert adapter.ChatlyticsAdapter is class_before
assert reloads == ["chatlytics_hermes.inbound"], reloads
print("SCENARIO OK")
""",
        tmp_path,
    )


def test_a_reload_that_fails_stays_degraded_and_a_later_call_heals(real_host, tmp_path) -> None:
    _run(
        r"""
adapter, inbound = import_degraded()
ctx = Recorder()
adapter.register(ctx)
factory = ctx.platform["adapter_factory"]

clear_obstruction()
register_host_platform()


def stops_halfway(module):
    # Past the guard and short of the class: the flag says True, the class is still the old one.
    if module.__name__ == adapter.__name__:
        module._HERMES_AVAILABLE = True
        raise ImportError("reload stopped halfway")
    return real_reload(module)


importlib.reload = stops_halfway
exc = expect_runtime_error(lambda: factory(CFG))
assert "reload stopped halfway" in str(exc), str(exc)
assert isinstance(exc.__cause__, ImportError), repr(exc.__cause__)
assert adapter._HERMES_AVAILABLE is False

importlib.reload = real_reload
assert_healed(adapter, inbound, factory(CFG))
print("SCENARIO OK")
""",
        tmp_path,
    )


def test_hermes_truly_absent_stays_quiet_and_factory_still_raises(tmp_path) -> None:
    _run(
        r"""


class NoHost:
    def find_spec(self, name, path=None, target=None):
        if name == "gateway":
            raise ModuleNotFoundError(f"No module named {name!r}", name=name)
        return None


sys.meta_path.insert(0, NoHost())
from chatlytics_hermes import adapter, inbound

assert adapter._HERMES_AVAILABLE is False
assert inbound._HERMES_AVAILABLE is False
assert isinstance(adapter._HERMES_IMPORT_ERROR, ModuleNotFoundError)
assert adapter._HERMES_IMPORT_ERROR.name == "gateway"
assert records.warnings() == [], records.warnings()

ctx = Recorder()
adapter.register(ctx)
assert ctx.platform["name"] == "chatlytics"

exc = expect_runtime_error(lambda: ctx.platform["adapter_factory"](CFG))
assert "hermes-agent>=0.14 must be installed" in str(exc), str(exc)
assert isinstance(exc.__cause__, ModuleNotFoundError), repr(exc.__cause__)
assert reloads == ["chatlytics_hermes.inbound"], reloads  # one attempt, per factory call
assert records.warnings() == [], records.warnings()
print("SCENARIO OK")
""",
        tmp_path,
    )
