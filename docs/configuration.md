# Configuration

> Applies to plugin **v4.5.3**.

Configure the plugin via environment variables (preferred — set them in the
gateway profile's `.env` / service environment) or the platform `extra`
block in the profile's `config.yaml`. **For every setting, the env var wins
over the `extra` key.**

## Auth

The only required setting is an auth token. Resolution precedence (highest
first):

1. env `CHATLYTICS_BOT_TOKEN`
2. `extra.bot_token`
3. env `CHATLYTICS_API_KEY` (legacy)
4. `extra.api_key` (legacy)

- **`CHATLYTICS_BOT_TOKEN`** (`sk_bot_...`) is the **preferred** auth: a
  per-bot bearer issued once on bot creation (Web UI: app.chatlytics.ai →
  Bots → Create Bot, or `chatlytics bots create`). The chatlytics server
  resolves it to the BOT identity and enforces the bot's `permission_scope`
  server-side.
- **`CHATLYTICS_API_KEY`** is the legacy operator/admin bearer. It still
  works as a fallback, but is **removed in plugin v5.0** — migrate now. The
  doctor and the connect log both tell you which path was taken
  (`authenticated as bot (fp=<8-char>)` vs `operator (legacy api_key,
  fp=...)`). Token plaintext never appears in logs — only an 8-char SHA-256
  fingerprint.
- **No token at all** (since v4.1.5): the gateway still boots. The platform
  registers in a degraded no-credential state; every data tool returns a
  get-a-token prompt, while `chatlytics_health` / `chatlytics_login` are
  exempt so they can still report the degraded state.

## Environment variables

| Variable | Required | Description |
| -------- | -------- | ----------- |
| `CHATLYTICS_BOT_TOKEN` | yes\* | Per-bot bearer (`sk_bot_...`). **Preferred auth.** |
| `CHATLYTICS_API_KEY` | yes\* | Legacy operator bearer. Fallback when `CHATLYTICS_BOT_TOKEN` is unset; removed in plugin v5.0. |
| `CHATLYTICS_BASE_URL` | no | Chatlytics gateway base URL. Default `https://node.chatlytics.ai`. Override to self-host or target a non-default node. **On-prem gateways should use the LAN URL** (e.g. `http://192.168.1.133:8050`) — the Cloudflare tunnel in front of the public DNS name caps concurrent longpolls and 502s past the limit. |
| `CHATLYTICS_INBOUND_MODE` | no | `webhook` (default — aiohttp PUSH server) or `longpoll` (PULL via `GET /api/v1/bot/updates`). |
| `CHATLYTICS_SESSION` | no\* | Default WAHA session name (e.g. `3cf11776_logan`) used for outbound `/api/v1/send` when no inbound-derived session is known. Required in webhook mode; longpoll envelopes carry the session per-message. |
| `CHATLYTICS_ACCOUNT_ID` | no | Default session/account ID for outbound sends. |
| `CHATLYTICS_HOME_CHANNEL` | no | Default chat_id for cron / notification delivery. |
| `CHATLYTICS_WEBHOOK_HOST` | no | Webhook bind host (default `0.0.0.0`). Webhook mode only. |
| `CHATLYTICS_WEBHOOK_PORT` | no | Webhook listener port (default `8765`). Webhook mode only. |
| `CHATLYTICS_WEBHOOK_PATH` | no | Webhook route (default `/webhook`). Validated at boot — must start with `/`, no `?`/`#`/control chars, must not collide with `/health`. |
| `CHATLYTICS_WEBHOOK_SECRET` | no | HMAC-SHA256 shared secret for `X-Chatlytics-Signature` verification. Webhook mode only. |
| `CHATLYTICS_STATUS_EDIT_IN_PLACE` | no | Progress-bubble edit-in-place (default **`true`**). `false` disables both the bubble and the edit (v4.3.0 behavior). |
| `CHATLYTICS_STATUS_BUBBLE_AFTER_S` | no | Seconds an agent turn must run before the "working…" bubble fires (default `8`). Values `<= 0` disable the bubble. |
| `CHATLYTICS_STATUS_BUBBLE_TEXT` | no | Text of the progress bubble (default `⏳ working…`). |
| `CHATLYTICS_UPLOAD_ALLOWED_ROOTS` | no | OS-pathsep-separated absolute paths that media tools may read from disk. **Default-deny when unset.** See below. |

\* One of `CHATLYTICS_BOT_TOKEN` / `CHATLYTICS_API_KEY` must be set for the
plugin to do anything useful. `CHATLYTICS_SESSION` is required in webhook
mode (the webhook transform does not always forward the session) and a
useful fallback in longpoll mode.

All knobs are parsed defensively — a typo'd value falls back to the default
and never raises at gateway boot.

## YAML config (`extra` block)

Every env var has an `extra` equivalent (env wins):

```yaml
platforms:
  chatlytics:
    enabled: true
    extra:
      # bot_token is the preferred auth; base_url is optional and defaults
      # to https://node.chatlytics.ai when omitted.
      bot_token: ${CHATLYTICS_BOT_TOKEN}
      # base_url: http://192.168.1.133:8050    # LAN URL for on-prem gateways
      # inbound_mode: longpoll                 # default: webhook
      # session: 3cf11776_logan                # WAHA session fallback
      account_id: 3cf11776_logan
      webhook_port: 8765
      home_channel: "120363100000000000@g.us"
      # status_edit_in_place: true
      # status_bubble_after_s: 8
      # status_bubble_text: "⏳ working…"
      # upload_allowed_roots: "/var/lib/chatlytics/uploads"
```

## Inbound modes

### `webhook` (default)

The plugin runs an aiohttp server inside `connect()` (same event loop as
outbound — no threads) that chatlytics POSTs inbound messages to. Configure
the bot/session's webhook URL on the chatlytics side to point at
`http://<gateway-host>:<CHATLYTICS_WEBHOOK_PORT><CHATLYTICS_WEBHOOK_PATH>`.
Optionally set `CHATLYTICS_WEBHOOK_SECRET` to enable HMAC verification.
Requires the gateway host to be reachable from chatlytics, and
`CHATLYTICS_SESSION` set for outbound replies.

### `longpoll`

```bash
export CHATLYTICS_INBOUND_MODE=longpoll
export CHATLYTICS_BOT_TOKEN=sk_bot_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

The plugin does **not** start the webhook server. Instead it PULLs inbound
messages by long-polling
`GET /api/v1/bot/updates?cursor=<opaque>&timeout_ms=25000&caps=control` and
acks each processed batch via `POST /api/v1/bot/updates/ack {cursor}`. The
GET does not advance the per-bot read pointer — the consumer acks only after
dispatching every envelope in a batch, so unprocessed envelopes re-deliver
on a crash/restart (at-least-once).

**The bot's `webhook_url` must be `null`** on the chatlytics side —
otherwise chatlytics will POST AND queue, double-delivering every message.

Replies still go out via `/api/v1/send`, which requires the WAHA `session` —
under longpoll this is threaded automatically from each inbound envelope's
`session_id`, so `CHATLYTICS_SESSION` is optional (but a useful fallback for
proactive sends to chats with no inbound history).

Longpoll is the transport that carries **control envelopes** (`/new` `/stop`
`/retry` conversation commands) and **owner-DM question resolutions** — see
[features.md](features.md) and [approvals.md](approvals.md). It is also
self-healing: see "Durable longpoll" in [features.md](features.md).

## Security: filePath upload allowlist (`CHATLYTICS_UPLOAD_ALLOWED_ROOTS`)

The 5 media tools (`chatlytics_send_image`, `chatlytics_send_voice`,
`chatlytics_send_video`, `chatlytics_send_file`,
`chatlytics_send_animation`) accept an optional `filePath` parameter that
uploads a local file to the Chatlytics gateway. To prevent prompt-injection
attacks from reading arbitrary host files (e.g. `/etc/passwd`), local-file
uploads are **default-deny**: every `filePath` value is rejected unless it
resolves under a configured allowed root.

Configure the allowlist using the OS path separator (`:` on POSIX, `;` on
Windows):

```bash
# POSIX
export CHATLYTICS_UPLOAD_ALLOWED_ROOTS="/var/lib/chatlytics/uploads:/tmp/chatlytics"
```

```powershell
# Windows PowerShell
$env:CHATLYTICS_UPLOAD_ALLOWED_ROOTS = "C:\Users\Public\Documents\chatlytics;C:\Temp\chatlytics"
```

When unset, every `filePath` upload returns
`{"success": false, "error": "Permission denied: Local file uploads are disabled; ..."}`.
URL-based uploads via `mediaUrl` are unaffected — only the local-file path
is gated.

Recommended practice: point the allowlist at a dedicated upload directory
that is OS-owned (mode `0700`), and pipe agent-produced files through that
directory before invoking a media tool.
