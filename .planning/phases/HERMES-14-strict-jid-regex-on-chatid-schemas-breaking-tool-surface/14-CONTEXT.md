---
phase: 14
phase_slug: strict-jid-regex-on-chatid-schemas-breaking-tool-surface
phase_name: "Strict JID regex on `chatId` schemas (BREAKING tool surface)"
project_code: HERMES
milestone: v3.0
infra_skip: true
infra_skip_reason: "Scope is fix-locked per v3.0 ROADMAP HERMES-14 + the operator's autonomous-launch brief. The regex (`@(c\\.us|g\\.us|lid|newsletter)$`), validator pattern choice (JSON Schema `pattern` — the established tools.py mechanism), error message text, test class plan, and `messageId` parity rule (do not over-tighten beyond JS canonical) are all encoded by the operator before launch. No grey areas need user discussion — gsd-discuss-phase would only paraphrase the locked decisions."
---

# HERMES-14 — Strict JID regex on `chatId` schemas (BREAKING tool surface) — CONTEXT

## Domain (Phase boundary from ROADMAP goal)

Tighten `chatId` validation across all tool schemas in
`src/chatlytics_hermes/tools.py` to match the sibling JS bundle's
canonical JID regex `/@(c\.us|g\.us|lid|newsletter)$/i`. Reject phone
numbers, display names, and ambiguous strings at the schema layer —
chat-resolution becomes the caller's responsibility (call
`chatlytics_search` first to resolve a name/phone to a JID).

This is a **BREAKING tool surface change**. Inputs that passed through
v2.1's permissive `_chat_id_field` helper (which only rejected empty
strings and C0 control characters) now get rejected at the schema
boundary. Closes v2.1 deferred item 2.

Scope:
- All **15 chatId-bearing schemas** in `tools.py` get the strict JID
  validator.
- All **6 messageId-bearing schemas** in `tools.py` stay permissive
  (matching the JS bundle, which does NOT regex-validate messageId).
- Validation failures surface naturally via JSON Schema's `pattern`
  keyword (jsonschema.ValidationError). The Hermes framework converts
  those to its standard error response — do NOT manually catch and
  re-shape.

## Decisions (encoded from operator-locked phase brief)

### D1 — JID regex (Python form)

```python
_JID_PATTERN: str = r"^.+@(c\.us|g\.us|lid|newsletter)$"
```

- Case-insensitivity: JSON Schema `pattern` is implementation-defined
  for flags. The four suffix families are lowercase by convention in
  WAHA / Chatlytics. Inputs with uppercase suffixes are exceedingly
  rare and a copy-paste glitch (e.g. `@C.US`) is better surfaced as a
  validation error than silently accepted. Decision: pattern is
  case-SENSITIVE for JSON Schema compatibility; the JS bundle's `/i`
  flag is permissive in JS but Python's `re` module and jsonschema's
  `pattern` validator are case-sensitive by default. Operator brief
  says "match the JS regex" — we match the **shape**, not the JS
  case-insensitivity flag (which is non-portable across schema
  validators). Matches the JS bundle's behavior for all real-world
  WAHA JIDs (which are lowercase).
- `^.+` requires at least one character before the `@`. This rejects
  bare `@c.us`, ambiguously-typed inputs, and copy-paste accidents
  where only the suffix made it through.
- `$` anchored so trailing whitespace, extra-suffix variants
  (`@c.usx`), and JID-shaped substrings inside a longer string all
  fail validation.

### D2 — Validator pattern choice (established mechanism in tools.py)

The tools.py module declares JSON Schemas as plain dicts and validates
via `jsonschema.Draft202012Validator`. There is **NO Pydantic** in
use anywhere in `tools.py`. The established mechanism is the
`pattern` keyword on the JSON Schema string type, exposed via the
`_chat_id_field()` helper.

**Decision:** Tighten the existing `_CHAT_ID_PATTERN` constant +
`_chat_id_field()` helper. All 15 chatId schema sites already call
`_chat_id_field()`, so the change lands in ONE place. Do NOT introduce
Pydantic, `Annotated`, or `field_validator` — the brief explicitly
says "use whichever pattern is already established."

