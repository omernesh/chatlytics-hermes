---
phase: 01-upstream-contract-scaffolding
review_date: 2026-05-17
depth: standard
implemented_by: claude-opus-4-7-1m
reviewed_by: gsd-code-review
files_reviewed:
  - src/chatlytics_hermes/__init__.py
  - src/chatlytics_hermes/adapter.py
  - plugin.yaml
  - pyproject.toml
  - tests/test_register.py
summary:
  blocker: 0
  high: 0
  medium: 2
  low: 4
  info: 2
overall_verdict: PASS_WITH_MINORS
---

# HERMES-01 -- Code Review

## Scope

Reviewed the 5 source files that constitute the plugin contract
scaffolding for HERMES-01. The phase is structural-only -- abstract
methods raise `NotImplementedError` -- so the review focuses on:

1. **Contract correctness** against `/tmp/hermes-ref-v0.14.0/` upstream
2. **Scope discipline** (no leakage of HERMES-02/03/04/05 behavior)
3. **Acceptance-criterion coverage** (5/5 ROADMAP ACs verified PASS)
4. **Cross-environment portability** (Windows host + Linux container)
5. **Security hygiene** (secret handling, env-var precedence, manifest
   password flags)

## Verdict: PASS WITH MINORS

Zero BLOCKER, zero HIGH. Two MEDIUM and four LOW concerns documented
below -- none affect acceptance criteria or block HERMES-02 from
starting. Recommended to address LOW concerns as part of HERMES-02's
first commit (low-cost cleanup), and to revisit the MEDIUM concerns
when the abstract methods land.

---

## Findings

### MEDIUM-01 -- adapter_factory positional vs keyword arg mismatch with IRC pattern

**File:** `src/chatlytics_hermes/adapter.py:136`

```python
adapter_factory=lambda cfg: ChatlyticsAdapter(cfg),
```

The base `BasePlatformAdapter.__init__` signature in
`/tmp/hermes-ref-v0.14.0/gateway/platforms/base.py:1276` is
`def __init__(self, config: PlatformConfig, platform: Platform)`. The
IRC reference at `plugins/platforms/irc/adapter.py:102` passes the
config positionally:

```python
def __init__(self, config, **kwargs):
    platform = Platform("irc")
    super().__init__(config=config, platform=platform)
```

`ChatlyticsAdapter.__init__(self, config: Any, **kwargs: Any)` matches
this shape, and the lambda passes `cfg` positionally -- so this works.
However, if Hermes ever calls the factory with `adapter_factory(cfg,
extra_kwarg=...)`, the lambda discards the kwargs silently. IRC's
factory has the same bug (also a bare `lambda cfg: ...`), so this is
consistent with upstream convention rather than a real defect.

**Disposition:** Accept for HERMES-01. Track as a possible adapter API
hardening item when HERMES-02 adds real `connect()` semantics. No
action required now.

### MEDIUM-02 -- Optional `PYTHONPATH=src` requirement for bare-import smoke

**File:** `pyproject.toml:46` + `src/chatlytics_hermes/`

The host smoke (`python -c "from chatlytics_hermes import register"`)
only works in environments where:
- `pip install -e .` has been run (puts the egg-info on the path), OR
- `PYTHONPATH=src` is set explicitly, OR
- The user runs from `cd src && python -c ...`

Acceptance criterion 1 in the ROADMAP doesn't specify which environment
the smoke runs in. The dockerized AC-4 path (`pip install -e .[dev]`)
exercises the editable install vector, so the contract IS satisfied.
For developer ergonomics, a `conftest.py` at repo root or a brief
README "Quick start" note pointing at the editable install would
prevent confusion.

**Disposition:** Document in HERMES-06 README rewrite. Not blocking.

### LOW-01 -- Unused `sys` import in tests/test_register.py

**File:** `tests/test_register.py:17`

```python
import sys
```

Never referenced in the module. Dead import.

**Fix:** Remove the import. One-line change.

### LOW-02 -- Redundant string-form forward reference on `send` return type

**File:** `src/chatlytics_hermes/adapter.py:114`

```python
async def send(
    self,
    chat_id: str,
    content: str,
    reply_to: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> "SendResult":  # type: ignore[name-defined]
```

`from __future__ import annotations` is active (line 27), so all
annotations are already evaluated lazily as strings. The explicit
`"SendResult"` quote + `# type: ignore[name-defined]` are belt-and-
suspenders -- the `# type: ignore` would still be needed when
`SendResult = None` after the ImportError shim fires, but the quote
itself is redundant. Cosmetic.

**Fix:** Drop the quotes when HERMES-02 returns a real `SendResult`
from this method. No action now.

### LOW-03 -- `PlatformConfig` imported but never referenced in the module

**File:** `src/chatlytics_hermes/adapter.py:34`

```python
from gateway.config import Platform, PlatformConfig
```

`PlatformConfig` is in the import block (and stubbed to `None` in the
ImportError fallback) but never used as a type annotation or runtime
value in HERMES-01. `Any` is used instead for `config` in `__init__`.

This is harmless but flags as an unused-import warning under strict
lint configs.

