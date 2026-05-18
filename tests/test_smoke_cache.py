"""HERMES-16: scripts/smoke.sh argument-parsing smoke test.

Verifies that the v3.0 ``--cached`` flag landed cleanly in the bash
script's argument parser WITHOUT regressing the existing ``--fast``
flag, the ``--help`` text, or the unknown-flag error path.

Notes:

- We do NOT exercise the actual cached install flow (would require
  docker + ~60s minimum + network) -- pytest stays a unit / static
  guard.
- Tests are skipped on hosts without ``bash`` on PATH (e.g. a
  hypothetical bash-less Windows CI). v2.1's existing smoke.sh has
  always assumed bash availability for the same reason.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SMOKE_SH = REPO_ROOT / "scripts" / "smoke.sh"

# Resolve bash via shutil.which so we use the same bash a developer
# would invoke from a terminal (e.g. Git Bash on Windows). Subprocess's
# default PATH-resolution can pick a different bash (e.g. WSL bash.exe
# that hangs without a configured distro) which is NOT what we want.
_BASH = shutil.which("bash")

# Skip entire module if bash isn't available.
pytestmark = pytest.mark.skipif(
    _BASH is None,
    reason="bash not on PATH; skipping smoke.sh argument-parsing tests",
)


def _run(*args: str, timeout: float = 30.0) -> subprocess.CompletedProcess:
    """Invoke ``bash scripts/smoke.sh`` with the given args."""
    return subprocess.run(
        [_BASH, str(SMOKE_SH), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(REPO_ROOT),
    )


def test_smoke_sh_passes_bash_syntax_check() -> None:
    """``bash -n scripts/smoke.sh`` must parse cleanly with --cached added."""
    result = subprocess.run(
        [_BASH, "-n", str(SMOKE_SH)],
        capture_output=True,
        text=True,
        timeout=30.0,
    )
    assert result.returncode == 0, (
        f"smoke.sh failed bash syntax check: {result.stderr}"
    )


def test_help_text_documents_cached_flag() -> None:
    """``--help`` output must mention --cached so the flag is discoverable."""
    result = _run("--help")
    assert result.returncode == 0, (
        f"smoke.sh --help should exit 0; got {result.returncode}: {result.stderr}"
    )
    assert "--cached" in result.stdout, (
        f"smoke.sh --help should document --cached; got:\n{result.stdout}"
    )


def test_unknown_flag_still_rejected() -> None:
    """Regression guard: unknown flags still exit non-zero with a message."""
    result = _run("--bogus-flag-that-does-not-exist")
    assert result.returncode != 0, (
        "smoke.sh should reject unknown flags with non-zero exit"
    )
    assert "unknown" in result.stderr.lower(), (
        f"smoke.sh should print an unknown-argument message; got stderr:\n{result.stderr}"
    )


def test_cached_and_fast_compose_in_help() -> None:
    """``--fast --cached --help`` must exit 0 (parser handles both flags)."""
    result = _run("--fast", "--cached", "--help")
    assert result.returncode == 0, (
        f"smoke.sh --fast --cached --help should exit 0; "
        f"got {result.returncode}: stderr={result.stderr!r}"
    )
    # The --cached-in-fast-mode warning may also fire here; that's
    # fine and not asserted (stderr-noise tolerant).
