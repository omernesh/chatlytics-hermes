# chatlytics-hermes

Hermes Agent platform adapter for Chatlytics WhatsApp.

## Install

```bash
pip install chatlytics-hermes
```

## Requirements

- Python >= 3.10

## Configure

```python
from chatlytics_adapter import ChatlyticsAdapter

adapter = ChatlyticsAdapter(
    base_url="http://localhost:8050",
    api_key="your-api-key",
    account_id="3cf11776_logan",   # optional -- locks to one WAHA session
    webhook_port=9090,             # inbound webhook listener port
)
```

## Usage

```python
import asyncio

async def main():
    await adapter.connect()

    # Send a message
    await adapter.send("972544329000@c.us", "Hello from Hermes")

    # Typing indicator
    await adapter.send_typing("972544329000@c.us", duration=2.0)

    # Chat info
    info = await adapter.get_chat_info("972544329000@c.us")

    await adapter.disconnect()

asyncio.run(main())
```

### Inbound messages

Start the webhook server and register a handler:

```python
adapter.start_webhook_server()

@adapter.on_message
def handle(payload):
    print("Incoming:", payload)
```

Point Chatlytics webhook config at `http://<host>:9090/webhook`.

## Integrate with Hermes Agent

This package is a standalone duck-typed shim — it talks to the Chatlytics
gateway over HTTP and does not subclass Hermes's `BasePlatformAdapter`. To
plug Chatlytics into an actual Hermes monorepo install you need a vendored
adapter at `gateway/platforms/chatlytics.py` that subclasses
`BasePlatformAdapter` and is registered in `gateway/run.py:_create_adapter()`.
See phase 169 ("vendor into hpg6 Hermes") for that work.

For ad-hoc scripts that just want an httpx wrapper around the Chatlytics
gateway API without dragging in the full Hermes runtime, the standalone
`ChatlyticsAdapter` in this package is sufficient — see the **Configure**
and **Usage** sections above.

Verified compatible with `hermes-agent==0.11.0` (tag `v2026.4.23`).

## Development

```bash
pip install chatlytics-hermes[test]
pytest
```

## License

MIT
