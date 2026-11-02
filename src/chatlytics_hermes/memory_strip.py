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
prompt, after the frozen part, so its marker is found only past that part.

Fail closed, because a silent no-op after a hermes-agent upgrade is the main risk:

- middleware errors are swallowed by the host and the request goes out unchanged, so
  nothing here may raise on a summoned request: it falls back to :func:`_lockdown`;
- :func:`strip_ready` runs the real host renderers through the strip, and a probe
  through the host's live middleware chain at every summon. If either fails,
  :func:`summon_refusal` drops the summoned turn before it is dispatched;
- anything shaped like memory that the exact patterns miss is cut, never passed on.

DO NOT widen this to other turns without an explicit decision (chatlytics#952).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, List, Mapping, Optional, Tuple

from .trigger_context import FROM_ME, TURN_CONTEXT_MARKER, render_trigger_context

logger = logging.getLogger(__name__)

LLM_REQUEST = "llm_request"

# hermes-agent agent/memory_context.py. A tag alone is not a fence: people and tools write
# about it. Only the note the host puts right after it makes one, and both of the host's
# note wordings start like this, so fences replayed from older turns match too.
_FENCE_OPEN = r"\s*<\s*memory-context\s*>\s*\[System note:\s*The following is recalled memory context"
_FENCE = re.compile(_FENCE_OPEN + r"[\s\S]*?<\s*/\s*memory-context\s*>", re.IGNORECASE)
_UNTERMINATED_FENCE = re.compile(_FENCE_OPEN, re.IGNORECASE)
# hermes-agent tools/memory_tool_store.py: MEMORY_BLOCK_HEADERS and _render_block.
_BLOCK_START = re.compile(
    r"^(?:═+\n)?(?:MEMORY \(your personal notes\)|USER PROFILE \(who the user is\))",
    re.MULTILINE,
)
# What follows the memory parts in hermes-agent agent/system_prompt.py, in this order:
# plugin sections (optional), profile line, date line, runtime section (optional).
_RUNTIME_END = "<!-- End Hermes runtime environment -->"
_RUNTIME_HEADING = re.compile(r"^# Hermes runtime environment", re.MULTILINE)
_DATE_LINE = re.compile(r"^Conversation started:", re.MULTILINE)
_PROFILE_LINE = re.compile(r"^Active Hermes profile:[^\n]*\n\n", re.MULTILINE)
_PLUGIN_SECTIONS_START = "<!-- hermes-plugin-sections:start -->"
_PLUGIN_SECTIONS_END = "<!-- hermes-plugin-sections:end -->\n\n"
_SYSTEM_ROLES = frozenset({"system", "developer"})
_SIGNED_KEYS = ("signature", "encrypted_content")
_SIGNED_TYPES = frozenset({"thinking", "redacted_thinking", "reasoning"})
# Tool schemas. They are not prompt text, and they hold keys like ``type: ["string", "null"]``.
_TOOL_KEYS = frozenset({"tools", "toolConfig"})
# hermes-agent tools/memory_tool_store.py: what a failed memory write sends back of the store.
_STORE_ECHO_KEYS = ("current_entries", "closest_entries", "matches")
_LOCKDOWN_TEXT = "Reply with one short line saying you cannot answer right now."
# Dropped outright: strict backends return 400 on an empty system prompt or on tool
# settings without tools (hermes-agent chat_completion_helpers._codex_summary_attempt).
_LOCKDOWN_DROPPED = _TOOL_KEYS | {"tool_choice", "parallel_tool_calls", "system"}
_PROBE_TURN = "chatlytics-952-probe"


@dataclass
class StripReport:
    fences: int = 0
    blocks: int = 0
    stray_tags: int = 0
    unanchored: int = 0
    store_echoes: int = 0

    @property
    def changed(self) -> bool:
        return bool(self.fences or self.blocks or self.stray_tags or self.store_echoes)


def _strip_fences(text: str, report: StripReport) -> str:
    text, count = _FENCE.subn("", text)
    report.fences += count
    stray = _UNTERMINATED_FENCE.search(text)
    if stray:
        # An unterminated fence: the rest of this string may be memory.
        report.stray_tags += 1
        text = text[: stray.start()]
    return text


def _drop_store_echo(text: str, report: StripReport) -> str:
    """A failed ``memory`` write returns the store, so history would hand it back."""
    if not any(key in text for key in _STORE_ECHO_KEYS):
        return text
    try:
        result = json.loads(text)
    except ValueError:
        return text
    if not isinstance(result, dict) or result.get("success") is not False:
        return text
    if not any(key in result for key in _STORE_ECHO_KEYS):
        return text
    report.store_echoes += 1
    kept = {k: v for k, v in result.items() if k not in _STORE_ECHO_KEYS}
    return json.dumps(kept, ensure_ascii=False)


def _last(pattern: "re.Pattern[str]", text: str, start: int, end: int) -> Optional["re.Match[str]"]:
    found = None
    for found in pattern.finditer(text, start, end):
        pass
    return found


def _block_end(text: str, after: int, report: StripReport) -> int:
    """Where the tail of the prompt starts, walking back from its last real part.

    Everything before the real date line can quote any of these, so the date line (the
    last one) is the only anchor taken on sight. The profile line and plugin sections
    count only when they sit right before it, the way the host joins them. Otherwise an
    entry quoting a part this prompt does not have would end the cut there.
    """
    marker = text.rfind(TURN_CONTEXT_MARKER, after)
    bound = text.rfind("\n", after, marker) + 1 if marker >= 0 else len(text)
    bound = max(bound, after)
    anchor = _last(_DATE_LINE, text, after, bound) or _last(_RUNTIME_HEADING, text, after, bound)
    if anchor is None:
        report.unanchored += 1
        return bound
    end = anchor.start()
    profile = _last(_PROFILE_LINE, text, after, end)
    if profile is not None and profile.end() == end:
        end = profile.start()
    if text.endswith(_PLUGIN_SECTIONS_END, after, end):
        sections = text.rfind(_PLUGIN_SECTIONS_START, after, end)
        if sections >= 0 and text[sections - 1] == "\n":
            end = sections
    return end


def _strip_system(text: str, report: StripReport) -> str:
    while (match := _BLOCK_START.search(text)) is not None:
        report.blocks += 1
        end = _block_end(text, match.end(), report)
        head, tail = text[: match.start()].rstrip(), text[end:]
        text = f"{head}\n\n{tail}" if head and tail else head or tail
    return text


def _is_tool_output(part: Mapping[str, Any]) -> bool:
    """A tool result carried inside a user row (Anthropic and Bedrock request shapes)."""
    return part.get("type") == "tool_result" or "toolResult" in part


def _map_strings(
    value: Any, fn: Callable[[str], str], tool_fn: Optional[Callable[[str], str]] = None
) -> Any:
    """A copy of ``value`` with ``fn`` applied to every string. Never edits in place.

    With ``tool_fn``, the strings of tool output nested in ``value`` get that instead.
    """
    if isinstance(value, str):
        return fn(value)
    if isinstance(value, dict):
        kind = value.get("type")
        if (isinstance(kind, str) and kind in _SIGNED_TYPES) or any(k in value for k in _SIGNED_KEYS):
            return value  # A provider-signed block is rejected if edited.
        if tool_fn is not None and _is_tool_output(value):
            return _map_strings(value, tool_fn)
        return {k: _map_strings(v, fn, tool_fn) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        kept: List[Any] = []
        for item in value:
            mapped = _map_strings(item, fn, tool_fn)
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


def _past_the_frozen_prompt(system: str) -> str:
    """The part of a system prompt no memory entry can reach.

    The context line is added after the frozen prompt, and memory sits inside it, above
    the date line and the runtime section. So the later of the last date line and the last
    end delimiter comes after anything an entry quotes, and a prompt stored before the
    runtime section existed still has its date line. Text after the marker can't fake either
    one: ``clean_chat_name`` makes the name one line and removes its angle brackets.
    """
    date = _last(_DATE_LINE, system, 0, len(system))
    start = max(system.rfind(_RUNTIME_END), date.start() if date is not None else -1)
    return system[max(start, 0) :]


def is_summoned_request(request: Mapping[str, Any]) -> bool:
    """Does the system prompt carry the context line, past its frozen part?

    Neither a contact pasting the marker into a chat nor a memory entry quoting it can
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
        rest = _strings({k: v for k, v in request.items() if k not in _TOOL_KEYS})
        return any(TURN_CONTEXT_MARKER in text for text in rest)
    # Joined: a provider may split the prompt into cache tiers.
    return TURN_CONTEXT_MARKER in _past_the_frozen_prompt("\n\n".join(system))


def strip_summoned_request(request: Mapping[str, Any]) -> Tuple[dict, StripReport]:
    """A copy of ``request`` without memory. ``tools`` are not touched (see README)."""
    report = StripReport()

    def fences(text: str) -> str:
        return _strip_fences(text, report)

    def blocks(text: str) -> str:
        return _strip_system(text, report)

    def echoes(text: str) -> str:
        return _drop_store_echo(text, report)

    def row(item: Any) -> Any:
        # Fences only ride user rows. Elsewhere a lookalike is someone's text or tool
        # arguments, and cutting it would corrupt them.
        if not isinstance(item, dict):
            return _map_strings(item, fences)
        if item.get("role") in _SYSTEM_ROLES:
            return _map_strings(item, blocks)
        if item.get("role") == "user":
            return _map_strings(item, fences, echoes)
        if item.get("role") == "tool" or item.get("type") == "function_call_output":
            return _map_strings(item, echoes)
        return item

    out: dict = {}
    for key, value in request.items():
        if key in _TOOL_KEYS:
            out[key] = value
        elif key in ("messages", "input") and isinstance(value, (list, tuple)):
            out[key] = [row(item) for item in value]
        elif key in ("system", "instructions"):
            out[key] = _map_strings(value, blocks)
        else:
            # A key this version does not know about could hold either kind of memory.
            out[key] = _map_strings(value, lambda text: fences(blocks(text)))
    return out, report


def _lockdown(request: Mapping[str, Any]) -> dict:
    """Last resort when stripping itself failed: send nothing from the original prompt."""
    out = {k: v for k, v in request.items() if k not in _LOCKDOWN_DROPPED}
    if "instructions" in out:  # Required by the Responses API.
        out["instructions"] = _LOCKDOWN_TEXT
    # Bedrock Converse takes only content blocks here.
    content: Any = [{"text": _LOCKDOWN_TEXT}] if "modelId" in out else _LOCKDOWN_TEXT
    for key in ("messages", "input"):
        if key in out:
            out[key] = [{"role": "user", "content": content}]
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
            "block(s), and %d memory tool echo(es). turn=%s",
            report.fences, report.blocks, report.store_echoes, turn_id,
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
    if kwargs.get("turn_id") != _PROBE_TURN:
        _log_pass(report, kwargs.get("turn_id"))
    if not report.changed:
        return None
    return {"request": stripped, "source": "chatlytics", "reason": "#952 memory stripped"}


# --- fail closed before dispatch ---------------------------------------------------

_registered = False
_shape_ok = False


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


def _probe_request() -> dict:
    """A summoned request made by the host's real renderers, with made-up memory."""
    from agent.memory_context import build_memory_context_block
    from tools.memory_tool_store import MemoryStore

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
    return {"messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}


def _probe_result_problem(result: Any) -> Optional[str]:
    text = "\n".join(_strings(result))
    leaked = [s for s in ("probe-memory-entry", "probe-user-entry", "probe-recalled-entry") if s in text]
    if leaked:
        return f"memory shape changed, not removed: {', '.join(leaked)}"
    lost = [s for s in ("probe-persona", "probe-user-text", TURN_CONTEXT_MARKER) if s not in text]
    if lost:
        return f"strip removes too much: {', '.join(lost)}"
    return None


def _host_calls_the_middleware() -> bool:
    """Is the request middleware applied anywhere in the agent package?

    Driving the host's request builder for real needs a whole agent, so this one check
    stays a source search. It spans the package, so moving the call does not refuse summons.
    """
    import agent

    return any(
        "apply_llm_request_middleware(" in path.read_text("utf-8", errors="replace")
        for root in agent.__path__
        for path in Path(root).rglob("*.py")
    )


def _probe_host() -> Optional[str]:
    """Run the host's real renderers through the strip. Returns why it failed, or None."""
    if not _host_calls_the_middleware():
        return "agent no longer applies llm_request middleware"
    request = _probe_request()
    if not is_summoned_request(request):
        return "the context line is not recognised in a system prompt"
    stripped, report = strip_summoned_request(request)
    problem = _probe_result_problem(stripped)
    if problem:
        return problem
    if report.stray_tags or report.unanchored or report.fences != 1:
        return f"memory shape changed: {report}"
    return None


def _probe_chain() -> Optional[str]:
    """Does a summoned request come out of the host's live middleware chain stripped?

    Run at every summon: a plugin unload removes the callback while this module stays
    loaded. It goes through the chain, so another plugin's middleware can't stand in.
    """
    from hermes_cli.middleware import apply_llm_request_middleware
    from hermes_cli.plugins import has_middleware

    if not has_middleware(LLM_REQUEST):
        return "llm_request middleware is no longer registered with the host"
    result = apply_llm_request_middleware(_probe_request(), turn_id=_PROBE_TURN)
    problem = _probe_result_problem(result.payload)
    return f"the host's middleware chain did not strip: {problem}" if problem else None


def strip_ready() -> Optional[str]:
    """None when summoned turns can be stripped, else why not.

    Only a passed shape check is remembered, since the renderers don't change in a
    running process. A failure may be transient, so the next summon checks again.
    """
    global _shape_ok
    if not _registered:
        return "llm_request middleware is not registered"
    try:
        if not _shape_ok:
            reason = _probe_host()
            if reason:
                return reason
            _shape_ok = True
        return _probe_chain()
    except Exception as exc:  # noqa: BLE001 -- any drift means not ready
        return f"host probe failed: {type(exc).__name__}: {exc}"


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
