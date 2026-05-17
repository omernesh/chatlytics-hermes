# Phase 11: Test infra cleanup - Context

**Gathered:** 2026-05-17
**Status:** Ready for planning
**Mode:** Auto-generated (infra-skip — fix shapes locked by ROADMAP + reviews)

<domain>
## Phase Boundary

Eliminate the conftest teardown gap, reduce smoke runtime + flakiness,
and consolidate duplicated test fixtures. Pure test infrastructure
hygiene — production code is NOT touched beyond importable shared
test-fixture helpers.

Closes:

- **02-MED-02** (carried as IN-03 in the milestone review) — `tests/conftest.py`
  session-scoped `_register_chatlytics_platform` fixture registers the
  chatlytics platform in `gateway.platform_registry` but never
  unregisters. Harmless for `pytest tests/` standalone, but the
  registration leaks if the test suite is ever embedded in a larger
  cross-plugin pytest run.
- **06-LOW-01** (audit) — smoke runtime: every `bash scripts/smoke.sh`
  pulls + compiles `hermes-agent` from `git+https://github.com/...@v2026.5.16`
  inside a fresh `python:3.13-slim` container, adding ~45-60s per run.
- **PR-review MED-03** — same `git+` install is **flaky-by-design** for
  CI; transient GitHub outages become smoke failures with no signal
  about whether OUR code is broken.
- **PR-review cross-cutting nit** (INFO-02) — `_FakePlatformConfig` shim
  is copy-pasted across 8 test files (test_outbound, test_media,
  test_inbound, test_concurrency, test_observability, test_live_loader,
  test_validation, test_register). Maintenance hazard if the upstream
  `PlatformConfig` shape evolves.

Out of scope (deferred to Phase 12):

- CHANGELOG / README updates (Phase 12 — Release).
- pyproject version bump (Phase 12).
- New CI configuration (operator decision; out-of-milestone).
- pytest-xdist or parallel test running (out-of-milestone).

## v2.0 invariants preserved (DO NOT regress)

- Hermes pin stays `>=0.14,<0.15`.
- 21 tools, no surface change (test-infra only).
- httpx outbound, aiohttp embedded inbound only.
- `{"success": bool, ...}` tool response shape.
- 86/86 baseline tests still pass (Phase 10 baseline) — Phase 11 adds
  ~2-3 meta-tests but MUST NOT break any existing test.
- Package name `chatlytics-hermes`, MIT license.
- Default `bash scripts/smoke.sh` behavior unchanged for operators who
  rely on the full dockerized smoke (compatibility lock).
</domain>

<decisions>
## Implementation Decisions

### Fix 1: conftest teardown gap (02-MED-02)

**File:** `tests/conftest.py`

The current fixture:

```python
@pytest.fixture(scope="session", autouse=True)
def _register_chatlytics_platform():
    ...
    platform_registry.register(entry)
    yield
```

Convert to a true yield-style fixture with finalization that
**unregisters** the entry on teardown, using `platform_registry.unregister("chatlytics")`
(public method confirmed in `gateway.platform_registry`).

Add a guard: if the registry already contained `chatlytics` before this
session (e.g. another plugin registered it first in an embedded run),
do NOT register/unregister — snapshot the pre-existing state and leave
it alone.

```python
if platform_registry.is_registered("chatlytics"):
    # Already present (embedded run / prior session leak) -- don't touch
    yield
    return

platform_registry.register(entry)
try:
    yield
finally:
    try:
        platform_registry.unregister("chatlytics")
    except Exception:
        # Idempotent teardown; never fail the session on cleanup
        pass
```

### Fix 2: Shared `_FakePlatformConfig` fixture (PR-review INFO-02)

**Strategy:** Move `_FakePlatformConfig` into `tests/conftest.py` as a
plain (non-fixture) helper class importable via
`from conftest import _FakePlatformConfig` — but pytest conftest classes
aren't import-friendly across the test directory by package path. Use
the recommended pattern: create `tests/_fixtures.py` as a sibling
helper module, define the class there, and re-export from `conftest.py`
for fixture-style consumers if needed.

Decision: create `tests/_fixtures.py` with the canonical
`FakePlatformConfig` class (drop the leading underscore — it's a shared
helper now, not a file-private shim). Each test file imports it:

```python
from tests._fixtures import FakePlatformConfig
```

The 8 duplicated definitions in test files become a single import. The
existing `class _FakePlatformConfig` definitions are deleted from each
file. References to `_FakePlatformConfig(...)` are renamed to
`FakePlatformConfig(...)` (one search-replace per file).

**Backwards-compat NOTE:** the helper signature stays identical
(`__init__(self, extra: Dict[str, Any]) -> None`, sets `enabled=True`,
`name="chatlytics"`, `token=None`, `api_key=extra.get("api_key")`,
`home_channel=extra.get("home_channel")`). No behavior change.

### Fix 3: `scripts/smoke.sh` `--fast` flag (06-LOW-01 + PR-MED-03)

**Strategy:** Add an opt-in `--fast` flag that bypasses docker entirely
and runs `pytest tests/` against the host venv. Default behavior
(no flag) is unchanged — the full dockerized smoke still pulls and
installs `hermes-agent` from git as before.

This is the lowest-risk option from the four enumerated in the task
brief:

- Option (a) docker volume cache: requires CI runners to share the
  volume — too environment-coupled.
