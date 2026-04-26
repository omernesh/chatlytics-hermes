#!/usr/bin/env bash
# Phase 168.6 (Fix 10, 2026-04-25): Reproducible pytest runner for the Hermes adapter.
#
# Local Python 3.14 hangs without output for reasons we couldn't isolate; this
# wrapper runs pytest inside a python:3.13-slim Docker container so anyone on
# any host can reproduce a known-good environment.
#
# Usage from anywhere:
#   bash integrations/hermes/scripts/test.sh
#   bash integrations/hermes/scripts/test.sh -k inbound      # filter by name
#   bash integrations/hermes/scripts/test.sh -x --tb=short   # any extra pytest args
#
# Requires: docker daemon running. Falls back to local python3 if `docker` is
# unavailable, but the Docker path is the canonical CI-style invocation.
#
# Mounts the REPO root (4 levels up from this script), not just integrations/hermes,
# because test_action_parity.py reads ../../../src/channel.ts at REPO_ROOT.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HERMES_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$HERMES_DIR/../.." && pwd)"

cd "$HERMES_DIR"

if command -v docker >/dev/null 2>&1; then
  echo "[test.sh] Running pytest inside python:3.13-slim container..."
  HOST_REPO="$REPO_ROOT"
  if [ -n "${MSYSTEM:-}" ] || uname | grep -qi mingw; then
    HOST_REPO="$(cd "$REPO_ROOT" && pwd -W 2>/dev/null || cd "$REPO_ROOT" && pwd)"
    export MSYS_NO_PATHCONV=1
  fi
  exec docker run --rm \
    -v "$HOST_REPO":/repo \
    -w //repo/integrations/hermes \
    python:3.13-slim \
    bash -c "pip install --quiet --disable-pip-version-check -e '.[test]' && pytest --tb=short $*"
fi

if command -v python3 >/dev/null 2>&1; then
  echo "[test.sh] docker not available — falling back to local python3..."
  python3 -m pip install --quiet --disable-pip-version-check -e ".[test]"
  exec python3 -m pytest --tb=short "$@"
fi

echo "[test.sh] FATAL: neither docker nor python3 available." >&2
exit 1
