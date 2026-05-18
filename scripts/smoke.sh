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
#   bash scripts/smoke.sh            # full dockerized smoke (release path)
#   bash scripts/smoke.sh --fast     # host-venv pytest only (local iteration)
#   bash scripts/smoke.sh --help     # show this help
#
# Requires (default mode): docker.
# Requires (--fast mode): host venv with hermes-agent + plugin installed.
#
# HERMES-11:
#  - Added --fast flag (closes 06-LOW-01: skip docker for local
#    iteration; opt-in -- default behavior preserved).
#  - Added --retries 3 to pip install commands (closes PR-MED-03:
#    transient GitHub outages no longer look like plugin bugs).
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# The hermes-agent git tag this smoke run pins against. Extracted to
# a variable (rather than embedded inline in the pip install line) so
# HERMES-16's --cached mode can sha256 it for cache invalidation.
# Bumping this tag here automatically invalidates an existing
# .smoke-cache/ on the next --cached run.
HERMES_AGENT_PIN_TAG="v2026.5.16"
HERMES_AGENT_PIN_SPEC="hermes-agent @ git+https://github.com/NousResearch/hermes-agent.git@${HERMES_AGENT_PIN_TAG}"

# --- Argument parsing ---------------------------------------------------------

FAST=0
for arg in "$@"; do
  case "$arg" in
    --fast)
      FAST=1
      ;;
    -h|--help)
      cat <<'USAGE'
Usage: scripts/smoke.sh [--fast]

Modes:
  (default)   Full dockerized smoke: fresh python:3.13-slim, pip install
              hermes-agent from git+ tag, install plugin editable, run
              pytest + live-loader. ~60-90s on a warm docker cache.
  --fast      Skip docker; run pytest tests/ against the host venv.
              Assumes hermes-agent + plugin already installed locally.
              Used for local iteration -- NOT a substitute for the full
              smoke before tagging a release. ~10-20s.

Examples:
  bash scripts/smoke.sh                 # release-gate smoke
  bash scripts/smoke.sh --fast          # quick local pytest
USAGE
      exit 0
      ;;
    *)
      echo "smoke.sh: unknown argument: $arg" >&2
      echo "Run 'bash scripts/smoke.sh --help' for usage." >&2
      exit 2
      ;;
  esac
done

# --- Fast path: host-venv pytest only -----------------------------------------

if [ "$FAST" = "1" ]; then
  echo "--- smoke --fast: host venv pytest only (no docker) ---"
  cd "${REPO_DIR}"
  exec python -m pytest tests/ -q --no-header
fi

# --- Default path: full dockerized smoke --------------------------------------

# MSYS_NO_PATHCONV stops Git Bash on Windows from mangling the
# container-side ``-w /work`` path into ``-w D:/.../work``.
MSYS_NO_PATHCONV=1 docker run --rm \
  -v "${REPO_DIR}:/work" \
  -w /work \
  -e HERMES_AGENT_PIN_SPEC="${HERMES_AGENT_PIN_SPEC}" \
  python:3.13-slim sh -c '
    set -euo pipefail

    apt-get update -qq >/dev/null 2>&1
    apt-get install -y -qq --no-install-recommends git ca-certificates >/dev/null 2>&1

    pip install --quiet --no-cache-dir --retries 3 "${HERMES_AGENT_PIN_SPEC}"
    pip install --quiet --no-cache-dir --retries 3 -e ".[dev]"

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
