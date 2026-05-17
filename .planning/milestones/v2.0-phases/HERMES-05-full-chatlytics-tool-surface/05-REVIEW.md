---
phase: 05-full-chatlytics-tool-surface
review_date: 2026-05-17
depth: standard
implemented_by: claude-opus-4-7-1m
reviewed_by: gsd-code-review
files_reviewed:
  - src/chatlytics_hermes/tools.py
  - src/chatlytics_hermes/adapter.py
  - tests/test_tools.py
  - tests/test_tool_schemas.py
  - pyproject.toml
summary:
  blocker: 0
  high: 0
  medium: 2
  low: 4
  info: 2
overall_verdict: PASS_WITH_MINORS
---

# HERMES-05 -- Code Review

## Scope

Reviewed the 5 files touched by HERMES-05:

1. `src/chatlytics_hermes/tools.py` (NEW, 587 LOC) -- 21 tool handlers + JSON schemas + TOOLS tuple + helpers
2. `src/chatlytics_hermes/adapter.py` (MODIFIED, +102 LOC) -- public `.client` property + `_make_tool_handler` wrapper + register-block tool loop
3. `tests/test_tools.py` (NEW, 156 LOC) -- 6 behavior tests
4. `tests/test_tool_schemas.py` (NEW, 109 LOC) -- 5 schema-discipline tests
5. `pyproject.toml` (MODIFIED, +1 LOC) -- `jsonschema>=4,<5` runtime dep

Focus areas:

1. **Tool surface completeness** -- 21 tools registered, schemas valid, names namespaced
2. **Return shape consistency** -- `{success: bool, ...}` on every code path (success, 4xx, 5xx, transport error, no-adapter)
3. **Adapter lookup at call time** -- register() runs before adapter_factory, so handlers must defer adapter binding
4. **Media-tool composition** -- 5 media tools wrap HERMES-04 adapter methods; URL vs filePath branching
5. **Acceptance-criterion coverage** (8/8 verified PASS in dockerized clean-room -- see `05-VERIFICATION.md`)
6. **Scope discipline** -- no README/CHANGELOG edits, no smoke test, no tool-result rendering

## Verdict: PASS WITH MINORS

Zero BLOCKER, zero HIGH. Two MEDIUM and four LOW concerns documented
below -- none affect acceptance criteria or block HERMES-06. The
MEDIUMs are either inherited from HERMES-04 (now surfaceable through
the new tool layer) or design-doc trade-offs (semantic divergence
between the brief's `chatlytics_actions` description and the MCP
bundle).

---

## Findings

### MEDIUM-01 -- Brief vs MCP-bundle semantic divergence for `chatlytics_actions`

**Files:** `src/chatlytics_hermes/tools.py:393-410` (ACTIONS_SCHEMA + `chatlytics_actions` handler), `.planning/phases/HERMES-05-full-chatlytics-tool-surface/05-01-PLAN.md:99-101`

The phase brief specifies `chatlytics_actions` as a "generic action
dispatcher: POST /api/v1/actions with arbitrary body". The Claude Code
MCP bundle (`chatlytics-mcp.js:181-194`, the canonical naming source
locked in `05-CONTEXT.md`) defines it as a **GET** that LISTS the
catalog, with `chatlytics_dispatch` being the POST dispatcher.

HERMES-05 implements the MCP-bundle semantics (GET, list catalog) and
satisfies the brief's "generic dispatcher" intent via the separate
`chatlytics_dispatch` tool. The PLAN's `<context>` block documents the
trade-off but a future contributor reading the brief in isolation may
misread the implementation.

**Suggested fix:** Add an inline comment near `chatlytics_actions` in
`tools.py` calling out the brief-vs-bundle divergence, and ensure
HERMES-06's README describes both tools clearly (catalog list vs
dispatch).

**Disposition:** ACCEPT with HERMES-06 README clarification. The
trade-off is documented in 05-CONTEXT.md, 05-01-PLAN.md, and
05-01-SUMMARY.md. No code change needed -- the MCP bundle naming is
load-bearing for users who already learned the names from Claude Code.

### MEDIUM-02 -- Carry-forward: blocking file I/O in `_resolve_media_url` is now reachable from tool surface

**Files:** `src/chatlytics_hermes/adapter.py:455-457`, `src/chatlytics_hermes/tools.py:556-578` (chatlytics_send_image with filePath)

The HERMES-04 review (04-REVIEW MED-02) deferred wrapping the
`open()+read()` in `asyncio.to_thread`. HERMES-05 exposes this path
through `chatlytics_send_image` (and the four other media tools) when
callers pass `filePath`. A concurrent burst of tool calls now blocks
the event loop for the full read duration of each file.

**Suggested fix:** Wrap the synchronous read in `asyncio.to_thread`:

```python
def _read_bytes(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()
content = await asyncio.to_thread(_read_bytes, path)
```

