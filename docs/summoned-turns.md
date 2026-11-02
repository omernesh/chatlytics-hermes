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
| MCP tools, any server | Judged only by the arguments that say where the tool goes, in any spelling of the key (`baseUrl`, `file-path`), at any depth. A place (`url`, `uri`, `path`, `file`, `endpoint`, `resource`, ...) is refused when it points at the archive's host or a host store (`state.db`, `sessions.json`, `.hermes/.env`), a command (`command`, `cmd`, `script`, `code`) when it names either, and a query (`sql`, `statement`, `query`, ...) when it selects from the archive. JSON held in such an argument is searched as text. No other key is searched. So a mail to an address at lextrove, a memory search for the word and `read_note` of `projects/lextrove.md` pass, and a fetch of the archive's URL doesn't. |
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
chatlytics#956: summon send refused, summon_route_closed chat=<id> scope=<key>
chatlytics#956: summon send refused, summon_action_off_route chat=<id> scope=<key>
chatlytics#956: summon media not posted: HTTP 409 chat=<id> scope=<key> route_closed=<bool>
chatlytics#956: dropped a redelivered summon in chat <id>
chatlytics#956: left the host's setup notice out of summon scope=<key>
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

**The hub's own MCP tools.** A gateway with the hub's MCP server connected gives the model a second way to send, which skips the plugin's account checks in `tools.py`, the closed-route check and the refusal of unroutable actions. The hub decides the account (`src/mcp-server.ts`), for the bearer that MCP server is configured with. A route belongs to one bearer, so with another bot's bearer no route applies and the send leaves from that bot's number. The read hook still sees these calls, so `read_messages` and reading dispatch actions are refused.

| Tool | Account with that bearer | If the route is closed |
|---|---|---|
| `send_message`, `send_media`, `send_poll`, no `session` | The owner's, when a live route covers the chat by JID; otherwise the bot's. A bare or `+` phone misses the route, so it leaves from the bot's. | Leaves from the bot's number, into the summoned chat if it can reach it. |
| The same, naming the owner's session | The owner's, when a live route covers the chat. | Hub refuses it: `from_me_route_expired`. |
| `send_reaction` | Always the bot's. It has no route check. | The same. |
| `chatlytics_dispatch_action`, non-send verbs (`react`, `edit`, `unsend`, `pin`, ...) | Always the bot's. | The same. |
| `chatlytics_dispatch_action`, send verbs | Refused by the hub, on any bearer that is a bot's. | The same. |
| `manage_group` | Always the bot's. Writes need an explicit grant on the bot, or the hub refuses them. | The same. |

So on such a gateway a summon can still send from the bot's number into its chat, a summon whose route closed can still post through a route a later summon reopened, and an ordinary turn's `send_message` follows the owner's live route. Rows H1 to H7 record this for real.

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
| H1 | Summon in a DM on a gateway with the hub's MCP server, bearer of the summoned bot. Have the model call `send_message` to the envelope's chat id. | Source says: from the owner's number. Record the sender on the phone. Repeat with `send_media` and `send_poll`. |
| H2 | The same, with `send_message` to the bare phone of that chat. | Source says: from the bot's number. Record the sender, or that the person received nothing. |
| H3 | Summon, close the route with a person's message to the bot in that chat, then have the running turn call `send_message` to the chat id with no session, and again naming the owner's session. | Source says: the first from the bot's number, the second refused with `from_me_route_expired`. Record both. |
| H4 | The same turn as H3 after a new summon reopened the route: have the old turn call `send_message`. | Source says: from the owner's number, with no `summon_route_closed` in the log, because the plugin doesn't see this send. Record it. |
| H5 | During an open route, have an ordinary turn in that chat call `send_message` with no session. | Source says: from the owner's number. Record the sender. |
| H6 | Summon, then have the model call `send_reaction`, and `chatlytics_dispatch_action` with `react` and with `pin`, on a message of the chat. | Source says: all on the bot's session, not the owner's. Record what the chat shows. WAHA may answer 200 and do nothing. |
| H7 | Summon in a group, then have the model call `chatlytics_dispatch_action` with `send`, and `manage_group` with `add_participant`. | Source says: `send` refused, and `manage_group` refused unless the bot has the write grant. With the grant it runs on the bot's session. Record both. |
| H8 | The gateway's MCP bearer differs from the summoned bot's, if it does on any profile. Have the model call `send_message` to the chat id. | Source says: from the MCP bearer's bot's number, whatever the route of the summoned bot. Record the sender and the bot fingerprint in the hub's log. |
| B1 | A long summon, `/stop` after the bubble is up. | Records the stuck bubble in **Known gaps**: whether "working..." stays, and for how long. |
