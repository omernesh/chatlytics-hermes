---
phase: 01-upstream-contract-scaffolding
plan: 01
status: complete
implemented_by: claude-opus-4-7-1m
completed: 2026-05-17
commits:
  - e1dd188 — feat(hermes-01): scaffold v2.0 plugin contract + delete v1.x adapter tree
  - ad69805 — test(hermes-01): add register entry-point tests + run all acceptance criteria
---

# HERMES-01 Plan 01 -- Summary

## Outcome

All three tasks landed across two atomic commits. v1.x standalone-shim
tree is fully gone. New `chatlytics_hermes` package exposes the
`register()` entry point per the v0.14.0 plugin contract documented at
`/tmp/hermes-ref-v0.14.0/gateway/platforms/ADDING_A_PLATFORM.md`
(Plugin Path).

## Per-task outcome

### Task 1: Remove v1.x tree + scaffold new package skeleton + plugin.yaml

Commit `e1dd188`. Files:
- DELETED: `src/chatlytics_adapter/{__init__.py,actions.py,adapter.py}`,
  `tests/test_adapter.py`, `tests/test_action_parity.py`
- CREATED: `src/chatlytics_hermes/__init__.py` (re-exports `register`),
  `src/chatlytics_hermes/adapter.py` (ChatlyticsAdapter skeleton +
  `register()`), `plugin.yaml` (manifest)

Adapter skeleton deviation: the import block is wrapped in
`try/except ImportError` (with module-level `_HERMES_AVAILABLE` flag
and a guard in `__init__`). The plan called this out explicitly — the
deviation IS the plan, but worth re-flagging because it touches the
abstract-base subclass identity. Without the shim, the bare-import
acceptance criterion (AC-1) would force `hermes-agent` to be a hard
runtime dep at import time, blocking the same-host fast feedback loop.
The runtime cost is one `if not _HERMES_AVAILABLE: raise RuntimeError`
in `__init__` — harmless when `hermes-agent` is installed.

### Task 2: Update pyproject.toml -- v2.0.0 deps + entry point

Commit `e1dd188` (squashed with Task 1 — both are pure scaffolding,
inseparable). Key changes:
- version: `1.1.0` -> `2.0.0`
- dependencies: dropped `httpx>=0.24` + `flask>=3.0`; pinned
  `hermes-agent>=0.14,<0.15`, `httpx>=0.27,<1`, `aiohttp>=3.9,<4`,
  `PyYAML>=6.0`
- entry point: `[project.entry-points."hermes_agent.plugins"]` with
  `chatlytics = "chatlytics_hermes:register"`
- added `[tool.setuptools.packages.find]` (`where = ["src"]`) and
  `[tool.pytest.ini_options]` (`asyncio_mode = "auto"`,
  `pythonpath = ["src"]`)
- renamed `[project.optional-dependencies].test` -> `.dev` (matches
  ROADMAP HERMES-06 smoke script convention)

### Task 3: Author test_register.py + run acceptance criteria

Commit `ad69805`. Five tests, all green:
1. `test_register_is_callable`
2. `test_register_adds_chatlytics_platform` (the ROADMAP-named test)
3. `test_register_does_not_declare_deferred_hooks` (scope discipline)
4. `test_plugin_yaml_is_valid`
5. `test_pyproject_declares_hermes_entry_point`

## Acceptance criteria results

| AC | Description | Result | Evidence |
|----|-------------|--------|----------|
| 1 | `python -c "from chatlytics_hermes import register; print(register.__name__)"` prints `register` | PASS | Confirmed in host (PYTHONPATH=src) and in docker container after `pip install -e .[dev]` |
| 2 | `pytest tests/test_register.py::test_register_adds_chatlytics_platform -q` passes | PASS | Part of `5 passed in 0.16s` host run + `5 passed in 0.30s` container run |
| 3 | `python -c "import yaml; yaml.safe_load(open('plugin.yaml'))"` succeeds | PASS | Exit 0 in host run |
| 4 | `pip install -e .` succeeds in clean venv with hermes-agent installed | PASS via GitHub tag fallback | See "Install vector" below |
| 5 | `pyproject.toml` declares the entry point with `chatlytics = "chatlytics_hermes:register"` | PASS | `grep -F` matched; `tomllib` parse confirms structure |