**Disposition:** DEFER -> HERMES-06. The fix is straightforward but
changes I/O ordering semantics under concurrent uploads. HERMES-06
owns the wrap + a concurrency regression test. Tracking via the
forward-action list in 05-01-SUMMARY.

### LOW-01 -- `_make_tool_handler` catches `Exception` from `ctx.get_platform`

**File:** `src/chatlytics_hermes/adapter.py:1043-1046`

```python
try:
    entry = get_platform("chatlytics")
except Exception:  # noqa: BLE001
    entry = None
```

The bare `Exception` catch is defensive against unknown
`PluginContext` implementations that might raise types other than
`KeyError` / `AttributeError`. It correctly leaves `BaseException`
subclasses (CancelledError, KeyboardInterrupt, SystemExit) free to
propagate. The cost is masking genuine bugs in a custom `get_platform`
implementation -- a stack trace would help diagnose, instead of a
silent fallback to the `platforms` dict.

**Suggested fix:** Log the swallowed exception at DEBUG level:

```python
except Exception as exc:  # noqa: BLE001
    logger.debug("get_platform('chatlytics') raised: %s", exc)
    entry = None
```

**Disposition:** ACCEPT. The current behavior is forgiving by design;
adding a debug log is cheap and useful. Defer to HERMES-06 unless we
hit a real diagnosis problem.

### LOW-02 -- Media-tool schemas validate `chatId` presence but not JID format

**File:** `src/chatlytics_hermes/tools.py:290-318` (`_media_schema`)

```python
"chatId": {"type": "string"},
```

The schema accepts any non-empty string for `chatId`. WhatsApp JIDs
have a constrained format (`<phone>@c.us`, `<id>@g.us`,
`<id>@lid`, `<id>@newsletter`). A typo'd chat ID will pass schema
validation, hit the gateway, and return a 4xx with a generic "invalid
chatId" -- which the tool surface correctly maps to `{success: False}`
but doesn't catch at schema-validation time.

**Suggested fix:** Add a `pattern` or `format` keyword to `chatId`:

```python
"chatId": {
    "type": "string",
    "pattern": r"^[A-Za-z0-9._-]+@(c\.us|g\.us|lid|newsletter)$",
}
```

(Or a softer regex that allows phone-only inputs that the gateway
auto-normalizes.)

**Disposition:** DEFER -> HERMES-06. The MCP bundle (canonical) does
not enforce a JID pattern either (`chatlytics-mcp.js:142` uses a plain
`z.string()`); enforcing here would be tighter than the source of
truth. HERMES-06 can decide whether to mirror the MCP bundle's
`looksLikeJid` helper at schema-validation time.

### LOW-03 -- `chatlytics_login` post-processing strips `success` from `raw_response`

**File:** `src/chatlytics_hermes/tools.py:518-531`

```python
"raw_response": {k: v for k, v in result.items() if k != "success"},
```

The `success` key is stripped from `raw_response` to avoid confusion
with the wrapper's derived `success`. This is intentional, but a
gateway returning `webhook_registered=false` will look like a generic
failure -- the tool returns `{success: True, webhook_registered: False,
sessions: ..., raw_response: {...}}`. Callers must inspect
`webhook_registered`, not `success`.

**Suggested fix:** Set `success` to `False` when `webhook_registered`
is not `True`, mirroring the MCP bundle's UX
(`chatlytics-mcp.js:267-279`).

**Disposition:** DEFER -> HERMES-06. The current behavior is "API
call succeeded; here's the webhook state". The MCP bundle treats it as
a fail when the webhook isn't registered. Either is defensible; the
MCP bundle's stricter semantic is friendlier for non-technical callers.

### LOW-04 -- Tool count assertion at module-import time

**File:** `src/chatlytics_hermes/tools.py:836-838`

```python
assert len(TOOLS) == 21, (
    f"Chatlytics tool surface drift: expected 21 tools, got {len(TOOLS)}"
)
```

A module-level `assert` is good for catching accidental drift but can
be optimized out by `python -O` (which strips assert statements).
Production callers running `python -O` would lose the guard.

**Suggested fix:** Promote to a real check:

```python
if len(TOOLS) != 21:
    raise RuntimeError(
        f"Chatlytics tool surface drift: expected 21 tools, got {len(TOOLS)}"
    )
```

**Disposition:** ACCEPT. Plugin code under Hermes is not typically run
with `-O`, and the test suite has a redundant assertion in
`test_tool_count_matches_claude_code_plugin_baseline`. No action.

### INFO-01 -- `chatlytics_send` follows ROADMAP's `/api/v1/send` path, diverging from MCP bundle's actions-dispatcher

**File:** `src/chatlytics_hermes/tools.py:434-450` (chatlytics_send handler)

The MCP bundle's `chatlytics_send` (`chatlytics-mcp.js:115-135`) POSTs
to `/api/v1/actions {action: "send", params: {chatId, text}}`. The
Hermes adapter (HERMES-02) uses the dedicated `/api/v1/send` endpoint,
and the HERMES-05 tool mirrors the adapter. Operators wanting the
actions-dispatcher form can use `chatlytics_dispatch action="send"
parameters={chatId, text}`.

