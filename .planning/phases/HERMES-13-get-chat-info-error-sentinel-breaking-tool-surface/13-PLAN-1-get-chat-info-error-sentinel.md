---
phase: 13
plan_index: 1
plan_slug: get-chat-info-error-sentinel
title: "get_chat_info _error sentinel (BREAKING tool surface)"
project_code: HERMES
milestone: v3.0
status: ready
infra_skip: true
verification: pytest
---

# HERMES-13 Plan 1 — `get_chat_info` `_error` sentinel

## Goal

Replace the v2.1 ambiguous bare-`{}` return on `ChatlyticsAdapter.get_chat_info`
with explicit three-way semantics:

- **Chat found** — adapter returns `dict` (chat payload as-is).
- **Chat-not-found legitimate empty** — adapter returns `None` (gateway
  responded 200 with empty chat field; currently unreachable but the
  code path is defined for forward-compat).
- **Transport / auth / server / validation error** — adapter raises
  `ChatlyticsLookupError(code: str, message: str)` where `code` is one
  of `transport_error | auth_error | server_error | validation_error |
  unknown_error`.

A new tool-layer wrapper translates the adapter contract into the
canonical `{"success": bool, ...}` shape, with error responses
additionally including `_error: "<code>"`. The wrapper is invoked
only by callers who want the tool-shape result (no new entry is added
to the registered `TOOLS` tuple — count stays at 21 per scope lock D5).

Closes v2.1 deferred item 1 (sentinel `_error` key on `get_chat_info`).

## Scope (locked per 13-CONTEXT.md)

**In:**
- `src/chatlytics_hermes/adapter.py` — new `ChatlyticsLookupError`,
  rewritten `get_chat_info`, updated docstring.
- `tests/test_outbound.py` — update v2.1 AC-6 assertion to new
  contract (with v3.0 CHANGELOG comment); add new tests for the null
  branch and each `_error` code.
- `tests/test_validation.py` — only the module docstring (line 10)
  mentions the v2.1 ambiguous semantics; update the docstring note so
  future readers do not assume the v2.1 contract still holds.
- `CHANGELOG.md` — append a single line under `## [Unreleased]`.

**Out:**
- JID regex tightening (Phase 14).
- Adapter `send_*` collapse (Phase 15).
- Version bumps in `pyproject.toml` / `plugin.yaml` (Phase 19).
- Pushing to git / publishing.
- Adding a new entry to the `TOOLS` tuple. The phase's wider error
  rollout to other handlers is v3.1.

## Invariants (DO NOT REGRESS)

- 88/88 v2.1 baseline tests still pass, except the explicitly-updated
  `get_chat_info` shape assertion (commented with v3.0 CHANGELOG
  reference).
- `assert len(TOOLS) == 21` invariant in `tools.py` stays satisfied.
- Hermes pin stays `>=0.14,<0.15`.
- All HTTP outbound via `httpx`.

## Tasks (atomic; each commits independently)

### T1 — Add `ChatlyticsLookupError` typed exception

**File:** `src/chatlytics_hermes/adapter.py`

Add a new exception class near `ChatlyticsConnectError`:

```python
class ChatlyticsLookupError(RuntimeError):
    """Raised by get_chat_info on transport / auth / server / validation errors.

    Carries a machine-readable ``code`` so the tool-layer wrapper can
    translate to ``{"success": false, "error": str, "_error": code}``
    without re-classifying the failure.

    Codes (lowercase snake_case):
    - ``transport_error`` — httpx.RequestError (network / timeout / DNS)
    - ``auth_error``      — HTTP 401 / 403
    - ``server_error``    — HTTP 5xx
    - ``validation_error``— HTTP 4xx other than 401/403 (incl. 404 for unknown JID)
    - ``unknown_error``   — non-JSON body on 2xx, unexpected raise
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
```

Acceptance:
- Import: `from chatlytics_hermes.adapter import ChatlyticsLookupError`
  works.
- `exc = ChatlyticsLookupError("auth_error", "401")` → `exc.code == "auth_error"`.

### T2 — Rewrite `ChatlyticsAdapter.get_chat_info`

**File:** `src/chatlytics_hermes/adapter.py`

Replace the existing `get_chat_info` (currently returns `dict` always,
swallowing all errors as `{}`) with the new three-way contract:

