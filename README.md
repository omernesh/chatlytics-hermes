# Chatlytics Hermes Adapter

Hermes Agent platform adapter for Chatlytics WhatsApp.

## Install

```bash
pip install .
```

Or with dev dependencies:

```bash
pip install -e ".[dev]"
```

## Configure

```python
from chatlytics_adapter import ChatlyticsAdapter

adapter = ChatlyticsAdapter(
    base_url="http://localhost:8050",
    api_key="your-api-key",
    account_id="3cf11776_logan",   # optional — locks to one WAHA session
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

```python
from hermes_agent import Agent
from chatlytics_adapter import ChatlyticsAdapter

adapter = ChatlyticsAdapter(
    base_url="http://localhost:8050",
    api_key="your-api-key",
)

agent = Agent(platform=adapter)
agent.run()
```
