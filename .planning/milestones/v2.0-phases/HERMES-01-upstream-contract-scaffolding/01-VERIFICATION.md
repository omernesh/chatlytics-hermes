---
phase: 01-upstream-contract-scaffolding
status: passed
verified_by: claude-opus-4-7-1m
verified: 2026-05-17
acceptance_criteria_total: 5
acceptance_criteria_passed: 5
tests_total: 5
tests_passed: 5
---

# HERMES-01 -- Verification

## Status: PASSED

All 5 ROADMAP Phase 1 acceptance criteria pass autonomously, both on
the host (Windows + Python 3.14) and in a clean `python:3.13-slim`
docker container with `hermes-agent` installed from the GitHub tag
`v2026.5.16` (the v0.14.0 "Foundation Release" tag).

## Acceptance criteria

### AC-1: importable register

Command: `python -c "from chatlytics_hermes import register; print(register.__name__)"`

Host (PYTHONPATH=src): exit 0, prints `register`.
Container (after `pip install -e .[dev]`): exit 0, prints `register`.

Result: **PASS**.

### AC-2: register_adds_chatlytics_platform test passes

Command: `python -m pytest tests/test_register.py::test_register_adds_chatlytics_platform -q`

Host: part of `5 passed in 0.16s`.
Container: part of `5 passed in 0.30s`.

Result: **PASS**.

### AC-3: plugin.yaml is valid YAML

Command: `python -c "import yaml; yaml.safe_load(open('plugin.yaml'))"`

Exit 0.

Result: **PASS**.

### AC-4: pip install -e . succeeds with hermes-agent installed

PyPI install of `hermes-agent>=0.14,<0.15` fails (v0.14 not yet
published; v0.13.0 is the latest on PyPI as of 2026-05-17). Per
ROADMAP CONTEXT, this is the documented install vector for the
interim period:

```sh
MSYS_NO_PATHCONV=1 docker run --rm -v "/d/docker/chatlytics-hermes-split:/work" -w /work python:3.13-slim sh -c \
  "apt-get update -qq && apt-get install -y -qq git && \
   pip install 'hermes-agent @ git+https://github.com/NousResearch/hermes-agent.git@v2026.5.16' && \
   pip install -e .[dev] && \
   python -c 'from chatlytics_hermes import register; print(register.__name__)'"
```

Container output (truncated to outcome lines):

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

Result: **PASS** via GitHub-tag install vector. HERMES-06 README will
document this install path until `hermes-agent` 0.14 is published to
PyPI.

### AC-5: entry-point block present in pyproject.toml

Command: `grep -F 'chatlytics = "chatlytics_hermes:register"' pyproject.toml`

Match. Structural check via `tomllib.loads(...)["project"]["entry-points"]["hermes_agent.plugins"]["chatlytics"] == "chatlytics_hermes:register"` also passes (`test_pyproject_declares_hermes_entry_point`).

Result: **PASS**.

## Test surface

`pytest tests/test_register.py -q`:

```
.....                                                                    [100%]
5 passed in 0.16s
```

Tests:
1. `test_register_is_callable` -- AC-1 in pytest form
2. `test_register_adds_chatlytics_platform` -- AC-2, ROADMAP-named
3. `test_register_does_not_declare_deferred_hooks` -- scope guard
4. `test_plugin_yaml_is_valid` -- AC-3 in pytest form
5. `test_pyproject_declares_hermes_entry_point` -- AC-5 in pytest form

## Scope discipline

`test_register_does_not_declare_deferred_hooks` confirms HERMES-01 did
NOT leak hooks owned by later phases:
- `env_enablement_fn`, `apply_yaml_config_fn` (HERMES-03)
- `cron_deliver_env_var`, `standalone_sender_fn` (HERMES-04)
- `check_fn`, `validate_config`, `is_connected`, `setup_fn` (later phases)
- `allowed_users_env`, `allow_all_env` (auth, deferred)

The `register()` call in `src/chatlytics_hermes/adapter.py` only
declares the minimum subset documented in PLAN.md Task 1: `name`,
`label`, `adapter_factory`, `required_env`, `install_hint`, `emoji`,
`platform_hint`.

## State of working tree

Clean. All HERMES-01 artifacts (code + tests + plan + summary +
verification) are committed across two atomic commits:
- `e1dd188` -- scaffold + delete v1.x + pyproject
- `ad69805` -- tests + acceptance run

`.claude/` (local agent overrides) and `__pycache__/` /
`src/chatlytics_hermes.egg-info/` from the editable-install smoke are
untracked but already covered by repo `.gitignore` conventions (they
do NOT appear in `git status` output).

## Blockers

None.

## Next phase

HERMES-02 -- outbound text + control parity. Will fill in `connect()`,
`disconnect()`, `send()`, `send_typing()`, `get_chat_info()` against
the Chatlytics REST API via `httpx.AsyncClient`.
