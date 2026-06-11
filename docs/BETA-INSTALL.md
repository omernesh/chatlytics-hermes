# Chatlytics for Hermes Agent — Beta Install (SUPERSEDED)

> **This guide is obsolete.** It described the v2.0.0 private beta
> (operator API keys, pip-only install, webhook-only inbound) and no longer
> matches the current plugin. Several settings it documented
> (`CHATLYTICS_ALLOWED_USERS`, `CHATLYTICS_ALLOW_ALL_USERS`, port 9090
> defaults) do not exist in v4.x.
>
> Use the current documentation instead:
>
> - **Install:** [install.md](install.md) — directory-plugin install
>   (survives hermes-agent updates), per-profile setup, updating
> - **Configure:** [configuration.md](configuration.md) — bot-token auth
>   (`CHATLYTICS_BOT_TOKEN`), inbound modes, all env vars
> - **Verify:** `python -m chatlytics_hermes.doctor` +
>   [troubleshooting.md](troubleshooting.md)
>
> TL;DR for the impatient:
>
> ```bash
> hermes plugins install omernesh/chatlytics-hermes
> hermes plugins enable chatlytics
> export CHATLYTICS_BOT_TOKEN="sk_bot_..."   # app.chatlytics.ai → Bots → Create Bot
> hermes gateway start
> python -m chatlytics_hermes.doctor
> ```

Beta feedback: **omernesher@gmail.com**.