```python
async def get_chat_info(self, chat_id: str) -> Optional[Dict[str, Any]]:
    """GET /api/v1/chat?chatId={id}.

    Returns:
        - ``dict`` — chat-found, gateway 200 with a JSON object payload.
        - ``None`` — chat-not-found legitimate empty: gateway 200 with
          a falsy / non-dict body (forward-compat code path).

    Raises:
        ChatlyticsLookupError — on transport / auth / server /
        validation errors. ``.code`` is one of:
        ``transport_error | auth_error | server_error |
        validation_error | unknown_error``.

        Notably, **404 from the gateway is `validation_error`** (the
        JID was malformed or unknown — NOT a "legitimate empty"). The
        legitimate-empty branch is reserved for 200 + empty body.

    The adapter does NOT validate the schema of a non-empty 200
    payload beyond ``isinstance(payload, dict)``.

    See v3.0 CHANGELOG entry "BREAKING — get_chat_info return shape"
    for migration guidance; the v2.1 bare-`{}` return on errors is
    gone.
    """
    if self._client is None:
        raise ChatlyticsLookupError(
            "unknown_error",
            "Adapter not connected: call connect() before get_chat_info()",
        )

    try:
        response = await self._client.get(
            "/api/v1/chat",
            params={"chatId": chat_id},
        )
    except httpx.RequestError as exc:
        logger.warning("get_chat_info transport error: %s", exc)
        raise ChatlyticsLookupError(
            "transport_error", f"Transport error: {exc}"
        ) from exc

    status = response.status_code
    if status in (401, 403):
        logger.warning("get_chat_info auth error %s for chat %s", status, chat_id)
        raise ChatlyticsLookupError(
            "auth_error", f"Authentication error: HTTP {status}"
        )
    if 500 <= status < 600:
        logger.warning("get_chat_info server error %s for chat %s", status, chat_id)
        raise ChatlyticsLookupError(
            "server_error", f"Server error: HTTP {status}"
        )
    if 400 <= status < 500:
        # 404-from-gateway for an unknown chatId is validation_error per
        # the v3.0 contract (NOT a legitimate empty).
        logger.warning(
            "get_chat_info validation error %s for chat %s", status, chat_id
        )
        raise ChatlyticsLookupError(
            "validation_error", f"Validation error: HTTP {status}"
        )
    # 2xx path.
    try:
        payload = response.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "get_chat_info JSON decode failed on 2xx response: %s", exc
        )
        raise ChatlyticsLookupError(
            "unknown_error", f"Malformed JSON in 2xx response: {exc}"
        ) from exc

    if not payload:
        # Legitimate empty: gateway 200 with falsy body (None, {}, []).
        return None
    if not isinstance(payload, dict):
        # 2xx with non-dict body — treat as malformed.
        raise ChatlyticsLookupError(
            "unknown_error",
            f"Expected dict payload, got {type(payload).__name__}",
        )
    return payload
```

Acceptance:
- All branches behave as documented (verified by T6 tests).
- Log levels match the existing convention (WARNING on operator-actionable
  error paths).

### T3 — Add tool-layer wrapper `chatlytics_get_chat_info`

**File:** `src/chatlytics_hermes/tools.py`

The phase scope lock (D5 in CONTEXT.md) says **count stays at 21**, so
the new wrapper is exported as a module-level coroutine **but NOT added
to the `TOOLS` tuple**. It exists for callers who want the canonical
`{"success": bool, ...}` shape with `_error` sentinel — including the
new tests in T6.

Add near the existing handlers, BEFORE the `TOOLS` tuple:

```python
async def chatlytics_get_chat_info(
    client: ChatlyticsClient,
    *,
    adapter: Any = None,
    chatId: str,
) -> Dict[str, Any]:
    """Tool-layer wrapper for adapter.get_chat_info with _error sentinel.

    Not registered in TOOLS (scope-locked at 21 tools for HERMES-13);
    exposed as a callable for in-plugin callers and tests that want
    the canonical {"success": bool, ...} shape with the v3.0 _error
    machine code.

    Return shapes:
        - {"success": True, "chat": {...}}     # chat found
        - {"success": True, "chat": None}      # legitimate empty
        - {"success": False, "error": "...",   # any error
           "_error": "<code>"}                 #   code per ChatlyticsLookupError
    """
    # Local import keeps the adapter <-> tools dependency direction
    # consistent with the existing module-load pattern (tools.py
    # already imports _coerce_success_payload from adapter.py).
    from .adapter import ChatlyticsLookupError

    if adapter is None:
        return {
            "success": False,
            "error": "chatlytics_get_chat_info requires a live adapter",
            "_error": "unknown_error",
        }
    try:
        chat = await adapter.get_chat_info(chatId)
    except ChatlyticsLookupError as exc:
        return {
            "success": False,
            "error": exc.message,
            "_error": exc.code,
        }
    return {"success": True, "chat": chat}
```

