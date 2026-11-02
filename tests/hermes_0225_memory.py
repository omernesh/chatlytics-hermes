"""Excerpts of hermes-agent v0.22.5 (MIT), copied verbatim from the tag this plugin runs on.

The shape the memory strip pins. DO NOT edit to match a new Hermes: when a new version
changes these, ``memory_strip._probe_host`` refuses summons on it, and that is the moment
to read the new code, update the strip and add the new shape next to this one.

Sources, by section: ``agent/memory_context.py``, ``tools/memory_tool_store.py``,
``agent/turn_context.py``, and ``hermes_cli/plugins_dispatch.py`` with
``hermes_cli/middleware.py`` for the middleware chain. ``_drop_repeated_recall_lines``
and the surrounding classes are left out.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional

# --- agent/memory_context.py -------------------------------------------------------


def build_memory_context_block(raw_context: str) -> str:
    """Wrap prefetched memory in a fenced block with system note."""
    if not raw_context or not raw_context.strip():
        return ""
    clean = raw_context
    return (
        "<memory-context>\n"
        "[System note: The following is recalled memory context, "
        "NOT new user input. Treat as authoritative reference data — "
        "this is the agent's persistent memory and should inform all responses.]\n\n"
        f"{clean}\n"
        "</memory-context>"
    )


# --- tools/memory_tool_store.py ----------------------------------------------------

MEMORY_BLOCK_HEADERS = {
    "memory": "MEMORY (your personal notes)", "user": "USER PROFILE (who the user is)"}

ENTRY_DELIMITER = "\n§\n"


class MemoryStore:
    def __init__(self, memory_char_limit: int = 2200, user_char_limit: int = 1375) -> None:
        self.memory_char_limit, self.user_char_limit = memory_char_limit, user_char_limit

    def _char_limit(self, target: str) -> int:
        return self.user_char_limit if target == "user" else self.memory_char_limit

    def _usage_pct(self, target: str, current: int) -> str:
        limit = self._char_limit(target)
        return f"{min(100, int((current / limit) * 100)) if limit > 0 else 0}% — {current:,}/{limit:,} chars"

    def _render_block(self, target: str, entries: List[str]) -> str:
        """System prompt block: header + usage indicator + entries ("" when empty)."""
        if not entries:
            return ""
        content, sep = ENTRY_DELIMITER.join(entries), "═" * 46
        title = MEMORY_BLOCK_HEADERS["user" if target == "user" else "memory"]
        return f"{sep}\n{title} [{self._usage_pct(target, len(content))}]\n{sep}\n{content}"


# --- agent/turn_context.py ---------------------------------------------------------


def compose_multimodal_context_part(ext_prefetch_cache: str, plugin_user_context: str) -> Optional[str]:
    fenced = build_memory_context_block(ext_prefetch_cache) if ext_prefetch_cache else ""
    injections = [part for part in (fenced, plugin_user_context) if part]
    return "\n\n".join(injections) if injections else None


def compose_user_api_content(content: Any, ext_prefetch_cache: str, plugin_user_context: str) -> Optional[str]:
    if not isinstance(content, str):
        return None
    injection = compose_multimodal_context_part(ext_prefetch_cache, plugin_user_context)
    return None if injection is None else content + "\n\n" + injection


def effective_system(active_system_prompt: str, ephemeral_system_prompt: str) -> str:
    effective = active_system_prompt or ""
    if ephemeral_system_prompt:
        effective = (effective + "\n\n" + ephemeral_system_prompt).strip()
    return effective


# --- hermes_cli/plugins_dispatch.py + hermes_cli/middleware.py -----------------------


def apply_request_chain(callbacks: List[Callable[..., Any]], request: dict) -> dict:
    """invoke_middleware + _apply_request_chain, reduced to what decides the payload.

    A raising callback is swallowed and the request goes on as it was.
    """
    current = request
    for cb in callbacks:
        try:
            ret = cb(request=current, original_request=request, turn_id="t")
        except (Exception, SystemExit):
            continue
        if isinstance(ret, dict) and isinstance(ret.get("request"), dict):
            current = ret["request"]
    return current


# What ``apply_llm_request_middleware`` and ``has_middleware`` read: the registered callbacks.
MIDDLEWARE: Dict[str, List[Callable[..., Any]]] = {}


def has_middleware(kind: str) -> bool:
    return bool(MIDDLEWARE.get(kind))


def apply_llm_request_middleware(request: dict, **context: Any) -> Any:
    callbacks = MIDDLEWARE.get("llm_request", [])
    current = request
    for cb in callbacks:
        try:
            ret = cb(request=current, original_request=request, **context)
        except (Exception, SystemExit):
            continue
        if isinstance(ret, dict) and isinstance(ret.get("request"), dict):
            current = ret["request"]
    return SimpleNamespace(payload=current, original_payload=request)
