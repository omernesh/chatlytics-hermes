# Summoned turns: read scope and clean start

When the owner of a WhatsApp number types a bot's trigger in one of their own chats, the hub
delivers an envelope with `trigger_source: "from_me"`. The reply goes into that chat from the
owner's number, so the other people there read it. Such a turn may read only that chat on that
account, and may not inherit earlier turns of the chat. Every other Chatlytics turn, except the owner's
own DM with the bot, has its reads limited too, and nothing else about it changes: see
[The chat read scope](#the-chat-read-scope).

## Read scope

Code: `src/chatlytics_hermes/read_scope.py`. The scope comes from the envelope when the turn is
dispatched, never from model arguments.

| When | Hook | What it does |
|---|---|---|
| Before the tool runs | `pre_tool_call` | Refuses a call outside the scope. Sees through `tool_call`. |
| After the tool ran | `transform_tool_result` | Replaces a result that can't be shown to be this chat. |
| At dispatch | `summon_refusal` | Drops the turn if the hooks can't run on this host. |

A session must be exactly the summon's: `all`, a missing session, a list and any other session are
refused. Case and surrounding spaces in a value are ignored, and so are the JID forms of one phone.

### Two keys

A summon reads through its own MCP server, which holds a Lextrove key limited to the owner's session. Every other turn keeps the main server and its unlimited key. Full Lextrove is only for the owner's own DM with the bot: the other turns are held to one chat by [the chat read scope](#the-chat-read-scope).

Hermes holds one credential per MCP server, set when it connects, and a plugin can't choose one per call. So the choice is `pre_tool_call` refusing the other server:

| Turn | The main server | The limited server |
|---|---|---|
| A summon | Refused: `lextrove_needs_limited_key`. So is any other tool with a Lextrove tool's name, on any server. See the limit below. | The rules below. |
| A summon, the limited key not set up | Refused, as above. Never a fallback. | Refused, with the code below. |
| Any other turn: a limited chat, the owner's DM, Telegram, the CLI, cron | As before. | Refused: `limited_key_is_for_summons`. The model is told to use the main server. |

**How the main server is recognised.** By its tool names and its name, not its key: the plugin can't tell which key a server holds. A tool named as in [the table below](#lextrove-tools) is refused on any server, and any tool of a server with `lextrove` in its name is refused too. So keep `lextrove` in the main server's name. Renamed without it, say to `archive`, `mcp__archive__list_messages` is still refused but `mcp__archive__read_resource`, a tool the table doesn't list, is treated as any other MCP tool, and it runs on the unlimited key. This isn't closed with a broader rule, because nothing tells such a tool from a legitimate MCP tool of another server.

**This is a backstop, not the main control.** The hook holds a summon to its chat and its session, and the rules below are what limit it. What the key adds is that a call which gets past them still can't read another account. It stops nothing else: a live test showed an ordinary turn reading the unlimited key out of the profile's env file through `execute_code` and calling the API with it, and a model that does that on a summon has no limit at all. The hook refuses text that names that file, that key or the archive (see [What this does not stop](#what-this-does-not-stop)), and that is text matching.

**Setup.** On the host, in the profile's `config.yaml`, copy the main server's entry and change its key:

```yaml
mcp_servers:
  lextrove_limited:
    command: <the same command as the main Lextrove server>
    env:
      LEXTROVE_API_KEY: ${LEXTROVE_LIMITED_API_KEY}
    tools:
      include: [list_messages, search_messages, list_media, list_links, list_polls, list_locations, list_reactions, list_group_events, list_contacts, get_group, get_message, get_media_url, get_poll, list_poll_votes]
```

And in the profile's `.env`, `LEXTROVE_LIMITED_API_KEY=<the key>`. `include` is what a summon may call: without it the limited server's 34 tools join every turn's list, for any model that lists them. The two names are the defaults, and `CHATLYTICS_LEXTROVE_LIMITED_SERVER` and `CHATLYTICS_LEXTROVE_LIMITED_KEY_NAME` change them. They name things. Neither holds a key. Set `CHATLYTICS_LEXTROVE_LIMITED_SERVER` and `CHATLYTICS_LEXTROVE_LIMITED_KEY_NAME` in the profile's `.env`, not only in the process env: at dispatch a primary bot reads `os.environ`, and in the turn the host's `get_secret` reads only the profile's env file, so a name set only in process env makes the prompt and the hook disagree (it fails closed).

It has to be its own process. The Lextrove MCP binary sends the one key it was started with on every call, and the bearer a client presents to it is only a gate, so a second connection to one running HTTP server with another bearer is not limited at all. A stdio entry like the one above, or a second HTTP server started with the limited key, both are.

**Why the plugin checks the key itself.** A stdio server's unset `${VAR}` in `env` is dropped by the host, and the process starts anyway (`_build_safe_env`). The Lextrove binary then reads a `.env` from where it runs, which can hold the unlimited key under the name it wants. So the host connecting says nothing, and on every summon read `limited_key_problem` looks at the server's entry in the raw config and at the key for this profile. The value is only tested for being there, and never kept or logged.

**Why the hook checks the key, and the context line doesn't.** The line is written at dispatch, on the adapter's poll task. Under `gateway.multiplex_profiles` the host connects the primary bot with no profile bound, so that task can't read any profile's key: the host's `get_secret` raises there. A secondary bot's task has its profile bound, and the turn itself always does. A check at dispatch would say the archive can't be read on every summon of a primary bot, while the hook allowed the read. So the line looks only at names and `config.yaml`, and the hook, which sees the key the turn would use, is what decides about the key.

| Code | Means |
|---|---|
| `limited_server_not_configured` | No such entry under `mcp_servers`. |
| `limited_server_not_on_limited_key` | The entry's `env` or `headers` doesn't take anything from the key's name. A copy of the main entry that kept its key gets this. |
| `limited_key_missing` | The name is empty or not set for this profile. |
| `limited_key_config_invalid` | A name in one of the two variables can't be a name. There is no fallback to the default. |
| `limited_key_check_failed` | The check itself failed, such as a tool call that reaches the hook on a multiplexer with no profile bound. |

The turn still runs and can still reply with any of these, a read is refused with its code, and the refusal tells the model the archive can't be read on this turn and to say so. It names no tool. The log has an ERROR: `this summon can read nothing from the archive`.

The codes other than `limited_key_missing` and `limited_key_check_failed` are also known at dispatch, so the context line already says the archive can't be read, and names no tool. `limited_key_check_failed` comes from the key read itself, which dispatch doesn't do, so it is found in the turn like `limited_key_missing`. With `limited_key_missing` the line names the limited server's tools, and the model finds out on its first read. That costs one tool call on a host that isn't set up. Nothing is read.

What this can't see: the plugin can't tell a limited key from an unlimited one by its value, so a wrong key under the right name passes every check above. That is row K1. And the key is for one session: a summon on any other session gets Lextrove's 403 on the limited server, which also fails closed.

### Lextrove tools

| Tool | On a summoned turn |
|---|---|
| `search_messages` | DM: `contact_name` = the phone, `chat_type` dm, `session`, `mode` fts. Group: `group_name` = the group JID, `session`, `mode` fts. The result must have a positive `resolved_entity` and every row in this chat. |
| `list_messages` | `chat_id` = a form of this chat, `session`. No `contact_name`, which also returns that person's group posts. |
| `list_media`, `list_links`, `list_polls`, `list_locations`, `list_reactions` | `chat_id` = a form of this chat, `session`. |
| `list_group_events`, `list_contacts`, `get_group` | The group chat only, by its JID, with `session`. |
| `get_message`, `get_media_url`, `get_poll` | An id that an in-scope read of this turn returned. |
| `list_poll_votes` | The same, with `session`. |
| `export_messages` | Refused: CSV rows can't be checked, and it hands out bearer links. |
| `resolve_entity` | Refused: returns `recent_messages` for whoever matches, on any account. |
| `list_sessions` | Refused: the session is in the context line, and this lists every other account. |
| `list_groups`, `list_newsletters`, `get_newsletter`, `get_contact`, `get_avatar`, `get_presence`, `get_entity_profile`, `list_entity_digests`, `list_calls`, `list_unavailable_polls` | Refused: no argument limits them to this chat and account. |
| `get_analytics`, `get_complex_analytics`, `get_composite_analytics`, `get_content_analytics`, `get_domain_analytics`, `get_sender_analytics`, `get_volume_analytics` | Refused: not every endpoint honours a chat filter, and none has been measured. |
| Any other tool of a server with `lextrove` in its name | Refused. A server without that word isn't known to be Lextrove: see [How the main server is recognised](#two-keys). |

An argument not listed for a tool is refused too: a filter this table doesn't know could widen the
read. To allow more, measure what the tool returns for that call first.

The model is told to read a chat with `list_messages` and to search a DM by its number. Since Lextrove's
#178 every form of a DM's `chat_id` finds the whole chat, and `chat_id` stays in that chat, where
`contact_name` also returns that person's group posts. Since its #180 a number matches exactly or finds
nothing. A number goes in international form only: a local form finds nothing, and the rule above refuses it.

A row keeps the form it was stored under, so a DM's rows can be under an id the envelope didn't give. In a
DM a person's id passes the result check unless it is known to be someone else (another phone, or another
`@lid` in a `@lid` DM). A person's id is an `@lid`, or digits before `@c.us` or `@s.whatsapp.net`. A group has
one form, and its rows must match it.

**So in a DM the result check no longer catches a bad result by itself.** It can't tell whose an id is that the
envelope didn't give: in a DM with a known phone any `@lid` passes, and with no phone any phone id does. Lextrove's
`chat_id` filter decides which rows come back. It finds a chat's other forms through `lid_mappings`, so a wrong
row there would return another person's messages, and neither this check nor the log would show it. The key
doesn't help when that person's chat is on the same session. This applies to [limited chat turns](#the-chat-read-scope)
as well, which use the same check on the bot's session. A wrong row is Lextrove's to prevent, and would have to
be noticed there.

### Other routes

| Route | On a summoned turn |
|---|---|
| `session_search` | Refused: it reads every session's transcript. |
| `chatlytics_read`, the hub MCP's `read_messages` | Refused: they read the bot's own number. |
| `chatlytics_dispatch` | Refused when the action reads (`get`, `list`, `search`, `messages`, ...). |
| The hub MCP's tools that send or act | Refused. See [The hub's own MCP tools](#the-hubs-own-mcp-tools). `chatlytics_send`, `chatlytics_react` and the plugin's other tools are not MCP tools and aren't affected. |
| `chatlytics_directory`, `chatlytics_search`, `chatlytics_resolve_entity` | Allowed: names and ids, no messages, and sending needs them. |
| `terminal`, `execute_code`, `process`, `read_file`, `search_files`, `cronjob` | Refused when the arguments name the archive or the host's own stores. This is text matching. |
| MCP tools, any server | Judged only by the arguments that say where the tool goes, in any spelling of the key (`baseUrl`, `file-path`), at any depth. A place (`url`, `uri`, `path`, `file`, `endpoint`, `resource`, ...) is refused when it points at the archive's host or a host store (`state.db`, `sessions.json`, `.hermes/.env`), a command (`command`, `cmd`, `script`, `code`) when it names either, and a query (`sql`, `statement`, `query`, ...) when it selects from the archive. JSON held in such an argument is searched as text. No other key is searched. So a mail to an address at lextrove, a memory search for the word and `read_note` of `projects/lextrove.md` pass, and a fetch of the archive's URL doesn't. |
| `execute_code` calling a tool | Goes through the same hook, found by task id. |
| `delegate_task` | Allowed. A subagent's calls go through the same hook, found by the copied context variable, or on a conversation its chat moved past, by its key. See [A conversation its chat moved past](#a-conversation-its-chat-moved-past). |
| Skills | Instructions only. A skill that says `session="all"` gets the refusal when the model follows it. |
| `memory` | Allowed. See the memory strip in the README. |

### What this does not stop

This limits a confused model. It is not a sandbox against a hostile one. Not blocked:

- Shell or code that hides the name: an encoded string, a variable (`$HERMES_HOME/sessions`), a script
  already on disk.
- Getting the unlimited key without naming it: `env`, `dict(os.environ)`, `cd` into the profile and
  `cat .env`, an encoded path. `~/.hermes/.env`, a profile's `.env`, `$HERMES_HOME/.env` and the word
  lextrove, which the default key names have, are refused. A key found this way goes around both the hook and [the limited key](#two-keys).
- Another MCP server with its own route to the same data, under a name and arguments that don't name
  the archive.
- A cron job scheduled during the turn with a prompt that doesn't name the archive. It runs later as
  an ordinary job, with no scope.
- A browser or web tool, logged in somewhere or given a URL: `browser_navigate` and `web_extract` are
  not read at all.
- `write_file` and `patch`, on any path.
- `chatlytics_dispatch` with an action whose name doesn't look like a read (`chats`, `groups`).
- A send to the hub by other means than its MCP tools: a `curl` from a shell, or another MCP server
  with its own send tool under another name, such as a `chatlytics_send` served over MCP.
- `memory` and any memory tool, which also means a summon can write what it read into memory. See
  [Clean start](#clean-start).

### The log

```
chatlytics#956: summon scope opened scope=<key> session=<s> chat=<id> chat_type=dm has_phone=True
chatlytics#956: summon read allowed tool=<name> scope=<key> turn=<id>
chatlytics#956: summon read refused tool=<name> reason=<code> scope=<key> turn=<id>
chatlytics#956: summon reply not posted: no reply route on the owner's number chat=<id> scope=<key> final=True
chatlytics#956: summon reply went to the owner's DM instead scope=<key>
chatlytics#956: summon send refused, summon_scope_lost chat=<id> scope=<key>
chatlytics#956: summon send refused, summon_target_unclear chat=<id> scope=<key>
chatlytics#956: summon send refused, summon_route_closed chat=<id> scope=<key>
chatlytics#956: summon send refused, summon_action_off_route chat=<id> scope=<key>
chatlytics#956: summon media not posted: HTTP 409 chat=<id> scope=<key> route_closed=<bool>
chatlytics#956: dropped a redelivered summon in chat <id>
chatlytics#956: left the host's setup notice out of summon scope=<key>
chatlytics#956: read refused tool=<name> reason=limited_key_is_for_summons turn=<id>
chatlytics-hermes#31: this summon can read nothing from the archive: <code>
```

Arguments and results are never logged. The exception is a refused send's target, cut to 64 characters
with anything unusual replaced.

## Which account a send leaves from

A summon runs alongside the chat's other turns, so every send names its account. With no session
named the hub follows the chat's live reply route, whichever turn is sending, and that sent replies
from the wrong number.

| Send | Session named | If the route is closed |
|---|---|---|
| A summon's, to its own chat in any spelling | The summon's, and the body carries the envelope's chat id | Hub 409. Nothing leaves from either number. |
| A summon's, to another chat | The bot's own | Not on a route. |
| A summon's in a DM, to a target that may be this person under another id | Nothing is sent | The tool gets an error saying so. ERROR in the log. |
| A summon's action other than a reaction, typing or marking seen, on its own chat | Nothing is sent | The tool gets an error saying so. ERROR in the log. |
| Any other turn's | The bot's own, from `GET /api/v1/bot/me` and ordinary envelopes | Not on a route. |
| Any other turn's, the bot's own session not known yet | None, as before | Follows the route. A WARNING says so. |
| A summon whose scope this process doesn't have | Nothing is sent | ERROR in the log. |

This covers the reply, media, typing, the progress bubble, `chatlytics_send`, `chatlytics_reply`,
`chatlytics_react`, the route-allowed verbs of `chatlytics_dispatch`, and the host's `send_message`
tool. With no route open the hub answers a named own session exactly as it answers no session.

**Actions the hub can't route.** The hub moves four `/api/v1/actions` verbs to the owner's number (`react`,
`startTyping`, `stopTyping`, `sendSeen`). With a bot's bearer it refuses the send verbs (`poll`, `send`, ...)
with a 403, and runs the others (`pin`, `edit`, `unsend`, `delete`, ...) from the bot's number; with the
operator key it runs them all on the default account. So on a summon, any other verb aimed at the summoned
chat is refused in the plugin, by `chatId`, `to`, `target` or the chat in a full message id. That covers
`chatlytics_poll`, `chatlytics_edit`, `chatlytics_unsend`, `chatlytics_delete`, `chatlytics_pin`,
`chatlytics_unpin` and `chatlytics_dispatch`. The same verbs on another chat stay on the bot's number.

**A reaction goes by its message id.** The hub refuses a route action whose `chatId` isn't the chat the
message id names. So when a full message id names this chat, in any form the envelope knows, that form is
the `chatId` sent, whatever the model wrote.

**Spellings of one chat.** The hub reads a route by JID and doesn't add `@c.us` to a bare or `+` phone,
and a named own session beats a live route. So the plugin decides, from the envelope:

| Summon in | Target | Is |
|---|---|---|
| Any chat | The envelope's id, with any case, device suffix, `@s.whatsapp.net`, or a `chat:` or `whatsapp:` prefix | This chat |
| A DM whose phone is known (`chat_phone`, or a phone JID) | That phone, bare, `+` or as a JID | This chat |
| A DM whose phone is known | Another phone | Another chat |
| A `@lid` DM with no `chat_phone` | Any phone, in any spelling | Unclear: refused |
| A `@lid` DM | Another `@lid` | Another chat |
| A phone DM | Any `@lid` | Unclear: refused |
| A DM | A group, newsletter or broadcast id | Another chat |
| A DM | Anything else, such as a name | Unclear: refused |
| A group | The group id, with or without `@g.us` | This chat |
| A group | Anything else | Another chat |

Refusing costs one tool call, and the model is told to use the chat's id from its context line. A
person whose phone the envelope doesn't give can't be written to by phone from a summon in their own
DM, even to say something that was meant for another chat of theirs: use an ordinary turn.

**Several bots on one gateway.** Everything a summon sends uses the bearer it arrived on: the reply,
media, typing, the bubble, and the `chatlytics_*` tools, whichever chat they send to, each naming that
bot's own session when it isn't the summon's. With another bot's bearer the hub would move the send to
that bot's number. A summon doesn't move the chat's other sends to its bot, and within one adapter an
ordinary turn's tools stay on its primary bot as before. A `/retry` of a summon takes the bearer of the
summon it repeats. If that isn't known and the gateway has several bots, the retry is refused, with an
ERROR in the log.

With `multiplex_profiles`, each profile has its own adapter, and a tool runs on the adapter of its own
turn. A summon's tools find theirs by the scope key. If that adapter is gone, as after a disconnect or a
restart, every `chatlytics_*` tool of the summon is refused and nothing is sent. An ordinary turn's tools
find theirs by `HERMES_SESSION_PROFILE`:

| The turn | Several adapters live | One adapter live |
|---|---|---|
| Names a profile that has an adapter | That adapter | That adapter |
| Names a profile with no adapter | Not connected | The last-connected adapter |
| Names no profile, and the default profile's adapter is live | The default profile's, with a WARNING | The default profile's |
| Names no profile, and the default profile's adapter isn't live | Not connected, with a WARNING | The last-connected adapter |

"Not connected" is the tool's usual error, and nothing is sent. The WARNING names the tool, not its
arguments. A summon doesn't use this table. A gateway with one adapter behaves as it did. The host's
`send_message` doesn't use any of this: it resolves the active profile's adapter itself.

What this still allows:

- **A cron job names no profile**, because the host binds no session to it, and one gateway runs every
  profile's jobs. A job of the default profile uses the default profile's adapter, whichever connected
  last. A job of another profile can't be told apart from that, so it uses the default profile's bearer
  too, or is not connected if the default profile has no adapter. The WARNING is how to find it.
- **"One adapter live" is counted when the tool runs.** On a gateway of two bots, while one reconnects, its
  turn takes the other's bearer if the other connected last. If the other connected first the last-connected
  entry is empty, and the turn is not connected.
- **A `profile_routes` entry that moves bot A's turn to profile B, where B has an adapter, uses B's bearer.**
  The turn names B, so B's adapter is its own, and the hub moves its sends to B's number.

On the legacy operator-key path a summon names its session and leaves the chat's session alone. This
needs a hub that marks summons with `trigger_source` (6.247.0 or later).

**How the bot's own session is known.** From `GET /api/v1/bot/me` at boot, per bot, or on a summon if
that failed, or from an ordinary envelope until then. An envelope that disagrees with what `/bot/me` said
doesn't change it: the plugin asks `/bot/me` again in the background and takes the answer. That covers
an owner-session envelope with no `trigger_source` (one queued by an older hub), which would otherwise
make every later ordinary send a 409, and a bot really moved to another session.

**When a summon's reply is refused** (a person messaged the bot in that chat, or the turn outlived the
15-minute window), nothing reaches the chat. The turn's reply goes to the owner in their DM with the
bot, saying where it was meant to go, and the log has an ERROR. Progress and notices don't. If the
envelope's sender might not be the owner (the hub leaves a DM's sender as the peer when it has no
owner on record, and an `@lid` sender can't be compared), or that DM is refused too, the reply is lost
and the ERROR is all there is. A refused file is not moved: the host reports the failure and the log
has an ERROR.

The envelope's sender is not known to be the owner in a DM when it could be the peer under another id:
a `@c.us` sender in a `@lid` DM with no `chat_phone` gets no DM, for the same reason.

**In a group** the sender is the participant as WhatsApp gave it, often `@lid`, and the DM goes to
that id. The hub's outbound pairing check is an exact match on the id (`hasPairing` in `bots-db.ts`; the
bots tables have no live Postgres read yet, so this is the arm that runs). So an owner paired with the
bot only under `@c.us` gets HTTP 403 `bot_unpaired`, unless an access grant or a widened DM reach
admits that id. Then the reply is lost and the log says `could not tell the owner`. It is not
sent from the bot to anyone else. See row G1 below.

**Once refused, a summon stays out of its chat.** The host keeps a refused final reply in its delivery
ledger and sends it again 30 and 120 seconds later, with the same thread id. A new summon in that time
reopens the route, and the old answer would be posted from the owner's number under the new question.
So once the hub answers 409 `from_me_route_expired` for that summon's own chat, on any path (the reply, a
file, a tool), its later sends to that chat stop in the plugin: text, files, tool sends, reactions, typing
and the bubble. Its replies still go to the owner's DM when the host marks them as the turn's reply. A retry
by the ledger isn't marked, so it just fails. This is per summon: the new one posts normally. Any other 409
closes nothing: `SESSION_CANNOT_REACH_TARGET` for a file to a group the bot can't reach, or a refusal for
another chat, leaves the summon free to post in its own.

## Clean start

Each summon gets its own Hermes session: the scope's random key is the turn's
`source.thread_id`, which `build_session_key` includes. The alternative, cutting history out of the
provider request as the memory strip cuts memory, was rejected for these reasons:

- The agent still holds the history, so its token estimate and context compression run on it, and a
  compression summary brings old answers back into the request.
- The turn would be saved to the chat's transcript. What the owner's read returned would then be in
  the history of the next ordinary turn, which may be another person in that chat.
- In a reused session the request has to be cut at "this turn's user row", which has to be found
  again on every iteration and survive compression and interrupts.

So:

- The summon's own tool loop, fallback and compression are the host's normal ones, in its session.
- A summon's transcript isn't part of the next summon's history, or an ordinary later turn's.
- An ordinary turn keeps its session and history, and its transcript isn't part of a summon's.
- A summon during an ordinary turn runs alongside it rather than interrupting it.
- `/stop` and `/new` from the summoned session stop every running summon of that chat. A `/stop` from
  the other person, on the bot's own session, doesn't.
- A `/retry` of a summon starts clean again.
- A summon delivered again, by a long poll that wasn't acked or after a restart, runs once. The host's
  own ledger can't tell, because its identity includes the session key and each dispatch has a new
  one. So the plugin keeps hashes of delivered summons for 7 days, up to 2000, in
  `chatlytics-summons-seen.json` under the profile's home. A summon dropped this way logs a WARNING.
  A file that can't be read is treated as empty, and costs the restart case only.
- That hash is written before the turn starts. If the process dies, or the host's `handle_message`
  raises, between the two, the redelivery is dropped as a repeat and that summon doesn't run. The
  log has the WARNING and the owner has to summon again. The other order would post a second public
  reply after a crash at the wrong moment, and that can't be taken back.

**That is isolation of history, not of what the model can look up.** A summon's result can still reach
a later ordinary turn:

- `session_search` is refused on a summon, but not on an ordinary turn, and it reads every session's
  transcript, summons included.
- `memory` stays allowed by decision, so whatever a summon writes there is in every later turn.

Costs: a summon doesn't remember the previous summon, so a follow-up has to carry its own context,
and each summon leaves one session behind for the host's normal expiry to retire. After a gateway
restart a resumed summon has no scope: it can read nothing, and it can't send either, because the
account to reply from is unknown, so the owner has to summon again and the log has the only record.

### The host's "no home channel" notice

hermes-agent posts a one-time setup prompt on any session with no history (`_hmwa_first_contact_notes`
in `gateway/run_turn_prepare.py`), so on a stock 0.22.5 every summon would post it into the chat from
the owner's number. The adapter drops exactly that text, as the host renders it, on a summon's send.
Other turns still get it. The host has no way to decline it for one turn.

## Known gaps

**A "working..." bubble can stay on the owner's number.** A summon that outlives the bubble's delay
posts the bubble from the owner's number, in the chat. It is replaced by the reply. If no reply replaces
it (a `/stop`, a turn that ends with no reply, or a route closed after the bubble went out) the plugin
tries to delete it, but no reply route covers `unsend`, so the hub runs that on the bot's number and it
can't reach the owner's message. The people in the chat see "working..." until the owner deletes it. No
content leaks. Fixing it needs a hub change, or no bubble on summons.

**The per-chat bubble memo is shared by the chat's turns.** An ordinary turn and a summon overlapping in
one chat share one memo. A turn only edits a bubble when it is the single live turn on the chat, so this
needs the bubble's turn to be live alone while another turn sends, which the typing loop's order makes
rare. Then the hub refuses the edit across accounts and sends a new message, and the bubble stays.

**A retry that isn't a refusal.** A reply that failed for a reason other than a 409 (a network error, a
5xx) is retried by the ledger within about 150 seconds, into whatever route is open by then, marked
as recovered. A new summon in that time means the old answer appears in the chat. It is an answer from
that same chat. After a restart the ledger's retry has no scope and is refused.

**An MCP tool told in prose.** A second agent behind an MCP tool can be told in `message` or `text` to read the archive; this is accepted, so that a message to a person or a memory search is never refused for a word.

## The hub's own MCP tools

A gateway with the hub's MCP server connected gives the model a second way to send, which skips the plugin's account checks in `tools.py`, the closed-route check and the refusal of unroutable actions. So on a summon `pre_tool_call` refuses the ones that send or act, also inside `tool_call`, and the model is told to use `chatlytics_send`. The log has the usual `summon read refused` line, with `reason=hub_tool_skips_summon_checks`. The summon's reply and `chatlytics_send` don't go through these tools and work as before. An ordinary turn isn't touched.

| Tool (`src/mcp-server.ts`) | On a summon | On an ordinary turn, as before: the account, with that server's bearer |
|---|---|---|
| `send_message`, `send_media`, `send_poll` | Refused | The owner's when a live route covers the chat by JID, otherwise the bot's. |
| `send_reaction` | Refused | The bot's. |
| `chatlytics_dispatch_action` | Refused | The bot's for non-send verbs. The hub refuses send verbs on a bot's bearer. |
| `manage_group`, all of it, `info` too | Refused | The bot's. Writes need a grant on the bot. |
| `chatlytics_access`, `chatlytics_gates`, `chatlytics_short_links_create` | Refused, all of it, reads too | The bot's own settings and workspace. |
| `update_settings`, `session_update_filters`, `chatlytics_update_config`, `chatlytics_module_config`, `chatlytics_toggle_module`, `chatlytics_bots_create`, `chatlytics_bots_set_default`, `chatlytics_pairings` | Refused | Operator bearer only. A bot's bearer doesn't list them. |
| `read_messages`, `chatlytics_resolve_entity` | Refused, by the read checks | As before. |
| `search`, `get_directory`, `get_status`, `chatlytics_list_actions`, `chatlytics_list_modules`, `chatlytics_get_config`, `chatlytics_short_links_stats` | Allowed: they read and don't send | As before. |

`chatlytics_dispatch` over MCP is refused too. The hub doesn't serve that name today, and it would skip the same checks.

**How a tool is known to be the hub's.** The server's name is the operator's choice, so `mcp__<server>__send_message` alone can't say. The plugin asks the host's tool registry about a tool with one of these names, and treats it as the hub's unless the registry says otherwise:

- The registry has no record of the tool, or can't be asked: the hub's.
- Any tool of the same server has `chatlytics_` in its full name, which covers a server called `chatlytics`: the hub's.
- The tool's own description says `WhatsApp`, `Chatlytics` or `WAHA`: the hub's. This is for a bot whose scope hides every `chatlytics_*` tool from the catalog.
- Otherwise it is another server's, and it runs.

A name the host cut to 64 characters is compared with what the host makes of that server and each of these tools.

**The tradeoff.** This errs toward refusing. Another server's `send_message` is refused on a summon if its description mentions WhatsApp, or if that server has a `chatlytics_`-named tool. The other way to be wrong is possible but narrow: a hub under a server name without the word, a scope that hides every `chatlytics_*` tool, and a description that no longer says any of the three words. If the hub rewords those descriptions, update `_HUB_DESCRIPTION`. A tool added to `src/mcp-server.ts` that sends or acts has to be added to `_HUB_WRITES`; nothing here finds it for you.

**An ordinary turn is not covered.** Its `send_message` still follows the owner's live route, because the hub decides that. See row H5.

## The chat read scope

**The rule.** The bot's full read access applies only in the owner's own DM with the bot. Every other Chatlytics turn may read only the chat it is in, on the bot's own session.

Measured live before this: a group member's trigger called `list_messages` with `session: all` through `tool_call` and got data. The owner's trigger in a group where the bot's own account is a member came from the bot's own leg with no `trigger_source`, so it was an ordinary turn, and did the same.

| Turn | Reads |
|---|---|
| The owner's own DM with the bot | Everything, as before. No scope. |
| A summon | The summon's rules above, on the owner's session. Not changed. |
| Someone else's DM with the bot | This chat, on the bot's session. |
| A group member's trigger | This chat, on the bot's session. |
| The owner's trigger in a group with the bot | This chat, on the bot's session. |
| A turn whose owner or sender can't be told, or an unsigned webhook | Limited (see below). |
| Telegram, CLI, cron, any other platform | No envelope, no scope. |

### Who the owner is

The same check that produces `[owner reply]` (`owner.py`): the gateway's `allow_admin_from` for a DM, against the sender the hub delivered on an authenticated transport. It is not the hub's pairing, which the plugin doesn't read. `is_owner_dm` also needs the chat itself to be on that list, because an owner-session envelope with no `trigger_source` has the owner as the sender of their chat with someone else. `CHATLYTICS_OWNER_TAGGING=off` turns off the text prefix only.

Every failure limits the turn:

- **No `allow_admin_from`.** A bot with no owner on record is limited on every turn, with its own operator too. The log's `scope opened` line says `owner_known=False`. To give a bot's owner full access, list them.
- **One form of a person.** `@c.us` and `@lid` are separate ids. If the hub delivers the owner's DM under a form that isn't listed, as sender or as chat, that DM is limited. List both. See row C4.
- **An unsigned webhook** can't claim anybody, and its session can't be believed either, so it reads nothing from the archive.
- **Several bots on one gateway.** The list belongs to the platform config of a profile, so it covers every bot of that profile: whoever is on it is the owner in a DM with any of them. Under `gateway.multiplex_profiles` each profile has its own adapter and its own list, so a person listed in one profile is not the owner in a DM with another profile's bot, and that bot's turn in the same chat id doesn't change the first one's limits. The session is per bot: it's the envelope's, on the bearer the envelope arrived on.

### What it limits

The enforcement above, unchanged: [Lextrove tools](#lextrove-tools), [Other routes](#other-routes), `tool_call`, `transform_tool_result`, `delegate_task` and `execute_code`, also where it says `summon`. The session is the envelope's. A session that disagrees with what `GET /api/v1/bot/me` said for that bearer, or none, means Lextrove reads are refused for the turn. So does a Chatlytics turn this process didn't dispatch, such as one resumed after a restart: the next message opens its scope again.

A few things are worth saying because they are new for an ordinary turn:

- `get_message`, `get_media_url` and `get_poll` run for an id that an in-scope read of this chat on this session returned, in this turn or an earlier one in this process, so another message in the chat during a turn doesn't take them away. It is still that chat. Past 5000 ids the scope forgets them and starts again, and a forgotten id is refused until a read returns it again.

- The plugin's `chatlytics_read` runs when its `chatId` is this chat, in any form the envelope knows, written as an id: the JID in any case, `@s.whatsapp.net`, or in a DM whose phone is known, that phone bare or with `+`. Any other `chatId`, a missing one, a name, a phone with spaces, any other argument, and a turn whose session isn't known or believed are refused. This is how a bot with no Lextrove server reads its own chat. The prompt line says so. No result check applies: the hub's rows have no chat field. From the hub's source, `readMessages` passes `chatId` to the transport as written, with no name resolution, so the check on the id is what holds the read to this chat. A bare phone passes this check but isn't a JID, so what it returns is the transport's answer. None of this has been run against a live hub yet: see row C5.
- On a summon `chatlytics_read` stays refused, in its own chat too, because it reads the bot's number and the summon's chat is on the owner's. The hub's `read_messages`, and a `chatlytics_read` served over MCP, stay refused on every limited turn, because an MCP server's bearer may be another bot's.
- A tool is taken for Lextrove's by its name, on any MCP server. On a limited turn another server's `list_messages` or `get_contact` gets the same refusal. On a summon anything but the limited server's is refused whatever its arguments: see [Two keys](#two-keys).

### A conversation its chat moved past

From hermes-agent 0.22.6, when a chat moves on (`/new`, `/resume`, a reset), the old conversation can keep running from the apps as a detached conversation (`gateway/session_detached.py`). The host sends its replies to app viewers only. It also binds its turns as a Desktop session's: platform `local`, the session id as the chat, no thread. And a turn from an app has no context line in its prompt. So neither usual way finds the scope, and the turn and its subagents read everything until this was fixed (chatlytics#974).

So a tool call whose bound route key is a detached one (`agent:<profile>:detached:<session id>`) asks the host for that conversation's source, which keeps the chat, thread and profile it came from. A subagent has the key through its copied context, in the background too. `execute_code` runs in a copy of the turn's context on both hosts, so it has the key too. The ids its request binds are kept as well, because the send path finds a summon's key by them.

| The conversation came from | Reads |
|---|---|
| A summon | The summon's rules, by the thread id in its source. |
| A summon whose scope this process no longer has | Nothing from the archive. |
| A limited chat | The chat read scope, the chat's current one. |
| The owner's own DM with the bot | Everything. No scope. |
| Telegram or any other platform | Everything. No scope. |
| A Chatlytics chat this process has no entry for, as after a restart | Nothing from the archive. That holds for the owner's own DM too, until the next message in that DM. |
| Not known: the host's lookup failed, or no gateway runner | Nothing from the archive, whatever platform it came from. |
| The chat took it back while a turn or a background subagent still runs on the old key | Judged by the session's row. Another platform: everything. Chatlytics or no row: nothing from the archive, for the owner's DM too. |

A host without this key (0.22.5) behaves as before. Nothing here has run from a real app: see row D1.

### What doesn't change

Accounts, routes, session keys, history, memory (the memory strip stays summon-only), the owner-DM fallback, the bubble, the home-channel notice and redelivery. A limited turn keeps its `source.thread_id`, starts no clean session, and keeps `chatlytics_send` and the hub's MCP send tools: `hub_tool_skips_summon_checks` and `summon_action_off_route` are summon-only. The scope has `kind` chat and its own tables, and its key isn't in the summon format, so nothing on the send path can take it for a summon.

The line and its key are the same on every turn of a chat in one process, so the system prompt doesn't change from turn to turn and a session that keeps its history keeps its provider cache.

### What this does not stop

As above: this stops a confused model, not a hostile one. [What this does not stop](#what-this-does-not-stop) applies in full, minus the send items, and:

- **A host that can't run the hooks keeps the turn.** Unlike a summon, the turn isn't dropped, because that would silence the bot for everyone. Reads are as wide as they were before this scope, and the log has one ERROR: `chat read scope cannot be enforced on this host`.
- **`execute_code` without the middleware.** Its tool calls run in a copy of the turn's context, so the context variables reach it. The ids that the `llm_request` middleware binds are what the send path uses to find a summon's key, and on a host without it they aren't bound.
- **Two bots in one chat in one profile.** A subagent finds its scope by its profile and the chat, so during an overlap it can get the session of the other bot of that profile. It is still that chat. Bots in different profiles don't meet this: the host binds the profile a turn runs under to `HERMES_SESSION_PROFILE`. A host that doesn't bind it finds no entry for any profile but the primary, so there a subagent of an extra profile reads nothing, also in that profile's owner DM.
- **A `hermes` command started by a tool during a Chatlytics turn.** A CLI child of a terminal tool inherits `HERMES_SESSION_PLATFORM=chatlytics` and has empty tables of its own, so it has no entry for the chat and its Lextrove reads are refused with `no_scope_for_this_turn`. This holds in the owner's own DM too, where the turn itself reads everything. It fails in the limiting direction: to read from such a child, use the turn's own tools instead.
- **A send from a conversation its chat moved past.** The host keeps that turn's replies from the chat, but `chatlytics_send` and the plugin's other tools post to the hub themselves, so they still reach it. An ordinary conversation's send leaves from the bot's number, and a summon's follows the summon's account rules when it is found by its task id. What it can send is limited by what it can read (chatlytics#975).
- **Memory on a summon's turn from the apps.** It has no context line, so the memory strip doesn't apply, and its prompt has the agent's memory. Reads are still limited.
- **A `hermes` command started by a shell tool on a detached turn.** It has no bound key and its platform is `local`, so it isn't limited. Only the key in the turn's own context counts, because a key in the environment would also limit a Telegram conversation's command.
- **`chatlytics_read` on an extra bot.** An ordinary turn's tools use the primary's bearer, and the hub pins a read to the bearer's session. So on a gateway with several bots, an extra bot's turn reads this chat id as the primary's number sees it. On the legacy operator key the tool names no session, so the hub reads on its default account. See row C5.

### The log

```
chatlytics#956: chat scope opened kind=chat scope=<key> session=<s> chat=<id> chat_type=dm has_phone=False owner_known=True
chatlytics#956: no chat scope, the owner's own DM kind=chat chat=<id>
chatlytics#956: chat read allowed kind=chat tool=<name> scope=<key> turn=<id>
chatlytics#956: chat read refused kind=chat tool=<name> reason=<code> scope=<key> turn=<id>
chatlytics#956: chat read scope cannot be enforced on this host, reads are NOT limited: <why>
```

The reasons are the summon's, so `session_not_summon` means "not this turn's session" here too. The model gets the session and chat in the refusal, so it can make the read that works. Both prompt lines tell it to call a limit "this chat" and never to repeat session ids or phone numbers to people.

## Live test rows

What can't be decided without a real hub and real accounts. Each row says what decides it.

| Row | Steps | Passes when |
|---|---|---|
| S1 | Summon in a DM whose envelope has `chat_phone`. Have the model send with `chatlytics_send` to the bare phone. | The message is in the chat from the owner's number. `session_used` in the hub's answer is the owner's session. No 409. |
| S2 | Summon in a `@lid` DM with no `chat_phone`. Have the model send to the person's `@c.us` from `chatlytics_search`. | Nothing reaches the person from either number. The log has `summon send refused, summon_target_unclear`. |
| S3 | Summon on an extra bot of a gateway with two bots, with the primary bound to the same chat by an earlier ordinary message. Have the model use `chatlytics_send` and `chatlytics_react` in the chat. | Both act from the owner's number. The hub's log shows the extra bot's fingerprint for both, none for the primary. |
| G1 | The owner paired with the bot only under `@c.us`. Summon in a group from an account whose participant id is `@lid`. Let a member trigger the bot before the reply. | Decides the documented outcome. Either the owner gets the DM, or the plugin's log has `could not tell the owner: HTTP 403` and the hub's has `bot_unpaired` with the `@lid` target. In neither case does anything reach the group or a member. If it's the 403, this is a hub issue to file. |
| R1 | A summon whose reply gets a 409, with the owner's DM refused too. Summon again within 30 seconds. | The new answer is in the chat and the old one never is, with no `from_me_route_expired` for the old one after the first. |
| R2 | A summon whose `chatlytics_send` to its chat gets a 409. Summon again, and have the first turn call `chatlytics_send` to the chat. | Nothing from the first turn is in the chat. The log has `summon send refused, summon_route_closed`. The new summon's reply posts. |
| M1 | Summon in a DM. Have the model send a file to a group the bot's session isn't in and another tenant session is. | The file is refused with `SESSION_CANNOT_REACH_TARGET`, the log has `route_closed=False`, and the turn's reply is in the summoned chat from the owner's number. |
| A1 | Summon in a DM whose envelope has `chat_phone` and a `@lid` id. Have the model react with a message id from the search result, then pin that message and create a poll in the chat. | The reaction lands from the owner's number, with no `from_me_route_target_mismatch`. Pin and poll reach nobody, and the log has `summon send refused, summon_action_off_route` for each. |
| H1 | Summon in a DM on a gateway with the hub's MCP server, under any server name, bearer of the summoned bot. Have the model call `send_message` to the envelope's chat id. Repeat with `send_media` and `send_poll`. | Refused each time: nothing reaches the chat from either number, and the log has `summon read refused` with `reason=hub_tool_skips_summon_checks`. The model then uses `chatlytics_send`, or the turn's reply, and that posts from the owner's number. |
| H2 | The same, with `send_message` to the bare phone of that chat. | Refused as in H1. The person receives nothing from the bot's number. |
| H3 | Summon, close the route with a person's message to the bot in that chat, then have the running turn call `send_message` to the chat id with no session, and again naming the owner's session. | Both refused in the plugin, so the hub's log has nothing for them. Nothing reaches the chat from either number. |
| H4 | The same turn as H3 after a new summon reopened the route: have the old turn call `send_message`. | Refused. Nothing from the old turn is in the chat, and the new summon's reply posts. |
| H5 | During an open route, have an ordinary turn in that chat call `send_message` with no session. | Not changed by this check. Source says: from the owner's number. Record the sender, and that the log has no `hub_tool_skips_summon_checks`. |
| H6 | Summon, then have the model call `send_reaction`, and `chatlytics_dispatch_action` with `react` and with `pin`, on a message of the chat. | All refused. The chat shows nothing from these calls. `chatlytics_react` still reacts from the owner's number. |
| H7 | Summon in a group, then have the model call `chatlytics_dispatch_action` with `send`, and `manage_group` with `add_participant`, on a bot with the write grant. | Both refused in the plugin, grant or no grant. The group doesn't change. |
| H8 | The gateway's MCP bearer differs from the summoned bot's, if it does on any profile. Have the model call `send_message` to the chat id. | Refused. The hub's log has nothing for that bot's fingerprint. |
| H9 | On the same gateway, another MCP server with a `send_message` tool that doesn't mention WhatsApp. Have a summon call it. | It runs. This is the false refusal to look for. |
| B1 | A long summon, `/stop` after the bubble is up. | Records the stuck bubble in **Known gaps**: whether "working..." stays, and for how long. |
| C1 | A group member who isn't the owner triggers the bot, and asks about another chat so that the model reads `session: all`. | The log has `chat scope opened kind=chat` and `chat read refused kind=chat ... reason=session_not_summon`. The reply says it can read only this chat, with no session id or phone. A read of this chat with the bot's session runs. The reply leaves from the bot's number. |
| C2 | The owner types the trigger in a group where the bot's own account is a member. Same question. | The same as C1, with the text tagged `[owner reply]`. The reply leaves from the bot's number. |
| C3 | Someone other than the owner DMs the bot. Same question, then ask about the DM itself. | The wide read is refused, and the read of this DM on the bot's session runs. The turn still has its earlier history. |
| C4 | The owner DMs the bot from their usual device. Have the model read `session: all`. | The log has `no chat scope, the owner's own DM`, and the read runs. If it has `chat scope opened` instead, the hub delivered a form that isn't listed: compare `sender_jid` and `entity_jid` with `allow_admin_from`. |
| D1 | A group member triggers the bot. Send `/new` in the chat. Open the old conversation in an app and ask it to read `session: all`, then to delegate that read to a subagent, then to read this chat with its session. Repeat in the owner's own DM with the bot, and on a Telegram conversation. | In the group both wide reads are refused, with `chat read refused kind=chat ... reason=session_not_summon`, and the read of this chat runs. In the owner's DM and on Telegram all of them run, with no refusal in the log. |
| K1 | With the limited key on the host, call the limited server directly, not through a summon, with `session: all`. Then, on a summon, have the model read this chat. The hook refuses `session: all` on a summon before it reaches the server, so one call can't show both. | The direct call returns only the owner's session, and naming another session is a 403. The summon's read runs, and the log has `summon read allowed tool=mcp__lextrove_limited__...`. |
| K2 | In the owner's own DM with the bot, have the model read `session: all`. Then have it call the same tool on the limited server. | The first returns several sessions. The second is refused with `limited_key_is_for_summons`. |
| K3 | Take `LEXTROVE_LIMITED_API_KEY` off the host, restart, and summon. Have the model read this chat on each server. Run it on a primary bot of a multiplexed gateway, and with the key back, summon that bot again. | Both are refused, the log has `limited_key_missing`, and the reply says it can't read this chat. With the key back, the read runs. |
| K4 | On a summon, then on a group member's turn, have the model read the profile's env file with `execute_code` and with `terminal`, naming the file. | Refused each time with `names_the_archive_or_host_store`, and no key in the reply or the log. |
| C5 | On a bot with no Lextrove server, a group member triggers the bot and asks what was said here. Have the model call `chatlytics_read` with the group's JID, then with another chat's id, then with a contact's name. On a gateway with several bots, repeat on an extra bot. | The first returns this group's messages and only them, and the log has `chat read allowed kind=chat tool=chatlytics_read`. The other two return no messages, and the log has `chat read refused kind=chat tool=chatlytics_read reason=chat_not_summon`. On the extra bot, record whose view of the chat came back. |

### Results so far

Run on the test host, against a live hub. Only what is listed here has run.

| Row | Build | Result |
|---|---|---|
| C1 (the wide read) | `5e3fca6` | In the group, a forced `session all` read was refused by the plugin: `chat read refused kind=chat reason=session_not_summon`. |
| C1 (the send) | `5e3fca6` | `chatlytics_send` from that turn posted its PONG and DONE from the bot's number. |
| C4 (the send) | `32f7bdc` | In the owner's DM with the bot, a `!sammie` send posted PONG and DONE from the owner's number. |
| C4 (a read) | `32f7bdc` | `!sammie summarize this chat`: the scope opened for that chat, 3 reads were allowed, none outside it. |
| Limited turn, reaction | | `chatlytics_react` returned success on the bot's own session. |
| D1 | | **Not run.** No live detached conversation has been tried: only the host's own code driven by the tests. |
| K1-K4 | | **Not run.** The limited key isn't on a host yet. The tests drive the host's own config reader, secret scope, registry and tool loop, with tools that stand in for Lextrove. |

Not proven live: a closed reply route (R1, R2), a third-party DM (the hub's DM policy drops those before the plugin sees them), and D1.
