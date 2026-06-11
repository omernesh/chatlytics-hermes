#!/usr/bin/env bash
# Reproducible pytest runner for chatlytics-hermes.
#
# v4.2.0 (P3 survivability) rewrite: the previous version was carried over
# from the monorepo era (mounted ../../.. , installed a nonexistent "[test]"
# extra) AND — critically — its host fallback ran a plain
# `pip install -e .` into the CURRENT environment. That exact pattern is how
# the v4.1.1 `hermes-agent==0.14.0` pin once DOWNGRADED a production
# hermes-agent 0.15.1 → 0.14.0. Rules baked in here:
#
#   * Docker path: installs happen in a DISPOSABLE python:3.13-slim
#     container — the resolver can do whatever it wants, the host is
#     untouched.
#   * Host path: NO pip install AT ALL. pyproject's
#     `[tool.pytest.ini_options] pythonpath = ["src"]` makes the package
#     importable for pytest without installing, so the host env (and its
#     hermes-agent) is never handed to the resolver.
#   * If you DO need to install this plugin into a live gateway venv, use
#     `pip install --no-deps .` (see README "Install" → no-downgrade rule)
#     and run `python -m chatlytics_hermes.doctor` afterwards.
#
# Usage from the repo root:
#   bash scripts/test.sh                       # docker if available, else host
#   bash scripts/test.sh -k longpoll           # extra pytest args pass through
#   TEST_SH_NO_DOCKER=1 bash scripts/test.sh   # force host path

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$REPO_DIR"

if [ -z "${TEST_SH_NO_DOCKER:-}" ] && command -v docker >/dev/null 2>&1; then
  echo "[test.sh] Running pytest inside python:3.13-slim container (disposable env)..."
  MSYS_NO_PATHCONV=1 exec docker run --rm \
    -v "$REPO_DIR":/work \
    -w /work \
    python:3.13-slim \
    bash -c "
      set -euo pipefail
      apt-get update -qq >/dev/null 2>&1
      apt-get install -y -qq --no-install-recommends git ca-certificates >/dev/null 2>&1
      pip install --quiet --disable-pip-version-check --retries 3 \
        'hermes-agent @ git+https://github.com/NousResearch/hermes-agent.git@v2026.5.16'
      pip install --quiet --disable-pip-version-check -e '.[dev]'
      pytest tests/ --tb=short $*
    "
fi

echo "[test.sh] Running pytest against the host environment (no pip install — host env untouched)..."
PY=python
command -v python >/dev/null 2>&1 || PY=python3
exec "$PY" -m pytest tests/ --tb=short "$@"