Note: the wrapper takes `adapter` (required) instead of `client` for
the lookup since the new contract lives on the adapter; the `client`
positional is kept for signature parity with the other handlers. The
`handler_takes_adapter()` introspection in `adapter.py:_make_tool_handler`
will route the adapter into the wrapper automatically IF it were
registered — keeping the signature consistent with the rest of the
module simplifies a future v3.1 minor that adds it to `TOOLS`.

Acceptance:
- `from chatlytics_hermes.tools import chatlytics_get_chat_info` works.
- `assert len(TOOLS) == 21` still holds (no new entry added).

### T4 — Update v2.1 test assertion (AC-6) in `tests/test_outbound.py`

**File:** `tests/test_outbound.py`

Update `test_get_chat_info_returns_dict` so the success-path assertion
matches the new contract (dict-on-200-with-payload still works; the
test was already shape-correct for that branch). Add the v3.0 CHANGELOG
comment explicitly.

Find:
```python
# --- AC-6: get_chat_info returns dict ---------------------------------

async def test_get_chat_info_returns_dict(
    adapter: ChatlyticsAdapter, mock_router: respx.MockRouter
) -> None:
    mock_router.get("/health").mock(return_value=httpx.Response(200, json={}))
    chat_route = mock_router.get("/api/v1/chat").mock(
        return_value=httpx.Response(
            200, json={"name": "Alice", "phone": "+15551234", "isGroup": False}
        )
    )
    await adapter.connect()
    info = await adapter.get_chat_info(CHAT_ID)
    assert isinstance(info, dict)
    assert info["name"] == "Alice"
    assert info["phone"] == "+15551234"
    assert info["isGroup"] is False
    # AC-8.
    assert chat_route.calls.last.request.headers.get("authorization") == EXPECTED_AUTH
    # Query param.
    assert chat_route.calls.last.request.url.params["chatId"] == CHAT_ID
    await adapter.disconnect()
```

Annotate the test with the v3.0 contract note (the assertion body
itself stays valid — chat-found still returns a dict):

```python
# --- AC-6: get_chat_info returns dict (HERMES-13 contract preserved) ---
#
# v3.0 HERMES-13 (BREAKING — see CHANGELOG entry "BREAKING —
# get_chat_info return shape"): the chat-found branch still returns
# a dict, but the error / empty branches changed (chat-not-found is
# now None; errors raise ChatlyticsLookupError). This test exercises
# only the success branch; new branches are covered in test_get_chat_info_*
# below.
```

Also update the AC-8 cross-cutting test (`test_all_requests_carry_bearer_auth`)
to ensure the mocked `/api/v1/chat` 200 response carries a valid dict
payload (it already does — `{"name": "X"}`), so no behavioral change is
needed there; just verify the assertion still passes after T2 lands.

Acceptance:
- `pytest tests/test_outbound.py -k get_chat_info` passes.
- `pytest tests/test_outbound.py -k all_requests` passes (no shape change).

### T5 — Update `tests/test_validation.py` docstring note

**File:** `tests/test_validation.py:9-12`

Find:
```python
Total: 19 tests. All other Phase 10 deliverables are docstring-only
(``get_chat_info`` empty-vs-error semantics in adapter.py docstring;
``send_image`` / ``send_image_file`` cross-reference; tool-layer
``chatlytics_send_image`` docstring) and verified by code review.
```

Replace the `get_chat_info` clause with a v3.0 cross-reference:

```python
Total: 19 tests. All other Phase 10 deliverables are docstring-only
(``get_chat_info`` empty-vs-error semantics — SUPERSEDED in v3.0
HERMES-13, now raises ``ChatlyticsLookupError`` with ``_error`` code,
see CHANGELOG; ``send_image`` / ``send_image_file`` cross-reference;
tool-layer ``chatlytics_send_image`` docstring) and verified by code
review.
```

