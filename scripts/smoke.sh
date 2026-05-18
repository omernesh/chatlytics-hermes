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
CACHED=0
for arg in "$@"; do
  case "$arg" in
    --fast)
      FAST=1
      ;;
    --cached)
      CACHED=1
      ;;
    -h|--help)
      cat <<'USAGE'
Usage: scripts/smoke.sh [--fast] [--cached]

Modes:
  (default)   Full dockerized smoke: fresh python:3.13-slim, pip install
              hermes-agent from git+ tag, install plugin editable, run
              pytest + live-loader. ~60-90s on a warm docker cache.
  --fast      Skip docker; run pytest tests/ against the host venv.
              Assumes hermes-agent + plugin already installed locally.
              Used for local iteration -- NOT a substitute for the full
              smoke before tagging a release. ~10-20s.
  --cached    Cache the hermes-agent wheel at .smoke-cache/ between
              runs. First --cached run populates the cache via
              pip download; subsequent runs install with --no-index
              (no network). Cache invalidates automatically when the
              pinned hermes-agent tag changes. Falls back to a normal
              network install if the cache install fails. No-op in
              --fast mode. ~60-90s first run; ~15-25s subsequent runs.

Examples:
  bash scripts/smoke.sh                       # release-gate smoke
  bash scripts/smoke.sh --fast                # quick local pytest
  bash scripts/smoke.sh --cached              # cached docker smoke
  bash scripts/smoke.sh --cached --fast       # cached flag is a no-op in --fast
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

# --cached is a no-op in --fast mode (host venv never installs hermes-
# agent fresh). Print a one-line note for clarity, continue normally.
if [ "$CACHED" = "1" ] && [ "$FAST" = "1" ]; then
  echo "smoke.sh: --cached is a no-op in --fast mode (host venv reused)" >&2
fi

# --- Fast path: host-venv pytest only -----------------------------------------

if [ "$FAST" = "1" ]; then
  echo "--- smoke --fast: host venv pytest only (no docker) ---"
  cd "${REPO_DIR}"
  exec python -m pytest tests/ -q --no-header
fi

# --- Cached docker path: populate .smoke-cache/, install with --no-index ------
#
# HERMES-16 opt-in caching. Active only when --cached is set AND --fast is not
# (the fast path uses the host venv and never installs hermes-agent fresh).
# On first --cached run the cache is populated via ``pip download`` against
# the same git+ pin used by the default path; subsequent --cached runs install
# with ``--no-index --find-links=.smoke-cache/`` for zero-network installs.
# Pin-hash invalidation auto-wipes the cache when HERMES_AGENT_PIN_TAG changes.
# Cache-miss fallback: drops to a normal network install AND refreshes the
# cache so the next --cached run is fast again.

if [ "$CACHED" = "1" ] && [ "$FAST" = "0" ]; then
  CACHE_DIR="${REPO_DIR}/.smoke-cache"
  PIN_HASH_FILE="${CACHE_DIR}/.pin-hash"

  # Compute current pin sha256 (portable; sha256sum is in busybox + GNU).
  CURRENT_PIN_HASH=$(printf '%s' "$HERMES_AGENT_PIN_TAG" | sha256sum | cut -d' ' -f1)

  # Invalidate cache if pin changed since last populated run.
  if [ -d "$CACHE_DIR" ] && [ -f "$PIN_HASH_FILE" ]; then
    STORED_PIN_HASH=$(cat "$PIN_HASH_FILE")
    if [ "$STORED_PIN_HASH" != "$CURRENT_PIN_HASH" ]; then
      echo "smoke.sh: hermes-agent pin changed (was ${STORED_PIN_HASH:0:12}, now ${CURRENT_PIN_HASH:0:12}); wiping cache" >&2
      rm -rf "$CACHE_DIR"
    fi
  fi

  mkdir -p "$CACHE_DIR"

  echo "--- smoke --cached: docker + .smoke-cache/ (pin hash ${CURRENT_PIN_HASH:0:12}) ---"

  MSYS_NO_PATHCONV=1 docker run --rm \
    -v "${REPO_DIR}:/work" \
    -w /work \
    -e HERMES_AGENT_PIN_SPEC="${HERMES_AGENT_PIN_SPEC}" \
    -e CURRENT_PIN_HASH="${CURRENT_PIN_HASH}" \
    python:3.13-slim sh -c '
      set -euo pipefail

      apt-get update -qq >/dev/null 2>&1
      apt-get install -y -qq --no-install-recommends git ca-certificates >/dev/null 2>&1

      CACHE_DIR=/work/.smoke-cache
      PIN_HASH_FILE="$CACHE_DIR/.pin-hash"

      # Populate cache if empty (first run or post-invalidation).
      # Filter out .pin-hash so a stale-hash-only dir still counts as empty.
      if [ -z "$(ls -A "$CACHE_DIR" 2>/dev/null | grep -v "^\\.pin-hash$" || true)" ]; then
        echo "--- smoke --cached: cache empty, pip download hermes-agent ---"
        pip download --quiet --no-cache-dir --retries 3 \
          -d "$CACHE_DIR" "${HERMES_AGENT_PIN_SPEC}"
        printf "%s" "$CURRENT_PIN_HASH" > "$PIN_HASH_FILE"
      fi

      echo "--- smoke --cached: pip install --no-index from cache ---"
      if ! pip install --quiet --no-cache-dir --no-index \
          --find-links="$CACHE_DIR" hermes-agent ; then
        echo "smoke.sh: cache install failed; falling back to network install + refreshing cache" >&2
        pip install --quiet --no-cache-dir --retries 3 "${HERMES_AGENT_PIN_SPEC}"
        # Refresh cache so the next --cached run is fast again.
        rm -rf "$CACHE_DIR"/*.whl "$CACHE_DIR"/*.tar.gz 2>/dev/null || true
        pip download --quiet --no-cache-dir --retries 3 \
          -d "$CACHE_DIR" "${HERMES_AGENT_PIN_SPEC}"
        printf "%s" "$CURRENT_PIN_HASH" > "$PIN_HASH_FILE"
      fi

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

      echo "--- smoke PASS (cached) ---"
    '
  exit $?
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
