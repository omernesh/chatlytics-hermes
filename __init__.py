"""Hermes directory-plugin entry shim for chatlytics.

Makes the repository root loadable as a Hermes *directory* plugin at
``$HERMES_HOME/plugins/chatlytics/``. Hermes imports this ``__init__.py`` as
the module ``hermes_plugins.chatlytics`` (via
``importlib.util.spec_from_file_location``) and calls ``register(ctx)`` on it.

Directory plugins survive a hermes-agent update (``setup-hermes.sh`` runs
``rm -rf venv``) because they live under ``$HERMES_HOME/plugins/``, not the
venv's ``site-packages``. The pip entry-point install does NOT survive — this
shim is the durable delivery channel.

The implementation lives in the ``chatlytics_hermes`` package under ``src/``.
Hermes does not add the plugin dir to ``sys.path``, so we add our bundled
``src/`` here (at position 0 so the bundled copy wins over any stale install),
then import the package normally.
"""
from __future__ import annotations

import os
import sys

_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
_SRC_DIR = os.path.join(_PLUGIN_DIR, "src")
if os.path.isdir(_SRC_DIR) and _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from chatlytics_hermes import register, __version__  # noqa: E402

__all__ = ["register", "__version__"]
