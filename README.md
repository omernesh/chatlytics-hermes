# chatlytics-hermes

Chatlytics WhatsApp platform plugin for [Hermes Agent](https://github.com/NousResearch/hermes-agent).

`chatlytics-hermes` connects Hermes to the Chatlytics WhatsApp gateway as a
first-class platform plugin. It registers a `BasePlatformAdapter` subclass,
21 Hermes tools (text + media + directory + sessions), an aiohttp inbound
webhook server, and a cron-delivery hook -- all auto-discovered by Hermes
through the `hermes_agent.plugins` entry-point group.

## Status

**v2.0 BETA.** Requires `hermes-agent>=0.14,<0.15`.

`hermes-agent` v0.14 is not yet on PyPI; install it from the GitHub tag
`v2026.5.16` (see [Install](#install) below). When v0.14 ships to PyPI the
install line simplifies to a plain `pip install hermes-agent>=0.14`.

## Install

```bash
git clone https://github.com/omernesh/chatlytics-hermes.git
cd chatlytics-hermes
pip install "hermes-agent @ git+https://github.com/NousResearch/hermes-agent.git@v2026.5.16"
pip install -e .
```

The `pip install -e .` step registers the `chatlytics` plugin under the
`hermes_agent.plugins` entry-point group, so Hermes discovers it
automatically on next gateway start.

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

`chatlytics_directory`, `chatlytics_search`, `chatlytics_actions` (lists the
gateway's action catalog).

### Sessions / health (3)

`chatlytics_health`, `chatlytics_login`, `chatlytics_dispatch` (generic
action POST).

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

## License

MIT. See [LICENSE](LICENSE).
