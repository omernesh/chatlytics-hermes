---
phase: 02-outbound-text-control-parity
plan: 01
status: complete
implemented_by: claude-opus-4-7-1m
completed: 2026-05-17
commits:
  - 1a5c747 — docs(02): write 02-01-PLAN.md for HERMES-02
  - f51b3d7 — feat(hermes-02): httpx client wrapper + auth/timeout plumbing
  - 5b49c94 — feat(hermes-02): connect/disconnect/send/send_typing/get_chat_info
  - 4670adf — test(hermes-02): 8 respx-mocked acceptance tests + platform-registry seed
---

# HERMES-02 Plan 01 -- Summary

## Outcome

All four tasks landed across four atomic commits. The
`ChatlyticsAdapter` outbound + control surface is fully implemented
against the v0.14.0 `BasePlatformAdapter` contract via a shared
`httpx.AsyncClient` instance managed by a new `ChatlyticsClient`
wrapper. 8 respx-mocked tests prove all 8 ROADMAP HERMES-02 acceptance
criteria; the existing 5 HERMES-01 tests continue to pass (no
regression). Total: 13 passed / 13 total in the dockerized clean-room.

## Per-task outcome

### Task 1: Thin httpx wrapper

Commit `f51b3d7`. Created `src/chatlytics_hermes/client.py`:

- `ChatlyticsClient(base_url, api_key, *, timeout=30.0, user_agent=...)`
- Sets `Authorization: Bearer {api_key}` as default header at construction
- `get(path, *, params)` and `post(path, *, json)` verbs only -- no
  PUT/DELETE/PATCH (scope discipline)
- Async-context-manager compatible (`__aenter__` / `__aexit__`)
- `aclose()` is idempotent; `is_closed` proxy exposed for test
  assertions

### Task 2: Adapter method implementations

Commit `5b49c94`. Modified `src/chatlytics_hermes/adapter.py`:

- Added `import httpx`, `import logging`, `from .client import ChatlyticsClient`
- New module-level `ChatlyticsConnectError(RuntimeError)` exception
- Added `self._client: Optional[ChatlyticsClient] = None` in `__init__`
- Replaced `connect()` stub: lazy client construction, `GET /health`
  preflight, raises `ChatlyticsConnectError` on non-200 or transport
  error
- Replaced `disconnect()` stub: idempotent client close, clears `_running`
- Replaced `send()` stub: `POST /api/v1/send` with
  `{chatId, text, accountId?, replyTo?, ...metadata}`, returns
  `SendResult(success=True/False)` with `messageId` + raw payload
- Added `send_typing(chat_id, metadata=None, duration=3.0)`: `POST /api/v1/typing`
- Added `get_chat_info(chat_id)`: `GET /api/v1/chat?chatId=...` -> dict
- Addressed HERMES-01 review LOW-02 (dropped redundant `"SendResult"`
  string-quote on `send()` return) and LOW-03
  (`config: Any` -> `config: "PlatformConfig"`)
- `register()` block byte-for-byte unchanged (scope discipline)

### Task 3: Acceptance tests + platform-registry seed

Commit `4670adf`. Created `tests/test_outbound.py` and `tests/conftest.py`:

- 8 `async def test_...` functions, one per ROADMAP acceptance criterion
- `respx` mocks all four Chatlytics endpoints
- `_FakePlatformConfig` shim provides the minimum `getattr(config, "extra")`
  surface the adapter touches -- decouples tests from upstream
  `PlatformConfig` field churn
- `tests/conftest.py` autouse session fixture seeds
  `gateway.platform_registry` with a chatlytics `PlatformEntry` so
  `Platform("chatlytics")` resolves inside the v0.14.0 `_missing_`
  dynamic-enum hook (chatlytics is not a bundled `plugins/platforms/`
  directory)

### Task 4: Dockerized acceptance run