Acceptance:
- No test logic changes.
- `pytest tests/test_validation.py -q` still passes (zero behavior change).

### T6 — Add new branch-coverage tests

**File:** `tests/test_outbound.py` (append at end)

Add tests that exercise every new branch of the contract. Use respx
for HTTP mocks (matches existing test style) and `from chatlytics_hermes.adapter
import ChatlyticsLookupError`.

```python
# --- HERMES-13: get_chat_info three-way contract -----------------------
#
# v3.0 BREAKING — see CHANGELOG entry "BREAKING — get_chat_info return
# shape". The adapter now returns dict|None and raises
# ChatlyticsLookupError on error paths with a machine-readable .code.
#
# Branches covered:
# 1. 200 + dict payload -> dict (already covered by AC-6 above)
# 2. 200 + falsy/empty payload -> None (legitimate empty)
# 3. transport error -> raises ChatlyticsLookupError(code='transport_error')
# 4. 401 -> raises ChatlyticsLookupError(code='auth_error')
# 5. 403 -> raises ChatlyticsLookupError(code='auth_error')
# 6. 500 -> raises ChatlyticsLookupError(code='server_error')
# 7. 404 -> raises ChatlyticsLookupError(code='validation_error')
#    (404 from gateway for an unknown chatId is validation, NOT empty)
# 8. tool-layer wrapper returns {success: True, chat: {...}} on found
# 9. tool-layer wrapper returns {success: True, chat: None} on empty
# 10. tool-layer wrapper returns {success: False, _error: <code>} on error


async def test_get_chat_info_returns_none_on_legitimate_empty(
    adapter: ChatlyticsAdapter, mock_router: respx.MockRouter
) -> None:
    """200 + falsy payload (None, {}, []) -> adapter returns None (chat-not-found)."""
    from chatlytics_hermes.adapter import ChatlyticsLookupError  # noqa: F401

    mock_router.get("/health").mock(return_value=httpx.Response(200, json={}))
    mock_router.get("/api/v1/chat").mock(
        return_value=httpx.Response(200, json=None)
    )
    await adapter.connect()
    result = await adapter.get_chat_info(CHAT_ID)
    assert result is None
    await adapter.disconnect()


async def test_get_chat_info_raises_transport_error(
    adapter: ChatlyticsAdapter, mock_router: respx.MockRouter
) -> None:
    """httpx.RequestError on the chat call -> ChatlyticsLookupError('transport_error')."""
    from chatlytics_hermes.adapter import ChatlyticsLookupError

    mock_router.get("/health").mock(return_value=httpx.Response(200, json={}))
    mock_router.get("/api/v1/chat").mock(
        side_effect=httpx.ConnectError("connection refused")
    )
    await adapter.connect()
    with pytest.raises(ChatlyticsLookupError) as excinfo:
        await adapter.get_chat_info(CHAT_ID)
    assert excinfo.value.code == "transport_error"
    await adapter.disconnect()


async def test_get_chat_info_raises_auth_error_on_401(
    adapter: ChatlyticsAdapter, mock_router: respx.MockRouter
) -> None:
    from chatlytics_hermes.adapter import ChatlyticsLookupError

    mock_router.get("/health").mock(return_value=httpx.Response(200, json={}))
    mock_router.get("/api/v1/chat").mock(
        return_value=httpx.Response(401, json={"error": "unauthorized"})
    )
    await adapter.connect()
    with pytest.raises(ChatlyticsLookupError) as excinfo:
        await adapter.get_chat_info(CHAT_ID)
    assert excinfo.value.code == "auth_error"
    await adapter.disconnect()


async def test_get_chat_info_raises_auth_error_on_403(
    adapter: ChatlyticsAdapter, mock_router: respx.MockRouter
) -> None:
    from chatlytics_hermes.adapter import ChatlyticsLookupError

    mock_router.get("/health").mock(return_value=httpx.Response(200, json={}))
    mock_router.get("/api/v1/chat").mock(
        return_value=httpx.Response(403, json={"error": "forbidden"})
    )
    await adapter.connect()
    with pytest.raises(ChatlyticsLookupError) as excinfo:
        await adapter.get_chat_info(CHAT_ID)
    assert excinfo.value.code == "auth_error"
    await adapter.disconnect()


async def test_get_chat_info_raises_server_error_on_500(
    adapter: ChatlyticsAdapter, mock_router: respx.MockRouter
) -> None:
    from chatlytics_hermes.adapter import ChatlyticsLookupError

    mock_router.get("/health").mock(return_value=httpx.Response(200, json={}))
    mock_router.get("/api/v1/chat").mock(
        return_value=httpx.Response(500, json={"error": "internal"})
    )
    await adapter.connect()
    with pytest.raises(ChatlyticsLookupError) as excinfo:
        await adapter.get_chat_info(CHAT_ID)
    assert excinfo.value.code == "server_error"
    await adapter.disconnect()


async def test_get_chat_info_raises_validation_error_on_404(
    adapter: ChatlyticsAdapter, mock_router: respx.MockRouter
) -> None:
    """404 from gateway -> validation_error (unknown JID), NOT legitimate empty."""
    from chatlytics_hermes.adapter import ChatlyticsLookupError

    mock_router.get("/health").mock(return_value=httpx.Response(200, json={}))
    mock_router.get("/api/v1/chat").mock(
        return_value=httpx.Response(404, json={"error": "not found"})
    )
    await adapter.connect()
    with pytest.raises(ChatlyticsLookupError) as excinfo:
        await adapter.get_chat_info(CHAT_ID)
    assert excinfo.value.code == "validation_error"
    await adapter.disconnect()


async def test_tool_wrapper_returns_success_true_with_chat_on_found(
    adapter: ChatlyticsAdapter, mock_router: respx.MockRouter
) -> None:
    """Tool-layer wrapper: chat-found -> {success: True, chat: {...}}."""
    from chatlytics_hermes.tools import chatlytics_get_chat_info

    mock_router.get("/health").mock(return_value=httpx.Response(200, json={}))
    mock_router.get("/api/v1/chat").mock(
        return_value=httpx.Response(
            200, json={"name": "Alice", "isGroup": False}
        )
    )
    await adapter.connect()
    result = await chatlytics_get_chat_info(
        adapter.client, adapter=adapter, chatId=CHAT_ID
    )
    assert result == {
        "success": True,
        "chat": {"name": "Alice", "isGroup": False},
    }
    await adapter.disconnect()


async def test_tool_wrapper_returns_success_true_with_null_on_empty(
    adapter: ChatlyticsAdapter, mock_router: respx.MockRouter
) -> None:
    """Tool-layer wrapper: legitimate empty -> {success: True, chat: None}.

    HERMES-13 NEW ASSERTION #1 (per phase brief): explicit null branch.
    """
    from chatlytics_hermes.tools import chatlytics_get_chat_info

    mock_router.get("/health").mock(return_value=httpx.Response(200, json={}))
    mock_router.get("/api/v1/chat").mock(
        return_value=httpx.Response(200, json=None)
    )
    await adapter.connect()
    result = await chatlytics_get_chat_info(
        adapter.client, adapter=adapter, chatId=CHAT_ID
    )
    assert result == {"success": True, "chat": None}
    await adapter.disconnect()


async def test_tool_wrapper_returns_error_with_underscore_error_on_500(
    adapter: ChatlyticsAdapter, mock_router: respx.MockRouter
) -> None:
    """Tool-layer wrapper: 5xx -> {success: False, _error: 'server_error'}.

    HERMES-13 NEW ASSERTION #2 (per phase brief): explicit _error sentinel
    on the error branch.
    """
    from chatlytics_hermes.tools import chatlytics_get_chat_info

    mock_router.get("/health").mock(return_value=httpx.Response(200, json={}))
    mock_router.get("/api/v1/chat").mock(
        return_value=httpx.Response(500, json={"error": "boom"})
    )
    await adapter.connect()
    result = await chatlytics_get_chat_info(
        adapter.client, adapter=adapter, chatId=CHAT_ID
    )
    assert result["success"] is False
    assert result["_error"] == "server_error"
    assert "error" in result and isinstance(result["error"], str)
    await adapter.disconnect()


async def test_tool_wrapper_returns_validation_error_on_404(
    adapter: ChatlyticsAdapter, mock_router: respx.MockRouter
) -> None:
    """Tool-layer wrapper: 404 -> {success: False, _error: 'validation_error'}.

    Explicit coverage of the 404-disambiguation rule (404 from gateway is
    a malformed/unknown JID, NOT a chat-not-found legitimate empty).
    """
    from chatlytics_hermes.tools import chatlytics_get_chat_info

    mock_router.get("/health").mock(return_value=httpx.Response(200, json={}))
    mock_router.get("/api/v1/chat").mock(
        return_value=httpx.Response(404, json={"error": "not found"})
    )
    await adapter.connect()
    result = await chatlytics_get_chat_info(
        adapter.client, adapter=adapter, chatId=CHAT_ID
    )
    assert result["success"] is False
    assert result["_error"] == "validation_error"
    await adapter.disconnect()
```

