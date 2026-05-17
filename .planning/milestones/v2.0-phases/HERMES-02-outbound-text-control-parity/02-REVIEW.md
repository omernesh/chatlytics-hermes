---
phase: 02-outbound-text-control-parity
review_date: 2026-05-17
depth: standard
implemented_by: claude-opus-4-7-1m
reviewed_by: gsd-code-review
files_reviewed:
  - src/chatlytics_hermes/client.py
  - src/chatlytics_hermes/adapter.py
  - tests/test_outbound.py
  - tests/conftest.py
summary:
  blocker: 0
  high: 0
  medium: 2
  low: 4
  info: 2
overall_verdict: PASS_WITH_MINORS
---

# HERMES-02 -- Code Review

## Scope

Reviewed the 4 source files that constitute the outbound text + control
parity surface for HERMES-02:

1. `src/chatlytics_hermes/client.py` (NEW, 90 LOC)
2. `src/chatlytics_hermes/adapter.py` (MODIFIED, +216/-23 LOC)
3. `tests/test_outbound.py` (NEW, 242 LOC)
4. `tests/conftest.py` (NEW, 51 LOC)

The phase is behavioral -- abstract methods now have real bodies that
talk to a remote REST gateway via `httpx.AsyncClient` -- so the review
focuses on:

1. **HTTP correctness** (verb/path/header/body/timeout) against the
   Chatlytics gateway contract documented in CONTEXT.md
2. **Lifecycle correctness** (client construction/close idempotency,
   `_running` flag transitions, exception cleanup paths)
3. **Acceptance-criterion coverage** (8/8 ROADMAP ACs verified PASS in
   dockerized clean-room — see `02-VERIFICATION.md`)
4. **Security hygiene** (Bearer header injection, no api_key in logs,
   metadata key-injection protection)
5. **Scope discipline** (no leakage of HERMES-03/04/05 behavior)

## Verdict: PASS WITH MINORS

Zero BLOCKER, zero HIGH. Two MEDIUM and four LOW concerns documented
below — none affect acceptance criteria or block HERMES-03 from
starting. Recommended to address LOW concerns opportunistically in
HERMES-03 (the inbound side will touch `adapter.connect()` again) and
to revisit the MEDIUM concerns when the real `PluginContext`
integration is exercised (HERMES-05 or HERMES-06 smoke).

---

## Findings

### MEDIUM-01 -- `register()` still omits `check_fn` (required by real PluginContext)

**File:** `src/chatlytics_hermes/adapter.py:323-342`

```python
ctx.register_platform(
    name="chatlytics",
    label="Chatlytics WhatsApp",
    adapter_factory=lambda cfg: ChatlyticsAdapter(cfg),
    required_env=["CHATLYTICS_BASE_URL", "CHATLYTICS_API_KEY"],
    install_hint=(...),
    emoji="\U0001f4ac",
    platform_hint=(...),
)
```

`hermes_cli/plugins.py:613-624` (the real `PluginContext.register_platform`)
declares `check_fn: Callable` as a required positional-or-keyword
argument that is forwarded to the `PlatformEntry` dataclass. Today
`tests/test_register.py::MockCtx` accepts arbitrary kwargs, so this
gap is invisible at the unit-test layer. When HERMES-06 runs the real
hermes-runtime smoke (`hermes setup`), `register_platform(...)` will
raise `TypeError: missing 'check_fn'`.

Carried over from HERMES-01 review (MED-01-like concern) but worth
escalating: the longer this latent defect stays in place, the more
likely a release smoke surprise. Suggested minimum fix:

```python
ctx.register_platform(
    ...
    check_fn=lambda: True,  # adapter deps are stdlib + httpx (always available once installed)
    ...
)
```

`httpx` is a declared dependency in `pyproject.toml`, so `check_fn`
returning `True` unconditionally is honest -- the import has already
happened by the time `register()` is called.

**Disposition:** Carry to HERMES-05 (the first phase that meaningfully
extends `register()` for tool registration) or HERMES-06 (release
smoke). NOT a HERMES-02 blocker because the phase scope explicitly
locked `register()` byte-for-byte.

### MEDIUM-02 -- `tests/conftest.py` fixture seeds a global registry that may leak across test sessions

**File:** `tests/conftest.py:21-49`

```python
@pytest.fixture(scope="session", autouse=True)
def _register_chatlytics_platform():
    ...
    platform_registry.register(entry)
    yield
```

The fixture has no teardown -- the `chatlytics` entry stays registered
in `gateway.platform_registry.platform_registry` for the lifetime of
the python process. This is harmless for `pytest tests/` runs but
unusual for an `autouse=True` session fixture (most session fixtures
explicitly clean up).

