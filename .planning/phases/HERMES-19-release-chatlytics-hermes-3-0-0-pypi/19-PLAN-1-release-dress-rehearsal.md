---
phase: 19
plan: 1
plan_name: v3.0.0 release dress rehearsal (PART A)
mode: infra-skip
date: 2026-05-18
status: ready
---

# HERMES-19 Plan 1 — v3.0.0 release dress rehearsal (PART A)

## Tasks (sequential; halt on any failure)

### T1 — Harden `.gitignore`
Add `dist/`, `build/`, `*.egg-info/`, `__pycache__/`, `*.pyc`,
`.venv*/`, and the scratch venv path to `.gitignore`. Verify by
`git status --ignored` that the to-be-created build artifacts are
ignored before building.

**Commit:** `release(v3.0.0): gitignore build artifacts (dist/, build/, *.egg-info/)`

### T2 — Version bumps (3 files, must match exactly `3.0.0`)
- `pyproject.toml` line `version = "2.1.0"` → `version = "3.0.0"`
- `plugin.yaml` line `version: 2.1.0` → `version: "3.0.0"`
  (quoted; safer YAML)
- `src/chatlytics_hermes/__init__.py` — ADD `__version__ = "3.0.0"`
  module attribute near the top (after the module docstring, before
  the `from .adapter import register` import). Add to `__all__`.

Sanity sweep after edit: `grep -rn '"2\.1\.0"\|version: 2\.1\.0\|version = "2\.1\.0"' pyproject.toml plugin.yaml src/chatlytics_hermes/__init__.py`
→ MUST return zero matches.

**Commit:** `release(v3.0.0): bump version to 3.0.0 in pyproject.toml, plugin.yaml, __init__.py`

### T3 — CHANGELOG 3.0.0 entry
Convert the existing `## [Unreleased]` heading to
`## [3.0.0] - 2026-05-18 (BREAKING)`. The body content already
documents the three breaking changes (Phases 13/14/15) in detail.
Preserve that content verbatim; just rename the heading. Add the
additive (Phase 16/17) and other (Phase 18) entries to keep the
release notes complete per CONTEXT.md D2.

**Commit:** `release(v3.0.0): cut CHANGELOG 3.0.0 BREAKING entry`

### T4 — README updates (PyPI install + Migration from 2.x)
- Replace the `## Status` body — current `v2.1.0 BETA` line becomes
  `**Stable v3.0.0 release.** Requires hermes-agent>=0.14,<0.15.`
  followed by the existing hermes-agent-not-on-PyPI note.
- Replace the `## Install` snippet's `pip install -e .` line with:
  `pip install chatlytics-hermes`
  (keep the `pip install "hermes-agent @ git+..."` line above — that
  dependency is still GitHub-only until hermes 0.14 ships on PyPI)
- Insert a new section `## Migration from 2.x` immediately after
  `## Status`, before `## What's new in v2.1`, listing the three
  breaking changes with one-line migration tips.
- Insert a new `## What's new in v3.0` section above
  `## What's new in v2.1`, summarising the breaking changes and
  pointing at CHANGELOG.md.
- The "Known issues" section's `get_chat_info returns {}` bullet is
  now obsolete (Phase 13 fixed it). Remove that bullet; keep the
  `filename for URL-path documents` bullet.

**Commit:** `release(v3.0.0): README — PyPI install path, Migration from 2.x, v3.0 highlights`

### T5 — Build
Clean `dist/`, `build/`, `src/chatlytics_hermes.egg-info/`. Run
`python -m build`. Verify both artifacts exist:
- `dist/chatlytics_hermes-3.0.0.tar.gz`
- `dist/chatlytics_hermes-3.0.0-py3-none-any.whl`

**No commit** — build artifacts are gitignored per T1.

### T6 — Gate 1: `twine check dist/*`
Run `python -m twine check dist/*`. MUST report `PASSED` for both
artifacts. On any FAILED, halt with `blocker`.

### T7 — Gate 2: PyPI name availability
Run `python -m pip index versions chatlytics-hermes 2>&1`. Expected:
the package name is NOT found on the index (this is the first
publish ever). If ANY version is reported, halt with `blocker`
(name conflict).

### T8 — Gate 3: Local wheel-install dress rehearsal
1. Scratch venv at `C:/Users/omern/AppData/Local/Temp/chatlytics-hermes-dress-19`
   (Windows path). If it already exists, remove it first.
2. Create venv: `python -m venv <scratch>`
3. Upgrade pip in venv: `<scratch>/Scripts/python -m pip install -U pip`
4. Install hermes-agent from GitHub (runtime dep):
   `<scratch>/Scripts/pip install "hermes-agent @ git+https://github.com/NousResearch/hermes-agent.git@v2026.5.16"`
5. Install the built wheel:
   `<scratch>/Scripts/pip install dist/chatlytics_hermes-3.0.0-py3-none-any.whl`
6. Install dev deps for pytest:
   `<scratch>/Scripts/pip install pytest pytest-asyncio respx`
7. Sanity import:
   `<scratch>/Scripts/python -c "import chatlytics_hermes; print(chatlytics_hermes.__version__)"`
   → MUST print exactly `3.0.0`
8. Copy `tests/` to scratch dir, copy `conftest.py` if at repo root,
   `cd` to scratch dir, run pytest such that the installed package
   (not source tree) is imported. Strategy: do NOT include
   `src/chatlytics_hermes` in PYTHONPATH or current dir layout.
9. Run pytest with the same env-var clearing as the source-tree
   baseline: `CHATLYTICS_API_KEY= CHATLYTICS_API_URL= CHATLYTICS_BASE_URL= CHATLYTICS_SESSION= <scratch>/Scripts/python -m pytest tests/ -q --no-header`
10. MUST be `120 passed`. On any failure, halt with `blocker`.

### T9 — Release-readiness summary
Write `19-RELEASE-READY.md` next to this plan, with the checklist
from CONTEXT.md D5 marked off, plus quantitative results (test
count, artifact sizes, gate verdicts).

**Commit:** `docs(19): release-readiness summary for PART A go/no-go`

### T10 — Phase summary
Write `19-SUMMARY.md` capturing the dress-rehearsal outcome:
commits, files changed, test counts (source tree + installed wheel),
validation gates passed, next action (operator go/no-go for PART B).

**Commit:** `docs(19): phase summary for PART A (dress rehearsal complete)`

## Halt conditions

- T6 `twine check` reports FAILED for any artifact
- T7 `pip index versions chatlytics-hermes` finds an existing version
- T8 venv install fails, import fails, `__version__` is wrong, or
  pytest reports any failure

On any halt: return JSON with `status: "blocker"`, `release_ready:
false`, the failing gate in `blockers_for_release`, and stop.

## Acceptance (post-execution)

- 120/120 tests passing against source tree (baseline preserved)
- 120/120 tests passing against installed wheel (Gate 3)
- All three version files match `3.0.0`
- `twine check` clean
- PyPI name available
- Release-readiness summary written
- No source-code edits beyond `__version__` addition in
  `__init__.py`
- No `twine upload`, no git tag, no git push
