# Installing chatlytics-hermes

> Applies to plugin **v4.5.8**. Requires `hermes-agent>=0.14,<1.0`
> (verified through 0.16.x) and Python 3.10+.

There are two install channels. **Use the directory plugin** unless you have
a specific reason not to:

| Channel | Survives a hermes-agent update? | How |
|---------|--------------------------------|-----|
| **Directory plugin** (recommended) | **YES** — lives under `$HERMES_HOME/plugins/`, not the venv | `hermes plugins install omernesh/chatlytics-hermes` |
| pip entry-point | NO — `setup-hermes.sh` runs `rm -rf venv`, wiping `site-packages` | `uv pip install --python <py> --no-deps ...` |

## 1. Directory-plugin install (recommended)

```bash
hermes plugins install omernesh/chatlytics-hermes   # clones the repo root → $HERMES_HOME/plugins/chatlytics/
hermes plugins enable chatlytics
```

Equivalent manual install:

```bash
git clone https://github.com/omernesh/chatlytics-hermes ~/.hermes/plugins/chatlytics
hermes plugins enable chatlytics
```

How it works: the repo root carries an `__init__.py` shim that Hermes imports
as `hermes_plugins.chatlytics`. The shim prepends the bundled `src/` to
`sys.path` (at position 0, so the bundled copy wins over any stale pip
install) and re-exports `register` + `__version__` from the
`chatlytics_hermes` package. No pip install is required — the runtime
dependencies (`httpx`, `aiohttp`, `PyYAML`, `jsonschema`) are already
satisfied by a standard hermes-agent install; verify with the doctor (step 3).

### Per-profile gateways

Each per-profile Hermes gateway has its **own** `$HERMES_HOME/plugins/`
directory. Install and enable the plugin once per profile. The
`plugins.enabled` config gates entry-point and directory plugins uniformly.

### ⚠️ CRITICAL: never leave backup copies inside a `plugins/` dir

hermes-agent **shadow-loads ANY importable directory** under
`~/.hermes/plugins/` (and per-profile `plugins/` dirs). A
`chatlytics.pre-upgrade.bak/` copy sitting next to the real plugin gets
loaded **alongside** it and can serve stale code while you think you've
deployed a fix. Put backups in a **sibling** directory instead:

```bash
mkdir -p ~/.hermes/plugin-backups
cp -r ~/.hermes/plugins/chatlytics ~/.hermes/plugin-backups/chatlytics.$(date +%F)
```

## 2. Configure

The only required setting is a bot token:

```bash
export CHATLYTICS_BOT_TOKEN="sk_bot_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
```

Get one in the Web UI (https://app.chatlytics.ai → Bots → Create Bot — the
token is shown exactly **once**) or via `chatlytics bots create`. Put it in
the gateway profile's `.env` / `config.yaml`, not in a systemd unit. The
gateway URL defaults to `https://node.chatlytics.ai`; on-prem gateways
should set `CHATLYTICS_BASE_URL` to the LAN URL (see
[configuration.md](configuration.md)).

Then restart the gateway:

```bash
hermes gateway start
```

On success the log shows `chatlytics platform registered, authenticated as
<bot name>`. A gateway with no token still boots (degraded, since v4.1.5):
the platform registers and data tools return a get-a-token prompt instead of
failing the whole load.

## 3. Verify (doctor)

```bash
python -m chatlytics_hermes.doctor
# explicit overrides:
python -m chatlytics_hermes.doctor --base-url http://192.168.1.133:8050 --token sk_bot_... --plugins-dir ~/.hermes/plugins/chatlytics
```

Six checks, `PASS`/`FAIL` per line, non-zero exit on any failure:
plugin-dir, config (token present), hermes-agent floor, `GET /health`,
`GET /api/v1/bot/me` (prints the bot name), `GET /api/v1/bot/updates`
(longpoll). FAIL lines carry symptom → fix hints. See
[troubleshooting.md](troubleshooting.md).

## 4. Updating the plugin

```bash
cd ~/.hermes/plugins/chatlytics
git pull --ff-only
find . -name __pycache__ -type d -exec rm -rf {} +   # clear stale bytecode
# restart the gateway for the profile(s) using this plugins dir
```

Always clear `__pycache__` after a pull — Python may otherwise serve stale
bytecode for moved/renamed modules. Then restart the gateway and re-run the
doctor.

## 5. pip install (fallback — does NOT survive hermes updates)

A pip install lands in the gateway venv's `site-packages`, which the next
hermes-agent update wipes. If you use it anyway:

### ⚠️ The install rule: ALWAYS `--no-deps`

`hermes-agent` is the host this plugin runs inside, so since v4.8.1 it is **not
a declared dependency** of the package at all (issue #4): no resolver can act
on it. A plain `pip install` of older releases once **silently downgraded a
production hermes-agent 0.15.1 -> 0.14.0** (v4.1.1 pinned
`hermes-agent==0.14.0`) and on 2026-10-09 a non-isolated install broke
`hermes update` on hpg6. The remaining dependencies are lower-bound only, but
`--no-deps` is still the rule: the host already provides httpx, aiohttp, PyYAML
and jsonschema, and the only thing that should ever change in that env is this
package. Target the hermes tool's own interpreter explicitly:

```bash
uv pip install --python <hermes tool python> --no-deps /path/to/chatlytics-hermes
python -m chatlytics_hermes.doctor     # verify nothing broke
```

(`<hermes tool python>` is the venv python the gateway runs on, e.g.
`~/.hermes/tools/<runtime>/bin/python`. Without `--python`, uv installs into
whatever env it happens to find.)

The plugin also self-defends: `register()` compares the installed
hermes-agent against its floor (`>=0.14`) at load time and logs an ERROR
(with the fix) when the environment is too old or has been downgraded; it
never blocks an otherwise-working load.

### Fresh-venv dev install

```bash
git clone https://github.com/omernesh/chatlytics-hermes.git
cd chatlytics-hermes
pip install "hermes-agent @ git+https://github.com/NousResearch/hermes-agent.git@v2026.5.16"
pip install -e ".[dev]"
pytest tests/
```

`scripts/test.sh` runs the suite without any `pip install` at all
(pytest `pythonpath=src`), so it can never touch a host venv.

## 6. Inbound transport choice

After installing, decide how inbound WhatsApp messages reach the gateway:

- **`longpoll`** (recommended for NAT'd / on-prem gateways): the plugin PULLs
  from `GET /api/v1/bot/updates`. No reachable webhook URL needed; the bot's
  `webhook_url` must be `null` on the chatlytics side. This is also the
  transport that carries control envelopes and owner-DM question
  resolutions.
- **`webhook`** (default): the plugin runs an aiohttp server that chatlytics
  POSTs to. Requires a URL chatlytics can reach. In legacy operator-key mode,
  also set `CHATLYTICS_SESSION` (or `extra.session`) for outbound session
  resolution; bot-token auth resolves the session server-side.

Details in [configuration.md](configuration.md) and
[features.md](features.md).
