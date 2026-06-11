# Chatlytics for Hermes Agent — Beta Install

Goal: send your first WhatsApp message from a Hermes Agent via Chatlytics in under 10 minutes.

This guide is for beta testers. It assumes you've already used Hermes Agent and have a working `hermes-agent` install.

> **Status:** v2.0.0 is in private beta. Not on PyPI. Install paths below
> use git+pip or filesystem drop-in. PyPI publication ships in a later
> milestone.

---

## 1. Prerequisites

You need:

- **Hermes Agent `>=0.14.0`** — install with `pip install "hermes-agent>=0.14,<1.0"`. The plugin requires the v0.14+ platform-adapter API; earlier versions will not work. **If hermes-agent is ALREADY installed**, never let a plugin install touch it: install this plugin with `--no-deps` (see below) so the resolver can't downgrade your hermes-agent.
- **Python 3.10 or newer.**
- **A Chatlytics account.** Private beta. Email **omernesher@gmail.com** for access if you don't have one.
- **A paired WhatsApp session** — open the Chatlytics admin panel at https://app.chatlytics.ai → Sessions. If your session shows `WORKING`, you're good. If not, scan the QR code from your phone (WhatsApp → Settings → Linked Devices).

---

## 2. Get your API key + session ID

1. Sign in at **https://app.chatlytics.ai**.
2. Go to **Settings → API Keys** → **Create Key**, name it (e.g. `hermes-beta`), copy the value. **You won't be able to see it again** — store it safely.
3. Note your **session ID** from the dashboard (e.g. `abc12345_yourname`).

---

## 3. Install the plugin

### Option A — pip from GitHub (recommended for beta)

```bash
pip install "git+https://github.com/omernesh/chatlytics-hermes@v2.0.0"
```

That registers `chatlytics-hermes` as a Python package and exposes the `chatlytics` entry point under the `hermes_agent.plugins` group. Hermes will auto-discover it on next start.

### Option B — filesystem drop-in

If you prefer not to install via pip:

```bash
git clone https://github.com/omernesh/chatlytics-hermes ~/.hermes/plugins/chatlytics
cd ~/.hermes/plugins/chatlytics
pip install --no-deps -e .
```

`--no-deps` matters when installing into an existing Hermes venv: it stops
the resolver from "satisfying" the plugin's `hermes-agent` requirement by
downgrading the one you already run. Install the few runtime deps (`httpx`,
`aiohttp`, `PyYAML`, `jsonschema`) separately if missing, then verify with
`python -m chatlytics_hermes.doctor`.

Hermes scans `~/.hermes/plugins/` on startup and loads any directory containing a valid `plugin.yaml`.

### Verify discovery

```bash
hermes plugins ls
```

You should see `chatlytics` listed with kind `platform` and version `2.0.0`.

---

## 4. Configure

The plugin reads its config from environment variables. Export them in your shell, your service unit, or `~/.hermes/config.yaml` — whichever you use for other Hermes env config.

| Variable | Required | Example |
|----------|----------|---------|
| `CHATLYTICS_BASE_URL` | yes | `https://app.chatlytics.ai` |
| `CHATLYTICS_API_KEY` | yes | (paste from step 2) |
| `CHATLYTICS_ACCOUNT_ID` | yes | (session ID from step 2, e.g. `abc12345_yourname`) |
| `CHATLYTICS_WEBHOOK_PORT` | no | `9090` (default; change if port is taken) |
| `CHATLYTICS_HOME_CHANNEL` | no | a chat ID for cron-driven sends |
| `CHATLYTICS_ALLOWED_USERS` | no | comma-separated phone numbers — restricts who Hermes will reply to |
| `CHATLYTICS_ALLOW_ALL_USERS` | no | `true` to disable the allow-list (NOT recommended in production) |

Example shell export:

```bash
export CHATLYTICS_BASE_URL="https://app.chatlytics.ai"
export CHATLYTICS_API_KEY="paste-your-key-here"
export CHATLYTICS_ACCOUNT_ID="abc12345_yourname"
```

