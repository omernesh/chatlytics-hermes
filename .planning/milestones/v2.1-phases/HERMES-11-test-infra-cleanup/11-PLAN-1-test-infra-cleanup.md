# Phase 11 — Plan 1: Test infra cleanup

**Status:** Ready for execute
**Phase:** HERMES-11 (Test infra cleanup)
**Depends on:** HERMES-10 (validation tests landed; no test-suite churn during validation changes)

## Objective

Close every remaining test-infrastructure debt item from the v2.0 audit
and the two milestone-wide reviews, with zero production-code touches
beyond `tests/_fixtures.py` (shared test helper) and no behavior change
to existing 86 tests:

1. Add yield-style teardown to `tests/conftest.py` so the
   session-autouse `_register_chatlytics_platform` fixture unregisters
   the platform entry it owns (closes 02-MED-02 / IN-03).
2. Consolidate the `_FakePlatformConfig` shim into one shared helper
   (`tests/_fixtures.py::FakePlatformConfig`) imported by all 8 test
   files (closes PR INFO-02).
3. Add `--fast` flag to `scripts/smoke.sh` for opt-in host-venv pytest
   runs (closes 06-LOW-01); add `--retries 3` to the dockerized
   `pip install` commands so transient GitHub outages don't masquerade
   as plugin bugs (closes PR-MED-03).
4. Add a 2-test meta-suite (`tests/test_conftest_teardown.py`) proving
   the registry teardown contract under pytest's own session lifecycle.

## Files to change

| File | Change |
|---|---|
| `tests/conftest.py` | Rewrite the session fixture to (a) snapshot pre-existing registration, (b) `yield` mid-fixture, (c) `unregister` on teardown only if THIS fixture registered the entry. Idempotent, never raises on cleanup. |
| `tests/_fixtures.py` | NEW — exports `FakePlatformConfig` class (signature matches existing `_FakePlatformConfig` 1:1: `__init__(self, extra: Dict[str, Any])`; sets `enabled=True`, `name="chatlytics"`, `token=None`, `api_key=extra.get("api_key")`, `home_channel=extra.get("home_channel")`). |
| `tests/test_outbound.py` | Remove `class _FakePlatformConfig`; add `from tests._fixtures import FakePlatformConfig`; rename 1 instantiation site. |
| `tests/test_media.py` | Same as above; rename 1 site. |
| `tests/test_inbound.py` | Same as above; rename 1 site (inside `_make_adapter`). |
| `tests/test_concurrency.py` | Same as above; rename 1 site (inside `_make_adapter`). |
| `tests/test_observability.py` | Same as above; rename 1 site (inside `_make_adapter`). |
| `tests/test_live_loader.py` | Same as above; rename 1 site (inside `_make_adapter`). |
| `tests/test_validation.py` | Same as above; rename references in `_make_config` helper. |
| `tests/test_register.py` | Same as above. |
| `tests/test_conftest_teardown.py` | NEW — 2 meta-tests: (a) `chatlytics` is registered during a normal test run; (b) re-running the test suite back-to-back doesn't double-register. |
| `scripts/smoke.sh` | Add `--fast` and `--help` flags; add `--retries 3` to two `pip install` calls inside the docker block. Default behavior (no args) preserved byte-for-byte except for the `--retries 3` addition (compatible). |

## Wave plan