**Fix:** Either (a) drop `PlatformConfig` from the import until
HERMES-02 needs it, or (b) replace `config: Any` with the proper
`config: "PlatformConfig"` annotation now. Option (b) is preferred for
clarity once `_HERMES_AVAILABLE` is True; the string-form annotation
keeps the ImportError shim path safe.

### LOW-04 -- Lambda factory doesn't preserve `__name__` for debuggability

**File:** `src/chatlytics_hermes/adapter.py:136`

```python
adapter_factory=lambda cfg: ChatlyticsAdapter(cfg),
```

Bare lambdas have `__name__ == "<lambda>"`. If Hermes's runtime logs
the factory name during platform registration failures, the trace will
be opaque. IRC has the same shape, so this is consistent with upstream
convention.

**Fix:** Optional -- define a module-level `def _create_adapter(cfg)`
function and pass that instead. Cosmetic.

### INFO-01 -- Manifest `name` lacks the `-platform` suffix used by IRC

**File:** `plugin.yaml:1`

```yaml
name: chatlytics
```

IRC reference uses `name: irc-platform`. Hermes accepts either, and
the routing name is `ctx.register_platform(name="chatlytics", ...)` in
`adapter.py:134`, so this is not a bug. Convention-only divergence.

**Disposition:** Accept. The manifest `name` is informational; the
`register_platform(name=...)` is the source of truth for routing.

### INFO-02 -- `account_id` env var not declared in plugin.yaml requires_env

**File:** `plugin.yaml` + `src/chatlytics_hermes/adapter.py:70-72`

`CHATLYTICS_ACCOUNT_ID` is correctly listed under `optional_env` and is
read in `__init__`. ROADMAP HERMES-02 marks it as `account_id?`
(optional default session), so this is correct. Flagged only because
the adapter unconditionally reads it -- if Hermes ever surfaces
required vs optional differently in the config wizard, both sides agree
this is optional.

**Disposition:** No action.

---

## Strengths

1. **Scope discipline is exemplary.** The deferred-hooks guard test
   (`test_register_does_not_declare_deferred_hooks`) is a great forcing
   function -- it will alert reviewers if HERMES-02/03/04 prematurely
   add hooks while still under HERMES-01's commit graph.

2. **Import shim is correct.** The `try/except ImportError` pattern is
   the right shape for satisfying AC-1 in environments without
   hermes-agent installed, and the `_HERMES_AVAILABLE` guard in
   `__init__` produces a clear error rather than a cryptic
   `AttributeError`. This matches the lazy-import pattern documented in
   the IRC reference's module docstring.

3. **ASCII-clean source.** After the Windows cp1255 decode failure
   during execution, the executor moved to escaped Unicode (`\U0001f4ac`
   for the speech-bubble emoji) and ASCII section separators. This
   prevents host-locale issues without sacrificing the operator-facing
   emoji.

4. **Security hygiene in `plugin.yaml`.** `CHATLYTICS_API_KEY` and
   `CHATLYTICS_WEBHOOK_SECRET` are both `password: true`, which means
   the `hermes config` wizard will mask them on input.

5. **Env-var precedence is correct.** All settings follow `env > extra >
   default`, matching the IRC reference convention.

6. **Docker smoke is reproducible.** The AC-4 install vector
   (`hermes-agent @ git+https://github.com/NousResearch/hermes-agent.git@v2026.5.16`)
   is documented in SUMMARY.md and survives the v0.14-not-on-PyPI
   constraint cleanly.

---

## Action items for HERMES-02

- Drop `import sys` from `tests/test_register.py` (LOW-01)
- Tighten `config: Any` -> `config: "PlatformConfig"` once hermes import
  is exercised (LOW-03)
- Add `respx`-backed mocks for the new `httpx.AsyncClient` paths and
  assert no secret material lands in `logging` calls
- Re-test `ChatlyticsAdapter.__init__` -- once HERMES-02 instantiates
  the adapter via `respx`-mocked `connect()`, the `_HERMES_AVAILABLE`
  guard branch becomes exercised

## Action items for HERMES-06 (release)

- Document the GitHub-tag install vector in README until hermes-agent
  0.14 lands on PyPI (MEDIUM-02 context)
- Either remove the `aiohttp` dep until HERMES-03 actually uses it, or
  keep it and add a comment block explaining the "install once" rationale

---

## Cross-references

- ROADMAP: `.planning/ROADMAP.md` (Phase 1 acceptance criteria 1-5)
- PLAN: `.planning/phases/HERMES-01-upstream-contract-scaffolding/01-01-PLAN.md`
- SUMMARY: `.planning/phases/HERMES-01-upstream-contract-scaffolding/01-01-SUMMARY.md`
- VERIFICATION: `.planning/phases/HERMES-01-upstream-contract-scaffolding/01-VERIFICATION.md`
- Upstream contract reference:
  `/tmp/hermes-ref-v0.14.0/gateway/platforms/ADDING_A_PLATFORM.md`
- Upstream plugin reference:
  `/tmp/hermes-ref-v0.14.0/plugins/platforms/irc/adapter.py`
