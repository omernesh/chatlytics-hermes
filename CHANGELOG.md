# Changelog

## 1.1.0 (2026-04-27)

- Verified compatible with `hermes-agent==0.11.0` (tag `v2026.4.23`).
- Removed bogus `[project.entry-points."hermes.adapters"]` block from
  `pyproject.toml` — Hermes's plugin entry-point group is `hermes_agent.plugins`
  and is meant for tools/hooks/commands, not platform adapters; platform
  adapters are hardcoded in `gateway/run.py:_create_adapter()` per
  `gateway/platforms/ADDING_A_PLATFORM.md`.
- Stripped the `from hermes_agent import Agent / Agent(platform=adapter)`
  README snippet — that API does not exist in upstream Hermes. README now
  documents the standalone-shim use case and points at phase 169 for the
  in-monorepo vendor pattern.
- Stashed unmerged WIP test files (`tests/test_actions.py`,
  `tests/test_in_reply_to.py`, `tests/test_inbound.py`) into
  `.wip-stash/` — they imported symbols that never landed
  (`LAST_INBOUND_MAX_ENTRIES`, `MESSAGE_DEDUP_MAX_ENTRIES`,
  `_default_session`, `webhook_secret` ctor param, `send_image`,
  `send_voice`) and broke `pytest` collection. Baseline test count back to
  the 12 that actually run against the shipped adapter.

## 1.0.0 (2026-04-20)

- Initial release
- Hermes Agent platform adapter for Chatlytics WhatsApp gateway
- Async HTTP client (httpx) for outbound messaging
- Flask webhook server for inbound messages
- Send messages, typing indicators, and chat info queries
- Bearer token authentication
