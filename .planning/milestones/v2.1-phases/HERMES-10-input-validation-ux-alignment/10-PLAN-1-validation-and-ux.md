# Phase 10 — Plan 1: Input validation + UX alignment

**Status:** Ready for execute
**Phase:** HERMES-10 (Input validation + UX alignment)
**Depends on:** HERMES-09 (observability landed; new validation paths can rely on the established log conventions)

## Objective

Close the carry-forward v2.0 audit lows + the two PR-style review MED/LOW items
that all sit at the input-validation / API-shape boundary:

1. Validate `webhook_path` at `ChatlyticsAdapter.__init__` — fail fast on invalid config (closes 03-LOW-01 + PR-MED-01).
2. Tighten media-tool `chatId` / `messageId` schema validation — reject empty strings and control chars only (cosmetic UX, NOT strict JID enforcement; closes 05-LOW-02 + PR-MED-01).
3. Align `chatlytics_login` semantics with the Claude Code MCP bundle — `success=False` when `webhook_registered !== True` (closes 05-LOW-03 + PR-LOW-03).
4. Document `get_chat_info` `{}`-vs-error semantics (closes 02-LOW-03 — doc-only).
5. Document the `send_image` / `send_image_file` API-shape split and the tool-layer dispatch (closes PR-LOW-06 — doc-only).

## Files to change

| File | Change |
|---|---|
| `src/chatlytics_hermes/adapter.py` | (a) add module-level `_validate_webhook_path` helper; (b) call it from `__init__` after `self.webhook_path` assignment; (c) tighten `get_chat_info` docstring documenting the four `{}` paths; (d) cross-reference `send_image`/`send_image_file` docstrings |
| `src/chatlytics_hermes/tools.py` | (a) add `_chat_id_field` + `_message_id_field` schema helpers; (b) apply helpers to 15 chatId-bearing + 6 messageId-bearing schemas; (c) rewrite `chatlytics_login` body to return `success=False` on `webhook_registered !== True`; (d) add docstring on `chatlytics_send_image` tool explaining adapter-layer split |
| `tests/test_validation.py` | NEW — 19 tests (8 init validation + 6 schema validation + 5 login semantics) |

## Wave plan

All edits are independent within their own file but the tools.py changes are
internal to two distinct subsystems (schema helpers + login rewrite). Execute
in 3 waves: schema helpers + adapter validation in parallel (no inter-edit
dependency), login rewrite next, tests last.

### Wave 1 — Adapter validation + schema helpers (parallel-safe)

**1.1** `src/chatlytics_hermes/adapter.py` — add `_CONTROL_CHARS` module constant and `_validate_webhook_path(path: str) -> None` helper near the top of the file (after `_coerce_success_payload`, before the `try: from gateway.platforms.base import ...` block — keep helpers grouped). Implementation per CONTEXT specifics.

**1.2** `src/chatlytics_hermes/adapter.py` — in `ChatlyticsAdapter.__init__`, after the `self.webhook_path = os.getenv(...) or extra.get(...)` assignment (line ~185-187), add a `_validate_webhook_path(self.webhook_path)` call. This is the fail-fast point.

**1.3** `src/chatlytics_hermes/adapter.py` — tighten `get_chat_info` docstring (line 522-561). Replace the existing docstring with a precise four-bullet contract:

```
GET /api/v1/chat?chatId={id} and return the JSON body as a dict.

Returns ``{}`` in four distinct paths -- callers cannot distinguish
from the return value alone; consult logs for the cause:

1. Adapter not connected (``self._client is None``) -- no log; this
   indicates a programmer error (called before ``connect()``).
2. Transport error (``httpx.RequestError``) -- WARNING log
   ``"get_chat_info transport error: ..."``.
3. Non-200 response from the gateway -- WARNING log
   ``"get_chat_info returned <status> for chat <id>"``.
4. Malformed JSON body or non-dict payload -- DEBUG log
   ``"get_chat_info JSON decode failed; returning {}"``.

When the gateway returns 200 with a valid JSON object, the dict is
returned as-is and is expected to contain ``name``, ``phone``,
``isGroup``, etc. per the Chatlytics gateway contract. The adapter
does NOT validate the schema beyond ``isinstance(payload, dict)``.

Future v2.2+ may introduce a richer return shape (``{success: bool,
chat: dict | None, error: str | None}``) to distinguish these paths
without log inspection; that is a breaking change and is out of
scope for v2.1.
```

