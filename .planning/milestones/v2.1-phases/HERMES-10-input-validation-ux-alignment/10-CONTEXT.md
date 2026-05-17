# Phase 10: Input validation + UX alignment - Context

**Gathered:** 2026-05-17
**Status:** Ready for planning
**Mode:** Auto-generated (infra-skip — fix shapes locked by ROADMAP + reviews)

<domain>
## Phase Boundary

Tighten input validation at the adapter and tool boundaries, align the
`chatlytics_login` tool semantics with the Claude Code MCP bundle, document
`get_chat_info`'s `{}`-vs-error contract, and harmonize the `send_image` /
`send_image_file` API surface so the dual-shape inconsistency surfaced in the
PR-style review is at least documented and observable.

Closes:

- **03-LOW-01** + PR-review **MED-01** — `webhook_path` not validated at
  `__init__`; `/health` route collision is a documented footgun.
- **05-LOW-02** + PR-review **MED-01** — media-tool `chatId` schemas accept
  any string; obvious garbage (empty, control chars) should fail at the
  schema layer with a clear message (cosmetic UX, NOT strict JID enforcement).
- **05-LOW-03** + PR-review **LOW-03** — `chatlytics_login` returns
  `success=True` even when `webhook_registered=False`, which contradicts the
  MCP bundle's behavior (the MCP returns `isError: true` in that case).
- **02-LOW-03** — `get_chat_info` returns `{}` both on empty-success AND
  several error paths; callers cannot distinguish.
- **PR-review LOW-06** — `send_image` (URL-first) vs `send_image_file`
  (path-first) API inconsistency surfaced cross-layer.

No tool surface change (still 21 tools). No new env vars. No breaking
config-schema changes. Validation is fail-fast at init or schema-time;
runtime semantics for already-valid inputs are unchanged.
</domain>

<decisions>
## Implementation Decisions

### Fix 1: `webhook_path` validation at `ChatlyticsAdapter.__init__` (03-LOW-01 + PR-MED-01)

`src/chatlytics_hermes/adapter.py` — after the `self.webhook_path = ...` assignment (line ~185-187), add a `_validate_webhook_path(self.webhook_path)` call that raises `ValueError` on:

- Empty string (after strip)
- Does not start with `/`
- Contains any character in `\r\n` or any control character (`\x00`-`\x1f`, `\x7f`)
- Contains `..` segment (path traversal smell)
- Contains `?` or `#` (URL queries/fragments — wrong layer)
- Equals `/health` (route-collision footgun from PR-MED-01)

Module-level helper `_validate_webhook_path(path: str) -> None` (no return; raises `ValueError`) so it's testable in isolation. Error message includes the offending value (truncated to 80 chars) and the rule violated.

Fail-fast at `__init__` matches Hermes conventions; do NOT silently rewrite the path.

### Fix 2: Media-tool chatId schema tightening (05-LOW-02 + PR-MED-01)

DECISION: **Permissive validation** — add `minLength: 1` and a JSON Schema `pattern` that rejects only obvious garbage:

```
"pattern": "^[^\\x00-\\x1f\\x7f]+$"
```

Rationale (from BACKWARDS-COMPAT WARNING in task brief): tool schemas are the public surface for users typing `/chatlytics_send`. We MUST NOT reject inputs that currently work. Chatlytics accepts:

- WhatsApp JIDs (`1234567890@c.us`, `1234567890@g.us`, `1234567890@newsletter`)
- Phone numbers (`+1234567890`, `1234567890`)
- Group display names (some Chatlytics gateway versions resolve these server-side)

We do NOT do strict JID regex matching — that would break phone-number and display-name shortcuts. We only reject empty strings and strings containing C0/C1 control characters (which are almost certainly a copy-paste glitch or injection attempt and never represent a real Chatlytics chatId).

Apply to: ALL tool schemas that have a `chatId` field — `SEND_SCHEMA`, `REPLY_SCHEMA`, `REACT_SCHEMA`, `EDIT_SCHEMA`, `UNSEND_SCHEMA`, `PIN_SCHEMA`, `UNPIN_SCHEMA`, `READ_SCHEMA`, `DELETE_SCHEMA`, `POLL_SCHEMA`, `SEND_IMAGE_SCHEMA`, `SEND_VOICE_SCHEMA`, `SEND_VIDEO_SCHEMA`, `SEND_FILE_SCHEMA`, `SEND_ANIMATION_SCHEMA`.

