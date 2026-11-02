"""What a turn the owner summoned may read, enforced in code (chatlytics#956).

A summoned turn (``trigger_source: "from_me"``, see ``trigger_context.py``) must read only the
summoned chat on the owner's own account. The written rule in the context line was not enough: in
a live test a fallback model called ``list_messages`` with ``session: all`` and re-sent other
accounts' messages. So the rule is enforced here, on the tool call:

- ``pre_tool_call`` refuses a read outside the chat and session of the envelope;
- ``transform_tool_result`` refuses a result that can't be shown to be that chat;
- :func:`scope_ready` makes the dispatch drop the turn when either hook can't run.

Every comparison is against values taken from the hub's envelope when the turn was dispatched
(:func:`open_for_event`), never against anything the model wrote.

**How a tool call finds its scope.** A hook is given no chat, so there are two ways in, and either
one is enough: the gateway's ``HERMES_SESSION_THREAD_ID`` context variable, which holds the scope's
key because that key is the turn's ``source.thread_id`` and which the host copies into hook workers
and delegated subagents; and the session and task ids that :func:`note_request` binds from the
system prompt on every provider request, which cover ``execute_code``'s RPC thread. A request with
no context line releases the binding, so an ordinary turn is never held to a summon's scope.

**Clean start.** The key as ``source.thread_id`` also gives each summon its own Hermes session
(``build_session_key`` includes it), so it has no earlier turns to inherit, and its own tool loop,
fallback and compression run unchanged inside that session. See docs/summoned-turns.md for why
this and not cutting history out of the request.

Every Lextrove tool is listed in :data:`LEXTROVE_RULES`. A tool on that server not listed there is
refused. This is enforcement against a confused model, not a sandbox: see :data:`_TEXT_ROUTE` for
what shell and code tools can still do.

DO NOT widen a rule without measuring what the tool returns for that call, and DO NOT make a
non-summoned turn reach any of this: it must behave exactly as it did before.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, FrozenSet, Iterable, List, Mapping, Optional, Set, Tuple

from .trigger_context import _safe_id, _safe_phone

logger = logging.getLogger(__name__)

PRE_TOOL_CALL = "pre_tool_call"
TRANSFORM_TOOL_RESULT = "transform_tool_result"

_SCOPE_KEY = re.compile(r"summon-[0-9a-f]{16}")
_SCOPES_MAX = 512
_BOUND_MAX = 2048
_SEEN_IDS_MAX = 5000
_PROBE_ID = "chatlytics-956-probe"
_PROBE_TOOL = "mcp__lextrove__list_messages"
# Names only, cut short: a tool name is written by the model, and this goes to the log.
_LOGGABLE_NAME = re.compile(r"[^A-Za-z0-9_.-]")

# The Tool Search bridge. tool_search and tool_describe return the catalog, never data.
_WRAPPER = "tool_call"
_CATALOG_TOOLS = frozenset({"tool_search", "tool_describe"})


@dataclass
class SummonScope:
    """The envelope's facts about one summoned turn. ``session`` None means nothing may be read."""

    key: str
    session: Optional[str] = None
    chat_id: str = ""
    is_group: bool = False
    phone: Optional[str] = None
    chat_forms: FrozenSet[str] = frozenset()
    seen_ids: Set[str] = field(default_factory=set)


@dataclass(frozen=True)
class Refusal:
    reason: str
    tool: str


# Where a request has the line without this process's token, or the line's scope is unknown here
# (a channel prompt pinned before a restart). It is a summon, and nothing says of which chat.
_NO_SCOPE = SummonScope(key="none")

_lock = threading.Lock()
_scopes: "OrderedDict[str, SummonScope]" = OrderedDict()
_bound: "OrderedDict[str, SummonScope]" = OrderedDict()


def _norm(value: Any) -> Optional[str]:
    return value.strip().casefold() if isinstance(value, str) and value.strip() else None


def _norm_jid(value: Any) -> Optional[str]:
    """A JID without case, spaces or a device suffix (``123:26@s.whatsapp.net``)."""
    text = _norm(value)
    return None if text is None else re.sub(r":\d+(?=@)", "", text)


