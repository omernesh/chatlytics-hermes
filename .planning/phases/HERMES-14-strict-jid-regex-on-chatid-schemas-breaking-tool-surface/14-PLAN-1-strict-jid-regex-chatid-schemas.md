---
phase: 14
plan_index: 1
plan_slug: strict-jid-regex-chatid-schemas
title: "Strict JID regex on chatId schemas (BREAKING tool surface)"
project_code: HERMES
milestone: v3.0
status: ready
infra_skip: true
verification: pytest
---

# HERMES-14 Plan 1 — Strict JID regex on `chatId` schemas

## Goal

Replace the v2.1 permissive `_chat_id_field` validator (rejects only
empty strings + C0 control chars) with a strict JID-only validator
matching the sibling JS bundle's canonical regex
`/@(c\.us|g\.us|lid|newsletter)$/i`. Apply to all **15 chatId-bearing
schemas** in `src/chatlytics_hermes/tools.py`. Keep `_message_id_field`
permissive (the JS bundle does NOT regex-validate `messageId` — per
the operator brief, "DO NOT over-tighten beyond what the JS canonical
does").

Closes v2.1 deferred item 2.

## Scope (locked per 14-CONTEXT.md)

**In:**
- `src/chatlytics_hermes/tools.py` — replace `_CHAT_ID_PATTERN` with a
  new strict `_JID_PATTERN`; update `_chat_id_field()` helper
  (description + pattern). Rename the v2.1 permissive constant to
  `_PERMISSIVE_ID_PATTERN` (still used by `_message_id_field()`) so
  the dual intent is explicit at module-load.
- `tests/test_tool_schemas.py` — add new `TestJidValidation` class
  with at least 8 parametrized cases (4 valid + 4 invalid).
- `tests/test_validation.py` — flip the v2.1 permissive-accept tests
  for media chatId (`test_media_chat_id_accepts_phone_number`,
  `test_media_chat_id_accepts_group_name`) to strict-reject. Add
  `# v3.0 schema tightening` comment.
- Any other test that passes a bare phone / display name as `chatId` —
  audit `tests/test_tools.py` + `tests/test_outbound.py` and switch to
  a proper JID, with the same comment.
- `CHANGELOG.md` — append a bullet under `## [Unreleased] / Breaking`.

**Out:**
- `messageId` schema tightening (JS canonical does NOT validate it).
- README rewrite (Phase 19 owns).
- Version bumps in `pyproject.toml` / `plugin.yaml` (Phase 19).
- Pushing to git / publishing.
- Modifying `get_chat_info` (Phase 13 — shipped).
- Modifying adapter `send_*` (Phase 15).
- Catching `jsonschema.ValidationError` to inject `_error: "validation_error"` —
  let the schema layer reject naturally; Hermes framework handles it.

## Invariants (DO NOT REGRESS)

- 98/98 baseline tests still pass (88 from v2.1 + 10 from Phase 13),
  EXCEPT the explicitly-flipped v3.0 schema-tightening tests in
  `test_validation.py` (which keep the same test name but invert the
  assertion, with v3.0 comment).
- `assert len(TOOLS) == 21` invariant in `tools.py` stays satisfied.
- Hermes pin stays `>=0.14,<0.15`.
- All HTTP outbound via `httpx`; aiohttp only for inbound server.
- Phase 13's `_error: "<code>"` contract on `chatlytics_get_chat_info`
  unchanged.

## Tasks (atomic; each commits independently)

### T1 — Replace `_CHAT_ID_PATTERN` with strict `_JID_PATTERN`

**File:** `src/chatlytics_hermes/tools.py`

Locate the existing constant near line 196-214:

```python
# HERMES-10 (05-LOW-02 + PR-MED-01): permissive chatId / messageId validation.
# ...
_CHAT_ID_PATTERN: str = r"^[^\x00-\x1f\x7f]+$"
```

Replace the constant block with:

```python
# HERMES-14 (v3.0 BREAKING — see CHANGELOG entry "BREAKING — strict JID
# regex on chatId schemas"): replaces v2.1's permissive
# ``_CHAT_ID_PATTERN`` (which only rejected empty + control chars) with
# a strict JID-only validator. Matches the sibling JS bundle's canonical
# ``looksLikeJid`` regex at
# ``C:/Users/omern/.claude/plugins/marketplaces/chatlytics-claude-code/servers/chatlytics-mcp.js:58-61``:
#
#     function looksLikeJid(s) {
#       if (typeof s !== "string" || s.length === 0) return false;
#       return /@(c\.us|g\.us|lid|newsletter)$/i.test(s);
#     }
#
# Suffix families (lowercase, per WAHA convention):
#   - ``@c.us``       — 1:1 contacts
#   - ``@g.us``       — groups
#   - ``@lid``        — NOWEB linked-id form
#   - ``@newsletter`` — channels / newsletters
#
# Phones, display names, and ambiguous strings are now rejected at the
# schema boundary. Callers MUST pre-resolve via ``chatlytics_search``
# before invoking any chatId-bearing tool.
#
# Note on case-sensitivity: JSON Schema ``pattern`` flags are
# implementation-defined; jsonschema's Python validator treats the
# pattern as case-sensitive by default. The JS ``/i`` flag is permissive
# but real-world WAHA JIDs are lowercase, so case-sensitivity matches
# the JS bundle's behavior for every legitimate input.
_JID_PATTERN: str = r"^.+@(c\.us|g\.us|lid|newsletter)$"

# ``_message_id_field`` stays on the v2.1 permissive validator (empty +
# control-char rejection only). The JS canonical bundle does NOT regex-
# validate WhatsApp messageIds — they are treated as opaque strings —
# so the Python plugin matches. Renamed from ``_CHAT_ID_PATTERN`` to
# make the dual intent explicit; the messageId helper still uses it.
_PERMISSIVE_ID_PATTERN: str = r"^[^\x00-\x1f\x7f]+$"
```

Update `_chat_id_field()` helper signature + body:

```python
def _chat_id_field(
    description: str = (
        "WhatsApp JID. Format: <id>@<suffix> where suffix is one of "
        "c.us (1:1), g.us (groups), lid (NOWEB linked-id), "
        "newsletter (channels). Phones and display names are rejected — "
        "use chatlytics_search first to resolve them to a JID."
    ),
) -> Dict[str, Any]:
    """Reusable schema fragment for ``chatId`` properties (strict JID).

    HERMES-14 (v3.0 BREAKING): emits a Draft 2020-12 string schema with
    ``minLength: 1`` and a ``pattern`` enforcing the JID suffix families
    (c.us / g.us / lid / newsletter). Inputs that lack a valid suffix —
    bare phones, display names, ambiguous strings — are rejected at
    validation time.

    Callers needing a permissive identifier (e.g. ``messageId``) should
    use :func:`_message_id_field` instead.
    """
    return {
        "type": "string",
        "minLength": 1,
        "pattern": _JID_PATTERN,
        "description": description,
    }
```

Update `_message_id_field()` to use the renamed permissive pattern (no
behavior change — same regex, new name):

```python
def _message_id_field(
    description: str = "Target message identifier.",
) -> Dict[str, Any]:
    """Reusable schema fragment for ``messageId`` properties.

    HERMES-14: stays permissive (empty + control-char rejection only).
    The sibling JS bundle (``looksLikeJid`` at
    ``servers/chatlytics-mcp.js``) does NOT regex-validate WhatsApp
    messageIds — they are treated as opaque strings. Matching that
    behavior here keeps the Python plugin and JS bundle in lockstep.
    """
    return {
        "type": "string",
        "minLength": 1,
        "pattern": _PERMISSIVE_ID_PATTERN,
        "description": description,
    }
```

Also update the per-`chatId` field descriptions that hard-coded the
old "JID, phone, or group identifier" wording. The `_chat_id_field()`
helper default is now correct; any call site that passed a custom
description with the old wording needs updating. Search the module for
`"Chat JID, phone"` and similar and either drop the override (let the
helper default apply) or update the override text.

Specific sites to update (call-site descriptions that mention "phone"):
- `SEND_SCHEMA.properties.chatId` — currently:
  `_chat_id_field("Chat JID (e.g. 12036...@g.us, 9725...@c.us) or phone.")` →
  drop the "or phone" hint, e.g.:
  `_chat_id_field("Chat JID (e.g. 12036...@g.us, 9725...@c.us). Use chatlytics_search to resolve names/phones.")`
- All other `_chat_id_field()` call sites currently pass no description
  or pass `"Optional chat context."`; those need no update.

Acceptance:
- `from chatlytics_hermes.tools import _JID_PATTERN, _PERMISSIVE_ID_PATTERN, _chat_id_field, _message_id_field` works.
- `_chat_id_field()["pattern"] == _JID_PATTERN`.
- `_message_id_field()["pattern"] == _PERMISSIVE_ID_PATTERN`.
- All 15 chatId schemas still validate as Draft 2020-12 schemas (covered
  by existing `test_every_tool_has_valid_json_schema`).
- `assert len(TOOLS) == 21` still holds.

### T2 — Add `TestJidValidation` class to `tests/test_tool_schemas.py`

**File:** `tests/test_tool_schemas.py`

Append a new test class after the existing module-level tests. Use
`pytest.mark.parametrize` for compactness. Cover at least the 8 cases
listed in 14-CONTEXT D4:

```python
# ---------------------------------------------------------------------------
# HERMES-14: strict JID regex on chatId schemas (BREAKING)
# ---------------------------------------------------------------------------
#
# v3.0 BREAKING — see CHANGELOG entry "BREAKING — strict JID regex on
# chatId schemas". The v2.1 permissive accept-set (anything non-empty,
# no control chars) is replaced with strict JID-only validation
# matching the sibling JS bundle's ``looksLikeJid`` regex.

import pytest

from chatlytics_hermes.tools import SEND_SCHEMA


class TestJidValidation:
    """Strict JID regex enforcement on chatId schemas (HERMES-14)."""

    @pytest.mark.parametrize(
        "chat_id,family",
        [
            ("972501234567@c.us", "c.us — 1:1 contact"),
            ("120363012345678901@g.us", "g.us — group"),
            ("123456789012345@lid", "lid — NOWEB linked-id"),
            ("123456789@newsletter", "newsletter — channels"),
        ],
    )
    def test_jid_accepted_for_each_suffix_family(
        self, chat_id: str, family: str
    ) -> None:
        """All 4 JID suffix families validate cleanly."""
        validator = jsonschema.Draft202012Validator(SEND_SCHEMA)
        # Should NOT raise.
        validator.validate({"chatId": chat_id, "text": "hi"})

    @pytest.mark.parametrize(
        "chat_id,reason",
        [
            ("12025551234", "bare phone — was permissive in v2.1"),
            ("Omer Nesher", "display name — was permissive in v2.1"),
            ("", "empty string"),
            ("12025551234@s.whatsapp.net", "JID-shaped but wrong suffix"),
            ("@c.us", "missing local part (id before @)"),
            ("12025551234@c.us ", "trailing whitespace breaks the anchor"),
            ("12025551234@C.US", "uppercase suffix (case-sensitive pattern)"),
            ("prefix-12025551234@c.us-suffix", "suffix not at end of string"),
        ],
    )
    def test_jid_rejected_for_invalid_inputs(
        self, chat_id: str, reason: str
    ) -> None:
        """v3.0 schema tightening — these inputs were permissive in v2.1.

        Callers must pre-resolve names/phones via ``chatlytics_search``
        before invoking any chatId-bearing tool.
        """
        validator = jsonschema.Draft202012Validator(SEND_SCHEMA)
        with pytest.raises(jsonschema.ValidationError):
            validator.validate({"chatId": chat_id, "text": "hi"})

    def test_jid_validator_applied_to_all_15_chat_id_schemas(self) -> None:
        """Audit: every schema with a ``chatId`` property uses the strict pattern.

        Guards against drift where a new chatId-bearing tool is added
        with a hand-rolled string schema instead of via ``_chat_id_field()``.
        """
        from chatlytics_hermes.tools import TOOLS, _JID_PATTERN

        chat_id_schemas = []
        for name, schema, _ in TOOLS:
            props = schema.get("properties", {})
            if "chatId" in props:
                chat_id_schemas.append((name, props["chatId"]))

        # Sanity: at least the 15 chatId-bearing tools enumerated in
        # 14-CONTEXT D-section are present.
        assert len(chat_id_schemas) >= 15, (
            f"Expected at least 15 chatId-bearing schemas; "
            f"found {len(chat_id_schemas)}: "
            f"{[n for n, _ in chat_id_schemas]}"
        )

        for name, field in chat_id_schemas:
            assert field.get("pattern") == _JID_PATTERN, (
                f"{name}: chatId schema must use the strict _JID_PATTERN; "
                f"got pattern={field.get('pattern')!r}. Use _chat_id_field()."
            )
```

Acceptance:
- `pytest tests/test_tool_schemas.py::TestJidValidation -v` — all 8+
  parametrized cases pass + the audit test passes.
- The audit test guards against future drift (adding a chatId-bearing
  tool with a hand-rolled schema fails the assertion).

### T3 — Flip permissive-accept tests in `tests/test_validation.py`

**File:** `tests/test_validation.py`

Two existing tests assert v2.1's permissive accept of phones and
display names on the media chatId schema. Flip them to strict-reject
with the `# v3.0 schema tightening` comment:

Find (around line 188-201):
```python
def test_media_chat_id_accepts_phone_number() -> None:
    """05-LOW-02: bare phone number passes (Chatlytics resolves these)."""
    validator = jsonschema.Draft202012Validator(SEND_IMAGE_SCHEMA)
    validator.validate(
        {"chatId": "+1234567890", "mediaUrl": "https://example.com/a.png"}
    )


def test_media_chat_id_accepts_group_name() -> None:
    """05-LOW-02: display-name strings pass (permissive accept-set)."""
    validator = jsonschema.Draft202012Validator(SEND_IMAGE_SCHEMA)
    validator.validate(
        {"chatId": "My Group Name", "mediaUrl": "https://example.com/a.png"}
    )
```

Replace with:
```python
def test_media_chat_id_rejects_phone_number() -> None:
    """v3.0 schema tightening — was bare phone in v2.1, now rejected.

    HERMES-14 (BREAKING): bare phone numbers no longer pass the chatId
    validator. Callers must pre-resolve via ``chatlytics_search`` to
    obtain a JID first. See CHANGELOG entry "BREAKING — strict JID
    regex on chatId schemas".
    """
    validator = jsonschema.Draft202012Validator(SEND_IMAGE_SCHEMA)
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(
            {"chatId": "+1234567890", "mediaUrl": "https://example.com/a.png"}
        )


def test_media_chat_id_rejects_group_name() -> None:
    """v3.0 schema tightening — was display name in v2.1, now rejected.

    HERMES-14 (BREAKING): display-name strings no longer pass the
    chatId validator. Use ``chatlytics_search`` first.
    """
    validator = jsonschema.Draft202012Validator(SEND_IMAGE_SCHEMA)
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(
            {"chatId": "My Group Name", "mediaUrl": "https://example.com/a.png"}
        )
```

The other tests in Section 2 (rejects empty, rejects control chars,
accepts JID format, messaging-tool rejects empty) all stay correct
under the new contract — JID format `1234567890@c.us` still validates,
empty + control chars are still rejected. No further changes there.

Update the section 2 docstring at the top of the file (line 1-15) to
reflect the v3.0 contract:

Find:
```python
- Tool schema validation of ``chatId`` / ``messageId`` (6 tests; 05-LOW-02 + PR-MED-01)
```

Replace with:
```python
- Tool schema validation of ``chatId`` / ``messageId`` (6 tests; 05-LOW-02 + PR-MED-01;
  v3.0 HERMES-14 tightened ``chatId`` to JID-only — see ``TestJidValidation``
  in ``test_tool_schemas.py`` for the canonical accept-set tests; the two
  v2.1 permissive-accept assertions in this file are now flipped to
  strict-reject assertions with a ``# v3.0 schema tightening`` comment)
```

Acceptance:
- `pytest tests/test_validation.py -q` — all 19 tests pass (with the
  two flipped tests asserting `pytest.raises(ValidationError)`).
- Test count unchanged (19); test names changed
  (`*_accepts_* → *_rejects_*`).

### T4 — Audit + update tests using bare phones / display names

**Files:** `tests/test_outbound.py`, `tests/test_tools.py`, `tests/test_media.py`

Grep the test suite for `chatId` values that use bare phones (no `@`)
or display names. Replace each with a proper JID
(`"1234567890@c.us"`) and add the `# v3.0 schema tightening` comment.

Process:
1. `Grep -n "chatId.*=.*[\"']" tests/` to find every chatId assignment.
2. For each match, check if the value is JID-shaped (has `@` followed
   by `c.us` / `g.us` / `lid` / `newsletter`). If NOT, update.
3. Inspect the surrounding test — many tests assert on the request body
   the gateway receives. If the test mocks a gateway endpoint and asserts
   the body, the updated JID flows through and the assertions still hold.
4. Tests that exercise the schema layer directly (not the handler)
   already live in `test_validation.py` + `test_tool_schemas.py` —
   no further updates needed there.

Important: existing tests that pass `chatId` to a TOOL HANDLER
(`chatlytics_send`, etc.) bypass schema validation because the
handlers are called directly in unit tests, not through the Hermes
framework's schema-validated dispatch. So those tests still work even
with bare phones. However, per the brief: "Existing tests that pass
phone-number-style chatIds MUST be updated to use proper JIDs, with a
`# v3.0 schema tightening — was bare phone in v2.1` comment." This is
a doc-discipline update, not a correctness fix.

Update strategy: find every test-side `chatId` literal that is NOT
JID-shaped and update it. If the test exercises a schema directly,
inversion (accept → reject) is mandatory; if it just feeds the handler,
the JID swap is mechanical.

Acceptance:
- `pytest tests/ -q` — all tests pass.
- Grep `tests/` for chatId literals: every non-test_validation /
  non-test_tool_schemas literal is JID-shaped OR has the
  `# v3.0 schema tightening` comment explaining why (e.g. negative
  test that intentionally feeds a bare phone to assert rejection).

### T5 — Append CHANGELOG entry

**File:** `CHANGELOG.md`

Append a bullet under the existing `## [Unreleased] / ### Breaking`
section (created in Phase 13). The Phase 13 entry should be the first
bullet; this is the second.

Find:
```markdown
## [Unreleased]

### Breaking
- `ChatlyticsAdapter.get_chat_info` now returns `dict | None` ...
```

Append:
```markdown
- `chatlytics_*` tool schemas now enforce strict WhatsApp JID format on
  every `chatId` field, matching the sibling JS bundle's canonical regex
  `/@(c\.us|g\.us|lid|newsletter)$/i`. The v2.1 permissive accept-set
  (which let phones and display names pass through) is gone. All 15
  chatId-bearing tool schemas reject bare phones, display names, and
  JID-shaped-but-wrong-suffix inputs at validation time. Callers must
  pre-resolve names/phones to a JID via `chatlytics_search` before
  invoking any chatId-bearing tool. `messageId` validation is
  unchanged (the JS canonical bundle does not regex-validate
  messageIds; the Python plugin matches). Closes v2.1 deferred item 2.
```

Acceptance:
- `CHANGELOG.md` has the new bullet under `[Unreleased] / Breaking`.
- No release-line bumps (Phase 19 owns 3.0.0 release).

## Verification

After all tasks land:

```bash
cd D:/docker/chatlytics-hermes-split
python -m pytest tests/ -q
```

Expected: 98 baseline (88 v2.1 + 10 Phase 13) + 9 new from
`TestJidValidation` (8 parametrized + 1 audit) = **107 passing tests**,
zero regressions. The two flipped tests in `test_validation.py` keep
their names (changed to `*_rejects_*`) and count, so the net delta is
+9 from `TestJidValidation`.

Sanity import + introspection:
```bash
python -c "from chatlytics_hermes.tools import _JID_PATTERN; print(_JID_PATTERN)"
# Expected: ^.+@(c\.us|g\.us|lid|newsletter)$

python -c "from chatlytics_hermes.tools import TOOLS, _JID_PATTERN; \
  n = sum(1 for _, s, _ in TOOLS if s.get('properties', {}).get('chatId', {}).get('pattern') == _JID_PATTERN); \
  print(f'{n} chatId schemas use _JID_PATTERN (expected >= 15)')"
# Expected: 15 chatId schemas use _JID_PATTERN (expected >= 15)
```

## Risks + mitigations

| Risk | Mitigation |
|------|------------|
| Tests that pass bare phones to handler functions (bypassing schema validation) silently continue to work, masking caller-side breakage | The `TestJidValidation::test_jid_validator_applied_to_all_15_chat_id_schemas` audit guards the schema layer; per-handler unit tests that bypass schema are an acceptable test-layer convenience (the framework path through Hermes IS schema-validated in production). |
| Case-sensitivity divergence from JS bundle (JS uses `/i`, Python pattern doesn't) | Documented in the constant block. Real-world WAHA JIDs are lowercase; uppercase suffixes are a copy-paste glitch better surfaced as validation error than silently accepted. |
| `_message_id_field` skipping tightening looks inconsistent | Documented in the helper docstring + CHANGELOG: the JS canonical does not validate messageId, so matching it keeps the two bundles in lockstep. Tightening messageId beyond JS canonical would be a divergence, not a fix. |
| Existing callers passing bare phones break in production | This is the intended breaking change. CHANGELOG entry documents the migration path (`chatlytics_search` first). |
| Phase 18 cosmetics sweep audit flags test count drift | Acceptable — STATE.md baseline updates from 98 to 107. Tests added are scope-locked deliverables of this phase. |

## Commit plan

One commit per task (T1..T5), each via the standard commit pattern.
Suggested messages (`!` marker per conventional-commits for breaking
changes):

- T1: `feat(14)!: strict JID regex on chatId schemas (matches JS bundle)`
- T2: `test(14): TestJidValidation covers 4 valid + 4 invalid JID cases`
- T3: `test(14): flip permissive-accept tests to strict-reject (v3.0)`
- T4: `test(14): update test-side chatId literals to use JID format`
- T5: `docs(14)!: changelog Unreleased entry for strict JID regex`