If a future HERMES-03/04 test imports a different Hermes module that
also registers a `chatlytics` adapter (e.g., via plugin entry-point
discovery), the second `platform_registry.register()` will raise or
overwrite silently depending on the registry's collision policy. The
registry's behavior in this case was not audited.

**Suggested fix:** Add explicit cleanup:

```python
yield
try:
    platform_registry._entries.pop("chatlytics", None)
except (AttributeError, KeyError):
    pass
```

Or use the registry's documented unregister API if one exists. Worth
auditing once HERMES-05 introduces tool registration (which also seeds
process-global state in the same registry).

**Disposition:** Address in HERMES-03 if the inbound test suite
exercises a second `register()` path; defer otherwise.

### LOW-01 -- `send()` metadata merge silently drops keys named like reserved fields

**File:** `src/chatlytics_hermes/adapter.py:200-208`

```python
if metadata:
    for key, value in metadata.items():
        if key not in {"chatId", "text", "accountId", "replyTo"}:
            body[key] = value
```

The threat-model justification (T-HERMES-02-03 in PLAN) is correct --
preventing caller-driven chatId redirection. But the silent drop loses
diagnostic signal. A caller passing `metadata={"chatId": "wrong-id"}`
gets no warning; the message goes to the intended `chat_id` instead.

**Suggested fix:** Log a WARNING when a reserved key appears in
metadata:

```python
reserved = {"chatId", "text", "accountId", "replyTo"}
for key, value in metadata.items():
    if key in reserved:
        logger.warning("send(): metadata key %r is reserved; dropping", key)
    else:
        body[key] = value
```

**Disposition:** Cosmetic. Apply if HERMES-03 or HERMES-04 touches
`send()`, else defer.

### LOW-02 -- `send_typing` swallows non-200 + transport errors silently after first WARNING

**File:** `src/chatlytics_hermes/adapter.py:262-275`

```python
try:
    response = await self._client.post(...)
    if response.status_code != 200:
        logger.warning(...)
except httpx.RequestError as exc:
    logger.warning("send_typing transport error: %s", exc)
```

