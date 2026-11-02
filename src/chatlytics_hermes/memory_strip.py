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
the turn is summoned: the context line rides ``channel_prompt`` into the system prompt,
after the frozen part, and carries a token random per process (``SUMMON_TOKEN``).

Detection is that token anywhere in the system strings, and no length is read to decide
it, so a forged block length can only make the cut bigger. A line without the current
token is a "stale line": the gateway pins a channel prompt, persists the pin, and hands
it back to synthetic turns after a restart (``_pinned_channel_inputs``). That still means
a third party may read the reply, so it counts when it sits past the prompt's tail, and it
is logged as an ERROR. An entry quoting the marker sits before the tail and changes nothing.

Memory entries are attacker-influenced text, so nothing here reads them to find where
they end. Each block's header carries the length of its content, computed by the host
(``MemoryStore._render_block``), and the cut is taken from that number. The middleware
can't compare against the store itself: it isn't given one, and a resumed session sends
the prompt frozen when the session began, which differs from the store after any write.
On Anthropic OAuth the host rewrites words in the prompt, so no length means anything:
the cut there runs from the first header to the latest tail anchor (:func:`_tail_start`).
Every entry comes before the host's own anchors, so an entry's copy of one can't be the
latest while a real one exists. The profile line goes with the cut on that route.

Fail closed, because a silent no-op after a hermes-agent upgrade is the main risk:

- middleware errors are swallowed by the host and the request goes out unchanged, so
  nothing here may raise on a summoned request: it falls back to :func:`_lockdown`;
- :func:`strip_ready` runs the real host renderers through the strip, and a probe
  through the host's live middleware chain, once per chain of callbacks. If either
  fails, :func:`summon_refusal` drops the summoned turn before it is dispatched;
- on a prompt the host did not rewrite, a block whose length doesn't check out, or a
  header line that isn't a whole block header, raises :class:`MemoryShapeError`, which
  locks the request down;
- a ``memory`` tool result that can't be parsed is replaced with a fixed error.