For `messageId`, the `_message_id_field()` helper stays as-is — it
uses the v2.1 permissive `_CHAT_ID_PATTERN` (rejects empty/control
chars only). Per the brief, do NOT over-tighten beyond what the JS
canonical does. The JS bundle does NOT regex-validate WhatsApp
messageIds — it just accepts them as strings — so the Python plugin
matches.

### D3 — Error message text (human-friendly, points to chatlytics_search)

JSON Schema `pattern` rejection produces a `ValidationError` with a
message like `'12025551234' does not match '...'`. To make this
operator-friendly, we add a `description` on the field that names the
expected format AND tells the caller where to resolve names/phones:

```
"WhatsApp JID. Format: <id>@<suffix> where suffix is one of "
"c.us (1:1), g.us (groups), lid (NOWEB linked-id), newsletter (channels). "
"Phones and display names are rejected — use chatlytics_search first "
"to resolve them to a JID."
```

The Hermes framework surfaces the schema's `description` field in its
error rendering for the LLM, so the actionable guidance reaches the
caller without needing a custom exception path.

### D4 — Test class plan (NEW `TestJidValidation`)

Add a NEW test class `TestJidValidation` in
`tests/test_tool_schemas.py` with at least **8 parametrized cases**:

| # | Input                       | Expected     | Family / reason             |
|---|-----------------------------|--------------|-----------------------------|
| 1 | `"972501234567@c.us"`       | VALID        | 1:1 contact                 |
| 2 | `"120363012345678901@g.us"` | VALID        | group                       |
| 3 | `"123456789012345@lid"`     | VALID        | NOWEB linked-id             |
| 4 | `"123456789@newsletter"`    | VALID        | channels / newsletters      |
| 5 | `"12025551234"`             | INVALID      | bare phone — was permissive in v2.1 |
| 6 | `"Omer Nesher"`             | INVALID      | display name — was permissive in v2.1 |
| 7 | `""`                        | INVALID      | empty (still rejected as before)    |
| 8 | `"12025551234@s.whatsapp.net"` | INVALID  | JID-shaped, wrong suffix    |

Parametrize via `pytest.mark.parametrize` for compactness.

### D5 — Existing test updates (carry-forward from v2.1)

The v2.1 file `tests/test_validation.py` (HERMES-10) has three tests
that exercise the **permissive accept-set**:

- `test_media_chat_id_accepts_phone_number` — `"+1234567890"`
- `test_media_chat_id_accepts_group_name` — `"My Group Name"`
- (Implicit: any test passing a phone-style chatId to a tool handler.)

These MUST be flipped from accept-assertions to **reject-assertions**
in v3.0 with a comment:
```python
# v3.0 schema tightening — was bare phone in v2.1, now rejected.
# Caller must resolve via chatlytics_search first.
```

Plus any test using a bare phone or display name in `tests/test_tools.py`
or `tests/test_outbound.py` must be updated to use a proper JID
(`"1234567890@c.us"`). Same `# v3.0 schema tightening` comment.

### D6 — Scope guards (DO NOT TOUCH)

- **Phase 13 — `get_chat_info` return shape** — already shipped. Do
  NOT modify `chatlytics_get_chat_info` wrapper or
  `adapter.get_chat_info`.
- **Phase 15 — adapter `send_*` collapse** — not this phase.
- **Version bump in `pyproject.toml` / `plugin.yaml`** — Phase 19
  owns release bumps.
- **CHANGELOG.md** — append a bullet under `## [Unreleased] / Breaking`
  next to the Phase 13 entry. Phase 19 finalizes the release notes.
- **No git push / no publish.**

### D7 — Tool count and contract invariants

- Tool surface stays at **21 tools** — `assert len(TOOLS) == 21` still
  holds; this phase only tightens existing schemas, no add/remove.
- All tool handlers still return `{"success": bool, ...}` — schema
  validation rejects BEFORE the handler runs, so the handler-level
  contract is untouched.