def _phone_of(value: Any) -> Optional[str]:
    """The digits when ``value`` says only a phone number: bare, ``+``-prefixed, spaced or a phone JID."""
    text = _norm(value)
    if text is None:
        return None
    bare = re.sub(r"[\s()+-]", "", re.sub(r"(?::\d+)?@(c\.us|s\.whatsapp\.net)$", "", text))
    return bare if re.fullmatch(r"[0-9]{1,15}", bare) else None


def _chat_forms(chat_id: str, phone: Optional[str]) -> FrozenSet[str]:
    """Every stored spelling of this chat. Lextrove's ``chat_id`` is an exact string match."""
    forms = {_norm_jid(chat_id) or ""}
    digits = phone or _phone_of(chat_id)
    if digits and not chat_id.casefold().endswith("@g.us"):
        forms |= {f"{digits}@c.us", f"{digits}@s.whatsapp.net"}
    return frozenset(forms)


def open_for_event(env: Mapping[str, Any], event: Any) -> Optional[SummonScope]:
    """Open the scope of a summoned turn and start its own session; None if it can't be done.

    Called after ``summon_refusal``, which has already refused malformed ids and a host that
    can't enforce this. Only the envelope is read.
    """
    chat_id, session = _safe_id(env.get("entity_jid")), _safe_id(env.get("session_id"))
    source = getattr(event, "source", None)
    if chat_id is None or session is None or not hasattr(source, "thread_id"):
        return None
    is_group = env.get("chat_type") == "group" or chat_id.casefold().endswith("@g.us")
    phone = None if is_group else _safe_phone(env.get("chat_phone"))
    scope = SummonScope(
        key=f"summon-{secrets.token_hex(8)}", session=session, chat_id=chat_id, is_group=is_group,
        phone=phone, chat_forms=_chat_forms(chat_id, phone),
    )
    source.thread_id = scope.key
    with _lock:
        _scopes[scope.key] = scope
        while len(_scopes) > _SCOPES_MAX:
            _scopes.popitem(last=False)
    logger.info(
        "chatlytics#956: summon scope opened scope=%s session=%s chat=%s chat_type=%s has_phone=%s",
        scope.key, session, chat_id, "group" if is_group else "dm", phone is not None,
    )
    return scope


def note_request(summoned: bool, system_text: str, session_id: Any, task_id: Any) -> None:
    """Called for every provider request. A summoned one binds its ids; any other releases them."""
    ids = [i for i in (session_id, task_id) if isinstance(i, str) and i]
    if not summoned:
        if _bound:
            with _lock:
                for i in ids:
                    _bound.pop(i, None)
        return
    found = _SCOPE_KEY.findall(system_text)
    with _lock:
        # The last one: the line follows the frozen prompt, where a stored entry could hold another.
        scope = _scopes.get(found[-1]) if found else None
        for i in ids:
            _bound[i] = scope or _NO_SCOPE
            _bound.move_to_end(i)
        while len(_bound) > _BOUND_MAX:
            _bound.popitem(last=False)


def _thread_scope() -> Optional[SummonScope]:
    from gateway.session_context import get_session_env

    return _scopes.get(get_session_env("HERMES_SESSION_THREAD_ID", ""))


def _scope_for(session_id: Any, task_id: Any) -> Optional[SummonScope]:
    try:
        scope = _thread_scope()
    except ImportError:
        scope = None
    if scope is not None:
        return scope
    for i in (session_id, task_id):
        if i and i in _bound:
            return _bound[i]
    return None


# --- the rules ----------------------------------------------------------------------------------

_PAGE = frozenset({"from", "to", "limit", "cursor"})


def _base_name(tool_name: Any) -> Tuple[str, bool]:
    """``(name without an MCP prefix, is it an MCP tool)``, for any spelling a model or host uses."""
    name = str(tool_name or "").strip().casefold().replace("-", "_").replace(".", "_")
    if name.startswith("mcp__"):
        _, _, rest = name[5:].partition("__")
        return rest or name[5:], True
    if name.startswith("mcp_"):
        return name[4:], True
    return name, False


