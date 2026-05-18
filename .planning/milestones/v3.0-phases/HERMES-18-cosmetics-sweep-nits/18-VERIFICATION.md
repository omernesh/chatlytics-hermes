---
phase: 18
verification_date: 2026-05-18
verified_by: claude-opus-4-7-1m
tests_pass: 120
tests_total: 120
tools_count: 21
behavior_change: none
files_changed: 6
status: verified
---

# HERMES-18 — Verification

## Acceptance criteria (per 18-PLAN-1)

| # | Criterion | Result |
|---|---|---|
| 1 | 120/120 tests pass at every commit | PASS — verified after T1 (120), T2 (120), T3 (120), T4 (120), T5 (120), T6 (no source) |
| 2 | 6 deferred items addressed, 4 skipped with quoted justifications | PASS — see 18-SUMMARY.md |
| 3 | `git diff` shows only docstring/signature/constant/comment changes in src/ | PASS — see commit-by-commit breakdown below |
| 4 | `git diff` empty in tests/ | PASS — `git diff c781629^..HEAD -- tests/` is empty |
| 5 | `git diff` empty in scripts/, pyproject.toml, plugin.yaml, CHANGELOG.md, README.md | PASS — none touched |
| 6 | `len(TOOLS) == 21` | PASS — `PYTHONPATH=src python -c "from chatlytics_hermes.tools import TOOLS; print(len(TOOLS))"` → `21` |
| 7 | Audit doc: no trailing whitespace; item 6 wording clarified | PASS — `grep -nP ' +$' .planning/HERMES-API-AUDIT.md` → no matches |
| 8 | 18-SUMMARY exists with skipped-item justifications | PASS |

## Final test run

```
$ CHATLYTICS_API_KEY= CHATLYTICS_API_URL= CHATLYTICS_BASE_URL= CHATLYTICS_SESSION= \
    python -m pytest tests/ -q --no-header
........................................................................ [ 60%]
................................................                         [100%]
120 passed in 25.82s
```

## Tool surface

```
$ PYTHONPATH=src python -c "from chatlytics_hermes.tools import TOOLS; print(len(TOOLS))"
21
```

## Commits in phase scope

| Commit | Phase | Files |
|---|---|---|
| `c781629` | 18 (infra) | `.planning/phases/HERMES-18-.../18-CONTEXT.md` |
| `b775fca` | 18 (infra) | `.planning/phases/HERMES-18-.../18-PLAN-1-...md` |
| `fdc2328` | 18 T1 | `src/chatlytics_hermes/adapter.py` (+11 / -0) |
| `ebb3322` | 18 T2 | `src/chatlytics_hermes/adapter.py` (+9 / -1) |
| `49f2224` | 18 T3 | `src/chatlytics_hermes/adapter.py` (+15 / -2) |
| `9a840bc` | 18 T4 | `src/chatlytics_hermes/tools.py` (+11 / -0) |
| `2db2060` | 18 T5 | `.planning/HERMES-API-AUDIT.md` (+2 / -2) |
| `d0e8344` | 18 T6 (infra) | `.planning/phases/HERMES-18-.../18-SUMMARY.md` |

## Diff scope verification

```
$ git diff --stat c781629^..HEAD
 .planning/HERMES-API-AUDIT.md                            |   4 +-
 .planning/phases/HERMES-18-cosmetics-sweep-nits/18-CONTEXT.md     | 172 +++++++
 .planning/phases/HERMES-18-cosmetics-sweep-nits/18-PLAN-1-...md   | 210 +++++++
 .planning/phases/HERMES-18-cosmetics-sweep-nits/18-SUMMARY.md     | 202 +++++++
 src/chatlytics_hermes/adapter.py                         |  38 +++-
 src/chatlytics_hermes/tools.py                           |  11 ++
 6 files changed, 632 insertions(+), 5 deletions(-)
```

Of the 6 files: 3 are planning infra (CONTEXT/PLAN/SUMMARY), 1 is the
audit doc (planning artifact, not source/test), 2 are source files
(`adapter.py`, `tools.py`). Tests untouched. Release artifacts
untouched.

## Behavior-change audit

Reviewed every src/ diff hunk:

- `adapter.py`:
  - `_RESERVED_BODY_KEYS` constant: new frozenset, same membership
    as the previous local set. `send()` reads `key in _RESERVED_BODY_KEYS`
    — identical truth table to `key in _reserved`.
  - `_send_typing_once` signature: added unused `metadata` kwarg
    with default `None`. Function body unchanged; the kwarg is not
    forwarded to the request body. Same request, same return.
  - `send_typing` body: now calls `self._send_typing_once(chat_id,
    duration, metadata=metadata)`. The previous call was
    `self._send_typing_once(chat_id, duration)`. Because
    `_send_typing_once` ignores `metadata` at the request layer,
    the on-the-wire request is byte-identical.
  - `_send_typing_once` docstring: text-only additions.
- `tools.py`:
  - Two inline comments above `"minLength": 1`. Schema dicts produce
    the same JSON. `jsonschema` validators see identical schemas.

**Verdict:** no observable behavior change on any user/caller surface.

## Invariants confirmed

- 21 tools registered (HERMES-13/14/15 contracts preserved)
- 120/120 tests pass (HERMES-16/17 baselines preserved)
- httpx outbound / aiohttp inbound — unchanged
- Hermes pin `>=0.14,<0.15` — pyproject.toml not touched
- v2.1 HI-01 upload allowlist — tools.py changes are comments only
- v2.1 BL-01 client lifecycle — adapter.py changes do not touch
  `connect()` / `disconnect()` / `_client` lifecycle

## Recommendation

**PROCEED to code review.** Phase 18 is cosmetic-only and ready
for the gsd-code-review pass. Reviewer should specifically validate:
(a) the `_RESERVED_BODY_KEYS` constant has identical membership to
the previous local set; (b) the `metadata` kwarg on
`_send_typing_once` is not forwarded to the wire; (c) the two
schema comments do not alter the JSON output.