Acceptance:
- `pytest tests/test_outbound.py -q` passes (88/88 baseline + 10 new
  tests = 98 in test_outbound.py + the rest of the suite). Total
  count grows by 10.

### T7 — Append CHANGELOG entry

**File:** `CHANGELOG.md`

Append a single line under an `## [Unreleased]` section (create the
section if it does not exist). Format matches the existing
release-entry style.

```markdown
## [Unreleased]

### Breaking
- `ChatlyticsAdapter.get_chat_info` now returns `dict | None` and raises
  `ChatlyticsLookupError(code, message)` on transport/auth/server/validation
  errors. The v2.1 bare-`{}` return on errors is gone. Tool-layer callers
  receive `{success: bool, ...}` with error responses additionally including
  `_error: "<machine_code>"`. Codes: `transport_error`, `auth_error`,
  `server_error`, `validation_error`, `unknown_error`. 404-from-gateway for
  an unknown chatId is `validation_error` (NOT a legitimate empty). See
  HERMES-13 phase docs for migration. Closes v2.1 deferred item 1.
```

Acceptance:
- `CHANGELOG.md` has the `[Unreleased] → ### Breaking` block.
- No release-line bumps (Phase 19 owns 3.0.0 release).

## Verification

After all tasks land:

```bash
cd D:/docker/chatlytics-hermes-split
python -m pytest tests/ -q
```

