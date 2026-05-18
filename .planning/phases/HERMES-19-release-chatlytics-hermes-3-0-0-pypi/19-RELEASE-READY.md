---
phase: 19
part: A
status: release_ready
date: 2026-05-18
next_action: operator go/no-go for real PyPI publish (PART B)
---

# HERMES-19 PART A — Release Readiness Summary

**Status:** READY for operator go/no-go.

All ten validation gates passed. The v3.0.0 release artifact has been
prepared, validated locally, and is ready for `twine upload` in
PART B once the operator confirms.

## Gate checklist

- [x] All 3 version strings bumped to `3.0.0` and match
  - `pyproject.toml` line 7: `version = "3.0.0"`
  - `plugin.yaml` line 4: `version: "3.0.0"`
  - `src/chatlytics_hermes/__init__.py`: `__version__ = "3.0.0"`
- [x] CHANGELOG `[3.0.0] - 2026-05-18 (BREAKING)` entry written with all
      breaking changes (Phases 13/14/15), additive items (Phases 16/17),
      internal cosmetics (Phase 18), and Migration-from-2.x section
- [x] README updated:
  - Status → `Stable v3.0.0 release`
  - Install snippet → `pip install chatlytics-hermes`
  - New `## Migration from 2.x` section
  - New `## What's new in v3.0` section
  - Obsolete `get_chat_info returns {}` Known-issue bullet removed
- [x] `dist/chatlytics_hermes-3.0.0.tar.gz` exists (80,155 bytes)
- [x] `dist/chatlytics_hermes-3.0.0-py3-none-any.whl` exists (44,647 bytes)
- [x] `twine check dist/*` → **PASSED** for both artifacts
- [x] `pip index versions chatlytics-hermes` → **No matching distribution**
      (package name available; first publish ever)
- [x] Scratch venv install of wheel succeeded
  - Path: `C:/Users/omern/AppData/Local/Temp/chatlytics-hermes-dress-19`
  - hermes-agent installed from GitHub tag `v2026.5.16` (runtime dep)
  - `dist/chatlytics_hermes-3.0.0-py3-none-any.whl` installed cleanly
- [x] `__version__` reads `3.0.0` from installed wheel
  - Import location verified at
    `<scratch>/Lib/site-packages/chatlytics_hermes/__init__.py`
- [x] Full pytest suite passes against installed wheel: **120/120 passed**
  - Strategy: copied `tests/`, `pyproject.toml`, `plugin.yaml`,
    `scripts/smoke.sh` to scratch dir; `pythonpath=["src"]` resolved
    to non-existent `./src`, fell through to site-packages
  - Same 120/120 baseline as source-tree run

## Validation gate results (quantitative)

| Gate | Result | Detail |
|---|---|---|
| `twine check dist/*` | PASSED | Both sdist + wheel |
| PyPI name availability | AVAILABLE | `pip index versions` reports no matching distribution |
| Wheel install in scratch venv | OK | hermes-agent 0.14.0 + chatlytics-hermes 3.0.0 + 14 transitive deps |
| `__version__` from installed wheel | `3.0.0` | Matches `pyproject.toml` + `plugin.yaml` |
| pytest against source tree | 120/120 | Baseline preserved |
| pytest against installed wheel | 120/120 | Dress rehearsal gate |

## Files changed (PART A)

| File | Nature |
|---|---|
| `.gitignore` | Added `dist/`, `build/`, `*.egg-info/`, `__pycache__/`, etc. |
| `pyproject.toml` | Version bump 2.1.0 → 3.0.0 |
| `plugin.yaml` | Version bump 2.1.0 → 3.0.0 (now quoted) |
| `src/chatlytics_hermes/__init__.py` | Added `__version__ = "3.0.0"` module attribute |
| `tests/test_register.py` | Version assertion bump 2.1.0 → 3.0.0 (two sites) |
| `CHANGELOG.md` | `[Unreleased]` → `[3.0.0] - 2026-05-18 (BREAKING)`, full release notes |
| `README.md` | PyPI install path, Migration from 2.x, What's new in v3.0 |
| `.planning/phases/HERMES-19-*/19-CONTEXT.md` | New (infra) |
| `.planning/phases/HERMES-19-*/19-PLAN-1-*.md` | New (infra) |
| `.planning/phases/HERMES-19-*/19-RELEASE-READY.md` | This file |

**No source code changed beyond the `__version__` addition in
`__init__.py`.** Test assertions for the version literal updated in
lockstep with the version bump (mandatory side-effect; the bump
itself breaks the suite without it).

## Invariants preserved

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

## Out of scope (PART B work)

Explicitly NOT done in PART A:

- `twine upload dist/*` (real PyPI publish)
- Post-publish install verification (`pip install chatlytics-hermes==3.0.0`
  from PyPI)
- `git tag -a v3.0.0 -m "..."` (annotated tag)
- `git push origin main && git push origin v3.0.0`

## Operator decision required

**GO:** Dispatch PART B. PART B will:
1. Run `python -m twine upload dist/*` (uses `~/.pypirc[pypi]` token)
2. Verify the package on https://pypi.org/project/chatlytics-hermes/
3. Run install verification from real PyPI in a fresh scratch venv
4. Create annotated tag `v3.0.0`
5. Push main + tag to origin

**NO-GO:** Surface specific concerns. PART A artifacts in `dist/` can
be rebuilt at any time; nothing has been published or tagged.

## Scratch venv

Path: `C:/Users/omern/AppData/Local/Temp/chatlytics-hermes-dress-19`

This venv can be cleaned up safely at any time; PART B should rebuild
a fresh one against PyPI-pulled artifacts (not local-built ones) so
the post-publish verification exercises the true publish path.