- Option (b) pre-built base image: out-of-scope for v2.1 (operator
  decision on Docker Hub / GHCR registry).
- Option (c) published wheel: hermes-agent has no PyPI release at
  v2026.5.16 (confirmed via PR-review); not actionable in v2.1.
- Option (d) `--fast` flag: 5-line script change, no infrastructure
  dependencies, full backwards compat. **Chosen.**

Additionally, follow PR-review MED-03 minimal hardening: add
`--retries 3` to the `pip install` calls inside the docker block, and
wrap the docker invocation with a `timeout` if the host has GNU
coreutils (graceful fallback if not).

```bash
# scripts/smoke.sh
set -euo pipefail

FAST=0
for arg in "$@"; do
  case "$arg" in
    --fast) FAST=1 ;;
    -h|--help)
      cat <<USAGE
Usage: $0 [--fast]

  (default)   Full dockerized smoke: fresh python:3.13-slim, pip install
              hermes-agent from git+ tag, install plugin editable, run
              pytest + live-loader.
  --fast      Skip docker; run pytest tests/ against the host venv.
              Assumes hermes-agent + plugin already installed. Used for
              local iteration -- NOT a substitute for the full smoke
              before tagging a release.
USAGE
      exit 0
      ;;
  esac
done

if [ "$FAST" = "1" ]; then
  echo "--- smoke --fast: host venv pytest only ---"
  exec python -m pytest tests/ -q --no-header
fi

# (existing docker block unchanged, with --retries 3 added to pip)
```

The `exec python` short-circuit means `--fast` returns the pytest exit
code directly. Documented help text makes it discoverable.

### Test changes

- `tests/test_conftest_teardown.py` — NEW meta-test, 2 cases:
  1. Asserts the platform_registry contains `chatlytics` during a
     session (active fixture).
  2. Asserts running pytest twice in succession does not double-register
     (idempotency check).
- `tests/_fixtures.py` — NEW module, exports `FakePlatformConfig`.
- 8 test files updated to import shared `FakePlatformConfig`:
  - `test_outbound.py`, `test_media.py`, `test_inbound.py`,
    `test_concurrency.py`, `test_observability.py`,
    `test_live_loader.py`, `test_validation.py`, `test_register.py`.

### Smoke script changes

- `scripts/smoke.sh` — add `--fast` flag, `--help`, `--retries 3` on pip
  installs, preserve all existing behavior on no-arg invocation.
- README / CHANGELOG mentions deferred to Phase 12.

### Files (create/modify)

- MODIFY `tests/conftest.py` — yield teardown, guard against
  pre-existing registration.
- CREATE `tests/_fixtures.py` — shared `FakePlatformConfig` class.
- CREATE `tests/test_conftest_teardown.py` — 2 meta-tests.
- MODIFY 8 test files — remove duplicated `_FakePlatformConfig`,
  import shared `FakePlatformConfig`.
- MODIFY `scripts/smoke.sh` — `--fast` flag, `--retries 3`.

### Acceptance criteria

1. `pytest tests/` reports 86/86 (or 88/88 with the new meta-tests)
   passing, no regressions.
2. `tests/conftest.py` calls `platform_registry.unregister("chatlytics")`
   on session teardown when it registered the entry itself.
3. `tests/conftest.py` does NOT unregister when the platform was
   already registered before the session started (idempotency guard).
4. `bash scripts/smoke.sh --fast` runs `pytest tests/` against the host
   venv and exits 0 when tests pass.
5. `bash scripts/smoke.sh --help` prints usage including `--fast`.
6. `bash scripts/smoke.sh` (no args) preserves the existing dockerized
   behavior 1:1 (full dockerized smoke still runs and passes).
7. `grep -rn 'class _FakePlatformConfig' tests/` returns ZERO matches
   after consolidation (all 8 duplicates removed).
8. `grep -rn 'from tests._fixtures import FakePlatformConfig' tests/`
   matches all 8 test files.
9. Tool surface unchanged (still 21 tools — sanity check via
   `test_register::test_register_creates_21_tools` still passes).
</decisions>

<artifacts>
## Inputs

- `.planning/STATE.md` — v2.1 phase plan table, HERMES-11 row.
- `.planning/ROADMAP.md` — Phase 11 section.
- `.planning/v2.0-MILESTONE-CODE-REVIEW.md` — IN-03 (conftest teardown),
  LO-01 (smoke runtime).
- `.planning/v2.0-MILESTONE-PR-REVIEW.md` — MED-03 (smoke `git+`
  flakiness), INFO-02 (test stub duplication).
- `tests/conftest.py` — current session fixture without teardown.
- `scripts/smoke.sh` — current dockerized smoke.
- 8 test files containing duplicated `_FakePlatformConfig` class.

## Outputs (post-execute)

- `tests/conftest.py` with yield teardown + idempotency guard.
- `tests/_fixtures.py` new shared helper module.
- `tests/test_conftest_teardown.py` new 2-test meta-suite.
- 8 test files updated to use shared `FakePlatformConfig`.
- `scripts/smoke.sh` with `--fast` flag + `--retries 3` hardening.
- Phase manifest: PLAN, EXECUTE, VERIFICATION, REVIEW under
  `.planning/phases/HERMES-11-test-infra-cleanup/`.
</artifacts>