def _lextrove_tool(tool_name: Any) -> Optional[str]:
    """The Lextrove tool this name means: a table name, or "unknown" for one not in the table.

    A renamed server is still caught by its tool names. That costs another server's tool of the
    same name on a summoned turn only, which is the cheap direction to be wrong in.
    """
    base, is_mcp = _base_name(tool_name)
    if base in LEXTROVE_RULES:
        return base
    if is_mcp:
        for known in sorted(LEXTROVE_RULES, key=len, reverse=True):
            if base.endswith(f"_{known}"):
                return known
    # The whole name: _base_name has dropped the server, which is where this word usually is.
    return "unknown" if "lextrove" in str(tool_name or "").casefold() else None


def _session_problem(args: Mapping[str, Any], scope: SummonScope) -> Optional[str]:
    """Exactly the summon's session. ``all``, a missing session, a list and any other are refused."""
    if "session" not in args:
        return "session_missing"
    return None if _norm(args["session"]) == _norm(scope.session) else "session_not_summon"


def _unknown_args(args: Mapping[str, Any], allowed: Iterable[str]) -> Optional[str]:
    # A filter this table doesn't know could widen the read, and a new one may OR with the chat.
    return None if set(args) <= set(allowed) else "arg_not_allowed"


def _chat_read(allowed: Iterable[str], *, chat_key: str = "chat_id", group_only: bool = False) -> Callable:
    keys = frozenset(allowed) | {chat_key, "session"}

    def rule(args: Mapping[str, Any], scope: SummonScope) -> Optional[str]:
        if group_only and not scope.is_group:
            return "not_a_group_chat"
        problem = _session_problem(args, scope) or _unknown_args(args, keys)
        if problem:
            return problem
        if chat_key not in args:
            return "chat_missing"
        return None if _norm_jid(args[chat_key]) in scope.chat_forms else "chat_not_summon"

    return rule


def _search_rule(args: Mapping[str, Any], scope: SummonScope) -> Optional[str]:
    """``search_messages``: the approved read of a DM, and of a group by its JID.

    ``chat_type`` dm is what keeps the person's group posts out, and fts is the one mode measured
    to return ``resolved_entity``, which the result check needs. A group has one form and
    ``group_name`` takes its JID (measured: exact, similarity 1). A DM with no phone has nothing to
    prove a contact match against, so it stays on ``list_messages``.
    """
    keys = {"session", "mode", "query", "from", "to", "limit", "cursor", "is_deleted", "sender_id", "chat_type"}
    problem = _session_problem(args, scope)
    if problem:
        return problem
    if _norm(args.get("mode")) != "fts":
        return "search_mode_not_fts"
    if scope.is_group:
        keys.add("group_name")
        if _norm_jid(args.get("group_name")) not in scope.chat_forms:
            return "chat_not_summon"
        if "chat_type" in args and _norm(args["chat_type"]) != "group":
            return "chat_type_not_summon"
    else:
        keys.add("contact_name")
        if scope.phone is None:
            return "dm_without_phone_use_list_messages"
        if _phone_of(args.get("contact_name")) != scope.phone:
            return "chat_not_summon"
        if _norm(args.get("chat_type")) != "dm":
            return "chat_type_not_summon"
    return _unknown_args(args, keys)


def _from_this_chat(key: str, allowed: Iterable[str] = (), *, session: bool = False) -> Callable:
    """By id only: allowed for an id an in-scope read of this turn returned."""
    keys = frozenset(allowed) | {key} | ({"session"} if session else frozenset())

    def rule(args: Mapping[str, Any], scope: SummonScope) -> Optional[str]:
        problem = (_session_problem(args, scope) if session else None) or _unknown_args(args, keys)
        if problem:
            return problem
        wanted = args.get(key)
        ids = wanted if isinstance(wanted, list) else [wanted]
        if not ids or not all(isinstance(i, str) and i.strip().casefold() in scope.seen_ids for i in ids):
            return "id_not_from_this_chat"
        return None

    return rule


def _media_url_rule(args: Mapping[str, Any], scope: SummonScope) -> Optional[str]:
    key = "message_ids" if "message_ids" in args else "message_id"
    return _from_this_chat(key, ("durable", "ttl_days"))(args, scope)


def _refused(reason: str) -> Callable:
    return lambda _args, _scope: reason


_ANALYTICS = _refused("analytics_not_scoped")
_NOT_SCOPED = _refused("tool_cannot_be_scoped")

