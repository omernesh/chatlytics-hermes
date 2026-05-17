# HERMES-06 -- SUMMARY

**Phase:** HERMES-06 -- Release + smoke test
**Plan:** 06-01
**Implemented:** 2026-05-17
**Implemented by:** claude-opus-4-7-1m

## What shipped

Final milestone-closing phase. Five commits land the v2.0.0 release
artifacts:

1. **`fix(hermes-06): wrap blocking open()/read() in asyncio.to_thread (04-MED-02)`**
   The local-file branch of `_resolve_media_url` now reads via
   `asyncio.to_thread`. Adds `test_send_image_file_reads_off_event_loop`
   regression test asserting `open()` runs on a worker thread, not the
   main/loop thread.
2. **`docs(hermes-06): README v2.0 rewrite`**
   Full rewrite from the v1.x standalone-shim perspective to the v2.0
   plugin perspective. Drops every `ChatlyticsAdapter(...)` constructor
   snippet, the standalone-shim language, and the phase-169 vendor-into-hpg6
   reference. Nine sections per the Phase 6 spec.
3. **`docs(hermes-06): CHANGELOG 2.0.0 (BREAKING) entry`**
   Prepended `## 2.0.0 (2026-05-17) -- BREAKING` above v1.x history.
   Documents Removed / Added / Changed / Migration (none).
4. **`chore(hermes-06): scripts/smoke.sh dockerized release verification`**
   Single bash script that runs against `python:3.13-slim`. Asserts
   import OK, `chatlytics` entry point discoverable, 45/45 tests pass.
   Uses stdlib `importlib.metadata` (setuptools no longer ships with
   the slim base image; `importlib.metadata` is the canonical Python
   3.10+ API).
5. **Tag `v2.0.0` (LOCAL ONLY)**
   `git tag -a v2.0.0 -m "v2.0.0 -- full Hermes plugin rebuild against
   hermes-agent v0.14"`. NOT pushed -- operator runs
   `git push origin v2.0.0` manually.

## Operator lock honored

**Zero `python -m build`. Zero `twine upload`. Zero PyPI publish.**

All grep matches for those tokens are in documentation strings asserting
the absence of those commands (in `.planning/phases/HERMES-06-.../`).
No executable code anywhere in `scripts/`, `src/`, or `tests/` invokes
them. The PyPI publish becomes a 1-command future operation when the
operator chooses.

## Forward action items addressed

| Source            | Item                                            | Resolution                                                      |
| ----------------- | ----------------------------------------------- | --------------------------------------------------------------- |
| 04-REVIEW MED-01  | `_keep_typing` async-cm shape divergence        | DOCUMENTED in README "Architecture notes" with rationale.       |
| 04-REVIEW MED-02  | blocking `open()/read()` in `_resolve_media_url`| FIXED via `asyncio.to_thread` wrap + regression test.           |
| 05-REVIEW MED-01  | `chatlytics_actions` vs `chatlytics_dispatch`   | DOCUMENTED in README "Tool catalog -- Directory / search" + "Sessions / health". |
| 05-REVIEW MED-02  | concurrency surfaced through tool layer         | FIXED (same `asyncio.to_thread` wrap covers this surface).      |

LOW-class items from prior reviews are deferred to v2.1+ (looksLikeJid
regex, send_typing log volume) -- noted in `<followups>` of 06-01-PLAN.md.

## Test count

- HERMES-01 (register / smoke):  5
- HERMES-02 (outbound):           8
- HERMES-03 (inbound):            9
- HERMES-04 (media + cron):     8+3 = 11
- HERMES-05 (tool surface):    9+2 = 11
- HERMES-06 (concurrency reg):    1
- **TOTAL:                       45**

All 45 pass dockerized against `python:3.13-slim` + `hermes-agent` from
tag `v2026.5.16`.

## Acceptance criteria

| AC | Asserts                                                     | Status |
| -- | ----------------------------------------------------------- | ------ |
| 1  | `bash scripts/smoke.sh` exits 0                             | PASS   |
| 2  | smoke output contains `chatlytics` in entry-point enumeration | PASS   |
| 3  | `pytest tests/` reports 0 failures                          | PASS (45/45) |
| 4  | `grep -c "ChatlyticsAdapter(" README.md` returns 0          | PASS   |
| 5  | CHANGELOG.md top has `## 2.0.0` + `BREAKING` marker         | PASS   |
| 6  | pyproject.toml `version=2.0.0` + entry-point present        | PASS   |
| 7  | `git tag --list v2.0.0` returns the tag                     | PASS (local) |
| 8  | No `python -m build` / `twine upload` in phase artifacts    | PASS   |

## Operator next step

```bash
git push origin v2.0.0
```

(Not run autonomously -- shared-state operation requires explicit
operator confirmation.)
