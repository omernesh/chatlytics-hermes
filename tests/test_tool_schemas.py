"""HERMES-05 acceptance tests for the Chatlytics tool surface (schemas).

Covers ROADMAP Phase 5 acceptance criteria:

- AC-1: every registered tool's schema validates under Draft 2020-12
- AC-2: messaging tools require ``chatId`` (or ``messageId`` for
        message-target actions); non-messaging tools require their own
        primary parameter (``action``, ``query``, none)
- AC-7: tool count >= 13 (8 baseline + 5 media) -- locked at 21
- AC-8: every tool name starts with ``chatlytics_``
"""

from __future__ import annotations

import jsonschema
import pytest

from chatlytics_hermes.tools import SEND_SCHEMA, TOOLS


# Sets that drive the per-group required-field check.  Kept module-level
# so a new tool added without updating the required-field test fails the
# completeness assertion at the bottom.
_REQUIRES_CHAT_OR_MESSAGE_ID = {
    "chatlytics_send",
    "chatlytics_reply",
    "chatlytics_react",
    "chatlytics_edit",
    "chatlytics_unsend",
    "chatlytics_pin",
    "chatlytics_unpin",
    "chatlytics_read",
    "chatlytics_delete",
    "chatlytics_poll",
    "chatlytics_send_image",
    "chatlytics_send_voice",
    "chatlytics_send_video",
    "chatlytics_send_file",
    "chatlytics_send_animation",
}
_REQUIRES_QUERY = {"chatlytics_search"}
_REQUIRES_ACTION = {"chatlytics_dispatch"}
_NO_REQUIRED = {
    "chatlytics_directory",
    "chatlytics_actions",
    "chatlytics_health",
    "chatlytics_login",
}


def test_every_tool_has_valid_json_schema() -> None:
    """AC-1: every schema validates under Draft 2020-12 (`check_schema` + compile)."""
    for name, schema, _ in TOOLS:
        # check_schema raises SchemaError on malformed schemas.
        jsonschema.Draft202012Validator.check_schema(schema)
        # Compiling the validator catches subtle refs / keywords that
        # check_schema alone allows.
        validator = jsonschema.Draft202012Validator(schema)
        assert validator is not None, f"{name}: validator construction returned None"


def test_every_tool_has_required_chat_id_field_when_applicable() -> None:
    """AC-2: messaging tools require chatId/messageId; others have their own required."""
    by_name = {name: schema for name, schema, _ in TOOLS}

    for name in _REQUIRES_CHAT_OR_MESSAGE_ID:
        required = set(by_name[name].get("required", []))
        assert (
            "chatId" in required or "messageId" in required
        ), f"{name}: schema 'required' must include chatId or messageId; got {required}"

    for name in _REQUIRES_QUERY:
        required = set(by_name[name].get("required", []))
        assert "query" in required, f"{name}: schema must require 'query'"

    for name in _REQUIRES_ACTION:
        required = set(by_name[name].get("required", []))
        assert "action" in required, f"{name}: schema must require 'action'"

    for name in _NO_REQUIRED:
        required = list(by_name[name].get("required", []))
        assert required == [], f"{name}: schema should have no required fields; got {required}"

    # Completeness guard: every tool name appears in exactly one group.
    classified = (
        _REQUIRES_CHAT_OR_MESSAGE_ID
        | _REQUIRES_QUERY
        | _REQUIRES_ACTION
        | _NO_REQUIRED
    )
    tool_names = {name for name, _, _ in TOOLS}
    unclassified = tool_names - classified
    assert not unclassified, (
        f"Tool(s) not classified for required-field check: {unclassified}. "
        "Update _REQUIRES_* sets in test_tool_schemas.py."
    )


def test_all_tools_namespace_chatlytics_() -> None:
    """AC-8: every tool name starts with ``chatlytics_``."""
    for name, _, _ in TOOLS:
        assert name.startswith("chatlytics_"), (
            f"Tool name '{name}' must start with 'chatlytics_'"
        )


def test_tool_count_matches_claude_code_plugin_baseline() -> None:
    """AC-7: tool count >= 13 (8 baseline + 5 media); locked at 21 for HERMES-05."""
    n = len(TOOLS)
    assert n >= 13, f"Expected at least 13 tools (8 baseline + 5 media); got {n}"
    assert n == 21, (
        f"HERMES-05 locks the tool count at 21; got {n}. "
        "If you intentionally added a tool, update CONTEXT + this assertion."
    )


def test_every_tool_disallows_extra_properties() -> None:
    """Bonus guard: schemas should disallow unknown fields so typos surface early."""
    for name, schema, _ in TOOLS:
        assert schema.get("additionalProperties") is False, (
            f"{name}: schema should set additionalProperties=False"
        )


# ---------------------------------------------------------------------------
# HERMES-14: strict JID regex on chatId schemas (BREAKING)
# ---------------------------------------------------------------------------
#
# v3.0 BREAKING -- see CHANGELOG entry "BREAKING -- strict JID regex on
# chatId schemas". The v2.1 permissive accept-set (anything non-empty,
# no control chars) is replaced with strict JID-only validation
# matching the sibling JS bundle's ``looksLikeJid`` regex.


class TestJidValidation:
    """Strict JID regex enforcement on chatId schemas (HERMES-14)."""

    @pytest.mark.parametrize(
        "chat_id,family",
        [
            ("972501234567@c.us", "c.us -- 1:1 contact"),
            ("120363012345678901@g.us", "g.us -- group"),
            ("123456789012345@lid", "lid -- NOWEB linked-id"),
            ("123456789@newsletter", "newsletter -- channels"),
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
            ("12025551234", "bare phone -- was permissive in v2.1"),
            ("Omer Nesher", "display name -- was permissive in v2.1"),
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
        """v3.0 schema tightening -- these inputs were permissive in v2.1.

        Callers must pre-resolve names/phones via ``chatlytics_search``
        before invoking any chatId-bearing tool.
        """
        validator = jsonschema.Draft202012Validator(SEND_SCHEMA)
        with pytest.raises(jsonschema.ValidationError):
            validator.validate({"chatId": chat_id, "text": "hi"})

    def test_jid_validator_applied_to_all_15_chat_id_schemas(self) -> None:
        """Audit: every schema with a ``chatId`` property uses the strict pattern.

        Guards against drift where a new chatId-bearing tool is added
        with a hand-rolled string schema instead of via
        ``_chat_id_field()``.
        """
        from chatlytics_hermes.tools import _JID_PATTERN

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