Expected: 88 baseline + 10 new = 98 passing tests, zero regressions.
Tool-count assertion (`assert len(TOOLS) == 21`) still passes.

Sanity import:
```bash
python -c "from chatlytics_hermes.adapter import ChatlyticsLookupError; print(ChatlyticsLookupError.__name__)"
python -c "from chatlytics_hermes.tools import chatlytics_get_chat_info; print(chatlytics_get_chat_info.__name__)"
```

Both should print the symbol name without ImportError.

## Risks + mitigations

| Risk | Mitigation |
|------|------------|
| Test count drift triggers Phase 18 cosmetic sweep audit | Acceptable — phase brief explicitly says "Add at least 2 NEW assertions". We add 10 for full coverage; STATE.md baseline updates from 88 to 98. |
| `assert len(TOOLS) == 21` accidentally bumped | T3 explicitly does NOT add the wrapper to `TOOLS`. Verification step re-runs the registration test. |
| Existing callers (e.g. external Hermes gateway code) relying on v2.1 `{}` return crash | This is the intended breaking change. CHANGELOG entry documents migration. No internal callers exist beyond tests. |
| Log volume regression (new WARNINGs on auth/server/validation paths) | Acceptable — these are operator-actionable failures, matching the v2.1 `get_chat_info returned %s` WARNING that already existed. Net log-level change is neutral. |

## Commit plan

One commit per task (T1..T7), each via `gsd-sdk query commit`. Suggested
messages:

- T1: `feat(13): add ChatlyticsLookupError typed exception`
- T2: `feat(13)!: rewrite get_chat_info with three-way contract`
- T3: `feat(13): add chatlytics_get_chat_info tool-layer wrapper`
- T4: `test(13): annotate AC-6 with v3.0 CHANGELOG cross-ref`
- T5: `docs(13): update test_validation.py docstring for v3.0 contract`
- T6: `test(13): add branch-coverage tests for get_chat_info contract`
- T7: `docs(13)!: changelog Unreleased breaking entry for get_chat_info`

The `!` marker in T2 and T7 commits signals the breaking change per
conventional-commits convention.
