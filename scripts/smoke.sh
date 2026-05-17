#!/usr/bin/env bash
# chatlytics-hermes -- dockerized release smoke test (HERMES-06 + HERMES-07).
#
# Spins up a fresh python:3.13-slim container, installs hermes-agent
# from the v2026.5.16 tag and this package editable, then asserts:
#
#   1. ``from chatlytics_hermes import register`` succeeds.
#   2. The ``chatlytics`` entry point is discoverable under the
#      ``hermes_agent.plugins`` group (via importlib.metadata). This is
#      the check Hermes uses internally -- ``hermes plugins list`` on
#      the CLI only enumerates plugins installed via ``hermes plugins
#      install <repo>``, NOT pip entry-point plugins like this one.
#   3. ``pytest tests/`` reports zero failures.
#   4. (HERMES-07) Live-loader integration: tests/test_live_loader.py
#      drives the real PluginContext contract -- ``register(ctx)``
#      registers the chatlytics platform and all 21 tools.
#
# Exits 0 on full success, non-zero otherwise.
#
# Usage:
#   bash scripts/smoke.sh
#
# Requires: docker.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# MSYS_NO_PATHCONV stops Git Bash on Windows from mangling the
# container-side ``-w /work`` path into ``-w D:/.../work``.
MSYS_NO_PATHCONV=1 docker run --rm \
  -v "${REPO_DIR}:/work" \
  -w /work \
  python:3.13-slim sh -c '
    set -euo pipefail

    apt-get update -qq >/dev/null 2>&1
    apt-get install -y -qq --no-install-recommends git ca-certificates >/dev/null 2>&1

    pip install --quiet --no-cache-dir \
        "hermes-agent @ git+https://github.com/NousResearch/hermes-agent.git@v2026.5.16"
    pip install --quiet --no-cache-dir -e ".[dev]"

    echo "--- smoke step 1/3: import chatlytics_hermes.register ---"
    python -c "from chatlytics_hermes import register; print(f\"register OK: {register.__name__}\")"

    echo "--- smoke step 2/3: hermes_agent.plugins entry-point discovery ---"
    python -c "
from importlib.metadata import entry_points
eps = entry_points(group=\"hermes_agent.plugins\")
names = sorted({ep.name for ep in eps})
assert \"chatlytics\" in names, f\"chatlytics not found in entry-points group; got: {names}\"
print(f\"entry-points OK: chatlytics in {names}\")
"

    echo "--- smoke step 3/4: pytest tests/ ---"
    pytest tests/ -q

    echo "--- smoke step 4/4: live-loader integration ---"
    pytest tests/test_live_loader.py -q --no-header --tb=short
    echo "live-loader: chatlytics platform + 21 tools registered"

    echo "--- smoke PASS ---"
  '
