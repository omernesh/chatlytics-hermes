"""Keep the owner's memory out of a turn whose reply reaches a third party.

When the owner summons a bot from their own number (``trigger_source: "from_me"``,
see ``trigger_context.py``), the reply goes into the owner's chat with someone else.
hermes-agent injects memory into every turn regardless of who reads the reply:

- ``<memory-context>`` fences on user messages, one per turn, replayed from history;
- the built-in MEMORY and USER PROFILE blocks, followed by the memory provider's
  block, in the system prompt, which is built once per session and frozen.

hermes-agent 0.22.5 has no per-turn switch. ``skip_memory`` is a constructor option,
the gateway never passes it, and the frozen prompt outlives any single turn. The one
supported point that covers all of it is ``llm_request`` middleware, which sees the
final provider request and runs again for every attempt, including after a fallback
to another model. So the rewrite happens here, and only when the request itself says
the turn is summoned: the context line rides ``channel_prompt`` into the system
prompt, so its marker is present on exactly those requests.

Fail closed, because a silent no-op after a hermes-agent upgrade is the main risk:

- middleware errors are swallowed by the host and the request goes out unchanged, so
  nothing here may raise on a summoned request: it falls back to :func:`_lockdown`;
- :func:`strip_ready` runs the real host renderers through the strip once per process.
  If that fails, :func:`summon_refusal` drops the summoned turn before it is dispatched;
- anything shaped like memory that the exact patterns miss is cut, never passed on.

DO NOT widen this to other turns without an explicit decision (chatlytics#952).
"""

from __future__ import annotations

import inspect
import logging
import re
from dataclasses import dataclass
from typing import Any, Callable, List, Mapping, Optional, Tuple

from .trigger_context import FROM_ME, TURN_CONTEXT_MARKER, render_trigger_context

logger = logging.getLogger(__name__)

LLM_REQUEST = "llm_request"

_FENCE = re.compile(r"\s*<\s*memory-context\s*>[\s\S]*?<\s*/\s*memory-context\s*>", re.IGNORECASE)
_STRAY_FENCE_TAG = re.compile(r"\s*<\s*/?\s*memory-context\s*>", re.IGNORECASE)
# hermes-agent tools/memory_tool_store.py: MEMORY_BLOCK_HEADERS and _render_block.
_BLOCK_START = re.compile(
    r"^(?:═+\n)?(?:MEMORY \(your personal notes\)|USER PROFILE \(who the user is\))",
    re.MULTILINE,
)
# What follows the memory parts in hermes-agent agent/system_prompt.py, in that order.
# The LAST match of the first anchor present ends the cut, so an entry quoting an anchor
# cannot stop it early. Without any anchor the cut runs to the context line.
_TAIL_ANCHORS = tuple(
    re.compile(pattern, re.MULTILINE)
    for pattern in (
        r"^<!-- hermes-plugin-sections:start -->",
        r"^Active Hermes profile:",
        r"^Conversation started:",
        r"^# Hermes runtime environment",
    )
)
_SYSTEM_ROLES = frozenset({"system", "developer"})
_SIGNED_KEYS = ("signature", "encrypted_content")
_SIGNED_TYPES = frozenset({"thinking", "redacted_thinking", "reasoning"})
_LOCKDOWN_TEXT = "Reply with one short line saying you cannot answer right now."


@dataclass
class StripReport:
    fences: int = 0
    blocks: int = 0
    stray_tags: int = 0
    unanchored: int = 0

    @property
    def changed(self) -> bool:
        return bool(self.fences or self.blocks or self.stray_tags)


def _strip_fences(text: str, report: StripReport) -> str:
    text, count = _FENCE.subn("", text)
    report.fences += count
    stray = _STRAY_FENCE_TAG.search(text)
    if stray:
        # An unterminated fence: the rest of this string may be memory.
        report.stray_tags += 1
        text = text[: stray.start()]
    return text


def _block_end(text: str, after: int, report: StripReport) -> int:
    marker = text.find(TURN_CONTEXT_MARKER, after)
    bound = text.rfind("\n", after, marker) + 1 if marker >= 0 else len(text)
    bound = max(bound, after)
    for anchor in _TAIL_ANCHORS:
        last = None
        for last in anchor.finditer(text, after, bound):
            pass
        if last is not None:
            return last.start()
    report.unanchored += 1
    return bound


