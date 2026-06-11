"""Chatlytics Hermes plugin -- v0.14 first-class platform plugin.

Entry point (registered via ``pyproject.toml``
``[project.entry-points."hermes_agent.plugins"]``)::

    chatlytics = "chatlytics_hermes:register"

Hermes discovers this plugin via
``importlib.metadata.entry_points(group="hermes_agent.plugins")`` in
``hermes_cli/plugins.py`` and invokes ``register(ctx)`` with a fresh
``PluginContext`` per the v0.14 contract
(``PluginManager._load_plugin`` -> ``register_fn(ctx)``).

The ``register`` function (defined in ``adapter.py``) is the plugin's
sole entry point. It:

1. Registers the ``chatlytics`` platform via
   ``ctx.register_platform(...)`` with the canonical ``PlatformEntry``
   fields (``name``, ``label``, ``adapter_factory``, ``check_fn``,
   ``required_env``, ``env_enablement_fn``, ``cron_deliver_env_var``,
   ``standalone_sender_fn``, ``emoji``, ``install_hint``,
   ``platform_hint``).
2. Iterates the locked-21 tool surface from
   ``chatlytics_hermes.tools.TOOLS`` and calls
   ``ctx.register_tool(name=, toolset="chatlytics", schema=, handler=)``
   for each, wrapping each handler via ``_make_tool_handler`` so the
   live adapter (and its authenticated ``ChatlyticsClient``) is
   injected at call time.

The live-loader contract is verified by ``tests/test_live_loader.py``,
which drives a ``PluginContext``-compatible recorder through
``register(ctx)`` end-to-end and asserts the platform + 21 tools land
correctly. That same file holds the strict-xfail regression tests for
BL-01, HI-01, HI-03 surfaced by the v2.0 milestone-wide review
(``.planning/v2.0-MILESTONE-CODE-REVIEW.md``); Phase 8 fixes them and
un-xfails the markers.
"""
from .adapter import register

__version__ = "4.4.0"

__all__ = ["register", "__version__"]
