---
phase: 19
phase_name: Release chatlytics-hermes 3.0.0 (PyPI) — PART A
part: A
status: dress_rehearsal_complete
mode: infra-skip
date: 2026-05-18
implemented_by: claude-opus-4-7-1m
files_changed: 9
source_files_changed: 4
doc_files_changed: 2
infra_files_changed: 3
loc_changed: "+619 / -20"
tests_before: 120
tests_after: 120
tests_installed_wheel: 120
tools_before: 21
tools_after: 21
version_before: "2.1.0"
version_after: "3.0.0"
twine_check: PASSED
pypi_name_available: true
review_blockers: 0
review_high: 0
review_med: 0
review_low: 0
review_info: 0
fix_pass_invoked: false
next_action: operator go/no-go for real PyPI publish (PART B)
---

# HERMES-19 PART A — Summary (v3.0.0 Release Dress Rehearsal)

## Outcome

**Release-ready.** The v3.0.0 release artifact has been built locally,
validated against all three gates (`twine check`, PyPI name
availability, local wheel-install dress rehearsal with full 120/120
pytest run against the installed wheel), and is staged for the
operator's go/no-go decision on PART B (real PyPI publish + git tag +
push).

**No source code changed** beyond the mandatory `__version__` addition
in `__init__.py` and the two test-assertion version literals in
`test_register.py` (in lockstep with the bump). All Phase 13-18
behavior preserved exactly.

## Commits (PART A, 8 total)

| Commit | Task |
|---|---|
| `fdbc202` | docs(19): infra-skip CONTEXT |
| `0fc792f` | docs(19): PLAN (10 sequential tasks) |
| `d6c1581` | T1 — `.gitignore` hardening (dist/, build/, *.egg-info/, etc.) |
| `54e4ba0` | T2 — version bump to 3.0.0 (pyproject + plugin.yaml + __init__.py + test_register lockstep) |
| `80292db` | T3 — CHANGELOG `[3.0.0] - 2026-05-18 (BREAKING)` entry |
| `a6e3d56` | T4 — README: PyPI install, Migration from 2.x, What's new in v3.0 |
| `e960258` | T9 — release-readiness summary |
| (this) | T10 — phase summary + REVIEW |

T5 (build) + T6 (twine check) + T7 (PyPI name check) + T8 (dress
rehearsal) produced no commits by design — build artifacts are
gitignored.

## Validation gate results

| Gate | Verdict | Detail |
|---|---|---|
| 1. `twine check dist/*` | PASSED | sdist 80,155 B + wheel 44,647 B both clean |
| 2. PyPI name availability | AVAILABLE | `pip index versions chatlytics-hermes` → no matching distribution |
| 3. Wheel install + pytest | PASSED | 120/120 against installed wheel in scratch venv |

Scratch venv path:
`C:/Users/omern/AppData/Local/Temp/chatlytics-hermes-dress-19`

Scratch tests dir (with copied REPO_ROOT fixtures):
`C:/Users/omern/AppData/Local/Temp/chatlytics-hermes-dress-19-tests`

## Files changed

| File | Lines | Nature |
|---|---|---|
| `.gitignore` | +15 / -0 | Build artifacts ignored |
| `pyproject.toml` | +1 / -1 | version 2.1.0 → 3.0.0 |
| `plugin.yaml` | +1 / -1 | version 2.1.0 → "3.0.0" (string-quoted) |
| `src/chatlytics_hermes/__init__.py` | +3 / -1 | `__version__ = "3.0.0"` added + exported |
| `tests/test_register.py` | +2 / -2 | Version assertion lockstep |
| `CHANGELOG.md` | +63 / -1 | `[3.0.0]` BREAKING entry + Additive/Internal/Migration |
| `README.md` | +68 / -14 | Status, PyPI install, Migration from 2.x, v3.0 highlights, obsolete bullet removed |
| `.planning/phases/HERMES-19-*/19-CONTEXT.md` | +205 | New (infra) |
| `.planning/phases/HERMES-19-*/19-PLAN-1-*.md` | +139 | New (infra) |
| `.planning/phases/HERMES-19-*/19-RELEASE-READY.md` | +122 | New (infra) |
| `.planning/phases/HERMES-19-*/19-REVIEW.md` | this | New (infra) |
| `.planning/phases/HERMES-19-*/19-SUMMARY.md` | this | New (infra) |

## Test results

| Run | Result | Notes |
|---|---|---|
| Source tree | 120 passed | Baseline preserved exactly |
| Installed wheel (scratch venv) | 120 passed | Identical to source-tree result |

Test parity confirms the built wheel ships the exact same `chatlytics_hermes`
module surface the source-tree tests exercise.

## Code review

Inline orchestrator review. Zero findings at every severity. See
`19-REVIEW.md` for the four-file source-diff audit and the CHANGELOG
+ README content audits.

## Invariants preserved (v3.0-so-far)

- Hermes pin `>=0.14,<0.15` — unchanged
- 21 tools registered — unchanged
- httpx outbound + aiohttp embedded inbound — unchanged
- `{"success": bool, ...}` tool response shape — unchanged
- `_error` sentinel contract (Phase 13) — unchanged
- Strict JID regex (Phase 14) — unchanged
- Adapter `send_*` collapse (Phase 15) — unchanged
- `smoke.sh --cached` flag (Phase 16) — unchanged
- `.planning/HERMES-API-AUDIT.md` (Phase 17) — unchanged
- Cosmetics nits (Phase 18) — unchanged
- 120/120 test count — unchanged

## Out of scope (PART B)

PART A explicitly did NOT do:
- `twine upload dist/*` (real PyPI publish)
- Post-publish install verification from PyPI
- `git tag -a v3.0.0`
- `git push origin main && git push origin v3.0.0`

## Operator decision required

**GO:** Dispatch PART B. PART B will:
1. `python -m twine upload dist/*` (uses `~/.pypirc[pypi]` token)
2. Verify https://pypi.org/project/chatlytics-hermes/ renders
3. Install verification: fresh venv, `pip install chatlytics-hermes==3.0.0`
   from PyPI, `python -c "import chatlytics_hermes; print(__version__)"`
4. `git tag -a v3.0.0 -m "v3.0.0 — first public PyPI release"`
5. `git push origin main && git push origin v3.0.0`

**NO-GO:** Surface concerns. Nothing has been published or tagged.
The `dist/` artifacts can be rebuilt at any time.
