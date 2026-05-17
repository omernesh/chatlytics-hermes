# Phase 10 — Verification

**Status:** PASS
**Date:** 2026-05-17

## Acceptance criteria

| AC | Status | Evidence |
|---|---|---|
| 1. `__init__` rejects `webhook_path=""` with ValueError | PASS | `tests/test_validation.py::test_init_rejects_empty_webhook_path` |
| 2. `__init__` rejects `webhook_path` without leading slash with ValueError | PASS | `tests/test_validation.py::test_init_rejects_webhook_path_without_leading_slash` |
| 3. `__init__` accepts `"/webhook"` and `"/api/webhook"` | PASS | `tests/test_validation.py::test_init_accepts_valid_webhook_paths` |
| 4. `chatlytics_login` returns `success=False` when webhook not registered | PASS | `tests/test_validation.py::test_chatlytics_login_returns_false_when_webhook_not_registered` |
| 5/6. `looksLikeJid` decision = permissive (rationale documented) | PASS | CONTEXT.md decisions section + `test_media_chat_id_accepts_*` tests prove the permissive accept-set covers JIDs / phones / display names |
| 7. README `get_chat_info` `{}` semantics | PARTIAL | Adapter docstring tightened in-code with the four-path contract. README update deferred to Phase 12 (release docs). |

## Coverage by finding

| Finding | Fix location | Tests |
|---|---|---|
| 03-LOW-01 (`webhook_path` not validated) | `adapter.py:_validate_webhook_path` + `__init__` call site | 8 tests in `test_validation.py` |
| PR-MED-01 (`/health` route collision) | Same `_validate_webhook_path` (rule 6) | `test_init_rejects_webhook_path_equal_to_health` |
| 05-LOW-02 (chatId schema too loose) | `tools._chat_id_field` + `_message_id_field` helpers + applied to all 15 chatId + 6 messageId schemas | 6 tests in `test_validation.py` |
| 05-LOW-03 + PR-LOW-03 (`chatlytics_login` semantics) | Rewritten `tools.chatlytics_login` (returns `success=False` on webhook_registered != True) | 5 tests in `test_validation.py` |
| 02-LOW-03 (`get_chat_info` `{}` semantics) | Doc-only: tightened docstring with four-path contract | Code-review verification (no behavior change) |
| PR-LOW-06 (`send_image` vs `send_image_file` inconsistency) | Doc-only: cross-reference paragraphs in both adapter methods + tool-layer docstring on `chatlytics_send_image` | Code-review verification |

## Test results

```
$ python -m pytest tests/ -q
84 passed in 23.32s
```

- 65 baseline tests still passing (v2.0 + v2.1 phases 7-9)
- 19 new tests in `tests/test_validation.py` covering all 5 fixes that have behavioral surface

## v2.0/v2.1 invariants verified

- Hermes pin `>=0.14,<0.15` — unchanged in `pyproject.toml`
- 21 tools — unchanged (validation tightening is schema-internal, no surface change). `test_tool_schemas.py::test_tool_count_matches_claude_code_plugin_baseline` still passes.
- httpx outbound, aiohttp embedded inbound only — unchanged
- `{"success": bool, ...}` tool response shape — preserved (`chatlytics_login` flips the bool when webhook is unregistered but the shape is identical)
- 65/65 baseline tests still passing — confirmed (now 84/84 with new tests)
- `chatlytics-hermes` package name — unchanged
- MIT license — unchanged

## Backwards compatibility

**Behavioral change:** `chatlytics_login` now returns `success=False` when the gateway is reachable but `webhook_registered !== True`. Previously it returned `success=True` with `webhook_registered: False` as a separate field. This matches the Claude Code MCP bundle (`chatlytics-mcp.js:267-280`) which is the canonical reference per ROADMAP HERMES-05 AC-7. Operators who relied on the old `success=True` would have been misled (gateway up, webhook down = effectively non-functional inbound); the new semantics surface that state as a clear failure.

**Schema tightening:** Empty strings and control characters in `chatId` / `messageId` are now rejected at the JSON-schema layer. NO existing tests broke — all baseline test fixtures use valid identifiers (`"chat-001"`, `"1234567890@c.us"`, etc.). The permissive pattern accepts WhatsApp JIDs, phone numbers, and group display names as documented in CONTEXT decisions.

**`webhook_path` validation:** Default `/webhook` and all explicit valid paths in tests pass the new validator. NO existing tests broke.

**Doc-only changes** (`get_chat_info`, `send_image`, `send_image_file`): zero behavioral impact.

## Out of scope (deferred to later phases)

- Conftest teardown for platform_registry (Phase 11 / 02-MED-02)
- Smoke build cache (Phase 11 / 06-LOW-01)
- `_FakePlatformConfig` consolidation (Phase 11)
- CHANGELOG / README / pyproject bump (Phase 12)
- Phase identifier leak in plugin.yaml (Phase 12 / PR-review MED-04)
- Collapsing `send_image` / `send_image_file` into one method (v2.2+ breaking change)
- Strict JID regex enforcement (would break phone numbers and display names)
- Sentinel `_error` key on `get_chat_info` (breaking change; v2.2+)