- Phase 13's `_error: "<code>"` shape applies only to
  `chatlytics_get_chat_info`. Schema-layer validation rejections
  surface as Hermes-framework-level errors, not handler-level errors.
  Do NOT manually catch `jsonschema.ValidationError` to inject
  `_error: "validation_error"` — let Pydantic + Hermes handle the
  schema layer naturally.

## Code context (files touched + established patterns)

### Files to modify

| File | Change |
|------|--------|
| `src/chatlytics_hermes/tools.py` | Replace `_CHAT_ID_PATTERN` with `_JID_PATTERN`. Update `_chat_id_field()` description + pattern. Keep `_message_id_field()` permissive (rename its internal pattern constant to `_PERMISSIVE_ID_PATTERN` so the dual intent is explicit). Update the v2.1 helper docstring to reflect the new strict accept-set. |
| `tests/test_tool_schemas.py` | Add new `TestJidValidation` class with 8+ parametrized cases. Existing schema-validation tests unchanged. |
| `tests/test_validation.py` | Flip permissive-accept tests to strict-reject. Add `# v3.0 schema tightening` comments. |
| `tests/test_outbound.py` / `tests/test_tools.py` / others | Audit for any test passing a bare phone or display name as `chatId`; switch to a proper JID. Add `# v3.0 schema tightening` comment. |
| `CHANGELOG.md` | Add bullet under `## [Unreleased] / Breaking`. |
| `README.md` | (Optional this phase — Phase 19 owns the README rewrite.) Skip unless a glaring contradiction; if mentioned, add a single sentence pointing to the new strict JID rule + `chatlytics_search`. |

### Established patterns

- **JSON Schema validation via jsonschema.Draft202012Validator** —
  schemas declared as module-level dicts, `pattern` keyword for string
  format constraints. See `_chat_id_field()` at `tools.py:217-232`.
- **Helper function for reusable schema fragments** — `_chat_id_field`,
  `_message_id_field`, `_media_schema`. New constraints land in the
  helper, not at each call site.
- **Pytest parametrization** — `tests/test_validation.py` and
  `tests/test_tool_schemas.py` already use direct test functions; the
  new `TestJidValidation` will use `pytest.mark.parametrize` on a
  single class.

### Reference: JS bundle canonical regex

Path: `C:/Users/omern/.claude/plugins/marketplaces/chatlytics-claude-code/servers/chatlytics-mcp.js:58-61`:

```js
function looksLikeJid(s) {
  if (typeof s !== "string" || s.length === 0) return false;
  return /@(c\.us|g\.us|lid|newsletter)$/i.test(s);
}
```

The JS bundle's `looksLikeJid` is also used by `resolveChatId` to
decide whether to skip the search-based resolution. The Python plugin
does NOT have a `resolveChatId` equivalent — schema-layer rejection
**IS** the equivalent guarantee: the caller MUST pre-resolve via
`chatlytics_search` before invoking any chatId-bearing tool.

### messageId rule (parity with JS bundle)

The JS bundle (`servers/chatlytics-mcp.js`) does NOT regex-validate
WhatsApp `messageId`. It treats messageId as an opaque string. WAHA's
messageId format (`<true|false>_<jid>_<msgid>`) is informally
documented but the JS canonical does NOT enforce it.

**Per the operator brief: "if the JS bundle treats messageId loosely,
the Python plugin should too — DO NOT over-tighten beyond what the JS
canonical does."** So `_message_id_field()` stays permissive (empty +
control-char rejection only, matching the v2.1 helper).

## Specifics (sequencing)

- **Phase 13 just landed** the `_error: "<code>"` shape on
  `chatlytics_get_chat_info`. Schema-layer rejection in this phase
  flows through Hermes's native error path (jsonschema.ValidationError
  surfaces before the handler runs), NOT through the new `_error`
  channel. The two are complementary, not coupled.
- **Phase 15 (next)** collapses adapter `send_*` methods. Independent
  of this phase's schema work.

## Deferred

**None** — scope is locked to the chatId/messageId schema tightening
per the operator brief. The wider rollout of strict validation (e.g.
URL format for `mediaUrl`, emoji-set check for `chatlytics_react`) is
not in scope and not on the v3.0 backlog.