**Disposition:** ACCEPT -- documented in 05-01-PLAN.md `<context>`
and 05-01-SUMMARY.md "Key design decisions". The two endpoints
converge on the same gateway action.

### INFO-02 -- Carry-forward: `_keep_typing` async-cm shape divergence (04-MED-01)

**File:** `src/chatlytics_hermes/adapter.py:721-786` (unchanged from HERMES-04)

04-REVIEW MED-01 flagged that `_keep_typing` is an
`@contextlib.asynccontextmanager` instead of the base coroutine.
HERMES-05's tool handlers do NOT call `_keep_typing` (none of the 21
are long-running enough to need it), so the shape divergence is
inert in this phase. The forward action item carries to HERMES-06
README docs.

**Disposition:** ACCEPT. No code action in HERMES-05.

---

## What was reviewed for and found CLEAN

- **All 21 tools registered**: TOOLS tuple asserts `len == 21` at
  module import time AND is double-checked by
  `test_tool_count_matches_claude_code_plugin_baseline` (in both test
  files for belt-and-suspenders coverage).
- **Schemas are Draft 2020-12 compliant**: `test_every_tool_has_valid_json_schema`
  passes `Draft202012Validator.check_schema` for each tool AND
  constructs a validator instance. `additionalProperties: False` on
  every schema (asserted by `test_every_tool_disallows_extra_properties`).
- **Return shape contract is universal**: success path uses `_ok`
  which merges and re-asserts `success=True`; HTTP-failure path uses
  `_err_from_response` which includes `error`, `status_code`,
  `raw_response`; transport-error path uses `_err_from_exception` which
  includes `error`. No code path returns a non-dict.
- **Required-field discipline**: `test_every_tool_has_required_chat_id_field_when_applicable`
  classifies every tool into one of four groups (requires chatId/messageId,
  requires query, requires action, requires none) and fails completeness
  if a new tool is added without classification.
- **Namespace prefix discipline**: `test_all_tools_namespace_chatlytics_`
  asserts every tool name starts with `chatlytics_`.
- **Adapter lookup is robust**: `_make_tool_handler` tries
  `ctx.get_platform` first, falls back to `ctx.platforms[name]`, and
  returns a structured error dict when nothing is connected -- never
  raises.
- **Media handlers don't break without adapter**: each of the 5 media
  handlers explicitly checks `if adapter is None` and returns the
  canonical failure dict, so a hypothetical custom PluginContext that
  doesn't expose the adapter at all degrades gracefully.
- **Feature-detect register_tool**: HERMES-01 MockCtx
  (`tests/test_register.py:26-38`, has only `register_platform`) is
  unaffected -- `hasattr(ctx, "register_tool")` is False, so the tool
  loop is skipped and existing HERMES-01 tests stay green.
- **Scope discipline**: No README edits. No CHANGELOG. No smoke
  test. No tool-result rendering code. No CLI commands registered.

---

## Carry-forward to HERMES-06

- **MED-02 above**: wrap `_resolve_media_url`'s `open()+read()` in
  `asyncio.to_thread` + add a concurrency regression test
- **LOW-01 above**: add DEBUG log when `ctx.get_platform` raises
- **LOW-02 above**: decide whether to mirror MCP bundle's
  `looksLikeJid` regex at schema-validation time (currently lenient)
- **LOW-03 above**: decide whether `chatlytics_login` should fail
  (`success: False`) when `webhook_registered` is not true, matching
  MCP bundle UX
- **MED-01 above**: document the `chatlytics_actions` (catalog list)
  vs `chatlytics_dispatch` (POST dispatcher) distinction clearly in
  README
- **04-MED-01 carry**: document the `_keep_typing` async-cm shape
  divergence in README
- **02-LOW-02 carry**: confirm whether `filename` is honored for
  URL-path documents (gateway-side question)
- **02-LOW-02 carry**: `send_typing` log flood at `logger.warning` --
  still pending

## Cross-references

- ROADMAP: `.planning/ROADMAP.md` (Phase 5 acceptance criteria 1-8)
- PLAN: `.planning/phases/HERMES-05-full-chatlytics-tool-surface/05-01-PLAN.md`
- SUMMARY: `.planning/phases/HERMES-05-full-chatlytics-tool-surface/05-01-SUMMARY.md`
- VERIFICATION: `.planning/phases/HERMES-05-full-chatlytics-tool-surface/05-VERIFICATION.md`
- HERMES-04 review (forward action items): `.planning/phases/HERMES-04-media-ux-polish-cron/04-REVIEW.md`
- Canonical MCP bundle: `C:/Users/omern/.claude/plugins/marketplaces/chatlytics-claude-code/servers/chatlytics-mcp.js`
- Upstream `register_tool` pattern: `/tmp/hermes-ref-v0.14.0/plugins/spotify/__init__.py`