Restart Hermes so the adapter picks up the new env.

---

## 5. Verify

Start Hermes and check the gateway status:

```bash
hermes gateway status
```

The `chatlytics` platform should report `connected: true` and `webhook: listening on :9090`.

If the webhook port shows as occupied, set `CHATLYTICS_WEBHOOK_PORT` to a free port and restart.

---

## 6. Configure the Chatlytics webhook URL

Tell Chatlytics where to deliver incoming WhatsApp messages:

1. In the Chatlytics admin panel → Sessions → your session → **Webhook URL**.
2. Set it to a URL Chatlytics can reach that points at your Hermes box's `CHATLYTICS_WEBHOOK_PORT`. Examples:
   - LAN: `http://192.168.1.X:9090/webhook`
   - Tailscale: `http://<tailscale-ip>:9090/webhook`
   - Cloudflare Tunnel / ngrok / tailscale funnel for external Hermes hosts
3. Save. The session config is preserved across Chatlytics restarts.

---

## 7. Send your first message

From a `hermes chat` session, ask:

```
send "hello from hermes via chatlytics" to <a contact in your WhatsApp>
```

Hermes will route this through the `chatlytics` platform adapter. Check your WhatsApp on the phone — the message should arrive within seconds.

---

## 8. Common issues

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `hermes plugins ls` doesn't show `chatlytics` | Plugin not on Python path | Verify with `python -c "import chatlytics_hermes; print(chatlytics_hermes.__file__)"`. If that fails, re-run the install step in the same venv Hermes uses. |
| Adapter reports `connected: false` | `CHATLYTICS_API_KEY` wrong or `CHATLYTICS_BASE_URL` unreachable | Test directly: `curl -H "Authorization: Bearer $CHATLYTICS_API_KEY" $CHATLYTICS_BASE_URL/health`. Should return `{"status":"ok",...}`. |
| `webhook: port already in use` | Another service is on the chosen port | Set `CHATLYTICS_WEBHOOK_PORT` to a free port (e.g. `9091`); update Chatlytics admin panel webhook URL to match. |
| `account not authorized` on first send | `CHATLYTICS_ACCOUNT_ID` doesn't match a real session | Check the exact ID at app.chatlytics.ai → Sessions. Copy verbatim. |
| Incoming messages don't reach Hermes | Chatlytics webhook URL not set, wrong, or unreachable from Chatlytics's host | Re-check step 6. Confirm `curl <webhook-url>` from the Chatlytics host succeeds. Firewall / NAT may need an inbound rule. |
| `Bot cannot initiate conversations with this contact (Can Initiate is disabled)` | WhatsApp's 24h session window — the contact hasn't messaged you first | The recipient must send you a message first to open the 24h window. This is a WhatsApp policy, not a Chatlytics or Hermes limit. |
| `Session is paused (operator_stopped)` | Someone ran `/kill` against this session from WhatsApp | Run `/unkill` from the same WhatsApp account in any chat with this session. Requires TOTP or kill-password. |

---

## 9. What you get

The Chatlytics adapter exposes the full Chatlytics WhatsApp surface to Hermes:

- **Outbound platform methods** — text, typing indicator, images, voice notes, video, documents, animations
- **Inbound delivery** — WhatsApp messages arrive as Hermes `MessageEvent` instances (text, image, audio, video, sticker, document, location, contact-card)
- **Tool surface** — every Chatlytics REST action registered as a Hermes tool, callable by the agent: contact search, group management (create/add/remove/promote), labels, polls, reactions, message edits, presence, channels/newsletters, status/stories, profile updates, and more
- **Cron-driven sends** — set `CHATLYTICS_HOME_CHANNEL` to enable scheduled deliveries from your Hermes routines

For the exact tool catalog and per-tool schemas, see [`README.md`](../README.md) and your Hermes `gateway status` output once the plugin is loaded.

---

## 10. Feedback

This is a beta. If something's broken, confusing, or missing, ping
**omernesher@gmail.com** — beta feedback shapes v2.1 and the eventual
PyPI release.
