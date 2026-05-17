---
phase: 02-outbound-text-control-parity
status: passed
verified_by: claude-opus-4-7-1m
verified: 2026-05-17
acceptance_criteria_total: 8
acceptance_criteria_passed: 8
tests_total: 13
tests_passed: 13
---

# HERMES-02 -- Verification

## Status: PASSED

All 8 ROADMAP Phase 2 acceptance criteria pass in a clean
`python:3.13-slim` docker container with `hermes-agent` installed from
the GitHub tag `v2026.5.16` (the v0.14.0 "Foundation Release" tag).
HERMES-01's 5 ACs continue to pass (no regression).

## Acceptance criteria

### AC-1: connect succeeds on 200 health

Test: `tests/test_outbound.py::test_connect_succeeds_on_200_health`

`respx` mocks `GET /health` -> 200, `await adapter.connect()` returns
True and `adapter.is_connected` is True. Bearer header asserted.

Result: **PASS**.

### AC-2: connect raises on non-200 health

Test: `tests/test_outbound.py::test_connect_raises_on_non_200_health`

`respx` mocks `GET /health` -> 503, `await adapter.connect()` raises
`ChatlyticsConnectError`. Bearer header asserted on the failing
request. `adapter._client` is cleared so a retry from a clean slate is
possible.

Result: **PASS**.

### AC-3: send returns success=True on 200

Test: `tests/test_outbound.py::test_send_returns_ok_true_on_200`

`respx` mocks `POST /api/v1/send` -> 200 with
`{success: true, messageId: "msg-42"}`. `result.success is True`,
`result.message_id == "msg-42"`. Body shape verified
(`chatId`, `text`, `accountId`).

Note: upstream `SendResult` field is `success` (not `ok`). The
ROADMAP AC text uses `result.ok` colloquially; tests assert the actual
contract field `success`. No alias was added (would be HERMES-05 polish).

Result: **PASS**.

### AC-4: send returns success=False on 400

Test: `tests/test_outbound.py::test_send_returns_ok_false_on_400`

`respx` mocks `POST /api/v1/send` -> 400 with
`{success: false, error: "invalid chatId"}`. `result.success is False`,
`"invalid chatId" in result.error`. `result.raw_response` carries the
diagnostic payload.

Result: **PASS**.

### AC-5: send_typing posts to /api/v1/typing

Test: `tests/test_outbound.py::test_send_typing_calls_typing_endpoint`

`await adapter.send_typing(chat_id, duration=2.0)` POSTs
`/api/v1/typing` with body `{chatId: ..., duration: 2.0}`. Bearer
header asserted.

Result: **PASS**.

### AC-6: get_chat_info returns dict

Test: `tests/test_outbound.py::test_get_chat_info_returns_dict`

`respx` mocks `GET /api/v1/chat?chatId=...` -> 200 with
`{name: "Alice", phone: "+15551234", isGroup: false}`. The adapter
returns the dict verbatim. Bearer header + query param asserted.

Result: **PASS**.

### AC-7: disconnect closes the httpx client

Test: `tests/test_outbound.py::test_disconnect_closes_client`

After `await adapter.disconnect()`, the captured `client_ref.is_closed`
flips True and `adapter._client` is None. A second `disconnect()` call
is a no-op (idempotency proven).

Result: **PASS**.

### AC-8: every request carries Authorization: Bearer

Test: `tests/test_outbound.py::test_all_requests_carry_bearer_auth`
(plus inline assertions in AC-1..AC-6 tests)

Cross-cutting test exercises all four endpoints (health + send + typing +
get_chat_info) and iterates every captured `respx` call asserting
`request.headers["authorization"] == "Bearer test-api-key-abc123"`.
Sanity check: 4 requests total.

Result: **PASS**.

## Test surface

Dockerized run (Windows -> python:3.13-slim, hermes-agent from tag):

```
docker run --rm -v "D:/docker/chatlytics-hermes-split:/work" -w /work \
  python:3.13-slim sh -c "apt-get update -qq >/dev/null 2>&1 && \
    apt-get install -y -qq git >/dev/null 2>&1 && \
    pip install --quiet 'hermes-agent @ git+https://github.com/NousResearch/hermes-agent.git@v2026.5.16' && \
    pip install --quiet -e '.[dev]' && \
    pytest tests/ -v"
```

Output (last lines):