The plan called this out as intentional ("typing is a UX hint, not a
critical path"). However, `logger.warning` at every typing call --
which can fire every ~1s during streaming -- will flood logs if the
gateway is unhealthy. Either:

(a) downgrade to `logger.debug` on transport error (already covered by
the gateway's own circuit-breaker if/when HERMES-05 wires one), OR
(b) add a once-per-N-seconds rate limit (HERMES-04 `_keep_typing` will
need this anyway).

**Disposition:** Defer to HERMES-04 (`_keep_typing` heartbeat will need
the same rate-limit logic; consolidate then).

### LOW-03 -- `get_chat_info` returns `{}` on error, indistinguishable from "valid empty response"

**File:** `src/chatlytics_hermes/adapter.py:299-336`

A 503 from the gateway, a transport error, AND a legitimate `{}` JSON
body all return the same `{}` to the caller. Downstream code can't
distinguish "chat does not exist" from "gateway unreachable".

This matches `BasePlatformAdapter.get_chat_info`'s implicit contract
(no error-channel field in the return type — it's just `Dict[str, Any]`),
so the design is forced by upstream. But callers that rely on this
method for `is_user_authorized()` -style decisions (HERMES-05) should
note the ambiguity.

**Suggested fix:** Add a sentinel key on error paths:

```python
return {"_error": "transport", "_status": response.status_code if 'response' in locals() else None}
```

…or accept the upstream contract and document the ambiguity in the
docstring.

**Disposition:** Accept as-is (upstream contract). Document in
HERMES-05 when `is_user_authorized()` integration lands.

### LOW-04 -- `ChatlyticsClient.__init__` raises `ValueError` before storing fields, but `aclose()` later accesses `self._client`

**File:** `src/chatlytics_hermes/client.py:30-52`

If `httpx.AsyncClient(...)` raises (e.g., bad TLS config), `self._client`
is never assigned, but the `ValueError("non-empty base_url")` and
`ValueError("non-empty api_key")` guards above protect the common
misconfiguration paths. The latent failure path (httpx constructor
raising on a valid base_url) is exotic enough not to warrant a
try/except wrapping the constructor.

**Disposition:** Accept. Document as a "won't happen in practice" edge
case. No action.

---

## Strengths

1. **Acceptance coverage is rigorous.** Each of the 8 ROADMAP ACs has
   a dedicated test plus AC-8 (Bearer header) is asserted in every
   other test as a belt-and-suspenders cross-cutting check. The
   `test_all_requests_carry_bearer_auth` test iterates `mock_router.routes`
   and explicitly asserts `request_count == 4` to catch accidental
   request elision.

2. **Lifecycle cleanup is clean.** On connect failure (transport error
   OR non-200 status), `self._client` is `aclose()`d AND set to None
   before raising, so a retry from a clean slate is possible. This is
   the right shape for a long-lived adapter that may reconnect after
   gateway restarts.

3. **`SendResult.retryable` is set correctly on 5xx.** Transport errors
   and 5xx responses both set `retryable=True`; 4xx responses (caller
   error) set `retryable=False`. The base class's automatic-retry path
   (per `SendResult.retryable` docstring) will Just Work.

4. **`platform_registry` seeding via conftest is the right shape.**
   Discovering this gap during the first dockerized test run (vs in
   HERMES-05/06) caught the real-vs-mock-context divergence early.
   The session-scoped autouse fixture has minimal blast radius and
   does not pollute production code with test scaffolding.

5. **`metadata` reserved-key protection.** The `send()` body-merge
   logic explicitly fences `chatId`/`text`/`accountId`/`replyTo` from
   metadata override (T-HERMES-02-03 mitigation). The fence is
   exercised in code but NOT asserted in a test -- worth adding a
   negative test in HERMES-03/04 if you want full coverage.

6. **No `api_key` in logs.** Module-level `logger.debug("send -> ... chatId=%s len=%d")`
   shows endpoint and payload size but never headers or full bodies.
   The Authorization header is built once at `ChatlyticsClient.__init__`
   and never logged. T-HERMES-02-02 mitigated.

7. **Scope discipline maintained.** `register()` is byte-for-byte
   unchanged from HERMES-01. `test_register_does_not_declare_deferred_hooks`
   continues to pass after the HERMES-02 changes.

---

## Action items for HERMES-03

- Audit conftest fixture for cleanup once inbound webhook server tests
  start exercising `gateway.platform_registry` from a second angle
  (MED-02)
- Apply LOW-01 (metadata reserved-key WARNING) opportunistically if
  `send()` is touched again
- Consider sharing a per-test httpx mock router fixture between
  outbound + inbound suites

## Action items for HERMES-05

- Add `check_fn=lambda: True` to `register()` (MED-01)
- Document `get_chat_info` ambiguity in the docstring (LOW-03)

## Action items for HERMES-06 (release smoke)

- Validate real `PluginContext.register_platform` works end-to-end
  with HERMES-02's `register()` shape (will surface MED-01 if not
  addressed earlier)

---

## INFO findings

### INFO-01 -- `ChatlyticsClient.timeout` is float, not `httpx.Timeout` object

**File:** `src/chatlytics_hermes/client.py:39-49`

`httpx` accepts both `float` (uniform timeout for all phases) and
`httpx.Timeout(connect=..., read=..., write=..., pool=...)` (per-phase
timeouts). Using a single float means a slow gateway during JSON
streaming gets the same 30s window as the initial connect. Fine for
v2.0; HERMES-05/06 may want finer-grained control once streaming
edits land.

**Disposition:** Accept for HERMES-02.

### INFO-02 -- `_FakePlatformConfig` may drift from upstream `PlatformConfig`

**File:** `tests/test_outbound.py:30-48`

The test shim declares `enabled`, `token`, `api_key`, `home_channel`
as attributes the base class touches in `__init__`. If hermes-agent
v0.14.x adds new attributes that `BasePlatformAdapter.__init__`
references, the shim will need a corresponding update or tests will
break with AttributeError.

**Disposition:** Acceptable cost. The shim is intentionally minimal to
keep test surface decoupled. HERMES-06 release smoke should re-run
against the real `PlatformConfig` import to catch drift.

---

## Cross-references

- ROADMAP: `.planning/ROADMAP.md` (Phase 2 acceptance criteria 1-8)
- PLAN: `.planning/phases/HERMES-02-outbound-text-control-parity/02-01-PLAN.md`
- SUMMARY: `.planning/phases/HERMES-02-outbound-text-control-parity/02-01-SUMMARY.md`
- VERIFICATION: `.planning/phases/HERMES-02-outbound-text-control-parity/02-VERIFICATION.md`
- HERMES-01 review (forward action items disposition): `.planning/phases/HERMES-01-upstream-contract-scaffolding/01-REVIEW.md`
- Upstream `SendResult` contract: `/tmp/hermes-ref-v0.14.0/gateway/platforms/base.py:1037`
- Upstream `register_platform` requiring `check_fn`: `/tmp/hermes-ref-v0.14.0/hermes_cli/plugins.py:613`
- Upstream `Platform._missing_` dynamic enum: `/tmp/hermes-ref-v0.14.0/gateway/config.py:131`