## Install vector used in dockerized smoke (criterion 4)

`hermes-agent>=0.14,<0.15` is NOT yet on PyPI (v0.13.0 is the latest
published as of 2026-05-17, per ROADMAP context). The smoke
auto-falls-back to the GitHub tag:

```
pip install 'hermes-agent @ git+https://github.com/NousResearch/hermes-agent.git@v2026.5.16'
```

`v2026.5.16` is the published tag for v0.14.0 ("The Foundation
Release"). The reproducer is:

```sh
MSYS_NO_PATHCONV=1 docker run --rm -v "/d/docker/chatlytics-hermes-split:/work" -w /work python:3.13-slim sh -c \
  "apt-get update -qq >/dev/null && apt-get install -y -qq git >/dev/null && \
   pip install --quiet 'hermes-agent @ git+https://github.com/NousResearch/hermes-agent.git@v2026.5.16' && \
   pip install --quiet -e .[dev] && \
   python -c 'from chatlytics_hermes import register; print(register.__name__)' && \
   python -m pytest tests/test_register.py -q"
```

Container output (truncated):
```
git version 2.47.3
--- Install hermes-agent from GitHub tag v2026.5.16
--- Install editable plugin
--- Bare-import smoke (AC-1 + AC-4)
register
--- Pytest suite
.....                                                                    [100%]
5 passed in 0.30s
```

HERMES-06 README will document this install vector as the canonical
install path until `hermes-agent` 0.14 lands on PyPI.

## Deviations from plan

None substantive. The plan called out:
- The `try/except ImportError` shim in `adapter.py` (implemented as
  specified; relabeled section banner to `# --- Abstract methods ... ---`
  using ASCII dashes after a Windows cp1255 decode failure on box-drawing
  characters revealed a portability bug. ASCII-only source is now
  enforced for this file.)
- The GitHub fallback for `hermes-agent` install (implemented; PyPI path
  attempts first per the plan's `||` fallback shape).

The docker invocation needed `MSYS_NO_PATHCONV=1` on this Windows host
because Git-Bash path translation was mangling `-v` and `-w` args.
That's an env quirk of the dev host, not a deviation from the plan.

## Files touched (final list)

CREATED:
- `src/chatlytics_hermes/__init__.py`
- `src/chatlytics_hermes/adapter.py`
- `plugin.yaml`
- `tests/test_register.py`
- `.planning/phases/HERMES-01-upstream-contract-scaffolding/01-01-PLAN.md`
- `.planning/phases/HERMES-01-upstream-contract-scaffolding/01-01-SUMMARY.md` (this file)

MODIFIED:
- `pyproject.toml`

DELETED:
- `src/chatlytics_adapter/__init__.py`
- `src/chatlytics_adapter/actions.py`
- `src/chatlytics_adapter/adapter.py`
- `tests/test_adapter.py`
- `tests/test_action_parity.py`

## What's deferred (in scope for HERMES-02 onwards)

- `connect()`, `disconnect()`, `send()` -- HERMES-02
- `send_typing`, `get_chat_info` -- HERMES-02
- aiohttp inbound webhook server inside `connect()` -- HERMES-03
- HMAC signature verification -- HERMES-03
- Media handlers (`send_image`, `send_voice`, `send_video`,
  `send_document`, `send_animation`, `send_image_file`) -- HERMES-04
- `_keep_typing` 30s heartbeat -- HERMES-04
- `cron_deliver_env_var` + `standalone_sender_fn` -- HERMES-04
- `ctx.register_tool(...)` per Chatlytics action -- HERMES-05
- README / CHANGELOG rewrite + smoke script -- HERMES-06