**1.4** `src/chatlytics_hermes/adapter.py` — append cross-reference paragraph to `send_image` docstring (after line 800):

```
Companion method :meth:`send_image_file` exists for backwards-compat
with the v1.x API surface that exposed a path-only variant; v2.0
preserved both. Both methods accept ``Union[str, bytes, bytearray]``
internally and route through the same ``_send_media_payload`` so
behavior is identical given equivalent inputs. New code should use
``send_image`` (URL-or-bytes-or-path Union shape) and treat
``send_image_file`` as a legacy alias.
```

**1.5** `src/chatlytics_hermes/adapter.py` — append cross-reference paragraph to `send_image_file` docstring (after line 911):

```
Companion to :meth:`send_image`. v2.0 retained both methods for
v1.x API-shape backwards-compat; new code may prefer ``send_image``
since it accepts the same ``Union[str, bytes, bytearray]`` shape.
Both methods share the same ``_send_media_payload`` body, so they
return identical ``SendResult`` shapes given equivalent inputs.
```

**1.6** `src/chatlytics_hermes/tools.py` — add `_CHAT_ID_PATTERN` module constant + `_chat_id_field(description)` and `_message_id_field(description)` helpers near the top of the schema section (above the `SEND_SCHEMA = ...` block). Implementation per CONTEXT specifics.

**1.7** `src/chatlytics_hermes/tools.py` — replace every `"chatId": {"type": "string", ...}` schema property with `"chatId": _chat_id_field(<description>)` across all 15 schemas. Where the existing description differs from the helper default, pass the existing description.

**1.8** `src/chatlytics_hermes/tools.py` — same for every `"messageId": {"type": "string"}` across REACT/EDIT/UNSEND/PIN/UNPIN/DELETE schemas (6 schemas).

### Wave 2 — chatlytics_login semantics rewrite

**2.1** `src/chatlytics_hermes/tools.py` — rewrite `chatlytics_login` (line 794-812) per CONTEXT specifics. Key invariant: when `webhook_registered !== True`, return `success=False` with `error` describing the state. When `True`, return `success=True`. When `_get` itself fails (transport / non-200), pass the failure through unchanged.

**2.2** `src/chatlytics_hermes/tools.py` — update `LOGIN_SCHEMA` description to reflect the new semantics:

```python
LOGIN_SCHEMA: Dict[str, Any] = {
    ...
    "description": (
        "Validate the configured API key and webhook registration via /health. "
        "Returns success=True only when webhook_registered is True (matches "
        "Claude Code MCP bundle semantics). On webhook_registered=False the tool "
        "returns success=False with the webhook_registered + sessions fields for "
        "diagnostic visibility."
    ),
    ...
}
```

**2.3** `src/chatlytics_hermes/tools.py` — add docstring to `chatlytics_send_image` tool handler (line 653-671) explaining the adapter-layer split:

```
"""Send an image via the Chatlytics gateway.

Dispatches to ``adapter.send_image(url)`` when ``mediaUrl`` is set,
or ``adapter.send_image_file(path)`` when only ``filePath`` is set.
The adapter retains the URL-vs-path split for v2.0 surface compat
(PR-review LOW-06); the tool layer offers a single unified entry
point so MCP / Claude Code users see one consistent
``chatlytics_send_image`` call shape regardless of source.
"""
```

### Wave 3 — Tests

**3.1** `tests/test_validation.py` (NEW) — 19 tests covering all 5 fixes. Test file structure:

