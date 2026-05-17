---
phase: HERMES-06-release-smoke-test
plan: 06-01
implemented_by: claude-opus-4-7-1m
reviewed_by: gsd-code-review
review_date: 2026-05-17
verdict: PASS WITH MINORS
blockers: 0
high: 0
medium: 1
low: 2
info: 1
---

# HERMES-06 -- REVIEW

## Scope reviewed

Five commits and the resulting release artifacts:

- `5e00da9` fix(hermes-06): wrap blocking open()/read() in asyncio.to_thread (04-MED-02)
- `ceb98e3` docs(hermes-06): README v2.0 rewrite
- `04dd3aa` docs(hermes-06): CHANGELOG 2.0.0 (BREAKING) entry
- `c345463` chore(hermes-06): scripts/smoke.sh dockerized release verification
- (tag) `v2.0.0` annotated tag (local only)

Files reviewed:
- `README.md` (full rewrite)
- `CHANGELOG.md` (prepended 2.0.0 entry)
- `scripts/smoke.sh` (CREATE)
- `src/chatlytics_hermes/adapter.py` (`_resolve_media_url` local-file branch)
- `tests/test_media.py` (added `test_send_image_file_reads_off_event_loop`)

## Verdict: PASS WITH MINORS

Zero BLOCKER, zero HIGH. One MEDIUM and two LOW concerns documented
below. None affect acceptance criteria or block the v2.0.0 release.
The MEDIUM is a known-deferred future-PyPI-publish ergonomics
question, not a defect.

## Findings

### MED-01 -- Smoke test does not exercise the live Hermes plugin loader

**Files:** `scripts/smoke.sh`
**Severity:** MEDIUM
**Category:** test coverage

The smoke verifies entry-point discoverability via
`importlib.metadata.entry_points(...)` -- this proves the package is
correctly registered and pip-discoverable. It does NOT, however,
spin up the Hermes gateway and observe `register(ctx)` being invoked
with a real context, nor does it observe the 21 tools being
registered onto a real `ctx.register_tool` callable.

The plan calls this "autonomous ceiling" -- a full gateway smoke
requires a Chatlytics gateway endpoint to authenticate against, which
the operator lock prevents. The current smoke is sufficient for the
v2.0.0 release-tag step (proves discovery + import + tests), but a
follow-up phase should add a stub-gateway integration test that wires
`hermes.gateway.bootstrap.load_plugins()` against a respx-mocked
Chatlytics backend and asserts that all 21 tools land on the
in-memory registry.

**Action:** deferred to a future v2.1 phase ("HERMES-07 / live-loader
integration smoke"); not blocking v2.0.0 release.

### LOW-01 -- Smoke test re-installs hermes-agent on every run

**Files:** `scripts/smoke.sh`
**Severity:** LOW
**Category:** performance

Every smoke invocation runs `apt-get install git ca-certificates` and
`pip install hermes-agent @ git+...` from scratch inside a fresh
container. The wall-clock cost is ~45-60 s of network I/O dominated
by the Hermes git clone. Could be cached by:

- pre-building a `chatlytics-hermes-smoke:latest` docker image with
  hermes-agent baked in, or
- mounting a host pip cache directory.

Neither is required for correctness; the current cost is acceptable
for a release-tag smoke step. Documented here so a future
contributor doesn't quietly add it without thinking through cache
invalidation when the hermes-agent tag bumps.

**Action:** deferred. Acceptable as-is.

### LOW-02 -- `_keep_typing` initial-fire failure is logged at DEBUG

**Files:** `src/chatlytics_hermes/adapter.py:777-783` (unchanged from HERMES-04)
**Severity:** LOW
**Category:** observability

If the very first `send_typing` call inside `_keep_typing` raises
(e.g. the chat_id is invalid or the gateway is down), the error is
swallowed at DEBUG level and the context manager proceeds to spawn
the heartbeat task anyway. The heartbeat will then also fail
silently. Under normal operation this is invisible.

A consistent failure mode (auth misconfig, dead gateway) would
appear as "the typing bubble never shows" with no visible error in
logs at INFO level. Bumping the FIRST-fire failure to WARNING (while
keeping subsequent heartbeats at DEBUG) would surface this without
flooding logs in the steady-state failure mode.

**Action:** deferred to v2.1. Not blocking v2.0.0.

### INFO-01 -- Future PyPI publish becomes a 1-command operation

**Files:** `pyproject.toml`, `CHANGELOG.md`, `README.md`
**Category:** release engineering

Manifest is fully ready for PyPI publish. When the operator decides
to ship, the entire publish flow becomes:

```bash
python -m build && twine upload dist/*
```

No additional plumbing required. The plugin discovery works
identically whether installed from PyPI or from `pip install -e .` --
the entry-point group resolves the same way.

(This INFO entry is intentionally redundant with SUMMARY content;
flagging it here so the reviewer can confirm the manifest review
matches the deferred-publish stance.)

## Carry-forward to follow-on milestones

- MED-01 above: live Hermes gateway loader integration smoke (future
  phase, depends on either a docker-compose Chatlytics test
  double or a recorded request-replay fixture).
- LOW-01 above: pre-built smoke docker image with cached deps.
- LOW-02 above: `_keep_typing` initial-fire log level bump.
- PyPI publish (operator decision, future milestone).
- Marketplace listing.
