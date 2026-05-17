---
phase: 05-full-chatlytics-tool-surface
date: 2026-05-17
verifier: claude-opus-4-7-1m
status: passed
---

# HERMES-05 Verification

## Test run (dockerized, clean python:3.13-slim)

Command:

```bash
MSYS_NO_PATHCONV=1 docker run --rm \
  -v "/d/docker/chatlytics-hermes-split:/work" -w /work python:3.13-slim sh -c "
  apt-get update -qq && apt-get install -y -qq --no-install-recommends git
  pip install --quiet 'hermes-agent @ git+https://github.com/NousResearch/hermes-agent.git@v2026.5.16'
  pip install --quiet -e '.[dev]'
  pytest tests/ -v
"
```

Result: **44 passed in 3.85s** — see the full pytest output in the SUMMARY commit message of the umbrella PR.

Breakdown:
- `tests/test_cron.py`        — 3 passed (HERMES-04)
- `tests/test_inbound.py`     — 9 passed (HERMES-03)
- `tests/test_media.py`       — 8 passed (HERMES-04)
- `tests/test_outbound.py`    — 8 passed (HERMES-02)
- `tests/test_register.py`    — 5 passed (HERMES-01, HERMES-04)
- `tests/test_tool_schemas.py`— 5 passed (HERMES-05 — NEW)
- `tests/test_tools.py`       — 6 passed (HERMES-05 — NEW)

## Acceptance criteria

| AC | Test | Status |
|---|---|---|
| AC-1 | `test_tool_schemas.py::test_every_tool_has_valid_json_schema` | PASS |
| AC-2 | `test_tool_schemas.py::test_every_tool_has_required_chat_id_field_when_applicable` | PASS |
| AC-3 | `test_tools.py::test_chatlytics_send_calls_send_endpoint` | PASS |
| AC-4 | `test_tools.py::test_chatlytics_react_calls_react_action` | PASS |
| AC-5 | `test_tools.py::test_chatlytics_search_returns_results_list` | PASS |
| AC-6 | `test_tools.py::test_tool_returns_success_false_on_400` | PASS |
| AC-7 | `test_tools.py::test_tool_count_matches_claude_code_plugin_baseline` (+ duplicate in test_tool_schemas.py) | PASS |
| AC-8 | `test_tool_schemas.py::test_all_tools_namespace_chatlytics_` | PASS |

## Regression guard

- `test_register.py::test_register_declares_hermes_04_hooks` still passes — `register_tool` was added behind a `hasattr(ctx, "register_tool")` feature-detect block, so the HERMES-01 `MockCtx` (no `register_tool`) sees no change.
- All HERMES-01..04 tests still pass (33/33 unchanged).
- The new `jsonschema` runtime dep is satisfied by the dockerized install path (pulled as a transitive dep + installed via the `.[dev]` extra).

## Tools registered (count)

`assert len(TOOLS) == 21` at module import time; both `tests/test_tools.py::test_tool_count_matches_claude_code_plugin_baseline` and `tests/test_tool_schemas.py::test_tool_count_matches_claude_code_plugin_baseline` re-assert the count.

| Group | Count | Tools |
|---|---|---|
| Messaging | 10 | send, reply, react, edit, unsend, pin, unpin, read, delete, poll |
| Media | 5 | send_image, send_voice, send_video, send_file, send_animation |
| Directory / search | 3 | directory, search, actions |
| Sessions / health | 3 | health, login, dispatch |
| **Total** | **21** | |

## Verdict

HERMES-05 acceptance criteria 1-8 all PASS. Phase 5 of the v2.0 milestone is complete.