For `messageId` fields (REACT/EDIT/UNSEND/PIN/UNPIN/DELETE), apply the same permissive `minLength: 1` + control-char rejection pattern — same rationale.

For `text` (SEND/REPLY/EDIT) and `query` (SEARCH): already have `minLength: 1`. Leave unchanged.

Implement via a tiny helper `_chat_id_field(description: str) -> dict` that returns the standard schema fragment, so every schema uses identical wording.

### Fix 3: `chatlytics_login` semantics alignment with MCP (05-LOW-03 + PR-LOW-03)

Reference: `C:/Users/omern/.claude/plugins/marketplaces/chatlytics-claude-code/servers/chatlytics-mcp.js:240-303`. The MCP bundle:

1. GETs `/health`
2. Reads `webhook_registered` and `sessions` fields
3. Returns `isError: true` when `webhook_registered !== true`
4. On success: returns text "Connected... Webhook registered. Sessions: N"

CURRENT Python implementation (`tools.py:794-812`) returns `success=True` UNCONDITIONALLY when `/health` returns 200, with `webhook_registered` as a separate field. This contradicts the MCP behavior.

NEW behavior:

- When `/health` itself fails (transport error, non-200): pass through the `_get` error result (already returns `success=False`).
- When `/health` returns 200 but `webhook_registered !== True`: return `{"success": False, "error": "webhook_registered=<value>; WhatsApp inbound may be down", "webhook_registered": <value>, "sessions": <count>, "raw_response": {...}}`.
- When `/health` returns 200 AND `webhook_registered === True`: return `{"success": True, "webhook_registered": True, "sessions": <count>, "raw_response": {...}}`.

`sessions` derivation matches MCP: list → `len(list)`; int → int; otherwise → `"unknown"`.

### Fix 4: `get_chat_info` empty-vs-error semantics (02-LOW-03)

`src/chatlytics_hermes/adapter.py:522-561` — `get_chat_info` returns `{}` in 4 distinct paths:

1. Adapter not connected (`self._client is None`)
2. Transport error (`httpx.RequestError`)
3. Non-200 response
4. JSON decode failure or non-dict payload

All four cases collapse to `{}`, losing operator signal. The adapter method has a fixed return type (`Dict[str, Any]`) and changing it would be a breaking change for callers that already handle the dict shape.

DECISION: keep the adapter method's return shape unchanged (still `Dict[str, Any]`), but:

1. Tighten the docstring to spell out the `{}` contract: empty dict means "no data" — could be either "chat does not exist" OR "lookup failed". Operators should consult logs (DEBUG/WARNING) for the distinction.
2. Document the existing WARNING log lines (non-200, transport error) as the operator surface for distinguishing.
3. Add a tool-level documentation comment in `tools.py` (no tool wraps `get_chat_info` directly — it's an adapter method, not a tool) but ensure the adapter docstring is clear.

NO BEHAVIORAL CHANGE — this is doc-only. Adding a sentinel `{"_error": "..."}` would be a real breaking change and is deferred to v2.2+ (out of milestone scope).

### Fix 5: `send_image` vs `send_image_file` API inconsistency (PR-LOW-06)

`src/chatlytics_hermes/adapter.py:779-821` (`send_image`) and `:897-921` (`send_image_file`).

Status: in v2.0 these are split (`send_image(url)` vs `send_image_file(path)`); the other 4 media handlers (`send_voice/video/document/animation`) accept either shape via `Union[str, bytes, bytearray]` and dispatch internally.

Decision per task brief: **do NOT collapse them in v2.1** (that would be a v2.2+ breaking change to public adapter surface). Instead:

1. Verify `send_image` ALREADY accepts the `Union[str, bytes, bytearray]` shape (it does — line 782 has `image_url: Union[str, bytes, bytearray]`).
2. Add an explicit doctring note on both methods cross-referencing each other and explaining the historical reason for the split.
3. At the TOOL layer (`chatlytics_send_image` in `tools.py:653-671`), the dispatch is already correct: `if mediaUrl: send_image(...) else: send_image_file(...)`. Add a docstring comment clarifying why both adapter methods exist.
4. Verify both methods produce the same `SendResult` shape on success/failure (they do — both go through `_send_media_payload` → `_make_send_result`).

NO BEHAVIORAL CHANGE — docs only. The public adapter surface stays identical to v2.0 (no rename, no signature change). Tool callers see identical behavior.

### Tests (`tests/test_validation.py` — new file)

1. `test_init_rejects_empty_webhook_path` — `webhook_path=""` → ValueError
2. `test_init_rejects_webhook_path_without_leading_slash` — `webhook_path="webhook"` → ValueError
3. `test_init_rejects_webhook_path_equal_to_health` — `webhook_path="/health"` → ValueError (route collision)
4. `test_init_rejects_webhook_path_with_control_chars` — `webhook_path="/web\nhook"` → ValueError
5. `test_init_rejects_webhook_path_with_traversal` — `webhook_path="/../etc"` → ValueError
6. `test_init_rejects_webhook_path_with_query_string` — `webhook_path="/webhook?x=1"` → ValueError
7. `test_init_accepts_valid_webhook_paths` — `"/webhook"`, `"/api/webhook"`, `"/v1/inbound"` all pass
8. `test_init_accepts_default_webhook_path` — no env override → defaults to `/webhook` (passes)

Tool schema tests (in `tests/test_validation.py` or extend existing `test_tool_schemas.py`):

9. `test_media_chat_id_rejects_empty_string` — schema validation rejects `chatId=""`
10. `test_media_chat_id_rejects_control_chars` — schema validation rejects `chatId="abc\x00def"`
11. `test_media_chat_id_accepts_jid_format` — `chatId="1234567890@c.us"` validates
12. `test_media_chat_id_accepts_phone_number` — `chatId="+1234567890"` validates
13. `test_media_chat_id_accepts_group_name` — `chatId="My Group"` validates (permissive)
14. `test_messaging_chat_id_rejects_empty_string` — same for non-media schemas

`chatlytics_login` tests:

15. `test_chatlytics_login_returns_false_when_webhook_not_registered` — mocked `/health` returns 200 + `{webhook_registered: False}` → tool returns `success=False`, error mentions webhook
16. `test_chatlytics_login_returns_true_when_webhook_registered` — mocked `/health` 200 + `webhook_registered: True` → tool returns `success=True`
17. `test_chatlytics_login_session_count_from_list` — `sessions: [...]` → `sessions=len`
18. `test_chatlytics_login_session_count_unknown_when_missing` — no `sessions` field → `sessions="unknown"`
19. `test_chatlytics_login_passes_through_get_failure` — `/health` returns 503 → tool returns `success=False` from underlying `_get` error

### Claude's discretion

- Whether to add `pattern` to JID schemas at ALL or just `minLength: 1` — pick: add pattern (control-char rejection is cheap and a real injection mitigation). Pattern uses standard JSON Schema syntax compatible with `jsonschema.Draft202012Validator`.
- Exact error wording for `webhook_path` errors — match Hermes convention (concise, mentions env var, includes offending value).
- Whether the chatlytics_login `webhook_registered=False` error message includes the raw boolean repr or just "false" — match MCP wording ("webhook_registered is not true").
</decisions>

<code_context>
## Existing code insights

### Files touched (modify)
- `src/chatlytics_hermes/adapter.py` — add `_validate_webhook_path` helper + call site in `__init__`; tighten docstrings on `get_chat_info` + `send_image` + `send_image_file`.
- `src/chatlytics_hermes/tools.py` — add `_chat_id_field` schema helper, apply to 15 schemas; rewrite `chatlytics_login` body; add tool-layer docstring on `chatlytics_send_image` cross-referencing the adapter split.

### Files created
- `tests/test_validation.py` — 19 tests (8 init validation + 6 schema validation + 5 login semantics).

### Files NOT touched
- `src/chatlytics_hermes/inbound.py` — no inbound changes
- `src/chatlytics_hermes/client.py` — no client changes
- `tests/conftest.py` — no fixture changes (Phase 11)
- `pyproject.toml`, `plugin.yaml`, `CHANGELOG.md` — no version/manifest churn (Phase 12)
- `README.md` — `get_chat_info` is an adapter method, not a documented tool; no README change needed for this phase (Phase 12 will revisit)

### Patterns observed
- `logger = logging.getLogger("chatlytics_hermes.{module}")` — already in place
- `pytestmark = pytest.mark.asyncio` for new async test files
- `_FakePlatformConfig` pattern duplicated across 3 test files; we reuse from `test_outbound.py` import for `test_validation.py` (Phase 11 will consolidate)
- Schema dicts are module-level constants in `tools.py`; helper functions are private (`_media_schema`)

### v2.0/v2.1 invariants (must preserve)
- Hermes pin `>=0.14,<0.15`
- 21 tools exactly (validation tightening is internal — no surface change)
- httpx outbound, aiohttp embedded inbound only
- `{"success": bool, ...}` tool response shape — `chatlytics_login` STAYS within this shape (just flips the bool when webhook is unregistered)
- 65/65 v2.0+v2.1 baseline tests still passing (new validation tests will bump the total)
- `chatlytics-hermes` package name
- MIT license
</code_context>

<specifics>
## Specific implementation guides

### `_validate_webhook_path` reference implementation

```python
_CONTROL_CHARS = "".join(chr(i) for i in range(32)) + "\x7f"

def _validate_webhook_path(path: str) -> None:
    """Validate ``webhook_path`` at adapter __init__.

    Raises ValueError with a clear message on invalid paths. See
    Phase 10 CONTEXT.md for the rule list.
    """
    if not isinstance(path, str):
        raise ValueError(
            f"CHATLYTICS_WEBHOOK_PATH must be a string; got {type(path).__name__}"
        )
    stripped = path.strip()
    if not stripped:
        raise ValueError("CHATLYTICS_WEBHOOK_PATH must be a non-empty string")
    if not stripped.startswith("/"):
        raise ValueError(
            f"CHATLYTICS_WEBHOOK_PATH must start with '/'; got {stripped[:80]!r}"
        )
    if any(c in _CONTROL_CHARS for c in stripped):
        raise ValueError(
            "CHATLYTICS_WEBHOOK_PATH must not contain control characters; "
            f"got {stripped[:80]!r}"
        )
    if ".." in stripped:
        raise ValueError(
            f"CHATLYTICS_WEBHOOK_PATH must not contain '..' segments; got {stripped[:80]!r}"
        )
    if "?" in stripped or "#" in stripped:
        raise ValueError(
            f"CHATLYTICS_WEBHOOK_PATH must not contain '?' or '#'; got {stripped[:80]!r}"
        )
    if stripped == "/health":
        raise ValueError(
            "CHATLYTICS_WEBHOOK_PATH cannot be '/health' (reserved for the "
            "health endpoint route registered by the embedded webhook server)"
        )
```

Call site: at the end of `__init__`, after `self.webhook_path = ...` assignment:

```python
_validate_webhook_path(self.webhook_path)
```

### `_chat_id_field` helper

```python
# JID / phone / display-name accepted; reject only empty + control chars.
_CHAT_ID_PATTERN = r"^[^\x00-\x1f\x7f]+$"

def _chat_id_field(description: str = "Chat JID, phone, or group identifier.") -> Dict[str, Any]:
    return {
        "type": "string",
        "minLength": 1,
        "pattern": _CHAT_ID_PATTERN,
        "description": description,
    }
```

Apply to every `chatId` property across the 15 chatId-bearing schemas. Same helper, customized description where the current schema has a more specific one (e.g., REACT_SCHEMA's "Optional chat context.").

`messageId` fields: define a sibling `_message_id_field(description)` helper with the same pattern. Apply to REACT/EDIT/UNSEND/PIN/UNPIN/DELETE schemas (6 schemas).

### `chatlytics_login` rewrite

```python
async def chatlytics_login(client: ChatlyticsClient) -> Dict[str, Any]:
    """Validate API key + webhook registration via /health.

    Aligns with the Claude Code MCP bundle's semantics
    (chatlytics-mcp.js:240-303): returns ``success=False`` when the
    gateway is reachable but ``webhook_registered`` is not literally
    ``True`` -- WhatsApp inbound is degraded even though the gateway
    responds. Operators see this as a clear failure instead of a
    misleading "login OK" with an inert webhook.
    """
    result = await _get(client, "/health")
    if not result.get("success"):
        return result  # transport / non-200 already populated by _get

    webhook_ok = result.get("webhook_registered") is True
    sessions = result.get("sessions")
    if isinstance(sessions, list):
        session_count: Any = len(sessions)
    elif isinstance(sessions, int):
        session_count = sessions
    else:
        session_count = "unknown"
    raw = {k: v for k, v in result.items() if k != "success"}
    if not webhook_ok:
        return {
            "success": False,
            "error": (
                f"webhook_registered is not true (got "
                f"{result.get('webhook_registered')!r}); WhatsApp inbound may be down"
            ),
            "webhook_registered": result.get("webhook_registered"),
            "sessions": session_count,
            "raw_response": raw,
        }
    return {
        "success": True,
        "webhook_registered": True,
        "sessions": session_count,
        "raw_response": raw,
    }
```

### `get_chat_info` docstring tightening

Replace the current docstring with a precise contract section that distinguishes the four `{}` paths and documents which log line operators should consult for each. NO code change.

### `send_image` / `send_image_file` docstring cross-reference

Append to `send_image` docstring: "Companion method :meth:`send_image_file` accepts a `str` local path explicitly (URL is rejected silently by treating the leading `/` as a path)..." — actually, since `send_image` already accepts `Union[str, bytes, bytearray]` including local paths, the split is purely API-shape historical. Document this in BOTH methods' docstrings.

Append to `chatlytics_send_image` tool docstring (currently absent): explain the dispatch — `mediaUrl` → `adapter.send_image`, `filePath` → `adapter.send_image_file`. Note the adapter split is preserved for v2.0 surface compat; the tool layer offers a single unified entry point.

### Test patterns

- For schema validation tests: use `jsonschema.Draft202012Validator(SCHEMA).validate({"chatId": "...", ...})` and catch `jsonschema.ValidationError`.
- For `__init__` validation tests: construct `_FakePlatformConfig(extra={"webhook_path": "..."})` and assert `pytest.raises(ValueError)` on `ChatlyticsAdapter(config)`.
- For `chatlytics_login` tests: use `respx.mock` to stub `/health`, instantiate a `ChatlyticsClient` directly, call `await chatlytics_login(client)`.

### Env var interaction

`_validate_webhook_path` runs after `self.webhook_path = os.getenv("CHATLYTICS_WEBHOOK_PATH") or extra.get("webhook_path", "/webhook")` so it validates the resolved final value. If the env var is set to garbage AND extra has a valid value, the env var wins (existing behavior) and validation rejects — operator sees a clear ValueError pointing at the env var name.

Tests must clear `CHATLYTICS_WEBHOOK_PATH` env var or use `monkeypatch.delenv` to ensure env doesn't override the test's intended config.

</specifics>

<deferred>
## Deferred ideas (DO NOT fix here — out of scope)

- Conftest teardown for platform_registry (Phase 11 / 02-MED-02)
- Smoke build cache (Phase 11 / 06-LOW-01)
- `_FakePlatformConfig` consolidation across test files (Phase 11)
- v2.1.0 CHANGELOG / README updates / pyproject bump (Phase 12)
- Phase identifier leak in plugin.yaml (Phase 12 / PR-review MED-04)
- Collapsing `send_image` and `send_image_file` into a single method (v2.2+ breaking change)
- Strict JID regex enforcement on chatId schemas (would break phone-number and display-name inputs)
- Adding a sentinel `_error` key to `get_chat_info` return (breaking change; v2.2+)
- New env vars (out of milestone)
- Log destination changes (out of milestone)
</deferred>