DO NOT widen this to other turns without an explicit decision (chatlytics#952).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, List, Mapping, Optional, Tuple

from .trigger_context import FROM_ME, SUMMON_TOKEN, TURN_CONTEXT_MARKER, render_trigger_context

logger = logging.getLogger(__name__)

LLM_REQUEST = "llm_request"

# hermes-agent agent/memory_context.py. A tag alone is not a fence: people and tools write
# about it. Only the note the host puts right after it makes one, and both of the host's
# note wordings start like this, so fences replayed from older turns match too.
_FENCE_OPEN = r"\s*<\s*memory-context\s*>\s*\[System note:\s*The following is recalled memory context"
# The closing tag as the host's sanitizer defines it (``_FENCE_TAG_RE``). Text it leaves in
# a recall is not a close, so ``< /memory-context>`` doesn't end the fence. The cut runs to
# the LAST close: that sanitizer makes one pass, so a recall can rebuild a real tag from two
# pieces. What follows the host's fence in the string is plugin context. The cut starts at the
# FIRST open for the same reason, so contact text holding the tag and note ahead of the owner's
# text in one string costs the owner's text too. Nothing here can tell that open from the real
# one without reading the recall, so it stays: a lost question, not a leak.
_FENCE = re.compile(_FENCE_OPEN + r"[\s\S]*</\s*memory-context\s*>", re.IGNORECASE)
_UNTERMINATED_FENCE = re.compile(_FENCE_OPEN, re.IGNORECASE)
# hermes-agent tools/memory_tool_store.py: MEMORY_BLOCK_HEADERS and _render_block.
_BLOCK_TITLE = re.compile(
    r"^(?:═+\n)?(?:MEMORY \(your personal notes\)|USER PROFILE \(who the user is\))",
    re.MULTILINE,
)
# The whole header. Group 1 is ``len(content)``, formatted by ``_usage_pct``.
_BLOCK_HEADER = re.compile(
    r"^═{46}\n(?:MEMORY \(your personal notes\)|USER PROFILE \(who the user is\)) "
    r"\[\d+% — ([\d,]+)/[\d,]+ chars\]\n═{46}\n",
    re.MULTILINE,
)
# What follows the memory parts in hermes-agent agent/system_prompt.py, in this order:
# plugin sections (optional), profile line, date line, runtime section (optional).
# A prompt frozen by an older host has no profile line. A timeless bot-chat prompt has a
# timezone line in place of the date line.
_RUNTIME_END = "<!-- End Hermes runtime environment -->"
_RUNTIME_HEADING = re.compile(r"^# Hermes runtime environment", re.MULTILINE)
_DATE_LINE = re.compile(r"^Conversation started:", re.MULTILINE)
_ZONE_LINE = re.compile(r"^Timezone:", re.MULTILINE)
_PROFILE_LINE = re.compile(r"^Active Hermes profile:", re.MULTILINE)
# agent/system_prompt.py ``_timestamp_line``: only the host's own keys, so an entry's date line
# with its text under it is not this.
_STAMP_PARAGRAPH = re.compile(
    r"(?:Conversation started:|Timezone:)[^\n]*"
    r"(?:\n(?:Today's date \(|Session ID: |Model: |Provider: |Platform: )[^\n]*)*\n\n"
)
_PLUGIN_SECTIONS_START = "<!-- hermes-plugin-sections:start -->"
_PLUGIN_SECTIONS_END = "<!-- hermes-plugin-sections:end -->\n\n"
_SYSTEM_ROLES = frozenset({"system", "developer"})
_SIGNED_KEYS = ("signature", "encrypted_content")
_SIGNED_TYPES = frozenset({"thinking", "redacted_thinking", "reasoning"})
# agent/anthropic_adapter.py ``_CLAUDE_CODE_SYSTEM_PREFIX``, its own block on an OAuth request.
_OAUTH_SYSTEM_PREFIX = "You are Claude Code, Anthropic's official CLI for Claude."
# Tool schemas. They are not prompt text, and they hold keys like ``type: ["string", "null"]``.
_TOOL_KEYS = frozenset({"tools", "toolConfig"})
# hermes-agent tools/memory_tool.py, tools/memory_tool_store.py and agent/tool_guardrails.py:
# the keys of a ``memory`` tool result that hold no stored text. Everything else goes, on
# success too (``removed_entry``, ``patched_entries``), and so does any key a new host adds.
_MEMORY_RESULT_KEYS = frozenset({
    "success", "done", "target", "usage", "entry_count", "message", "note", "error",
    "staged", "proposal_staged", "pending_id", "guardrail", "drift_backup", "remediation",
})
# On a row whose tool can't be identified, these say it may be a ``memory`` result.
_STORE_KEY_HINTS = ("_entries", "_entry", "matches")
_MEMORY_ERROR_WITHHELD = "Memory tool result withheld on this turn."
_MEMORY_RESULT_LOST = json.dumps({"success": False, "error": _MEMORY_ERROR_WITHHELD}, ensure_ascii=False)
# The ``error`` texts of tools/memory_tool_store.py that are safe to show: their only free text is
# what the model itself sent as old_text and content. ``_stale_entry_message`` holds a whole stored
# entry, and any wording not listed here is treated the same way.
_SAFE_MEMORY_ERROR = re.compile(
    r"(?:Operation \d+ \(\w+\): )?(?:"
    r"No entry matched '[\s\S]*'\.(?: old_text must come from ONE entry \('§' separates entries\); use one op per entry\.)?"
    r" Retry the \w+ with old_text copied verbatim from the intended entry(?: \(closest_entries below\))?\."
    r"|Multiple entries matched '[\s\S]*'\. Be more specific\."
    r"|'[\s\S]*' appears \d+ times in the matched entry, and patch edits one span\. Include more surrounding text so old_text occurs once\."
    r"|'[\s\S]*' matched multiple distinct entries -- be more specific\."
    r"|Patch is a no-op: '[\s\S]*' -> '[\s\S]*' leaves the entry unchanged\."
    r"|Memory consolidation failed \d+ times this turn\. Stop retrying memory calls [^\n']*"
    r"|(?:Content|old_text|new_content|new_text) cannot be empty\.[^\n']*"
    r"|Memory at [\d,]+/[\d,]+ chars; adding this entry \(\d+ chars\) would exceed the limit by [\d,]+ chars\.[^\n]*"
    r"|Patch would put memory at [\d,]+/[\d,]+ chars\.[^\n]*"
    r"|Applying all \d+ operations would put memory at [\d,]+/[\d,]+ chars[^\n]*"
    r"|(?:Blocked|Stopped) \w+: [^\n']*"
    r")(?: No operations were applied \(batch is all-or-nothing\)\.)?"
)
# agent/tool_guardrails.py ``append_toolguard_guidance`` and the identical-call notice: a repeated
# failure's stop instructions, plain text after the JSON. The host's only stop for a failure with
# no retry cap (``ambiguous``).
_GUARDRAIL_TAIL = re.compile(r"(?:\n\n\[(?:Tool loop (?:hard stop|warning): \w+; count=\d+; |hermes note: )[^\n]*\])+\Z")
# agent/anthropic_adapter.py renames the tool on OAuth requests (``mcp__context_notes``).
_MEMORY_TOOL_NAMES = frozenset({"memory", "context_notes"})
# agent/anthropic_message_convert.py, what the host fills an empty user row with.
_EMPTY_USER_TEXT = "(empty message)"
_LOCKDOWN_TEXT = "Reply with one short line saying you cannot answer right now."
# Dropped outright: strict backends return 400 on an empty system prompt or on tool
# settings without tools (hermes-agent chat_completion_helpers._codex_summary_attempt).
_LOCKDOWN_DROPPED = _TOOL_KEYS | {"tool_choice", "parallel_tool_calls", "system"}
_PROBE_TURN = "chatlytics-952-probe"
# What a real turn passes (agent/turn_api_request.py), so another plugin in the chain has
# no missing id to trip on when it sees the probe.
_PROBE_CONTEXT = {
    "turn_id": _PROBE_TURN, "task_id": _PROBE_TURN, "api_request_id": _PROBE_TURN,
    "session_id": _PROBE_TURN, "platform": "chatlytics", "model": "probe-model",
    "provider": "probe", "base_url": "", "api_mode": "chat_completions", "api_call_count": 1,
}


class MemoryShapeError(Exception):
    """The prompt holds memory this module can't bound. The request gets locked down."""


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


def _safe_error(value: Any) -> bool:
    return value == _MEMORY_ERROR_WITHHELD or (
        isinstance(value, str) and _SAFE_MEMORY_ERROR.fullmatch(value) is not None
    )


def _scrub_memory_json(text: str, report: StripReport) -> str:
    try:
        result = json.loads(text)
    except ValueError:
        result = None
    if not isinstance(result, dict):
        report.store_echoes += 1
        return _MEMORY_RESULT_LOST
    if all(key in _MEMORY_RESULT_KEYS for key in result) and ("error" not in result or _safe_error(result["error"])):
        return text
    report.store_echoes += 1
    kept = {k: v for k, v in result.items() if k in _MEMORY_RESULT_KEYS}
    if "error" in kept and not _safe_error(kept["error"]):
        kept["error"] = _MEMORY_ERROR_WITHHELD
    return json.dumps(kept, ensure_ascii=False)


def _scrub_memory_result(text: str, report: StripReport) -> str:
    """A ``memory`` tool result without stored text. Fail closed if it can't be read.

    The host's tool loop guardrail appends plain text to a repeated failure, and that stop
    instruction is kept. It only counts when what precedes it is one line of JSON, since the
    host's JSON escapes every newline, so stored text can't pose as the tail. A long result
    can be truncated, so a row can hold the store and still not parse.
    """
    tail = _GUARDRAIL_TAIL.search(text)
    if tail is not None and "\n" not in text[: tail.start()] and text.startswith("{"):
        return _scrub_memory_json(text[: tail.start()], report) + tail.group()
    return _scrub_memory_json(text, report)


def _last(pattern: "re.Pattern[str]", text: str, start: int, end: int) -> Optional["re.Match[str]"]:
    found = None
    for found in pattern.finditer(text, start, end):
        pass
    return found


def _memory_extent(text: str, limit: int) -> Optional[Tuple[int, int, int]]:
    """``(start, end, blocks)`` of the built-in memory blocks before ``limit``, or None.

    Taken from the headers' length field, so an entry's content can't move it. The content
    of each block is skipped, so nothing it quotes is read. A length that runs past the
    context line can only be forged, and it ends at the line, which over-cuts. A length that
    doesn't land on the blank line the host joins parts with means a transform changed the
    text, and the number can't be trusted. A title line outside any block means memory in a
    shape this can't bound. Text past the line is never memory: the prompt is frozen before it.
    """
    start = end = None
    blocks = pos = 0
    has_line = limit < len(text)
    while (header := _BLOCK_HEADER.search(text, pos, limit)) is not None:
        if _BLOCK_TITLE.search(text, pos, header.start()):
            raise MemoryShapeError("a memory block header without a length")
        stop = header.end() + int(header.group(1).replace(",", ""))
        start = header.start() if start is None else start
        blocks += 1
        if has_line and stop >= limit:
            return start, limit, blocks
        if stop > len(text) or (stop < len(text) and not text.startswith("\n\n", stop)):
            raise MemoryShapeError("a memory block's length does not match its text")
        end, pos = stop, stop
    if _BLOCK_TITLE.search(text, pos, limit):
        raise MemoryShapeError("a memory block header without a length")
    return None if start is None or end is None else (start, end, blocks)


def _tail_start(text: str, start: int, end: int, keep_profile: bool = True) -> Optional[int]:
    """Where the host's tail starts in ``text[start:end]``, past anything an entry can write.

    No one anchor can be trusted alone: a host may not write that one, and then the last copy
    is an entry's, with the rest of the entries after it. So the cut goes to the latest of all
    four. The host writes each of them after every memory part, so that is past every entry as
    long as the prompt holds any real one. A later copy in text past the tail only over-cuts.

    From there it steps back over what the host joins directly before: the date or timezone
    paragraph before the runtime section, when it holds only the host's keys, then, with
    ``keep_profile``, a single profile line. A step back can't be told from an entry whose last
    line copies that shape, so ``keep_profile`` is only for a caller that starts past the
    entries. Neither 0.22.5 nor 2026.5.16 writes a prompt with no real anchor: a timeless prompt
    has its timezone line, and an older one its date line.
    """
    marks = {p: m.start() for p in (_PROFILE_LINE, _DATE_LINE, _ZONE_LINE, _RUNTIME_HEADING)
             if (m := _last(p, text, start, end)) is not None}
    if not marks:
        return None
    at = max(marks.values())
    stamp = max((marks[p] for p in (_DATE_LINE, _ZONE_LINE) if p in marks), default=None)
    if at == marks.get(_RUNTIME_HEADING) and stamp is not None and _STAMP_PARAGRAPH.fullmatch(text, stamp, at):
        at = stamp
    profile = marks.get(_PROFILE_LINE)
    if keep_profile and profile is not None and profile < at:
        if text.endswith("\n\n", profile, at) and "\n" not in text[profile:at - 2]:
            at = profile
    return at


def _block_end(text: str, after: int, limit: int, report: StripReport) -> int:
    """Where the tail of the prompt starts: everything from ``after`` up to here goes.

    ``after`` is past the entries, so what is searched is the provider block and the real
    tail. Plugin sections count only when they sit right before the tail, the way the host
    joins them.
    """
    bound = max(limit, after)
    end = _tail_start(text, after, bound)
    if end is None:
        report.unanchored += 1
        return bound
    if text.endswith(_PLUGIN_SECTIONS_END, after, end):
        sections = text.rfind(_PLUGIN_SECTIONS_START, after, end)
        if sections >= 0 and text[sections - 1] == "\n":
            end = sections
    return end


def _rewritten_extent(text: str, limit: int) -> Optional[Tuple[int, int, int]]:
    """The memory parts of a prompt the host rewrote, where no block length can be trusted.

    From the first header to the tail, so an entry's headers, dates and frames all go with it.
    Plugin sections and the profile line go too: an entry can imitate either, and here nothing
    says where the entries ended.
    """
    first = _BLOCK_HEADER.search(text, 0, limit)
    if _BLOCK_TITLE.search(text, 0, first.start() if first is not None else limit):
        raise MemoryShapeError("a memory block header without a length")
    if first is None:
        return None
    end = _tail_start(text, first.end(), limit, keep_profile=False)
    if end is None:
        raise MemoryShapeError("a rewritten prompt with no tail to cut to")
    return first.start(), end, sum(1 for _ in _BLOCK_HEADER.finditer(text, first.start(), end))


def _strip_system(text: str, report: StripReport, rewritten: bool = False) -> str:
    limit = _context_line_start(text, rewritten)
    if rewritten:
        extent = _rewritten_extent(text, limit)
        if extent is None:
            return text
        start, end, blocks = extent
    else:
        extent = _memory_extent(text, limit)
        if extent is None:
            return text
        start, after, blocks = extent
        # One cut from the first block to the tail. If a length were off by a transform that
        # still lands on a blank line, what is left of an entry goes with the provider block.
        end = _block_end(text, after, limit, report)
    report.blocks += blocks
    head, tail = text[:start].rstrip(), text[end:]
    return f"{head}\n\n{tail}" if head and tail else head or tail


def _is_tool_output(part: Mapping[str, Any]) -> bool:
    """A tool result carried inside a user row (Anthropic and Bedrock request shapes)."""
    return part.get("type") == "tool_result" or "toolResult" in part


def _map_strings(
    value: Any, fn: Callable[[str], str], tool_part: Optional[Callable[[Mapping[str, Any]], Any]] = None
) -> Any:
    """A copy of ``value`` with ``fn`` applied to every string. Never edits in place.

    With ``tool_part``, a tool result nested in ``value`` is handed to that instead.
    """
    if isinstance(value, str):
        return fn(value)
    if isinstance(value, dict):
        kind = value.get("type")
        if (isinstance(kind, str) and kind in _SIGNED_TYPES) or any(k in value for k in _SIGNED_KEYS):
            return value  # A provider-signed block is rejected if edited.
        if tool_part is not None and _is_tool_output(value):
            return tool_part(value)
        return {k: _map_strings(v, fn, tool_part) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        kept: List[Any] = []
        for item in value:
            mapped = _map_strings(item, fn, tool_part)
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


def _line_start(text: str, at: int) -> int:
    return text.rfind("\n", 0, at) + 1


def _token_line_start(text: str) -> Optional[int]:
    """Where the context line holding the token starts. A token outside such a line (the model
    wrote it into an entry) is no boundary: the rest of that entry would count as past it."""
    at = text.rfind(SUMMON_TOKEN)
    if at < 0:
        return None
    start = _line_start(text, at)
    return start if text.startswith(TURN_CONTEXT_MARKER, start) else None


def _stale_line_at(text: str, rewritten: bool) -> Optional[int]:
    """The last marker in ``text``, when it is past what memory can reach; else None.

    Only reached with no current token. An entry quoting the marker comes before the host's
    tail, and that holds whatever its length says, so an ordinary turn stays byte-identical.
    The lengths are a second opinion for text past the line that imitates the tail. They mean
    nothing on a rewritten prompt, and with no tail and no usable length the answer is yes.
    """
    at = text.rfind(TURN_CONTEXT_MARKER)
    if at < 0:
        return None
    found = [m.start() for p in (_PROFILE_LINE, _DATE_LINE, _RUNTIME_HEADING) if (m := _last(p, text, 0, len(text)))]
    if found and at > max(found):
        return at
    if rewritten:
        return at if not found else None
    try:
        extent = _memory_extent(text, len(text))
    except MemoryShapeError:
        return at if not found else None
    return at if extent is None or at >= extent[1] else None


def _context_line_start(text: str, rewritten: bool) -> int:
    """Where the context line starts in ``text``, or its end when it holds no such line."""
    start = _token_line_start(text)
    if start is not None:
        return start
    at = _stale_line_at(text, rewritten)
    return _line_start(text, at) if at is not None else len(text)


def _is_rewritten(request: Mapping[str, Any]) -> bool:
    """Did the host's OAuth transform rewrite this prompt? Its prefix is a whole block of its
    own, which no prompt text can make. Other rewrites aren't known, so they lock down."""
    system = request.get("system")
    return isinstance(system, (list, tuple)) and any(
        isinstance(part, dict) and part.get("text") == _OAUTH_SYSTEM_PREFIX for part in system
    )


_LINE, _STALE, _TOKEN_ONLY = "line", "stale", "token only"


def _summon_evidence(request: Mapping[str, Any]) -> Optional[str]:
    """What says this request is summoned, or None for every other turn.

    Only the system strings count: history and tool results can hold any token, including
    this process's if the model repeated it. When no system prompt can be located the shape
    is new to us, so the whole request is searched: a false positive costs memory, not privacy.
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
        return _LINE if any(TURN_CONTEXT_MARKER in text or SUMMON_TOKEN in text for text in rest) else None
    # Joined: a provider may split the prompt into cache tiers.
    joined = "\n\n".join(system)
    if SUMMON_TOKEN in joined:
        return _LINE if _token_line_start(joined) is not None else _TOKEN_ONLY
    return _STALE if _stale_line_at(joined, _is_rewritten(request)) is not None else None


def is_summoned_request(request: Mapping[str, Any]) -> bool:
    """Does the system prompt say this turn's reply reaches a third party?"""
    return _summon_evidence(request) is not None


def _tool_names(request: Mapping[str, Any]) -> dict:
    """Tool call id to tool name, from the assistant turns in all four request shapes."""
    names: dict = {}

    def note(call_id: Any, name: Any) -> None:
        if isinstance(call_id, str) and isinstance(name, str):
            names[call_id] = name

    for key in ("messages", "input"):
        rows = request.get(key)
        for row in rows if isinstance(rows, (list, tuple)) else ():
            if not isinstance(row, dict):
                continue
            if row.get("type") == "function_call":
                note(row.get("call_id") or row.get("id"), row.get("name"))
            for call in row.get("tool_calls") or ():
                if isinstance(call, dict) and isinstance(call.get("function"), dict):
                    note(call.get("id"), call["function"].get("name"))
                    note(call.get("call_id"), call["function"].get("name"))
            content = row.get("content")
            for part in content if isinstance(content, (list, tuple)) else ():
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "tool_use":
                    note(part.get("id"), part.get("name"))
                elif isinstance(part.get("toolUse"), dict):
                    note(part["toolUse"].get("toolUseId"), part["toolUse"].get("name"))
    return names


def _result_scrubber(name: Any, report: StripReport) -> Optional[Callable[[str], str]]:
    """What to do to a tool result's text. None for any tool that isn't ``memory``.

    A result whose call can't be found is treated as one only if it mentions a store key.
    """
    if isinstance(name, str):
        if name.removeprefix("mcp__").removeprefix("mcp_") in _MEMORY_TOOL_NAMES:
            return lambda text: _scrub_memory_result(text, report)
        return None

    def if_store_shaped(text: str) -> str:
        if any(hint in text for hint in _STORE_KEY_HINTS):
            return _scrub_memory_result(text, report)
        return text

    return if_store_shaped


def _scrub_structured(value: Any, scrub: Callable[[str], str]) -> Optional[str]:
    """The scrubbed text of a non-text value, or None when scrubbing left it as it was."""
    text = json.dumps(value, ensure_ascii=False, default=str)
    scrubbed = scrub(text)
    return None if scrubbed == text else scrubbed


def _scrub_result_content(content: Any, scrub: Callable[[str], str]) -> Any:
    """The text of a tool result, as a string or a list of parts."""
    if isinstance(content, str):
        return scrub(content)
    if not isinstance(content, (list, tuple)):
        scrubbed = _scrub_structured(content, scrub)
        return content if scrubbed is None else scrubbed
    kept: List[Any] = []
    for part in content:
        if isinstance(part, dict) and isinstance(part.get("text"), str):
            kept.append({**part, "text": scrub(part["text"])})
            continue
        # Bedrock's structured output, or a part the memory tool doesn't produce.
        value = part["json"] if isinstance(part, dict) and "json" in part else part
        scrubbed = _scrub_structured(value, scrub)
        kept.append(part if scrubbed is None else {"text": scrubbed})
    return kept


def _scrub_tool_row(row: Mapping[str, Any], names: Mapping[str, str], report: StripReport) -> Any:
    """A tool result in any request shape. The call id stays, so the pairing holds."""
    if row.get("type") == "function_call_output":
        call_id, key, holder = row.get("call_id"), "output", row
    elif row.get("type") == "tool_result":
        call_id, key, holder = row.get("tool_use_id"), "content", row
    elif isinstance(row.get("toolResult"), dict):
        call_id, key, holder = row["toolResult"].get("toolUseId"), "content", row["toolResult"]
    else:
        call_id, key, holder = row.get("tool_call_id"), "content", row
    scrub = _result_scrubber(names.get(call_id) or row.get("name"), report)
    if scrub is None or key not in holder:
        return row
    cleaned = {**holder, key: _scrub_result_content(holder[key], scrub)}
    return cleaned if holder is row else {**row, "toolResult": cleaned}


def _fill_emptied(before: Mapping[str, Any], after: dict) -> dict:
    """The host fills empty user rows before the middleware runs, so one emptied here stays
    empty unless it is filled the same way. Providers reject an empty user turn."""
    was, now = before.get("content"), after.get("content")
    if isinstance(was, str) and was.strip() and isinstance(now, str) and not now.strip():
        return {**after, "content": _EMPTY_USER_TEXT}
    if isinstance(was, (list, tuple)) and was and isinstance(now, list) and not now:
        shape = next((p for p in was if isinstance(p, dict) and isinstance(p.get("text"), str)), {})
        return {**after, "content": [{**shape, "text": _EMPTY_USER_TEXT}]}
    return after


def strip_summoned_request(request: Mapping[str, Any]) -> Tuple[dict, StripReport]:
    """A copy of ``request`` without memory. ``tools`` are not touched (see README)."""
    report = StripReport()
    rewritten = _is_rewritten(request)

    def fences(text: str) -> str:
        return _strip_fences(text, report)

    def blocks(text: str) -> str:
        return _strip_system(text, report, rewritten)

    names = _tool_names(request)

    def tool_part(part: Mapping[str, Any]) -> Any:
        return _scrub_tool_row(part, names, report)

    def row(item: Any) -> Any:
        # Fences only ride user rows. Elsewhere a lookalike is someone's text or tool
        # arguments, and cutting it would corrupt them.
        if not isinstance(item, dict):
            return _map_strings(item, fences)
        if item.get("role") in _SYSTEM_ROLES:
            return _map_strings(item, blocks)
        if item.get("role") == "user":
            return _fill_emptied(item, _map_strings(item, fences, tool_part))
        if item.get("role") == "tool" or item.get("type") == "function_call_output":
            return _scrub_tool_row(item, names, report)
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
            "block(s), and rewrote %d memory tool result(s). turn=%s",
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
        evidence = _summon_evidence(request)
        if evidence is None:
            return None
        if evidence != _LINE:
            logger.error(
                "chatlytics#952: treating a turn as summoned (%s): the system prompt has the "
                "context line without this process's token, or the token without the line. Usually "
                "a channel prompt pinned before a restart or upgrade, otherwise text quoting them. "
                "Memory is removed from it. turn=%s", evidence, kwargs.get("turn_id"),
            )
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
_chain_ok: Optional[Tuple[Any, ...]] = None


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
    line = render_trigger_context({"trigger_source": FROM_ME, "entity_jid": "probe@c.us", "session_id": "probe"})
    system = "\n\n".join(
        (
            "probe-persona",
            store._render_block("memory", ["probe-memory-entry"]),
            store._render_block("user", ["probe-user-entry"]),
            "Active Hermes profile: probe",
            "Conversation started: probe",
            line or "",
        )
    )
    user = "probe-user-text\n\n" + build_memory_context_block("probe-recalled-entry")
    return {"messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}


def _probe_result_problem(result: Any) -> Optional[str]:
    text = "\n".join(_strings(result))
    leaked = [s for s in ("probe-memory-entry", "probe-user-entry", "probe-recalled-entry") if s in text]
    if leaked:
        return f"memory shape changed, not removed: {', '.join(leaked)}"
    lost = [s for s in ("probe-persona", "probe-user-text", "Active Hermes profile", TURN_CONTEXT_MARKER) if s not in text]
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
    if _summon_evidence(request) != _LINE:
        return "the context line is not recognised in a system prompt"
    stripped, report = strip_summoned_request(request)
    problem = _probe_result_problem(stripped)
    if problem:
        return problem
    if report.stray_tags or report.unanchored or report.fences != 1:
        return f"memory shape changed: {report}"
    return None


def _chain_callbacks() -> Optional[Tuple[Any, ...]]:
    """The callbacks in the host's chain, or None if this host doesn't let us see them."""
    from hermes_cli.plugins import _delivery_manager

    callbacks = getattr(_delivery_manager(), "_middleware", {}).get(LLM_REQUEST)
    return tuple(callbacks) if isinstance(callbacks, list) else None


def _probe_chain() -> Optional[str]:
    """Does a summoned request come out of the host's live middleware chain stripped?

    The registration check runs at every summon: a plugin unload removes the callback while
    this module stays loaded. The request itself goes through once per process per chain of
    callbacks, because other plugins see it, and it runs in the dispatch path. A chain that
    can't be read is probed every time. It goes through the chain, so another plugin's
    middleware can't stand in.
    """
    global _chain_ok
    from hermes_cli.middleware import apply_llm_request_middleware
    from hermes_cli.plugins import has_middleware

    if not has_middleware(LLM_REQUEST):
        return "llm_request middleware is no longer registered with the host"
    try:
        chain = _chain_callbacks()
    except (ImportError, AttributeError):
        chain = None
    if chain is not None and chain == _chain_ok:
        return None
    request = {"model": "probe-model", **_probe_request()}
    result = apply_llm_request_middleware(request, **_PROBE_CONTEXT)
    problem = _probe_result_problem(result.payload)
    if problem:
        return f"the host's middleware chain did not strip: {problem}"
    _chain_ok = chain
    return None


def strip_ready() -> Optional[str]:
    """None when summoned turns can be stripped, else why not.

    Only passes are remembered, since the renderers don't change in a running process and
    a chain of the same callbacks is the same chain. A failure may be transient, so the
    next summon checks again.
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
