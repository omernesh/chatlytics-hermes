# Summoned turns: read scope and clean start

When the owner of a WhatsApp number types a bot's trigger in one of their own chats, the hub
delivers an envelope with `trigger_source: "from_me"`. The reply goes into that chat from the
owner's number, so the other people there read it. Such a turn may read only that chat on that
account, and may not inherit earlier turns of the chat. Other turns are not affected in any way.

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
| Any other tool on that server | Refused. |

An argument not listed for a tool is refused too: a filter this table doesn't know could widen the
read. To allow more, measure what the tool returns for that call first.

### Other routes

| Route | On a summoned turn |
|---|---|
| `session_search` | Refused: it reads every session's transcript. |
| `chatlytics_read`, the hub MCP's `read_messages` | Refused: they read the bot's own number. |
| `chatlytics_dispatch` | Refused when the action reads (`get`, `list`, `search`, `messages`, ...). |
| `chatlytics_directory`, `chatlytics_search`, `chatlytics_resolve_entity` | Allowed: names and ids, no messages, and sending needs them. |
| `terminal`, `execute_code`, `process`, `read_file`, `search_files`, `cronjob` | Refused when the arguments name the archive or the host's own stores. This is text matching. |
| MCP tools that send (the tool part of the name has `send`, `post`, `reply`, `mail`, `message`, ...) | Refused when an argument other than words for a person to read (`text`, `message`, `body`, `name`, ...) names them, at any depth. JSON held in any argument is opened and searched the same way. So a mail about Lextrove passes, and an HTTP post whose JSON body holds a Lextrove URL doesn't. |
| Other MCP tools | Refused when any argument or key names them, at any depth, including JSON held in a string. `message` to another agent, `name` of a resource and `content` of a database call are all routes. The cost: a note-taking tool given the word in a title is refused too. |
| Chatlytics' own MCP tools, MCP memory tools (the server's name has `chatlytics`, `memor`, `mem0`, `cognee` or `honcho`) | Allowed whatever they are asked about, apart from the reads and dispatch above, unless a `url`, `uri`, `path` or `sql` argument names them. Sending and memory are not refused for mentioning a word. The server decides, not the tool's name: `mcp_fs_read_memory_file` gets no exemption. |
| `execute_code` calling a tool | Goes through the same hook, found by task id. |
| `delegate_task` | Allowed. A subagent's calls go through the same hook, found by the copied context variable. |
| Skills | Instructions only. A skill that says `session="all"` gets the refusal when the model follows it. |
| `memory` | Allowed. See the memory strip in the README. |

### What this does not stop

This limits a confused model. It is not a sandbox against a hostile one. Not blocked:

- Shell or code that hides the name: an encoded string, a variable (`$HERMES_HOME/sessions`), a script
  already on disk.
- Another MCP server with its own route to the same data, under a name and arguments that don't name
  the archive.
- An MCP tool with a send word in its name, told in `text` to go and read the archive. A second
  agent behind `mcp_other_agent_send_message` reads the words as a request.
- A memory or Chatlytics server told in `query` to read the archive. Only `url`, `uri`, `path` and
  `sql` are searched there.
- A cron job scheduled during the turn with a prompt that doesn't name the archive. It runs later as
  an ordinary job, with no scope.
- A browser or web tool, logged in somewhere or given a URL: `browser_navigate` and `web_extract` are
  not read at all.
- `write_file` and `patch`, on any path.
- `chatlytics_dispatch` with an action whose name doesn't look like a read (`chats`, `groups`).
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
chatlytics#956: dropped a redelivered summon in chat <id>
chatlytics#956: left the host's setup notice out of summon scope=<key>
```

Arguments and results are never logged.

## Which account a send leaves from

A summon runs alongside the chat's other turns, so every send names its account. With no session
named the hub follows the chat's live reply route, whichever turn is sending, and that sent replies
from the wrong number.

| Send | Session named | If the route is closed |
|---|---|---|
| A summon's, to its own chat in any spelling | The summon's, and the body carries the envelope's chat id | Hub 409. Nothing leaves from either number. |
| A summon's, to another chat | The bot's own | Not on a route. |
| A summon's in a DM, to a target that may be this person under another id | Nothing is sent | The tool gets an error saying so. ERROR in the log. |
| Any other turn's | The bot's own, from `GET /api/v1/bot/me` and ordinary envelopes | Not on a route. |
| Any other turn's, the bot's own session not known yet | None, as before | Follows the route. A WARNING says so. |
| A summon whose scope this process doesn't have | Nothing is sent | ERROR in the log. |

This covers the reply, media, typing, the progress bubble, `chatlytics_send`, `chatlytics_reply`,
`chatlytics_react`, the route-allowed verbs of `chatlytics_dispatch`, and the host's `send_message`
tool. With no route open the hub answers a named own session exactly as it answers no session.

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
that bot's number. A summon doesn't move the chat's other sends to its bot, and an ordinary turn's tools
stay on the primary as before. A `/retry` of a summon takes the bearer of the summon it repeats. If that
isn't known and the gateway has several bots, the retry is refused, with an ERROR in the log.

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
So after a 409 that summon's later sends to its chat, files, typing and bubble stop in the plugin, and
its replies still go to the owner's DM when the host marks them as the turn's reply. A retry by the
ledger isn't marked, so it just fails. This is per summon: the new one posts normally.

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

## Live test rows

What can't be decided without a real hub and real accounts. Each row says what decides it.

| Row | Steps | Passes when |
|---|---|---|
| S1 | Summon in a DM whose envelope has `chat_phone`. Have the model send with `chatlytics_send` to the bare phone. | The message is in the chat from the owner's number. `session_used` in the hub's answer is the owner's session. No 409. |
| S2 | Summon in a `@lid` DM with no `chat_phone`. Have the model send to the person's `@c.us` from `chatlytics_search`. | Nothing reaches the person from either number. The log has `summon send refused, summon_target_unclear`. |
| S3 | Summon on an extra bot of a gateway with two bots, with the primary bound to the same chat by an earlier ordinary message. Have the model use `chatlytics_send` and `chatlytics_react` in the chat. | Both act from the owner's number. The hub's log shows the extra bot's fingerprint for both, none for the primary. |
| G1 | The owner paired with the bot only under `@c.us`. Summon in a group from an account whose participant id is `@lid`. Let a member trigger the bot before the reply. | Decides the documented outcome. Either the owner gets the DM, or the plugin's log has `could not tell the owner: HTTP 403` and the hub's has `bot_unpaired` with the `@lid` target. In neither case does anything reach the group or a member. If it's the 403, this is a hub issue to file. |
| R1 | A summon whose reply gets a 409, with the owner's DM refused too. Summon again within 30 seconds. | The new answer is in the chat and the old one never is, with no `from_me_route_expired` for the old one after the first. |
| B1 | A long summon, `/stop` after the bubble is up. | Records the stuck bubble in **Known gaps**: whether "working..." stays, and for how long. |