```
============================= test session starts ==============================
platform linux -- Python 3.13.13, pytest-9.0.3, pluggy-1.6.0 -- /usr/local/bin/python3.13
cachedir: .pytest_cache
rootdir: /work
configfile: pyproject.toml
plugins: anyio-4.13.0, asyncio-1.3.0, respx-0.23.1
asyncio: mode=Mode.AUTO, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collecting ... collected 13 items

tests/test_outbound.py::test_connect_succeeds_on_200_health PASSED       [  7%]
tests/test_outbound.py::test_connect_raises_on_non_200_health PASSED     [ 15%]
tests/test_outbound.py::test_send_returns_ok_true_on_200 PASSED          [ 23%]
tests/test_outbound.py::test_send_returns_ok_false_on_400 PASSED         [ 30%]
tests/test_outbound.py::test_send_typing_calls_typing_endpoint PASSED    [ 38%]
tests/test_outbound.py::test_get_chat_info_returns_dict PASSED           [ 46%]
tests/test_outbound.py::test_disconnect_closes_client PASSED             [ 53%]
tests/test_outbound.py::test_all_requests_carry_bearer_auth PASSED       [ 61%]
tests/test_register.py::test_register_is_callable PASSED                 [ 69%]
tests/test_register.py::test_register_adds_chatlytics_platform PASSED    [ 76%]
tests/test_register.py::test_register_does_not_declare_deferred_hooks PASSED [ 84%]
tests/test_register.py::test_plugin_yaml_is_valid PASSED                 [ 92%]
tests/test_register.py::test_pyproject_declares_hermes_entry_point PASSED [100%]

============================== 13 passed in 0.40s ==============================
```

13 passed, 0 failed, 0 errors, 0 warnings.

## Notable findings

1. **`Platform("chatlytics")` requires registry seeding.** The v0.14.0
   `Platform._missing_` hook only creates pseudo-members for bundled
   plugins (under `plugins/platforms/`) OR names registered in
   `gateway.platform_registry.platform_registry`. Since chatlytics is
   distributed as a third-party pip-installable plugin (not bundled into
   hermes-agent), the test suite seeds the registry via
   `tests/conftest.py` autouse session fixture. At runtime, Hermes's
   `PluginContext.register_platform` handles this transparently.

2. **`get_chat_info` is `@abstractmethod` in v0.14.0.** HERMES-01's
   skeleton omitted it; HERMES-02 adds the implementation. Without it,
   `ChatlyticsAdapter` could not be instantiated at all.

3. **`SendResult.success` (not `.ok`).** ROADMAP HERMES-02 AC text uses
   `result.ok` informally; the upstream dataclass field is `success`.
   No alias was added.

4. **`PluginContext.register_platform` requires `check_fn`.** The real
   hermes runtime requires a positional-after-`adapter_factory`
   `check_fn` argument that HERMES-01's `register()` does NOT provide.
   This is not currently exercised because `tests/test_register.py`
   uses a `MockCtx` that accepts arbitrary kwargs. This will surface
   in HERMES-06 release smoke if not addressed earlier; flagging here
   for HERMES-05 or HERMES-06 to add a `check_fn=lambda: True`.

## State of working tree

Clean. HERMES-02 artifacts (plan + code + tests + summary +
verification) are committed across 4 atomic commits:

- `1a5c747` -- docs(02): write 02-01-PLAN.md
- `f51b3d7` -- feat(hermes-02): httpx client wrapper + auth/timeout
- `5b49c94` -- feat(hermes-02): connect/disconnect/send/send_typing/get_chat_info
- `4670adf` -- test(hermes-02): 8 respx-mocked acceptance tests + platform-registry seed

## HERMES-01 forward-action-item disposition

| ID | Status | Notes |
|----|--------|-------|
| MED-01 (adapter_factory positional-vs-kw mismatch) | DEFERRED | IRC reference has same shape; track for HERMES-05 |
| MED-02 (PYTHONPATH=src bare-import workaround) | DEFERRED | Editable install (`pip install -e .`) covers all dev/CI paths; doc note in HERMES-06 README |
| LOW-01 (unused `import sys`) | ALREADY-DONE | Verified absent in `tests/test_register.py` (HERMES-01 commit a1fddeb already addressed) |
| LOW-02 (redundant `"SendResult"` quote) | DONE | Quote dropped on `send()` return annotation |
| LOW-03 (`config: Any` -> `config: "PlatformConfig"`) | DONE | Tightened in `__init__` signature |
| LOW-04 (lambda factory `__name__`) | DEFERRED | Cosmetic; matches IRC reference convention |
| INFO-01 (manifest `name` lacks `-platform` suffix) | ACCEPT | Conventional only; routing name is canonical |
| INFO-02 (`account_id` env-var declaration) | ACCEPT | Already in `optional_env` per HERMES-01 |

## Blockers

None.

## Next phase

HERMES-03 -- inbound transport migration. Will fill in the embedded
aiohttp webhook server inside `connect()` / `disconnect()`, the HMAC
signature verifier, and the per-request -> `MessageEvent` mapper.