# DO NOT CHANGE a rule without measuring the tool: a wider one is a leak to the people in that chat.
# Table with reasons: docs/summoned-turns.md. The read filters take the chat by exact id and the
# session by name. Anything else returning chat data is refused until a measurement says a call of
# it stays inside one chat of one account.
LEXTROVE_RULES: Dict[str, Callable[[Mapping[str, Any], SummonScope], Optional[str]]] = {
    "list_messages": _chat_read(_PAGE | {"message_type", "is_deleted", "include_reactions", "sender_id"}),
    "search_messages": _search_rule,
    "list_media": _chat_read(_PAGE | {"media_type", "sender_id"}),
    "list_links": _chat_read(_PAGE | {"domain", "sender_id"}),
    "list_polls": _chat_read(_PAGE),
    "list_locations": _chat_read(_PAGE | {"sender_id"}),
    "list_reactions": _chat_read(_PAGE),
    "list_group_events": _chat_read(_PAGE | {"event_type"}, group_only=True),
    "list_contacts": _chat_read({"limit", "cursor"}, group_only=True),
    "get_group": _chat_read((), chat_key="group_id", group_only=True),
    "get_message": _from_this_chat("message_id"),
    "get_media_url": _media_url_rule,
    "get_poll": _from_this_chat("poll_id"),
    "list_poll_votes": _from_this_chat("poll_id", session=True),
    # A whole file with bearer links, and CSV rows carry names only, so no row can be checked.
    "export_messages": _refused("export_not_allowed"),
    # Returns recent_messages for whoever the query matches, on any account.
    "resolve_entity": _refused("resolve_returns_other_chats"),
    # The session is in the context line, and this lists every other account.
    "list_sessions": _refused("session_list_not_needed"),
    "list_groups": _NOT_SCOPED,
    "list_newsletters": _NOT_SCOPED,
    "get_newsletter": _NOT_SCOPED,
    "get_contact": _NOT_SCOPED,
    "get_avatar": _NOT_SCOPED,
    "get_presence": _NOT_SCOPED,
    "get_entity_profile": _NOT_SCOPED,
    "list_entity_digests": _NOT_SCOPED,
    "list_calls": _NOT_SCOPED,
    "list_unavailable_polls": _NOT_SCOPED,
    # Several endpoints ignore or don't take a chat, and not one is measured.
    "get_analytics": _ANALYTICS,
    "get_complex_analytics": _ANALYTICS,
    "get_composite_analytics": _ANALYTICS,
    "get_content_analytics": _ANALYTICS,
    "get_domain_analytics": _ANALYTICS,
    "get_sender_analytics": _ANALYTICS,
    "get_volume_analytics": _ANALYTICS,
}

# Reads every session's transcript on this agent: other chats, and old answers.
_ALWAYS_REFUSED = {"session_search": "transcripts_of_other_chats"}
# The hub's read of the bot's own number: another account, by name under either surface.
_CHATLYTICS_READS = frozenset({"chatlytics_read", "read_messages"})
_CHATLYTICS_DISPATCH = frozenset({"chatlytics_dispatch", "chatlytics_dispatch_action"})
_READ_ACTION = re.compile(r"^(get|read|list|search|fetch|export|find|load)|messages|history|overview", re.IGNORECASE)
# DO NOT read this as containment. These tools run arbitrary text, and the plugin can see only
# the text. It stops a model reaching for the archive or the host's own stores by name; encoding
# the name, or a script written earlier, goes past it. Listed as open in docs/summoned-turns.md.
_TEXT_ROUTE = frozenset({"terminal", "execute_code", "process", "read_file", "search_files", "cronjob"})
# Not every .hermes path: skills and their scripts live there, and they are ordinary tools.
_TEXT_ROUTE_MARKS = re.compile(
    r"lextrove|state\.db|sessions\.json"
    r"|\.hermes(?:[/\\]+profiles[/\\]+[^\s/\\\"']+)?[/\\]+(?:config\.ya?ml|\.env|sessions\b)"
    r"|\.hermes[/\\]*(?=[\"'\s]|$)",
    re.IGNORECASE,
)