No file changes. Verification only. See `02-VERIFICATION.md` for full
pytest output. 13 passed, 0 failed, 0 errors, 0 warnings in 0.40s.

## Acceptance criteria results

| AC | Test | Result |
|----|------|--------|
| 1 | `test_connect_succeeds_on_200_health` | PASS |
| 2 | `test_connect_raises_on_non_200_health` | PASS |
| 3 | `test_send_returns_ok_true_on_200` | PASS |
| 4 | `test_send_returns_ok_false_on_400` | PASS |
| 5 | `test_send_typing_calls_typing_endpoint` | PASS |
| 6 | `test_get_chat_info_returns_dict` | PASS |
| 7 | `test_disconnect_closes_client` | PASS |
| 8 | `test_all_requests_carry_bearer_auth` | PASS |

All 8 PASS. HERMES-01's 5 ACs also continue to pass (no regression).

## Deviations from plan

1. **Single combined commit for adapter changes.** The plan listed
   two separate commits ("connect/disconnect lifecycle" and
   "send/send_typing/get_chat_info"). Because the changes share the
   new `ChatlyticsConnectError` symbol, `_client` field, and httpx
   import, splitting them would have produced an intermediate state
   where tests fail. Combined them into one well-described commit
   (`5b49c94`).

2. **Added `tests/conftest.py` (not in the plan).** Plan did not
   anticipate the `Platform("chatlytics")` enum-resolution issue --
   only discovered during Task 4's dockerized run. Adding the
   autouse session fixture is the minimum-surface fix; no production
   code change was required.

3. **LOW-01 (`import sys`) was already addressed in HERMES-01.** Plan
   listed it as a HERMES-02 cleanup; verification showed it was never
   present in the final HERMES-01 commit. No action taken.

## Files touched (final list)

CREATED:
- `src/chatlytics_hermes/client.py`
- `tests/test_outbound.py`
- `tests/conftest.py`
- `.planning/phases/HERMES-02-outbound-text-control-parity/02-01-PLAN.md`
- `.planning/phases/HERMES-02-outbound-text-control-parity/02-01-SUMMARY.md` (this file)
- `.planning/phases/HERMES-02-outbound-text-control-parity/02-VERIFICATION.md`

MODIFIED:
- `src/chatlytics_hermes/adapter.py` (5 method implementations + `__init__`
  client field + LOW-02 + LOW-03)

DELETED: none.

## HERMES-01 forward action items disposition

- **LOW-01** (unused `import sys`): ALREADY-DONE in HERMES-01 a1fddeb
- **LOW-02** (redundant `"SendResult"` quote): DONE in commit 5b49c94
- **LOW-03** (`config: Any` -> `config: "PlatformConfig"`): DONE in 5b49c94
- **LOW-04** (lambda factory `__name__`): DEFERRED (cosmetic; matches IRC ref)
- **MED-01** (adapter_factory kwargs): DEFERRED to HERMES-05 (no behavior
  change in HERMES-02 register())
- **MED-02** (PYTHONPATH=src bare-import): DEFERRED to HERMES-06 README
- **INFO-01**, **INFO-02**: ACCEPT (informational)

## What's deferred (in scope for HERMES-03 onwards)

- aiohttp inbound webhook server inside `connect()` -- HERMES-03
- HMAC signature verification -- HERMES-03
- Per-request -> `MessageEvent` mapping -- HERMES-03
- Media handlers (`send_image`, `send_voice`, `send_video`,
  `send_document`, `send_animation`, `send_image_file`) -- HERMES-04
- `_keep_typing` 30s heartbeat -- HERMES-04
- `cron_deliver_env_var` + `standalone_sender_fn` -- HERMES-04
- `ctx.register_tool(...)` per Chatlytics action -- HERMES-05
- `check_fn` argument to `register_platform()` (real Hermes runtime
  requires it; MockCtx accepts the omission today) -- HERMES-05 or HERMES-06
- README / CHANGELOG rewrite + release smoke -- HERMES-06