def _strip_system(text: str, report: StripReport) -> str:
    text = _strip_fences(text, report)
    while (match := _BLOCK_START.search(text)) is not None:
        report.blocks += 1
        end = _block_end(text, match.end(), report)
        head, tail = text[: match.start()].rstrip(), text[end:]
        text = f"{head}\n\n{tail}" if head and tail else head or tail
    return text


def _map_strings(value: Any, fn: Callable[[str], str]) -> Any:
    """A copy of ``value`` with ``fn`` applied to every string. Never edits in place."""
    if isinstance(value, str):
        return fn(value)
    if isinstance(value, dict):
        if value.get("type") in _SIGNED_TYPES or any(k in value for k in _SIGNED_KEYS):
            return value  # A provider-signed block is rejected if edited.
        return {k: _map_strings(v, fn) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        kept: List[Any] = []
        for item in value:
            mapped = _map_strings(item, fn)
            # A part that held only a fence must go: providers reject empty text parts.
            emptied = (
                isinstance(item, dict)
                and isinstance(item.get("text"), str)
                and item["text"].strip()
                and isinstance(mapped, dict)
                and not str(mapped.get("text", "")).strip()
            )
            if not emptied:
                kept.append(mapped)
        return kept
    return value


def _strings(value: Any) -> List[str]:
    found: List[str] = []
    _map_strings(value, lambda s: found.append(s) or s)
    return found


def is_summoned_request(request: Mapping[str, Any]) -> bool:
    """Does the system prompt carry the context line?

    Only the system prompt counts, so a contact pasting the marker into a chat cannot
    switch memory off. When no system prompt can be located the shape is new to us, so
    the whole request is searched: a false positive costs memory, not privacy.
    """
    system: List[str] = []
    located = False
    for key in ("system", "instructions"):
        if key in request:
            located = True
            system += _strings(request[key])
    for key in ("messages", "input"):
        items = request.get(key)
        for item in items if isinstance(items, (list, tuple)) else ():
            if isinstance(item, dict) and item.get("role") in _SYSTEM_ROLES:
                located = True
                system += _strings(item)
    if not located:
        system = _strings({k: v for k, v in request.items() if k != "tools"})
    return any(TURN_CONTEXT_MARKER in text for text in system)


def strip_summoned_request(request: Mapping[str, Any]) -> Tuple[dict, StripReport]:
    """A copy of ``request`` without memory. ``tools`` are not touched (see README)."""
    report = StripReport()

    def fences(text: str) -> str:
        return _strip_fences(text, report)

    def system(text: str) -> str:
        return _strip_system(text, report)

    out: dict = {}
    for key, value in request.items():
        if key == "tools":
            out[key] = value
        elif key in ("messages", "input") and isinstance(value, (list, tuple)):
            out[key] = [
                _map_strings(item, system)
                if isinstance(item, dict) and item.get("role") in _SYSTEM_ROLES
                else _map_strings(item, fences)
                for item in value
            ]
        else:
            # system, instructions, and any key this version does not know about.
            out[key] = _map_strings(value, system)
    return out, report


def _lockdown(request: Mapping[str, Any]) -> dict:
    """Last resort when stripping itself failed: send nothing from the original prompt."""
    out = {k: v for k, v in request.items() if k not in ("tools", "tool_choice")}
    for key in ("system", "instructions"):
        if key in out:
            out[key] = ""
    message = [{"role": "user", "content": _LOCKDOWN_TEXT}]
    for key in ("messages", "input"):
        if key in out:
            out[key] = message
    return out


_logged_turn: Optional[str] = None


def _log_pass(report: StripReport, turn_id: Any) -> None:
    global _logged_turn
    if report.stray_tags or report.unanchored:
        logger.error(
            "chatlytics#952: memory shape on a summoned turn differs from what this plugin "
            "pins (stray fence tags=%d, blocks without a tail anchor=%d); removed by "
            "over-cutting. Check the hermes-agent version. turn=%s",
            report.stray_tags, report.unanchored, turn_id,
        )
    if turn_id == _logged_turn:
        return
    _logged_turn = turn_id
    if report.changed:
        logger.info(
            "chatlytics#952: summoned turn: removed %d memory fence(s) and %d memory prompt "
            "block(s). turn=%s", report.fences, report.blocks, turn_id,
        )
    else:
        logger.warning(
            "chatlytics#952: summoned turn: no memory found to remove. Expected only when "
            "this agent has no memory; otherwise check the hermes-agent version. turn=%s",
            turn_id,
        )


def llm_request_middleware(**kwargs: Any) -> Optional[dict]:
    """hermes-agent ``llm_request`` middleware: acts only on a summoned request."""
    request = kwargs.get("request")
    if not isinstance(request, dict):
        logger.error(
            "chatlytics#952: llm_request middleware got no request dict (%s). hermes-agent "
            "contract changed; a summoned turn cannot be stripped.", type(request).__name__,
        )
        return None
    try:
        if not is_summoned_request(request):
            return None
        stripped, report = strip_summoned_request(request)
    except Exception:  # noqa: BLE001 -- the host would swallow it and send memory on
        logger.exception("chatlytics#952: strip failed on a summoned turn; request locked down")
        return {"request": _lockdown(request), "source": "chatlytics", "reason": "#952 lockdown"}
    _log_pass(report, kwargs.get("turn_id"))
    if not report.changed:
        return None
    return {"request": stripped, "source": "chatlytics", "reason": "#952 memory stripped"}


# --- fail closed before dispatch ---------------------------------------------------

_registered = False
_ready_reason: Optional[str] = None
_ready_checked = False


def register_strip(ctx: Any) -> None:
    """Register the middleware. Without it summoned turns are refused, never unstripped."""
    global _registered
    register = getattr(ctx, "register_middleware", None)
    if not callable(register):
        logger.error(
            "chatlytics#952: this hermes-agent has no register_middleware, so owner memory "
            "cannot be kept out of summoned turns. They will be refused."
        )
        return
    register(LLM_REQUEST, llm_request_middleware)
    _registered = True


def _probe_host() -> Optional[str]:
    """Run the host's real renderers through the strip. Returns why it failed, or None."""
    from agent import turn_api_request
    from agent.memory_context import build_memory_context_block
    from tools.memory_tool_store import MemoryStore

    if "apply_llm_request_middleware" not in inspect.getsource(turn_api_request):
        return "agent.turn_api_request no longer applies llm_request middleware"
    store = MemoryStore()
    system = "\n\n".join(
        (
            "probe-persona",
            store._render_block("memory", ["probe-memory-entry"]),
            store._render_block("user", ["probe-user-entry"]),
            "Conversation started: probe",
            f"{TURN_CONTEXT_MARKER} probe",
        )
    )
    user = "probe-user-text\n\n" + build_memory_context_block("probe-recalled-entry")
    request = {"messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    if not is_summoned_request(request):
        return "the context line is not recognised in a system prompt"
    stripped, report = strip_summoned_request(request)
    text = "\n".join(_strings(stripped))
    leaked = [s for s in ("probe-memory-entry", "probe-user-entry", "probe-recalled-entry") if s in text]
    if leaked:
        return f"memory shape changed, not removed: {', '.join(leaked)}"
    lost = [s for s in ("probe-persona", "probe-user-text", TURN_CONTEXT_MARKER) if s not in text]
    if lost:
        return f"strip removes too much: {', '.join(lost)}"
    if report.stray_tags or report.unanchored or report.fences != 1:
        return f"memory shape changed: {report}"
    return None


def strip_ready() -> Optional[str]:
    """None when summoned turns can be stripped, else why not. Checked once per process."""
    global _ready_checked, _ready_reason
    if not _registered:
        return "llm_request middleware is not registered"
    if not _ready_checked:
        try:
            _ready_reason = _probe_host()
        except Exception as exc:  # noqa: BLE001 -- any drift means not ready
            _ready_reason = f"host probe failed: {type(exc).__name__}: {exc}"
        _ready_checked = True
    return _ready_reason


def summon_refusal(env: Mapping[str, Any]) -> Optional[str]:
    """Why this envelope must not be dispatched; None for every non-summoned envelope."""
    if env.get("trigger_source") != FROM_ME:
        return None
    if render_trigger_context(env) is None:
        return "malformed chat or session id, so the turn would carry no context line"
    return strip_ready()


def log_summon_refused(chat_id: Any, reason: str) -> None:
    logger.error(
        "chatlytics#952: refused a turn the owner summoned in chat %s, because owner memory "
        "could not be kept out of it: %s. The bot stays silent in that chat. Fix the "
        "hermes-agent/plugin pairing to restore summons.", chat_id, reason,
    )
