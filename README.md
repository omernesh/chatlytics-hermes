# chatlytics-hermes

> **The most comprehensive WhatsApp integration for Hermes Agent.**

Production-grade messaging through the [Chatlytics](https://chatlytics.ai)
gateway -- text, all six media types (image, voice, video, document, animation,
sticker), reactions, groups, channels, contacts, polls, labels, presence,
profile. **21 Hermes tools, the full upstream `BasePlatformAdapter` contract,
end-to-end async.**

[![PyPI](https://img.shields.io/pypi/v/chatlytics-hermes.svg)](https://pypi.org/project/chatlytics-hermes/) [![Python](https://img.shields.io/pypi/pyversions/chatlytics-hermes.svg)](https://pypi.org/project/chatlytics-hermes/) [![License](https://img.shields.io/pypi/l/chatlytics-hermes.svg)](LICENSE)

A first-class platform plugin for [Hermes Agent](https://github.com/NousResearch/hermes-agent).
One install hands your agent a fully-stocked WhatsApp toolbox -- a
`BasePlatformAdapter` subclass implementing all required methods plus all six
media variants, 21 Hermes tools auto-registered through `ctx.register_tool()`,
a durable long-poll inbound consumer (or an aiohttp webhook server living
*inside* `connect()`), native exec-approval / clarify routing to the bot
owner's DM, and a cron-delivery hook for scheduled sends.

## Status

**Stable v4.5.3.** Requires `hermes-agent>=0.14,<1.0` (runs on 0.14.x through
0.16.x — the v4.5.2/v4.5.3 hotfixes specifically target the hermes 0.16
`PluginContext` and tool-registry dispatch shapes). Python 3.10+.

Releases ship from this GitHub repo via the Hermes **directory-plugin**
channel (see [Install](#install)). PyPI hosts the 3.x line only.

## Documentation

| Page | Contents |
|------|----------|
| [docs/install.md](docs/install.md) | Directory-plugin install (the durable channel), per-profile installs, updating, the no-downgrade rule, the `.bak`-shadow-load gotcha |
| [docs/configuration.md](docs/configuration.md) | Every env var / `extra` config key, auth precedence, inbound modes, progress-bubble knobs, upload allowlist |
| [docs/features.md](docs/features.md) | Durable longpoll, control envelopes (`/new` `/stop` `/retry`), per-channel prompts, progress-bubble edit-in-place |
| [docs/approvals.md](docs/approvals.md) | Owner-DM exec approvals + clarify questions (`send_exec_approval`, `send_clarify`, `ask_approval`, `ask_clarify`) |
| [docs/troubleshooting.md](docs/troubleshooting.md) | Doctor CLI, "adapter is not connected", `task_id` TypeError, silent-bot diagnosis, symptom → fix table |

## Why chatlytics-hermes?

- **Full surface, not a stub** -- every Chatlytics REST action exposed as a
  Hermes tool. 21 tools covering send (text + 6 media), read, search,
  directory, sessions, presence, profile, actions enumeration, health,
  dispatch.
- **Survives the host** -- installs as a Hermes *directory plugin* under
  `$HERMES_HOME/plugins/`, so a hermes-agent update (`setup-hermes.sh`'s
  `rm -rf venv`) cannot wipe it. A no-downgrade guard at `register()` time
  catches venvs where a careless install dragged hermes-agent backwards.
- **Survives the network** -- the longpoll inbound loop reconnects through
  chatlytics restarts with a bounded jittered backoff ladder (1s → 30s cap,
  never gives up). Gateways auto-recover; no manual restart needed.
- **Conversation-command native** -- WhatsApp users can type `/new`, `/stop`,
  `/retry`; the chatlytics server turns them into `control` envelopes the
  plugin executes against the live Hermes runner (reset conversation, cancel
  the in-flight turn, replay the last message).
- **Fail-closed approvals** -- dangerous-command approvals and clarify
  questions route to the bot owner's WhatsApp DM; an unanswered approval
  times out to DENY.
- **Production-grade safety** -- default-deny `CHATLYTICS_UPLOAD_ALLOWED_ROOTS`
  allowlist on every file-bearing tool, strict JID validation at the schema
  layer, machine-readable `_error` sentinels on every failure mode, token
  fingerprints (never plaintext) in logs.
- **Async-native** -- `httpx` for all outbound calls, inbound on the same
  event loop as outbound (no leaked threads), `_keep_typing` 30s heartbeat,
  retry-with-backoff baked into the gateway client.

## Install

**Recommended: directory plugin** (survives hermes-agent updates):

```bash
hermes plugins install omernesh/chatlytics-hermes   # clones the repo root → $HERMES_HOME/plugins/chatlytics/
hermes plugins enable chatlytics
```

Then set your bot token and restart the gateway:

```bash
export CHATLYTICS_BOT_TOKEN="sk_bot_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
hermes gateway start
```

That's it — `base_url` defaults to `https://node.chatlytics.ai`. Get a token
at https://app.chatlytics.ai → Bots → Create Bot (shown only once), or via
`chatlytics bots create`.

Full instructions — including per-profile installs, updating with
`git pull --ff-only`, the `--no-deps` rule for pip installs into an existing
gateway venv, and the **never-leave-`.bak`-dirs-inside-`plugins/`** gotcha —
live in [docs/install.md](docs/install.md).

### ⚠️ No-downgrade rule (pip installs into an EXISTING gateway venv)

Never let the resolver touch `hermes-agent` in a live gateway venv. A plain
`pip install` of this package once **silently downgraded a production
hermes-agent 0.15.1 → 0.14.0** (the v4.1.1 release pinned
`hermes-agent==0.14.0`; the resolver "helpfully" satisfied it). The pin has
been a floor (`hermes-agent>=0.14,<1.0`) since v4.1.2, but the discipline
stands — always install with `--no-deps` into an env that already has
hermes-agent:

```bash
uv pip install --no-deps /path/to/chatlytics-hermes   # or: pip install --no-deps .
python -m chatlytics_hermes.doctor                    # verify nothing broke
```

The plugin also self-defends: `register()` compares the installed
hermes-agent against its floor (`>=0.14`) at load time and logs an ERROR
(with the fix) when the environment has been downgraded — it never blocks an
otherwise-working load.

## Self-check (doctor)

When the bot goes silent — classic symptom: the gateway boots with
"2 platforms" instead of 3 — run the doctor from the gateway venv:

```bash
python -m chatlytics_hermes.doctor
# or explicitly:
python -m chatlytics_hermes.doctor --base-url http://192.168.1.133:8050 --token sk_bot_...
```

It prints `PASS`/`FAIL` per check and exits non-zero on any failure:

| Check         | What it verifies                                                              |
| ------------- | ----------------------------------------------------------------------------- |
| `plugin-dir`  | directory-plugin install present (`$HERMES_HOME/plugins/chatlytics/__init__.py`) |
| `config`      | auth token present (`CHATLYTICS_BOT_TOKEN`, legacy `CHATLYTICS_API_KEY`)       |
| `hermes-agent`| installed hermes-agent meets the plugin floor (no-downgrade guard)             |
| `health`      | base_url reachable — `GET /health`                                             |
| `bot-me`      | token valid — `GET /api/v1/bot/me` (prints the bot name)                       |
| `longpoll`    | `GET /api/v1/bot/updates` reachable                                            |

Every partial load failure also emits one ERROR line prefixed
`CHATLYTICS PLUGIN FAILED TO LOAD:` — a stable grep target in the gateway
log. Symptom → fix mappings (timeout / refused / 401 / 502) are in
[docs/troubleshooting.md](docs/troubleshooting.md).

## Configuration

The only required setting is an auth token. Set `CHATLYTICS_BOT_TOKEN`
(your `sk_bot_...` per-bot bearer — **preferred**) and every other variable
has a sane default. The legacy operator `CHATLYTICS_API_KEY` still works as
a fallback but is **removed in plugin v5.0** — migrate now.

Resolution precedence (highest first): env `CHATLYTICS_BOT_TOKEN` →
config `extra.bot_token` → env `CHATLYTICS_API_KEY` → `extra.api_key`.

Quick reference (full table + per-profile YAML examples in
[docs/configuration.md](docs/configuration.md)):

| Variable | Required | Description |
| -------- | -------- | ----------- |
| `CHATLYTICS_BOT_TOKEN` | yes\* | Per-bot bearer (`sk_bot_...`). **Preferred auth.** |
| `CHATLYTICS_API_KEY` | yes\* | Legacy operator bearer. Fallback; removed in plugin v5.0. |
| `CHATLYTICS_BASE_URL` | no | Gateway base URL. Default `https://node.chatlytics.ai`. On-prem gateways should use the LAN URL. |
| `CHATLYTICS_INBOUND_MODE` | no | `webhook` (default) or `longpoll` (PULL via `GET /api/v1/bot/updates` — use behind NAT). |
| `CHATLYTICS_SESSION` | no | Default WAHA session for outbound sends. Required in webhook mode; longpoll envelopes carry it per-message. |
| `CHATLYTICS_STATUS_EDIT_IN_PLACE` | no | Progress-bubble edit-in-place (default `true`). |
| `CHATLYTICS_UPLOAD_ALLOWED_ROOTS` | no | Default-deny allowlist for local-file media uploads. |

\* One of `CHATLYTICS_BOT_TOKEN` / `CHATLYTICS_API_KEY`. Since v4.1.5 a
token-less gateway still boots (degraded): data tools return a
get-a-token prompt instead of failing the whole platform load.

## Feature highlights (v4.2 → v4.5)

See [docs/features.md](docs/features.md) for the full behavior contracts.

- **Durable longpoll (v4.2.0)** — connection refused/reset/timeout, non-200s,
  and the chatlytics graceful-shutdown signal are all NORMAL retry events on a
  bounded jittered backoff ladder (1s → 2s → 5s → 15s → 30s cap, never gives
  up). One WARNING on healthy→degraded, one INFO on recovery. Since v4.5.1
  the loop exits **only** on `CancelledError` — any unexpected exception is
  logged with a traceback and retried.
- **Control envelopes (v4.3.0)** — every longpoll GET advertises
  `caps=control`; the server then delivers `/new` `/stop` `/retry` typed by
  WhatsApp users as `kind:"control"` envelopes (`new_conversation`, `stop`,
  `retry_last`). Unknown kinds/actions are warn-once ignored, never
  dispatched as message text.
- **Progress-bubble edit-in-place (v4.4.0)** — agent turns slower than 8s
  (configurable) send ONE "⏳ working…" bubble flagged `progress: true`, then
  the final reply carries `edit_message_id` so the server edits the bubble
  in place instead of stacking a second message. Default ON; flag off for
  byte-identical v4.3.0 behavior.
- **Owner-DM approvals + clarify (v4.5.0)** — `send_exec_approval` /
  `send_clarify` POST to `/api/v1/bot/questions`; the owner replies
  `/approve <id>` / `/deny <id>` / `/answer <id> <text>` in their DM and the
  resolution rides back as a `question_resolved` control envelope. Unresolved
  approvals time out to DENY (fail-closed). Awaitable `ask_approval` /
  `ask_clarify` primitives are exposed for tooling.
  See [docs/approvals.md](docs/approvals.md).
- **Per-channel prompts (v4.5.0)** — inbound envelopes (longpoll AND webhook)
  may carry an additive `channel_prompt` field, applied per-turn via
  `MessageEvent.channel_prompt` (never persisted to transcript); falls back
  to the local `channel_prompts` config map.
- **Tools work everywhere (v4.5.2/v4.5.3)** — a module-level live-adapter
  registry fixed "adapter is not connected" on hermes 0.16 longpoll
  gateways, and host-injected kwargs (e.g. `task_id`) are now filtered to
  each handler's signature. If you see either symptom, you are on an older
  plugin — update. See [docs/troubleshooting.md](docs/troubleshooting.md).

## Usage

Once installed and enabled, `chatlytics` is auto-registered as a Hermes
platform plugin. Start the gateway as usual:

```bash
hermes gateway start
```

Send a text message from an agent toolset:

```python
result = await ctx.tools.chatlytics_send(
    chatId="120363100000000000@g.us",
    text="Hello from Hermes",
)
# -> {"success": True, "messageId": "..."}
```

## Tool catalog

Twenty-one tools are registered under the `chatlytics` toolset, grouped by
function. Full JSON schemas live in
[`src/chatlytics_hermes/tools.py`](src/chatlytics_hermes/tools.py).

### Messaging (10)

`chatlytics_send`, `chatlytics_reply`, `chatlytics_react`, `chatlytics_edit`,
`chatlytics_unsend`, `chatlytics_pin`, `chatlytics_unpin`, `chatlytics_read`,
`chatlytics_delete`, `chatlytics_poll`.

### Media (5)

`chatlytics_send_image`, `chatlytics_send_voice`, `chatlytics_send_video`,
`chatlytics_send_file`, `chatlytics_send_animation`. Each accepts either a
remote `mediaUrl` or a local `filePath`; local files are uploaded to the
gateway's `/api/v1/upload` endpoint first and gated by the
`CHATLYTICS_UPLOAD_ALLOWED_ROOTS` default-deny allowlist.

### Directory / search (3)

`chatlytics_directory`, `chatlytics_search`, `chatlytics_actions`.
`chatlytics_actions` is read-only -- it issues a **GET** against the
gateway's action catalog and returns the list of dispatchable actions
with their schemas. Use it when an agent needs to discover what actions
are available before invoking one.

### Sessions / health (3)

`chatlytics_health`, `chatlytics_login`, `chatlytics_dispatch`.
`chatlytics_dispatch` is the **POST** counterpart to `chatlytics_actions`
-- it invokes a generic gateway action by name with arbitrary payload.
Use the actions tool to discover, the dispatch tool to invoke.

Every tool returns `{"success": bool, ...}`. On non-2xx responses or
transport errors the result is `{"success": False, "error": "...", ...}`
with the original status code and parsed body preserved. `chatId`-bearing
tools enforce strict JID validation (`@c.us` / `@g.us` / `@lid` /
`@newsletter`) — resolve names/phones via `chatlytics_search` first.

## Migration notes

- **3.x → 4.x:** `CHATLYTICS_BOT_TOKEN` becomes the preferred auth;
  `CHATLYTICS_API_KEY` keeps working as a fallback (removed in v5.0). Full
  5-step checklist in [CHANGELOG.md](CHANGELOG.md) `## [4.0.0]`.
- **2.x → 3.x:** `chatlytics_get_chat_info` return shape, strict JID regex on
  `chatId` schemas, and the adapter-level `send_*_file` → `send_*(resource=...)`
  collapse. See [CHANGELOG.md](CHANGELOG.md) `## [3.0.0]`.

## Development

```bash
git clone https://github.com/omernesh/chatlytics-hermes.git
cd chatlytics-hermes
pip install "hermes-agent @ git+https://github.com/NousResearch/hermes-agent.git@v2026.5.16"
pip install -e ".[dev]"
pytest tests/
bash scripts/smoke.sh   # dockerized clean-room verification
```

`scripts/smoke.sh` runs the package against a fresh `python:3.13-slim`
container -- it installs hermes-agent + this plugin in a clean Python
environment, asserts the `chatlytics` entry point is discoverable, then runs
the full test suite. Use this to validate a release before tagging. Pass
`--cached` to cache the `hermes-agent` wheel between runs at `.smoke-cache/`,
or `--fast` for host-venv pytest only. `scripts/test.sh` runs the suite
without any `pip install` (pytest `pythonpath=src`), so it can never
downgrade a host venv.

## Architecture notes

A few intentional design decisions, surfaced here so they don't surprise
contributors:

- **Inbound transport lives inside `connect()`.** In webhook mode the aiohttp
  server starts when `adapter.connect()` is called and stops on
  `disconnect()`; in longpoll mode `connect()` spawns the poll task instead.
  Either way inbound handling runs on the same event loop as outbound sends —
  deterministic ordering, no cross-thread state, no leaked threads on plugin
  reload.
- **Outbound transport is `httpx.AsyncClient`** with a 30 s default timeout
  (the longpoll GET uses an explicit per-request timeout with read = server
  hold + 15s). The client is created at `connect()` and torn down at
  `disconnect()`.
- **Tool handlers resolve the adapter at call time**, through
  `ctx.get_platform` / `ctx.platforms` when the host exposes them, falling
  back to the module-level `_LIVE_ADAPTERS` registry that `connect()`
  populates (v4.5.2 — real hermes 0.16 `PluginContext`s expose neither
  accessor). Host-injected dispatch kwargs are filtered to each handler's
  signature at bind time (v4.5.3).
- **`_keep_typing` matches the upstream coroutine signature**
  `(self, chat_id, interval=30.0, metadata=None, stop_event=None)`; the
  in-plugin async-cm ergonomics live in `_typing_scope(chat_id)`. The same
  `stop_event` anchors the progress-bubble timer (v4.4.0), so the bubble
  machinery adds no new per-turn task lifecycle.
- **Local media files are read off the event loop** (`asyncio.to_thread`),
  so concurrent media-tool calls don't stall the loop on multi-MB reads.
- **Token discipline (INV-02):** token plaintext never appears in logs; the
  adapter logs an 8-char SHA-256 fingerprint matching the chatlytics-side
  `tokenFingerprint` shape.

## Known issues

- **`filename` for URL-path documents may not be honored by the gateway.**
  `chatlytics_send_file` accepts a `filename` parameter that the
  Chatlytics gateway is expected to surface as the saved filename on the
  recipient end. For local-path uploads the filename is set when the
  bytes are POSTed to the gateway's upload endpoint, so it always takes
  effect. For URL-path documents (where the plugin only forwards a
  `mediaUrl`), it is not yet confirmed that the gateway re-sets the
  filename downstream. If you rely on filename control, prefer the
  local-path mode and the `CHATLYTICS_UPLOAD_ALLOWED_ROOTS` allowlist.
- **A pending progress bubble before a media reply is left behind** as an
  acceptable residual — media sends do not participate in edit-in-place.

## License

MIT. See [LICENSE](LICENSE).