def _wrapped_calls(args: Mapping[str, Any]) -> Optional[List[Tuple[Any, Any]]]:
    """The ``(name, arguments)`` of each entry of a ``tool_call``; None when it can't be read.

    Follows ``tools/tool_search_validation.normalize_tool_call_entries``: ``calls`` as a list, one
    object or a JSON string, the legacy single shape, and ``arguments`` as an object or a JSON string.
    """
    calls = args.get("calls")
    if calls is None:
        calls = [{"name": args.get("name"), "arguments": args.get("arguments")}]
    if isinstance(calls, str):
        try:
            calls = json.loads(calls)
        except ValueError:
            return None
    if isinstance(calls, dict):
        calls = [calls]
    if not isinstance(calls, list) or not calls:
        return None
    out: List[Tuple[Any, Any]] = []
    for call in calls:
        if not isinstance(call, dict) or not str(call.get("name") or "").strip():
            return None
        out.append((call.get("name"), call.get("arguments")))
    return out


def _as_args(raw: Any) -> Optional[Dict[str, Any]]:
    """Arguments as a dict, without the nulls models send for optional fields. None if unreadable."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return None
    if not isinstance(raw, dict):
        return None
    return {k: v for k, v in raw.items() if v is not None}


def _check_one(tool_name: Any, raw_args: Any, scope: SummonScope) -> Optional[Refusal]:
    label = _LOGGABLE_NAME.sub("?", str(tool_name or ""))[:64]
    base, is_mcp = _base_name(tool_name)
    if base in _CATALOG_TOOLS and not is_mcp:
        return None
    # DO NOT CHANGE. The failed live turn called the archive through this wrapper. The host usually
    # unwraps a one-entry call before this hook, but when its unwrap fails, or for a batch, the hook
    # sees ``tool_call`` and the host then runs the entry with this hook skipped. So every entry is
    # checked here, one out-of-scope entry refuses the whole call, and an unreadable one refuses.
    if base == _WRAPPER and not is_mcp:
        entries = _wrapped_calls(raw_args if isinstance(raw_args, dict) else {})
        if entries is None:
            return Refusal("wrapper_unreadable", label)
        for name, arguments in entries:
            if _base_name(name)[0] == _WRAPPER:
                return Refusal("wrapper_nested", label)
            refused = _check_one(name, arguments, scope)
            if refused is not None:
                return refused
        return None
    args = _as_args(raw_args)
    lextrove = _lextrove_tool(tool_name)
    if lextrove is not None:
        if lextrove == "unknown":
            return Refusal("unknown_lextrove_tool", label)
        if scope.session is None:
            return Refusal("no_scope_for_this_turn", label)
        if args is None:
            return Refusal("args_unreadable", label)
        reason = LEXTROVE_RULES[lextrove](args, scope)
        return Refusal(reason, label) if reason else None
    if not is_mcp and base in _ALWAYS_REFUSED:
        return Refusal(_ALWAYS_REFUSED[base], label)
    if base in _CHATLYTICS_READS:
        return Refusal("chatlytics_reads_another_account", label)
    if base in _CHATLYTICS_DISPATCH:
        action = (args or {}).get("action")
        if args is None or not isinstance(action, str) or _READ_ACTION.search(action):
            return Refusal("chatlytics_read_action", label)
        return None
    if is_mcp or base in _TEXT_ROUTE:
        text = raw_args if isinstance(raw_args, str) else json.dumps(raw_args, ensure_ascii=False, default=str)
        if _TEXT_ROUTE_MARKS.search(text or ""):
            return Refusal("names_the_archive_or_host_store", label)
    return None


def _how_to_read(scope: SummonScope) -> str:
    if scope.session is None:
        return "This turn has no readable chat."
    if scope.is_group:
        return (f"To read this chat use list_messages with chat_id {scope.chat_id} and session {scope.session}, "
                f"or search_messages with group_name {scope.chat_id}, session {scope.session} and mode fts.")
    if scope.phone:
        return (f"To read this chat use search_messages with contact_name {scope.phone}, chat_type dm, "
                f"session {scope.session}, mode fts and no query.")
    return f"To read this chat use list_messages with chat_id {scope.chat_id} and session {scope.session}."


def _refusal_text(reason: str, scope: SummonScope) -> str:
    return (
        f"Refused ({reason}): on this turn you cannot read outside this chat and this account. "
        "Do not try another tool, session or chat to get it. "
        f"{_how_to_read(scope)} "
        "If that does not answer the request, say that you cannot read outside this chat."
    )


def _log_refused(refusal: Refusal, scope: SummonScope, turn_id: Any) -> None:
    # A stable line, and no arguments: they can hold names and search terms. grep "summon read refused".
    logger.warning(
        "chatlytics#956: summon read refused tool=%s reason=%s scope=%s turn=%s",
        refusal.tool, refusal.reason, scope.key, turn_id,
    )


def pre_tool_call(
    tool_name: Any = "", args: Any = None, session_id: Any = "", task_id: Any = "", turn_id: Any = "", **_: Any
) -> Optional[Dict[str, str]]:
    """hermes-agent ``pre_tool_call``: None for every turn that isn't summoned.

    DO NOT CHANGE: this must not raise or return None on a failure of its own. Most host paths
    treat a hook failure as "proceed", so an error here must become a refusal. Verified on
    hermes-agent 0.22.5 against the failed live turn's exact call shape (chatlytics#956).
    """
    try:
        scope = _scope_for(session_id, task_id)
    except Exception:  # noqa: BLE001 -- this turn may be a summon, and nothing says it isn't
        logger.exception("chatlytics#956: summon read refused tool=? reason=scope_lookup_failed")
        return {"action": "block", "message": _refusal_text("scope_lookup_failed", _NO_SCOPE)}
    if scope is None:
        return None
    try:
        refusal = _check_one(tool_name, args, scope)
    except Exception:  # noqa: BLE001 -- see the docstring
        logger.exception("chatlytics#956: summon read refused tool=? reason=check_failed scope=%s", scope.key)
        return {"action": "block", "message": _refusal_text("check_failed", scope)}
    if refusal is None:
        if _lextrove_tool(tool_name) is not None:
            logger.info("chatlytics#956: summon read allowed tool=%s scope=%s turn=%s",
                        _LOGGABLE_NAME.sub("?", str(tool_name))[:64], scope.key, turn_id)
        return None
    _log_refused(refusal, scope, turn_id)
    return {"action": "block", "message": _refusal_text(refusal.reason, scope)}


# --- the result ---------------------------------------------------------------------------------


# Reads whose rows belong to this chat, so a later read by one of their ids is in scope too.
_ID_SOURCES = frozenset({"list_messages", "search_messages", "list_media", "list_polls", "list_reactions"})


def _payload(result: Any) -> Optional[Any]:
    """The tool's JSON, under the host's MCP envelope (``{"result": "<text>"}``); None if not JSON."""
    try:
        value = json.loads(result) if isinstance(result, str) else result
        if isinstance(value, dict) and isinstance(value.get("result"), str):
            value = json.loads(value["result"])
        elif isinstance(value, dict) and isinstance(value.get("result"), dict):
            value = value["result"]
    except (TypeError, ValueError):
        return None
    return value


def _rows(payload: Any) -> List[Mapping[str, Any]]:
    """Measured shapes: ``data.items`` (messages), ``data`` a list (media, polls), ``data`` one object."""
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        data = data["items"]
    elif isinstance(data, dict):
        data = [data]
    return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []


def _entity_problem(payload: Any, scope: SummonScope) -> Optional[str]:
    """A positive match, or the read stops. A missing field is not a pass (see trigger_context.py)."""
    data = payload.get("data") if isinstance(payload, dict) else None
    entity = data.get("resolved_entity") if isinstance(data, dict) else None
    if not isinstance(entity, dict) or entity.get("ambiguous") is not False or entity.get("similarity_score") != 1:
        return "result_match_not_positive"
    identifier = _norm_jid(entity.get("identifier")) or ""
    if scope.is_group:
        return None if identifier in scope.chat_forms else "result_other_entity"
    return None if identifier.startswith(f"{scope.phone}@") else "result_other_entity"


def _result_problem(tool: str, result: Any, scope: SummonScope) -> Optional[str]:
    payload = _payload(result)
    must_prove = tool == "search_messages"
    if payload is None:
        # Too long to parse, say. A read by exact chat is in scope from its arguments alone.
        return "result_unreadable" if must_prove else None
    if isinstance(payload, dict) and "error" in payload and "data" not in payload:
        return None  # A tool error holds no rows, and the model should see why it failed.
    rows = _rows(payload)
    if must_prove:
        problem = _entity_problem(payload, scope)
        if problem:
            return problem
    for row in rows:
        chat = row.get("chat_id")
        if chat is None and must_prove:
            return "result_row_without_chat"
        if chat is not None and _norm_jid(chat) not in scope.chat_forms:
            return "result_row_other_chat"
    if tool in _ID_SOURCES:
        for row in rows:
            for key in ("id", "message_id", "target_message_id"):
                value = row.get(key)
                if isinstance(value, str) and len(scope.seen_ids) < _SEEN_IDS_MAX:
                    scope.seen_ids.add(value.strip().casefold())
    return None


def transform_tool_result(
    tool_name: Any = "", result: Any = None, session_id: Any = "", task_id: Any = "", turn_id: Any = "", **_: Any
) -> Optional[str]:
    """hermes-agent ``transform_tool_result``: a result outside the scope never reaches the model."""
    try:
        scope = _scope_for(session_id, task_id)
        if scope is None:
            return None
        tool = _lextrove_tool(tool_name)
        if tool is None or scope.session is None:
            return None
        reason = _result_problem(tool, result, scope)
    except Exception:  # noqa: BLE001 -- the host returns the raw result if this raises
        logger.exception("chatlytics#956: summon read refused tool=? reason=result_check_failed")
        return json.dumps({"error": _refusal_text("result_check_failed", _NO_SCOPE)}, ensure_ascii=False)
    if reason is None:
        return None
    _log_refused(Refusal(reason, _LOGGABLE_NAME.sub("?", str(tool_name))[:64]), scope, turn_id)
    return json.dumps({"error": _refusal_text(reason, scope)}, ensure_ascii=False)


# --- fail closed before dispatch ----------------------------------------------------------------

_registered = False
_chain_ok: Optional[Tuple[Any, ...]] = None


def register_scope(ctx: Any) -> None:
    """Register the hooks. Without them summoned turns are refused, never unenforced."""
    global _registered
    register = getattr(ctx, "register_hook", None)
    if not callable(register):
        logger.error(
            "chatlytics#956: this hermes-agent has no register_hook, so a summoned turn's reads "
            "cannot be limited. They will be refused."
        )
        return
    register(PRE_TOOL_CALL, pre_tool_call)
    register(TRANSFORM_TOOL_RESULT, transform_tool_result)
    _registered = True


def _probe_chain() -> Optional[str]:
    """Does the host's own dispatch refuse an out-of-scope read through this hook?

    Run once per chain of callbacks, since other plugins see the probe, and it never executes.
    """
    global _chain_ok
    from hermes_cli.plugins import _delivery_manager, get_pre_tool_call_directive

    hooks = getattr(_delivery_manager(), "_hooks", {})
    chain = tuple(hooks.get(PRE_TOOL_CALL) or ())
    if pre_tool_call not in chain:
        return "pre_tool_call hook is no longer registered with the host"
    if transform_tool_result not in (hooks.get(TRANSFORM_TOOL_RESULT) or ()):
        return "transform_tool_result hook is no longer registered with the host"
    if chain == _chain_ok:
        return None
    with _lock:
        _bound[_PROBE_ID] = _NO_SCOPE
    try:
        directive, message = get_pre_tool_call_directive(
            _PROBE_TOOL, {"session": "all"}, session_id=_PROBE_ID, task_id=_PROBE_ID, turn_id=_PROBE_ID,
        )
    finally:
        with _lock:
            _bound.pop(_PROBE_ID, None)
    if directive != "block" or "no_scope_for_this_turn" not in (message or ""):
        return "the host's pre_tool_call dispatch did not refuse a probe read"
    _chain_ok = chain
    return None


def scope_ready() -> Optional[str]:
    """None when a summoned turn's reads can be limited, else why not. Only a pass is remembered."""
    if not _registered:
        return "the read scope hooks are not registered"
    try:
        return _probe_chain()
    except Exception as exc:  # noqa: BLE001 -- any drift means not ready
        return f"read scope probe failed: {type(exc).__name__}: {exc}"

