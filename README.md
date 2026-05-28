# chatlytics-hermes

> **The most comprehensive WhatsApp integration for Hermes Agent.**

Production-grade messaging through the [Chatlytics](https://chatlytics.ai)
gateway -- text, all six media types (image, voice, video, document, animation,
sticker), reactions, groups, channels, contacts, polls, labels, presence,
profile. **21 Hermes tools, 100% of Hermes 0.14's upstream
`BasePlatformAdapter` contract, end-to-end async.**

[![PyPI](https://img.shields.io/pypi/v/chatlytics-hermes.svg)](https://pypi.org/project/chatlytics-hermes/) [![Python](https://img.shields.io/pypi/pyversions/chatlytics-hermes.svg)](https://pypi.org/project/chatlytics-hermes/) [![License](https://img.shields.io/pypi/l/chatlytics-hermes.svg)](LICENSE)

A first-class platform plugin for [Hermes Agent](https://github.com/NousResearch/hermes-agent).
One `pip install` hands your agent a fully-stocked WhatsApp toolbox -- a
`BasePlatformAdapter` subclass implementing all five required methods plus all
six media variants, 21 Hermes tools auto-registered through
`ctx.register_tool()`, an aiohttp inbound webhook server living *inside*
`connect()` (no Flask, no leaked threads), and a cron-delivery hook for
scheduled sends. Auto-discovered through the `hermes_agent.plugins`
entry-point group.

## Why chatlytics-hermes?

- **Full surface, not a stub** -- every Chatlytics REST action exposed as a
  Hermes tool. No "we ship 5 read tools, write your own for the rest" -- 21
  tools covering send (text + 6 media), read, search, directory, sessions,
  presence, profile, actions enumeration, health, dispatch.
- **Upstream contract end-to-end** -- implements every method on Hermes 0.14's
  `BasePlatformAdapter` (`connect/disconnect/send/send_typing/get_chat_info`
  plus media variants plus inbound `MessageEvent` dispatch via
  `MessageType.{TEXT,IMAGE,AUDIO,VIDEO,DOCUMENT,STICKER,...}`). No fork, no
  vendor -- the plugin tracks the canonical contract.
- **Production-grade safety** -- default-deny `CHATLYTICS_UPLOAD_ALLOWED_ROOTS`
  allowlist on every file-bearing tool (no `filePath=/etc/passwd`
  prompt-injection), strict JID validation at the schema layer (no ambiguous
  chat-id strings ever reach the gateway), machine-readable `_error` sentinels
  on every failure mode (`transport_error`, `auth_error`, `server_error`,
  `validation_error`, `unknown_error`).
- **Async-native** -- `httpx` for all outbound calls (matches Hermes runtime
  conventions), aiohttp inbound *inside* `connect()` so plugin reload doesn't
  leak threads, `_keep_typing()` 30s heartbeat for the WhatsApp 24h window,
  retry-with-exponential-backoff baked into the gateway client.
- **First public release (v3.0.0)** -- `pip install chatlytics-hermes` and
  your agent has WhatsApp.

---

## Status

**Stable v4.0.0 release.** Requires `hermes-agent>=0.14,<0.15`.

`hermes-agent` v0.14 is not yet on PyPI; install it from the GitHub tag
`v2026.5.16` (see [Install](#install) below). When v0.14 ships to PyPI the
install line simplifies to a plain `pip install hermes-agent>=0.14`.

## What's new in v4.0

**Multi-Bot Platform alignment.** v4.0 lands the plugin-side half of the
chatlytics v4.0 Multi-Bot Platform milestone. Each Hermes profile now maps
1:1 to a chatlytics bot via a per-bot bearer token (`CHATLYTICS_BOT_TOKEN`,
`sk_bot_...` shape).

- **Preferred auth:** `CHATLYTICS_BOT_TOKEN` (per-bot bearer issued by
  `chatlytics bots create`). The chatlytics gateway resolves it through
  `resolveBotFromBearer` — identifies the BOT, not the operator. Per-bot
  `permission_scope` (tool allowlist + rate limit) is enforced server-side.
- **Back-compat:** `CHATLYTICS_API_KEY` continues to work as a one-minor-
  cycle fallback. Existing v3.x deployments need NO config change to keep
  running on plugin v4.0.
- **Resolution precedence** (highest first): env `CHATLYTICS_BOT_TOKEN` →
  `extra.bot_token` → env `CHATLYTICS_API_KEY` → `extra.api_key`.
- **Operator visibility:** the adapter logs
  `chatlytics adapter authenticated as bot (fp=<8-char>)` on connect so
  operators can confirm which auth path the plugin took. Token plaintext
  NEVER appears in logs.

See [CHANGELOG.md](CHANGELOG.md) `## [4.0.0]` for the full migration
checklist (5 numbered steps). One-minor-cycle deprecation: `api_key`
fallback slated for removal in plugin v5.0.

## Migration from 2.x

v3.0 is the first public PyPI release and carries three breaking changes
that close every deferred breaking-change item from the v2.1 backlog.
**Tool-surface count unchanged at 21 tools.**

If you call the MCP tool surface only:

- **`chatlytics_get_chat_info`** -- callers checking
  `result.get("success") is False` to detect "chat not found" must
  now check `result.get("chat") is None` (success path). Error
  detection: `result.get("success") is False and result.get("_error")`
  surfaces the machine-readable code (`transport_error`, `auth_error`,
  `server_error`, `validation_error`, `unknown_error`).
- **`chatId` validation** -- bare phone numbers and display names are
  now rejected at the schema layer. Resolve to a JID via
  `chatlytics_search` first, then pass the
  `@c.us`/`@g.us`/`@lid`/`@newsletter` JID to chatId-bearing tools.
  Regex: `/@(c\.us|g\.us|lid|newsletter)$/i`.

If you call the Python adapter directly (library users):

- **`adapter.send_image_file` / `send_animation_file` /
  `send_video_file` / `send_file_file`** -- removed. Use
  `adapter.send_image(chat_id, resource=...)` etc. The `resource`
  argument auto-detects URL vs local-path. `Path` objects, existing
  path strings, `bytes`, and `bytearray` are all accepted. The
  `CHATLYTICS_UPLOAD_ALLOWED_ROOTS` default-deny allowlist from v2.1
  is preserved on every file branch.

See [CHANGELOG.md](CHANGELOG.md) `## [3.0.0]` for the full breaking
list, additive items (smoke wheel caching, API audit doc), and
migration notes.

## What's new in v3.0

**Breaking-change harmonization.** Three deferred breaking changes
from the v2.1 backlog ship in 3.0: `chatlytics_get_chat_info` return
shape disambiguates empty-vs-error with a machine-readable `_error`
sentinel code, `chatId` schemas tighten to JID-only (matches the
sibling JS bundle's regex), and the adapter's six `send_*_file`
methods collapse into unified `send_*(resource=...)` with
URL-vs-path auto-detection. **Tool surface unchanged at 21 tools.**

**Additive.** `scripts/smoke.sh --cached` caches the `hermes-agent`
wheel between smoke runs for faster local iteration.
`.planning/HERMES-API-AUDIT.md` inventories every `hermes.*` import
in the plugin so a future hermes 0.15 upgrade is fast.

**Quality.** Cosmetics sweep across `adapter.py` and `tools.py`
closes six explicitly-deferred LOW/INFO nits from the v2.1 audit
(docstring tightening, signature parity, module-level constants).
Zero behavior change. Test count: 120/120, preserved exactly.

## What's new in v2.1

**Security.** v2.1.0 closes one BLOCKER and two HIGH issues carried
forward from the v2.0 milestone-wide review. BL-01 was a `_keep_typing`
async-cm vs base-coroutine shape mismatch that would have crashed the
plugin on the first production inbound message. HI-01 was an arbitrary
local-file read primitive on the 5 media tools (prompt-injectable
`filePath="/etc/passwd"`); v2.1 introduces a default-deny
`CHATLYTICS_UPLOAD_ALLOWED_ROOTS` env-configured path allowlist. HI-03
fixed `**kwargs` gaps on two media overrides for upstream-signature
forward-compat. **All v2.0.0 callers should upgrade.**

**Quality.** Live-loader integration smoke (`tests/test_live_loader.py`)
now exercises the real `register(ctx)` path against a respx-mocked
gateway and asserts all 21 tools land -- the test harness gap that hid
BL-01 is closed. Observability hardening: `send_typing` transport errors
log at DEBUG (not WARNING); dropped reserved metadata keys emit a WARN
per drop; silent `ctx.get_platform` failures now leave a DEBUG breadcrumb.
Test infra cleanup: conftest now teardown-clean, `_FakePlatformConfig`
consolidated into one shared fixture, `scripts/smoke.sh --fast` skips
docker for local iteration. **The 21-tool surface is unchanged** -- v2.1
is a drop-in upgrade from v2.0.

See [CHANGELOG.md](CHANGELOG.md) for the full Security / Added / Changed
/ Fixed / Docs breakdown.

## Install

```bash
pip install "hermes-agent @ git+https://github.com/NousResearch/hermes-agent.git@v2026.5.16"
pip install chatlytics-hermes
```

`pip install chatlytics-hermes` pulls the latest 3.x from PyPI and
registers the `chatlytics` plugin under the `hermes_agent.plugins`
entry-point group, so Hermes discovers it automatically on next
gateway start.

For a development install from source:

```bash
git clone https://github.com/omernesh/chatlytics-hermes.git
cd chatlytics-hermes
pip install "hermes-agent @ git+https://github.com/NousResearch/hermes-agent.git@v2026.5.16"
pip install -e ".[dev]"
```

## Configuration

Configure the plugin via environment variables (preferred) or a YAML config
block (`hermes config edit`).

### Environment variables

| Variable                      | Required | Description                                                                       |
| ----------------------------- | -------- | --------------------------------------------------------------------------------- |
| `CHATLYTICS_BASE_URL`         | yes      | Chatlytics gateway base URL (e.g. `https://gateway.chatlytics.ai`)                |
| `CHATLYTICS_API_KEY`          | yes      | Bearer token for REST authentication                                              |
| `CHATLYTICS_ACCOUNT_ID`       | no       | Default session/account ID for outbound sends                                     |
| `CHATLYTICS_WEBHOOK_PORT`     | no       | Local port for the aiohttp inbound webhook listener (default: `8765`)             |
| `CHATLYTICS_WEBHOOK_SECRET`   | no       | HMAC-SHA256 shared secret for `X-Chatlytics-Signature` verification               |
| `CHATLYTICS_HOME_CHANNEL`     | no       | Default chat_id for cron / notification delivery                                  |
| `CHATLYTICS_UPLOAD_ALLOWED_ROOTS` | no   | OS-pathsep-separated absolute paths that media tools may read from disk (default-deny when unset; see Security below) |

### Security: filePath upload allowlist (`CHATLYTICS_UPLOAD_ALLOWED_ROOTS`)

The 5 media tools (`chatlytics_send_image`, `chatlytics_send_voice`,
`chatlytics_send_video`, `chatlytics_send_file`,
`chatlytics_send_animation`) accept an optional `filePath` parameter
that uploads a local file to the Chatlytics gateway. To prevent
prompt-injection or LLM-manipulation attacks from reading arbitrary
host files (e.g. `/etc/passwd`, `C:\Windows\System32\config\SAM`),
local-file uploads are **default-deny**: every `filePath` value is
rejected unless it resolves under a configured allowed root.

Configure the allowlist via `CHATLYTICS_UPLOAD_ALLOWED_ROOTS`, using
the OS path separator (`:` on POSIX, `;` on Windows):

```bash
# POSIX
export CHATLYTICS_UPLOAD_ALLOWED_ROOTS="/var/lib/chatlytics/uploads:/tmp/chatlytics"
```

```powershell
# Windows PowerShell
$env:CHATLYTICS_UPLOAD_ALLOWED_ROOTS = "C:\Users\Public\Documents\chatlytics;C:\Temp\chatlytics"
```

When `CHATLYTICS_UPLOAD_ALLOWED_ROOTS` is **unset**, every `filePath`
upload returns
`{"success": false, "error": "Permission denied: Local file uploads are disabled; ..."}`.
URL-based uploads via `mediaUrl` are unaffected — only the local-file
path is gated.

Recommended practice: point the allowlist at a dedicated upload
directory that is OS-owned (mode `0700`), and pipe agent-produced
files through that directory before invoking a media tool.

### YAML config (optional)

```yaml
platforms:
  chatlytics:
    enabled: true
    extra:
      base_url: https://gateway.chatlytics.ai
      api_key: ${CHATLYTICS_API_KEY}
      account_id: 3cf11776_logan
      webhook_port: 8765
      home_channel: "120363100000000000@g.us"
```

## Usage

Once installed, `chatlytics` is auto-registered as a Hermes platform plugin.
Start the gateway as usual:

```bash
hermes gateway start
```

To verify the plugin loaded, enumerate registered entry points:

```bash
python -c "import pkg_resources; print([ep.name for ep in pkg_resources.iter_entry_points('hermes_agent.plugins')])"
# -> [..., 'chatlytics', ...]
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
gateway's `/api/v1/upload` endpoint first.

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
Use the actions tool to discover, the dispatch tool to invoke. The split
is intentional and mirrors the Chatlytics Claude Code MCP bundle's
GET-vs-POST separation.

Every tool returns `{"success": bool, ...}`. On non-2xx responses or
transport errors the result is `{"success": False, "error": "...", ...}`
with the original status code and parsed body preserved.

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
the full test suite. Use this to validate a release before tagging.

For faster local iteration, pass `--cached` to cache the `hermes-agent`
wheel between runs at `.smoke-cache/`:

```bash
bash scripts/smoke.sh --cached
```

The first cached run downloads the wheel; subsequent runs install from
the local cache (no network). The cache invalidates automatically when
the pinned `hermes-agent` tag changes. If the cached install fails
(corrupted wheel, missing dep), the script falls back to a normal
network install and refreshes the cache.

## Architecture notes

A few intentional design decisions, surfaced here so they don't surprise
contributors:

- **Inbound webhook lives inside `connect()`.** The aiohttp server starts
  when `adapter.connect()` is called and stops on `disconnect()`. There is
  no separate thread or process -- inbound webhook handling runs on the
  same event loop as outbound sends, which keeps message ordering
  deterministic and avoids cross-thread state coordination.
- **Outbound transport is `httpx.AsyncClient`** with a 30 s default
  timeout. The client is created at `connect()` and torn down at
  `disconnect()`; tool handlers resolve it lazily through
  `ctx.get_platform("chatlytics").adapter.client`.
- **`_keep_typing` is an `async` context manager**, not the base coroutine
  shape some upstream Hermes platforms use. The asynccontextmanager form
  composes cleanly with `async with` in tool handlers and guarantees the
  background heartbeat task is cancelled on exit even if the wrapped body
  raises. This shape divergence is intentional; the heartbeat fires
  immediately on enter (so the typing bubble appears without waiting the
  full 30 s interval) and re-fires every 30 s thereafter.
- **Local media files are read off the event loop.** The local-path branch
  of `_resolve_media_url` wraps `open()+read()` in `asyncio.to_thread` so
  concurrent media-tool calls don't stall the loop while a multi-MB file
  is read from disk.
- **`_keep_typing` shape (v2.1).** v2.1 rewrote `_keep_typing` as a plain
  coroutine matching the upstream `BasePlatformAdapter` signature
  `(self, chat_id, interval=30.0, metadata=None, stop_event=None)`. The
  in-plugin async-cm ergonomics callers used in v2.0 are preserved via
  a new `_typing_scope(chat_id)` helper -- callers should
  `async with self._typing_scope(chat_id):` instead of
  `async with self._keep_typing(chat_id):`. Public tool surface
  unchanged.

## Known issues

- **`filename` for URL-path documents may not be honored by the gateway.**
  `chatlytics_send_file` accepts a `filename` parameter that the
  Chatlytics gateway is expected to surface as the saved filename on the
  recipient end. For local-path uploads, the filename is set when the
  bytes are POSTed to the gateway's upload endpoint, so it always takes
  effect. For URL-path documents (where the plugin only forwards a
  `mediaUrl`), it is not yet confirmed that the gateway re-sets the
  filename downstream. Tracking upstream; if you rely on filename
  control, prefer the local-path mode and the
  `CHATLYTICS_UPLOAD_ALLOWED_ROOTS` allowlist.
## License

MIT. See [LICENSE](LICENSE).