```python
"""Phase 10 tests: input validation + UX alignment.

Covers:
- __init__ validation of webhook_path (8 tests)
- Tool schema validation of chatId / messageId (6 tests)
- chatlytics_login MCP-aligned semantics (5 tests)
"""

from __future__ import annotations

import json as _json
import os
from typing import Any, Dict

import httpx
import jsonschema
import pytest
import respx

from chatlytics_hermes.adapter import ChatlyticsAdapter
from chatlytics_hermes.client import ChatlyticsClient
from chatlytics_hermes.tools import (
    SEND_SCHEMA,
    REACT_SCHEMA,
    SEND_IMAGE_SCHEMA,
    chatlytics_login,
)

pytestmark = pytest.mark.asyncio

BASE_URL = "https://gateway.test.chatlytics.ai"
API_KEY = "test-api-key-validation"


class _FakePlatformConfig:
    """Minimal PlatformConfig stand-in matching tests/test_outbound.py."""
    def __init__(self, extra: Dict[str, Any]) -> None:
        self.extra = extra
        self.enabled = True
        self.token = None
        self.api_key = extra.get("api_key")
        self.home_channel = extra.get("home_channel")
```

**3.2** Tests 1-8: `__init__` validation. Pattern:

```python
def test_init_rejects_empty_webhook_path(monkeypatch):
    monkeypatch.delenv("CHATLYTICS_WEBHOOK_PATH", raising=False)
    cfg = _FakePlatformConfig(extra={
        "base_url": BASE_URL,
        "api_key": API_KEY,
        "webhook_path": "",
    })
    with pytest.raises(ValueError, match="non-empty"):
        ChatlyticsAdapter(cfg)
```

Repeat shape for: no-leading-slash ("webhook"), equals-/health, control-char (`/web\nhook`), traversal (`/../etc`), query string (`/webhook?x=1`), valid path (no raise), default (no override → no raise).

**3.3** Tests 9-14: schema validation. Pattern:

```python
def test_media_chat_id_rejects_empty_string():
    validator = jsonschema.Draft202012Validator(SEND_IMAGE_SCHEMA)
    with pytest.raises(jsonschema.ValidationError):
        validator.validate({"chatId": "", "mediaUrl": "https://example.com/a.png"})

def test_media_chat_id_accepts_jid_format():
    validator = jsonschema.Draft202012Validator(SEND_IMAGE_SCHEMA)
    validator.validate({"chatId": "1234567890@c.us", "mediaUrl": "https://example.com/a.png"})

def test_media_chat_id_accepts_phone_number():
    validator = jsonschema.Draft202012Validator(SEND_IMAGE_SCHEMA)
    validator.validate({"chatId": "+1234567890", "mediaUrl": "https://example.com/a.png"})

def test_media_chat_id_accepts_group_name():
    validator = jsonschema.Draft202012Validator(SEND_IMAGE_SCHEMA)
    validator.validate({"chatId": "My Group Name", "mediaUrl": "https://example.com/a.png"})

def test_media_chat_id_rejects_control_chars():
    validator = jsonschema.Draft202012Validator(SEND_IMAGE_SCHEMA)
    with pytest.raises(jsonschema.ValidationError):
        validator.validate({"chatId": "abc\x00def", "mediaUrl": "https://example.com/a.png"})

def test_messaging_chat_id_rejects_empty_string():
    validator = jsonschema.Draft202012Validator(SEND_SCHEMA)
    with pytest.raises(jsonschema.ValidationError):
        validator.validate({"chatId": "", "text": "hello"})
```

**3.4** Tests 15-19: `chatlytics_login`. Pattern (uses respx + a real ChatlyticsClient):

