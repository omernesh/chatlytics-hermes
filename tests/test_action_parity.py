"""Action-catalog parity test (phase 168 Half B).

Reads src/action-catalog.ts (or channel.ts ACTION_HANDLERS / EXPOSED_ACTIONS
where the catalog refers to them) and asserts that every action name is
either implemented in :data:`IMPLEMENTED_ACTIONS` or explicitly listed in
:data:`INTENTIONALLY_UNMAPPED`.

Drift here means an LLM tool name in the catalog has no Python wrapper —
that's the regression this test exists to catch.
"""

from __future__ import annotations

import re
from pathlib import Path

# Reuses the gateway.* sys.modules stubs.
from tests import test_adapter  # noqa: F401

import pytest

from chatlytics_adapter.actions import (
    IMPLEMENTED_ACTIONS,
    INTENTIONALLY_UNMAPPED,
)


REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_action_catalog() -> set[str]:
    """Extract all action names registered in src/channel.ts's ACTION_HANDLERS.

    The action-catalog.ts file builds its catalog from ``EXPOSED_ACTIONS``
    plus ``Object.keys(ACTION_HANDLERS)``. Walking the actual handler map
    is more authoritative than the catalog's PARAM_CATALOG (which only
    documents ~80 entries; some handlers exist without docs).
    """
    channel_ts = REPO_ROOT / "src" / "channel.ts"
    if not channel_ts.exists():
        pytest.skip(f"channel.ts not found at {channel_ts}")

    text = channel_ts.read_text(encoding="utf-8")

    # Locate the ACTION_HANDLERS object body.
    handlers_start = text.find("export const ACTION_HANDLERS")
    assert handlers_start >= 0, "ACTION_HANDLERS not found in channel.ts"

    # Find the matching closing brace via a brace counter — the value is
    # an object literal so we can rely on `{` ... `};` balance.
    open_brace = text.find("{", handlers_start)
    depth = 0
    end = -1
    for i in range(open_brace, len(text)):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    assert end > open_brace, "Could not find ACTION_HANDLERS closing brace"
    body = text[open_brace + 1 : end]

    # Match `actionName: (...)` keys at the top level. We strip
    # comments first so commented-out entries don't pollute the set.
    body = re.sub(r"//[^\n]*", "", body)
    body = re.sub(r"/\*[\s\S]*?\*/", "", body)

    # Top-level keys: identifier followed by `:` then an arrow function
    # whose params start with `(p` or `(p,` — every handler in this map has
    # signature `(p, cfg, aid)` or `(p, cfg)`. This filters out nested
    # `type: (p.scope as ...)` literals inside handler bodies.
    name_re = re.compile(
        r"^\s{2,4}([a-zA-Z_][a-zA-Z0-9_]*)\s*:\s*(?:async\s*)?\(p\b",
        re.MULTILINE,
    )
    names = {m.group(1) for m in name_re.finditer(body)}
    # Drop the readMessages-internal alias `type` — it's a return-object
    # field, not a handler key. Defensive even after the regex tighten.
    names.discard("type")
    return names


def _load_exposed_actions() -> set[str]:
    """Extract STANDARD_ACTIONS + UTILITY_ACTIONS — the LLM-facing surface."""
    channel_ts = REPO_ROOT / "src" / "channel.ts"
    text = channel_ts.read_text(encoding="utf-8")
    found: set[str] = set()
    for var in ("STANDARD_ACTIONS", "UTILITY_ACTIONS"):
        m = re.search(rf"export const {var}\s*=\s*\[([\s\S]*?)\]", text)
        if not m:
            continue
        body = re.sub(r"//[^\n]*", "", m.group(1))
        body = re.sub(r"/\*[\s\S]*?\*/", "", body)
        for item in re.findall(r'["\']([a-zA-Z_][a-zA-Z0-9_]*)["\']', body):
            found.add(item)
    return found


class TestActionParity:
    def test_every_action_handler_is_mapped_or_documented(self) -> None:
        catalog = _load_action_catalog()
        # The catalog should be sizeable — sanity bound to catch regex drift.
        assert len(catalog) >= 80, (
            f"Action catalog too small ({len(catalog)} actions) — "
            "regex probably broke. Inspect channel.ts ACTION_HANDLERS."
        )

        unmapped = catalog - IMPLEMENTED_ACTIONS - INTENTIONALLY_UNMAPPED
        assert not unmapped, (
            "Action(s) in src/channel.ts ACTION_HANDLERS are neither in "
            "IMPLEMENTED_ACTIONS nor INTENTIONALLY_UNMAPPED — add a wrapper "
            "or document the intentional skip:\n  " + "\n  ".join(sorted(unmapped))
        )

    def test_implemented_actions_actually_exist_in_catalog(self) -> None:
        # Reverse direction: if we claim to implement an action that doesn't
        # exist server-side, the LLM call will 404. Catch typos here.
        catalog = _load_action_catalog()
        # Some IMPLEMENTED_ACTIONS map to standard names (e.g. "send",
        # "react") that LIVE in the gateway's MESSAGE_ACTION_TARGET_MODE
        # map, not in ACTION_HANDLERS. Allow those by also checking
        # STANDARD_ACTIONS + UTILITY_ACTIONS.
        exposed = _load_exposed_actions()
        valid = catalog | exposed
        missing = IMPLEMENTED_ACTIONS - valid
        assert not missing, (
            "IMPLEMENTED_ACTIONS contains names not present in channel.ts "
            "ACTION_HANDLERS or EXPOSED_ACTIONS:\n  "
            + "\n  ".join(sorted(missing))
        )

    def test_intentionally_unmapped_does_not_overlap_implemented(self) -> None:
        overlap = IMPLEMENTED_ACTIONS & INTENTIONALLY_UNMAPPED
        assert not overlap, (
            f"Action(s) listed in BOTH IMPLEMENTED and INTENTIONALLY_UNMAPPED: {overlap}"
        )

    def test_all_typing_voice_actions_documented(self) -> None:
        # Half A is adding `startTyping`/`stopTyping`/`sendVoice`. We
        # implement them in the mixin/adapter — assert presence in
        # IMPLEMENTED_ACTIONS even if the catalog doesn't have them yet
        # (the parity test allows this — Half A merge will close the loop).
        for name in ("startTyping", "stopTyping", "sendVoice"):
            assert name in IMPLEMENTED_ACTIONS, (
                f"{name} must be in IMPLEMENTED_ACTIONS (Half B contract)."
            )
