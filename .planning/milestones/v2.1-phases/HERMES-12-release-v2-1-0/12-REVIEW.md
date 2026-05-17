---
phase: 12
verdict: APPROVE
review_depth: standard
review_focus: docs accuracy, version consistency, operator-lock invariants
implemented_by: claude-opus-4-7
reviewed_by: gsd-code-reviewer
review_date: 2026-05-17
tests_total: 88
tests_passing: 88
findings:
  blocker: 0
  high: 0
  med: 0
  low: 0
  info: 2
last_updated: "2026-05-17T00:00:00.000Z"
status: clean
---

# HERMES-12 — Code Review

## Verdict: APPROVE (clean — 0 BLOCKER / 0 HIGH / 0 MED / 0 LOW; 2 INFO nits)

Phase 12 is a docs / version / tag release wrap-up. No source code under
`src/chatlytics_hermes/` changed. The review focused on:

1. **Docs accuracy** — every claim in CHANGELOG.md and README.md matches
   the actual source state.
2. **Version consistency** — pyproject.toml, plugin.yaml, and test
   assertions all agree on `2.1.0`.
3. **Operator-lock invariants** — no `git push`, no PyPI publish, no
   GitHub release, no remote-facing action.
4. **v2.0 invariants preserved** — Hermes pin, tool surface, transport
   layer, response shape, license, package name unchanged.

## Scope of changes reviewed

| File | Change | LOC |
|------|--------|-----|
| `CHANGELOG.md` | Prepend `## [2.1.0]` entry (Security / Added / Changed / Fixed / Docs / Internal) | +136 |
| `README.md` | Add `## What's new in v2.1` section; clarify actions/dispatch tools; add `## Known issues` section; update Status badge to v2.1.0 BETA; add v2.1 _typing_scope note to architecture | +66 / -5 |
| `pyproject.toml` | `version = "2.0.0"` → `"2.1.0"` | +1 / -1 |
| `plugin.yaml` | `version: 2.0.0` → `2.1.0`; strip `HERMES-03` x2 and `HERMES-04` x1 from optional_env descriptions | +4 / -4 |
| `tests/test_register.py` | Update 2 version assertions from "2.0.0" to "2.1.0" | +2 / -2 |

Total: 5 files, ~209 LOC of additive docs/version edits.

## Accuracy verification (the documented claims)

| Claim | File | Verified |
|-------|------|----------|
| v2.1 closes BL-01 / HI-01 / HI-03 | CHANGELOG, README | YES — Phase 8 commit history + HERMES-08 review |
| `CHATLYTICS_UPLOAD_ALLOWED_ROOTS` env var exists | README, CHANGELOG | YES — `grep -r CHATLYTICS_UPLOAD_ALLOWED_ROOTS src/` → match in `adapter.py` |
| `_typing_scope` async-cm helper exists | README architecture | YES — `grep _typing_scope src/` → match in `adapter.py` |
| `_keep_typing` is plain coroutine matching base signature | README, CHANGELOG | YES — Phase 8 fix; signature verified via test_live_loader BL-01 regression |
| 21-tool surface preserved | README, CHANGELOG | YES — `len(TOOLS)=21` in `tools.py` (introspected at review time) |
| `chatlytics_actions` = GET catalog | README | YES — tool description: "List the full Chatlytics action catalog (~100 actions)" |
| `chatlytics_dispatch` = POST generic action | README | YES — tool description: "Dispatch any Chatlytics action by name (POST /api/v1/actions)" |
| `scripts/smoke.sh --fast` flag | CHANGELOG, README | YES — `scripts/smoke.sh:39-78` |
| `pip install --retries 3` | CHANGELOG | YES — `scripts/smoke.sh:93,95` |
| `webhook_path` validation rejects `/health` collision | CHANGELOG | YES — Phase 10 review confirmed |
| 88 tests total | CHANGELOG, VERIFICATION | YES — `pytest tests/ -q` → `88 passed` |
| `chatlytics_login` returns success=False when webhook_registered=false | CHANGELOG | YES — tool description in `tools.py` matches |
| `tests/_fixtures.FakePlatformConfig` consolidation | CHANGELOG | YES — confirmed in HERMES-11 review |
| `plugin.yaml` no longer contains `HERMES-NN` | CHANGELOG | YES — `grep HERMES- plugin.yaml` → no matches |

All claims verified.

## Version consistency

```
pyproject.toml:7:version = "2.1.0"
plugin.yaml:4:version: 2.1.0
tests/test_register.py:116:assert manifest["version"] == "2.1.0"
tests/test_register.py:148:assert project["version"] == "2.1.0"
```

Consistent across all 4 sites.

## Operator-lock invariants (verified)

| Constraint | Status |
|------------|--------|
| No `git push` ever ran | CONFIRMED — no `git push` in shell history for this phase |
| No `python -m build` ran | CONFIRMED |
| No `twine upload` ran | CONFIRMED |
| No `gh release create` ran | CONFIRMED |
| Tag (when created) will be local-only | PENDING — Wave 5 below |

## v2.0 invariants preserved

| Invariant | Status |
|-----------|--------|
| `hermes-agent>=0.14,<0.15` pin | UNCHANGED in pyproject.toml |
| 21 tools | UNCHANGED — verified via TOOLS introspection |
| httpx outbound, aiohttp inbound | UNCHANGED — no transport edits |
| `{"success": bool, ...}` response shape | UNCHANGED — no `tools.py` edits |
| MIT license | UNCHANGED — LICENSE file untouched |
| `chatlytics-hermes` package name | UNCHANGED in pyproject.toml `[project] name` |
| 88/88 tests passing | UNCHANGED — same baseline as Phase 11 |

## INFO nits (deliberately not gating)

### INFO-01 — CHANGELOG section heading style mix

The new 2.1.0 entry uses `## [2.1.0] -- 2026-05-17` (Keep-a-Changelog
style with bracketed version + double-dash separator). The pre-existing
2.0.0 entry uses `## 2.0.0 (2026-05-17) -- BREAKING` (parenthesized date,
no brackets). Functionally identical for parsers; cosmetically
inconsistent. Not gating — future releases can converge on one style;
keeping the existing 2.0.0 entry verbatim was intentional (we did not
rewrite history).

### INFO-02 — README "Status" line says "v2.1.0 BETA"

Bumped from "v2.0 BETA" to "v2.1.0 BETA" for consistency with the
version bump. Consider whether v2.1.0 should still carry the BETA badge
— the plugin shipped through 6 phases of v2.1 review with 88/88 tests
passing and the BLOCKER/HIGHs fixed. Removing BETA is an operator
judgment call (BETA was originally claimed because the plugin had not
been exercised against a live Chatlytics gateway, and that's still
true). Defer to operator.

## Files NOT changed (intentional)

- `src/chatlytics_hermes/*.py` — Phase 12 is docs/version only.
- `LICENSE` — MIT, unchanged.
- Other tests beyond `test_register.py` — no code semantics changed,
  so no other test updates needed.
- `scripts/smoke.sh` — already at the Phase 11 state including `--fast`
  and `--retries 3`; no Phase 12 changes needed.

## Tag creation gate

Tag has NOT been created yet. The review passes; proceed to:

1. `git tag -a v2.1.0 -m "v2.1.0 -- tech debt resolution + critical safety fixes (BL-01/HI-01/HI-03)"`
2. Verify `git tag --list v2.1.0`
3. Verify NO `git push` ran (reflog clean)

After tag creation, send the milestone-ship telegram.

## Recommendation

APPROVE — proceed to local tag creation. No fix-pass needed; INFO nits
are deferrable.