```python
@pytest.fixture
async def login_client():
    client = ChatlyticsClient(base_url=BASE_URL, api_key=API_KEY)
    yield client
    await client.aclose()


async def test_chatlytics_login_returns_false_when_webhook_not_registered(login_client):
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        router.get("/health").mock(
            return_value=httpx.Response(
                200,
                json={"webhook_registered": False, "sessions": []},
            )
        )
        result = await chatlytics_login(login_client)
    assert result["success"] is False
    assert "webhook_registered" in result["error"]


async def test_chatlytics_login_returns_true_when_webhook_registered(login_client):
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        router.get("/health").mock(
            return_value=httpx.Response(
                200,
                json={"webhook_registered": True, "sessions": [{"id": "s1"}, {"id": "s2"}]},
            )
        )
        result = await chatlytics_login(login_client)
    assert result["success"] is True
    assert result["webhook_registered"] is True
    assert result["sessions"] == 2


async def test_chatlytics_login_session_count_from_list(login_client):
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        router.get("/health").mock(
            return_value=httpx.Response(
                200, json={"webhook_registered": True, "sessions": [1, 2, 3, 4]},
            )
        )
        result = await chatlytics_login(login_client)
    assert result["sessions"] == 4


async def test_chatlytics_login_session_count_unknown_when_missing(login_client):
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        router.get("/health").mock(
            return_value=httpx.Response(
                200, json={"webhook_registered": True},
            )
        )
        result = await chatlytics_login(login_client)
    assert result["sessions"] == "unknown"


async def test_chatlytics_login_passes_through_get_failure(login_client):
    with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
        router.get("/health").mock(return_value=httpx.Response(503, text="upstream busy"))
        result = await chatlytics_login(login_client)
    assert result["success"] is False
```

## Verification

1. `python -m pytest tests/ -q` → 65 baseline + 19 new = **84 tests passing**
2. `python -m pytest tests/test_validation.py -q` → **19 tests passing**
3. No regression in `test_outbound.py`, `test_tools.py`, `test_tool_schemas.py`, `test_observability.py`, `test_media.py`
4. `test_tool_schemas.py::test_every_tool_has_valid_json_schema` still passes (the new `pattern` constraints are valid Draft 2020-12)
5. `test_tool_schemas.py::test_tool_count_matches_claude_code_plugin_baseline` still passes (still 21 tools)

## Acceptance criteria (from ROADMAP HERMES-10)

| AC | Status under this plan |
|---|---|
| 1. `__init__` rejects `webhook_path=""` with ValueError | Test 3.2 case `test_init_rejects_empty_webhook_path` |
| 2. `__init__` rejects `webhook_path` without leading slash with ValueError | Test 3.2 case `test_init_rejects_webhook_path_without_leading_slash` |
| 3. `__init__` accepts `"/webhook"` and `"/api/webhook"` | Test 3.2 case `test_init_accepts_valid_webhook_paths` |
| 4. `chatlytics_login` returns `success=False` when `webhook_registered=False` | Test 3.4 case `test_chatlytics_login_returns_false_when_webhook_not_registered` |
| 5/6. `looksLikeJid` decision = permissive | DECIDED: permissive — see CONTEXT decisions. Tests 3.3 cover the permissive contract. |
| 7. README `get_chat_info` `{}` semantics | Adapter docstring tightened (in-code). README update deferred to Phase 12. |

## Backwards-compat verification

- Existing 65 tests must still pass — they use:
  - `webhook_path=` either default `/webhook` (env override not set in fixtures) or unset → passes new validator
  - `chatId` values like `"chat-001"`, `"1234567890@c.us"` → all pass new permissive schema
  - `chatlytics_login` had ONE existing test? No — `test_tool_schemas.py` only validates the schema, doesn't call the handler. Test count check unaffected by login rewrite.

- Public-facing change: `chatlytics_login` will return `success=False` in a case that previously returned `success=True`. This IS a semantic change but matches the MCP bundle's contract (which is the canonical reference per ROADMAP Phase 5 acceptance criterion 7). Operators who relied on the old `success=True` would have been misled (gateway up, webhook down = effectively non-functional). The new semantics catch this state at the tool layer.

- All other changes are doc-only (get_chat_info, send_image, send_image_file).