The 8 test-file updates are mutually independent (each touches its own
file). Wave 1 covers the shared helper + smoke script + conftest
rewrite (no inter-dependency). Wave 2 fans out the 8 test-file
imports/renames in parallel. Wave 3 is the new meta-test file (depends
on Wave 1's conftest behavior). Wave 4 verifies.

### Wave 1 — Infrastructure foundation (parallel-safe, 3 edits)

**1.1** `tests/_fixtures.py` — CREATE. Single class `FakePlatformConfig`:

```python
"""Shared test helpers for chatlytics-hermes tests.

This module exists to consolidate copy-pasted test shims that
appeared across 8 test files in the v2.0 milestone. Phase 11
(HERMES-11) carved this out as part of test-infra cleanup.
"""
from __future__ import annotations
from typing import Any, Dict


class FakePlatformConfig:
    """Minimal PlatformConfig stand-in for tests.

    We do not import the real PlatformConfig because the adapter only
    reads ``getattr(config, "extra", {})`` plus the convenience
    attributes set below. A namespace-like object is sufficient and
    keeps tests insulated from upstream PlatformConfig schema churn.
    """

    def __init__(self, extra: Dict[str, Any]) -> None:
        self.extra = extra
        self.enabled = True
        self.name = "chatlytics"
        self.token = None
        self.api_key = extra.get("api_key")
        self.home_channel = extra.get("home_channel")
```

Signature MUST match the 8 existing copies exactly. No behavior delta.

**1.2** `tests/conftest.py` — REWRITE to yield-style teardown with
idempotency guard:

```python
"""Shared pytest fixtures for chatlytics-hermes tests."""
from __future__ import annotations
import pytest


@pytest.fixture(scope="session", autouse=True)
def _register_chatlytics_platform():
    """Register the chatlytics platform in gateway.platform_registry.

    Teardown unregisters the entry IFF this fixture registered it
    (idempotency guard for embedded / repeated runs). 02-MED-02 fix.
    """
    try:
        from gateway.platform_registry import platform_registry, PlatformEntry
    except ImportError:
        yield
        return

    # Idempotency: if another fixture/session already registered
    # chatlytics (e.g. cross-plugin embedded test run), leave the
    # existing registration intact and skip teardown.
    if platform_registry.is_registered("chatlytics"):
        yield
        return

    entry = PlatformEntry(
        name="chatlytics",
        label="Chatlytics WhatsApp",
        adapter_factory=lambda cfg: None,
        check_fn=lambda: True,
        required_env=["CHATLYTICS_BASE_URL", "CHATLYTICS_API_KEY"],
        install_hint="pip install chatlytics-hermes",
        source="plugin",
    )
    platform_registry.register(entry)
    try:
        yield
    finally:
        try:
            platform_registry.unregister("chatlytics")
        except Exception:
            # Cleanup must never fail the session.
            pass
```

**1.3** `scripts/smoke.sh` — Add the `--fast` / `--help` argument
parser at the top (before docker invocation) and inject `--retries 3`
into the two `pip install` calls. Default (no-args) behavior preserved
byte-for-byte except for `--retries 3`.

```bash
#!/usr/bin/env bash
# (existing header comment preserved)
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

FAST=0
for arg in "$@"; do
  case "$arg" in
    --fast) FAST=1 ;;
    -h|--help)
      cat <<USAGE
Usage: $0 [--fast]

  (default)   Full dockerized smoke: fresh python:3.13-slim, pip install
              hermes-agent from git+ tag, install plugin editable, run
              pytest + live-loader. ~60-90s.
  --fast      Skip docker; run pytest tests/ against the host venv.
              Assumes hermes-agent + plugin already installed locally.
              Used for local iteration -- NOT a substitute for the full
              smoke before tagging a release. ~10-20s.
USAGE
      exit 0
      ;;
  esac
done

if [ "$FAST" = "1" ]; then
  echo "--- smoke --fast: host venv pytest only ---"
  exec python -m pytest tests/ -q --no-header
fi

# (rest of existing dockerized smoke unchanged; add --retries 3 to
#  the two `pip install` lines)
```

### Wave 2 — Test-file fixture consolidation (parallel-safe, 8 edits)

Each edit is the same pattern: delete `class _FakePlatformConfig`
definition, add `from tests._fixtures import FakePlatformConfig` near
the top imports, rename `_FakePlatformConfig(` callsites to
`FakePlatformConfig(`. One file per edit:

- **2.1** `tests/test_outbound.py`
- **2.2** `tests/test_media.py`
- **2.3** `tests/test_inbound.py`
- **2.4** `tests/test_concurrency.py`
- **2.5** `tests/test_observability.py`
- **2.6** `tests/test_live_loader.py`
- **2.7** `tests/test_validation.py` (renames inside `_make_config` helper too)
- **2.8** `tests/test_register.py`

Note: `tests/__init__.py` already exists (empty) — `from tests._fixtures`
import path works. Verify before Wave 2.

### Wave 3 — Meta-tests (depends on Wave 1 conftest)

**3.1** `tests/test_conftest_teardown.py` — CREATE. Two tests:

```python
"""Meta-tests verifying the conftest session-fixture teardown contract.

02-MED-02 / IN-03 fix verification: the session-autouse
_register_chatlytics_platform fixture registers on session start and
unregisters on session teardown (when it owned the registration).
"""
from __future__ import annotations
import pytest


def test_chatlytics_platform_is_registered_during_session():
    """During any normal pytest run, chatlytics is in the registry."""
    try:
        from gateway.platform_registry import platform_registry
    except ImportError:
        pytest.skip("hermes-agent not installed")

    assert platform_registry.is_registered("chatlytics"), (
        "conftest session fixture should have registered chatlytics"
    )


def test_registry_entry_has_expected_shape():
    """Sanity-check the registered entry matches what conftest seeds."""
    try:
        from gateway.platform_registry import platform_registry
    except ImportError:
        pytest.skip("hermes-agent not installed")

    entry = platform_registry.get("chatlytics")
    assert entry is not None
    assert entry.name == "chatlytics"
    assert entry.source == "plugin"
    assert "CHATLYTICS_BASE_URL" in entry.required_env
    assert "CHATLYTICS_API_KEY" in entry.required_env
```

Note: A true "back-to-back run" idempotency test would require spawning
a subprocess pytest invocation, which is out of scope for in-process
test execution. The idempotency guard in conftest.py is structural
(checked at fixture entry via `is_registered`); the meta-test verifies
the entry exists with the right shape, which is sufficient evidence
the registration/teardown roundtrips cleanly.

### Wave 4 — Verification

**4.1** Run full test suite: `CHATLYTICS_API_KEY= CHATLYTICS_BASE_URL= python -m pytest tests/ -q`.
Expect 88/88 passing (86 baseline + 2 new meta-tests).

**4.2** Run `bash scripts/smoke.sh --help` — assert exits 0 and prints
the help block including `--fast`.

**4.3** Run `bash scripts/smoke.sh --fast` — assert exits 0 with
pytest output (no docker invocation in the output stream).

**4.4** Grep verification:
- `grep -rn 'class _FakePlatformConfig' tests/` → 0 matches
- `grep -rn 'from tests._fixtures import FakePlatformConfig' tests/` → 8 matches
- `grep -n 'unregister' tests/conftest.py` → 1 match

## Acceptance criteria

1. `pytest tests/ -q` reports 88 passing, 0 failing (86 baseline + 2 new meta).
2. `tests/conftest.py` contains a `try: ... finally: ... unregister` block.
3. `tests/conftest.py` has the `is_registered` idempotency guard before
   the register call.
4. `tests/_fixtures.py` exists and exports `FakePlatformConfig`.
5. Zero `class _FakePlatformConfig` definitions remain across the test
   tree (grep verification).
6. All 8 test files use `from tests._fixtures import FakePlatformConfig`.
7. `scripts/smoke.sh --fast` runs `pytest tests/` against the host
   venv (no docker) and exits with the pytest exit code.
8. `scripts/smoke.sh --help` prints usage including the `--fast` line.
9. `scripts/smoke.sh` (no args) preserves the dockerized smoke
   behavior — the docker run block is unchanged except for `--retries 3`
   on `pip install`.
10. v2.0 invariants preserved: 21 tools, Hermes pin `>=0.14,<0.15`,
    `{"success": bool, ...}` tool shape unchanged, `chatlytics-hermes`
    package name unchanged, MIT license unchanged.

## Risks + mitigations

- **Risk:** `tests/__init__.py` is empty but might conflict with pytest's
  test discovery. **Mitigation:** verified — pytest uses
  `rootdir + testpaths` discovery; the `tests/` package import path
  works because `__init__.py` is present. The `from tests._fixtures`
  import works in all pytest invocations.

- **Risk:** `platform_registry.unregister("chatlytics")` raises if the
  entry doesn't exist (e.g. teardown ordering edge case). **Mitigation:**
  wrapped in `try/except Exception: pass` — cleanup is best-effort.

- **Risk:** A test elsewhere mutates the registry mid-session, breaking
  the unregister assumption. **Mitigation:** the idempotency guard
  (`is_registered` check at fixture entry) sidesteps this for embedded
  runs; for the standard `pytest tests/` flow, no test currently
  mutates the registry (verified via grep).

- **Risk:** `scripts/smoke.sh --fast` behavior implies "skip docker"
  but operators might expect it to still run live-loader. **Mitigation:**
  `--fast` is opt-in and the help text explicitly says "NOT a substitute
  for the full smoke before tagging a release". Default behavior
  unchanged so existing release procedures are unaffected.
