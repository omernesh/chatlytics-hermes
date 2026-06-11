# Troubleshooting

> Applies to plugin **v4.5.3**.

## First move: run the doctor

```bash
python -m chatlytics_hermes.doctor
# explicit overrides:
python -m chatlytics_hermes.doctor --base-url http://192.168.1.133:8050 --token sk_bot_... --plugins-dir ~/.hermes/plugins/chatlytics
```

Six checks, `PASS`/`FAIL` per line, exits non-zero on any failure:

| Check | What it verifies |
|-------|------------------|
| `plugin-dir` | directory-plugin install present (`$HERMES_HOME/plugins/chatlytics/__init__.py`); warns if only a pip install is found (pip installs don't survive hermes updates) |
| `config` | auth token present (`CHATLYTICS_BOT_TOKEN` preferred; warns on legacy api_key shape) |
| `hermes-agent` | installed hermes-agent meets the plugin floor (no-downgrade guard) |
| `health` | base_url reachable — `GET /health` |
| `bot-me` | token valid — `GET /api/v1/bot/me` (prints the bot name) |
| `longpoll` | `GET /api/v1/bot/updates` reachable (404 = server predates the bot-updates contract) |

Second move: grep the gateway log for the stable load-failure prefix —
every partial load failure emits exactly one line:

```
CHATLYTICS PLUGIN FAILED TO LOAD: <reason + fix>
```

## Connection symptom → fix map

The adapter, the longpoll loop, and the doctor all classify connect
failures through the same mapper (`diagnostics.map_connect_error`):

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| connect **timeout** | dead Tailscale-style IP in `CHATLYTICS_BASE_URL` (TCP half-connects, HTTP hangs) | use the LAN URL on-prem (e.g. `http://192.168.1.133:8050`) or `https://node.chatlytics.ai` |
| **connection refused** | wrong host/port | fix `CHATLYTICS_BASE_URL` |
| **401** | bad/rotated token | rotate via the chatlytics admin; update `CHATLYTICS_BOT_TOKEN` |
| **502** | Cloudflare tunnel concurrent-longpoll limit on the public DNS name | switch the gateway to the LAN base_url |

## Specific failures

### Every tool call returns `"adapter is not connected"` (but the bot receives messages)

Plugin **< 4.5.2** on hermes-agent 0.16+. The real
`hermes_cli.plugins.PluginContext` exposes neither `get_platform` nor a
`platforms` mapping — the only accessors older tool wrappers probed — so
every `chatlytics_*` tool failed even while the longpoll loop was alive.
**Fix:** update to >= 4.5.2 (`git pull --ff-only` in the plugin dir, clear
`__pycache__`, restart the gateway). v4.5.2 adds a module-level
`_LIVE_ADAPTERS` registry that `connect()` populates and the tool wrapper
falls back to.

### `TypeError: chatlytics_health() got an unexpected keyword argument 'task_id'`

Plugin **4.5.2** exactly (and masked-latent before that). Hermes' tool
registry dispatches `entry.handler(args, **kwargs)` with host bookkeeping
kwargs (observed: `task_id`), which the wrapper forwarded raw into bare
handlers. **Fix:** update to >= 4.5.3 — the wrapper now inspects each
handler's signature once at bind time and drops any kwarg the handler
cannot accept (handlers declaring `**kwargs` opt in to everything). Robust
to any future host-injected key.

### Gateway boots with "2 platforms" instead of 3 / bot silent

The classic load-failure class. Run the doctor; grep for
`CHATLYTICS PLUGIN FAILED TO LOAD:`. Common causes: missing token (the
gateway still boots since v4.1.5 — data tools return a get-a-token prompt),
unreachable base_url, plugin dir missing after a botched update, or the
plugin was pip-installed and a hermes-agent update wiped the venv
(reinstall as a directory plugin — see [install.md](install.md)).

### hermes-agent got DOWNGRADED after installing the plugin

A plain `pip install` of plugin v4.1.1 (pinned `hermes-agent==0.14.0`) once
dragged a production 0.15.1 down to 0.14.0. The pin is a floor since v4.1.2,
and `register()` logs an ERROR (with the fix) whenever it detects a
downgraded hermes-agent. **Rule:** always `pip install --no-deps` into an
existing gateway venv, then run the doctor. To recover, reinstall the
correct hermes-agent, then `uv pip install --no-deps <plugin>`.

### Stale code keeps running after an update

Two known causes:

1. **`__pycache__`** — clear it after every `git pull` in the plugin dir,
   then restart the gateway.
2. **A backup copy inside `plugins/`** — hermes shadow-loads ANY importable
   directory under `~/.hermes/plugins/` (and per-profile `plugins/` dirs).
   A `chatlytics.bak/` next to the real plugin gets loaded alongside it and
   serves stale code. Move backups to a sibling dir such as
   `~/.hermes/plugin-backups/`. See [install.md](install.md).

### `longpoll GET transport error ()` log spam

Plugin **< 4.1.1**: the longpoll GET inherited a 30s client timeout against
the server's 25s hold, so empty polls occasionally tripped a `ReadTimeout`
(which httpx stringifies to `()`). Fixed in 4.1.1 with an explicit
per-request read timeout of hold + 15s. Update the plugin.

### Every message is delivered twice (longpoll mode)

The bot's `webhook_url` is still set on the chatlytics side, so the server
POSTs AND queues. Set the bot to queue-only (`webhook_url: null`) when using
`CHATLYTICS_INBOUND_MODE=longpoll`.

### `/new` `/stop` `/retry` typed in WhatsApp do nothing

- Plugin < 4.3.0 doesn't advertise `caps=control`, so the server withholds
  control envelopes entirely — update the plugin.
- Webhook inbound mode never receives control envelopes (they ride the
  longpoll) — switch to `CHATLYTICS_INBOUND_MODE=longpoll`.
- A chatlytics server older than v5.4 has no conversation-command floor.

### Approval/clarify questions fail with 409 `gateway_not_control_capable`

The server refuses `POST /api/v1/bot/questions` from gateways that didn't
advertise `caps=control` on their longpoll. The gateway must run longpoll
inbound mode on plugin >= 4.3.0 (the question flow itself needs >= 4.5.0).
See [approvals.md](approvals.md).

### Replies fail with 400 `"chatId and session are required"`

The outbound send couldn't resolve a WAHA session for the chat. In webhook
mode set `CHATLYTICS_SESSION` (or `extra.session`); in longpoll mode this
resolves automatically from each inbound envelope — for proactive sends to
chats with no inbound history, set the fallback too.

### Local file upload returns "Permission denied: Local file uploads are disabled"

`CHATLYTICS_UPLOAD_ALLOWED_ROOTS` is unset (default-deny) or the path is
outside every allowed root. See the allowlist section in
[configuration.md](configuration.md).

### Longpoll degraded WARNINGs during a chatlytics restart

Expected and self-healing: the loop logs ONE WARNING on healthy→degraded
and one INFO on recovery, retrying on a 1s→30s jittered backoff forever.
**Do not restart the gateway** — it reconnects on its own. If it never
recovers, the WARNING's symptom→fix hint (table above) tells you why.

### WhatsApp-side errors

| Symptom | Cause | Fix |
|---------|-------|-----|
| `Bot cannot initiate conversations with this contact` | WhatsApp's 24h session window — the contact hasn't messaged first | The recipient must message you first. WhatsApp policy, not a plugin limit. |
| `Session is paused (operator_stopped)` | Someone ran `/kill` against the session | `/unkill` from the same WhatsApp account (TOTP or kill-password required). |
