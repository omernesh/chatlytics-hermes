---
phase: 19
phase_name: Release chatlytics-hermes 3.0.0 (PyPI) — PART A (dress rehearsal only)
mode: infra-skip
date: 2026-05-18
part: A
---

# HERMES-19 — Context (infra-skip, PART A)

## Domain (boundary)

**PART A — prepare + validate v3.0.0 release artifact via local
wheel-install dress rehearsal.** This run produces the build artifacts,
runs all validation gates locally, and STOPS at the operator go/no-go
checkpoint. Real PyPI publish, git tag, and `git push` happen in a
separate **PART B** session after the operator confirms.

The work is the **first public PyPI publish** for chatlytics-hermes.
Everything that touches the public artifact (version strings,
CHANGELOG, README) is in scope for PART A; everything that goes live
on the internet (`twine upload`, `git tag`, `git push`) is **explicitly
out of scope** for PART A.

## Decisions

- **D1 — Version bumps (locked, exact value `3.0.0`):**
  - `pyproject.toml` `[project] version = "3.0.0"`
  - `plugin.yaml` top-level `version: "3.0.0"` (string-quoted to
    survive YAML implicit-float parsing — current value is unquoted
    `2.1.0` which works only because three dotted numbers fall through
    to string; the new `3.0.0` form follows the same pattern but
    quoting is the safe convention)
  - `src/chatlytics_hermes/__init__.py` `__version__ = "3.0.0"` —
    **note:** v2.1.0 `__init__.py` does NOT currently declare
    `__version__`; we will ADD the module attribute as part of this
    phase so the dress-rehearsal `python -c "import chatlytics_hermes;
    print(chatlytics_hermes.__version__)"` gate has something to read
  - All three values MUST be equal. A `grep -rn '"2\.1\.0"\|: 2\.1\.0'`
    sweep before commit confirms no orphan `2.1.0` references remain
    in the version-declaring files.

- **D2 — CHANGELOG entry (locked shape):**
  Heading: `## [3.0.0] - 2026-05-18 (BREAKING)`
  Subsections (in order):
  - **Breaking — tool surface**
    - `chatlytics_get_chat_info` return-shape disambiguation
      (Phase 13) — old `{success: false, error}` for both
      not-found-and-transport-error → new `{success: true, chat: null}`
      for legit empty and `{success: false, error, _error: "<code>"}`
      for errors with machine-readable code
    - `chatId` schemas now reject non-JID strings at validation layer
      (Phase 14). Regex: `/@(c\.us|g\.us|lid|newsletter)$/i`. Phone
      numbers, display names, and ambiguous inputs are rejected.
      Callers must resolve via `chatlytics_search` first.
  - **Breaking — library API**
    - Adapter `send_image_file` / `send_animation_file` /
      `send_video_file` / `send_file_file` removed (Phase 15). Use
      `send_image(resource: str | Path)` etc. — resource is auto-
      detected as URL or path. Tool-layer callers
      (`chatlytics_send_image` etc.) unaffected.
  - **Additive**
    - `smoke.sh --cached` flag enables wheel caching for faster
      repeat runs (Phase 16)
    - `.planning/HERMES-API-AUDIT.md` — Hermes 0.14 API surface
      inventory for future 0.15 migration (Phase 17)
  - **Other**
    - Cosmetics sweep: log-level/style consistency, docstring
      tightening, lint nits (Phase 18). No behavior change.

- **D3 — README updates (locked):**
  - Replace the GitHub-only install snippet (`pip install -e git+...`)
    with the PyPI command `pip install chatlytics-hermes`. Keep the
    `hermes-agent @ git+...` line for the pinned hermes dependency
    until hermes 0.14 ships on PyPI (per current Status section).
  - Remove the "Status: BETA" pre-publish disclaimer line. This IS
    the public publish; new line: `Stable release. Requires
    hermes-agent>=0.14,<0.15.`
  - Update the "v2.1.0 BETA" mention to a stable v3.0.0 status line.
  - Add a "Migration from 2.x" section near the top: bullet the three
    breaking changes with one-line migration tips.
  - Update the "What's new in v2.1" section heading → keep that
    section as historical context but precede it with a "What's new
    in v3.0" section summarising the breaking changes.
  - Optional updates ONLY if directly version-related: any "Current
    version: 2.1.0" → `3.0.0`. No prose rewrites elsewhere — keep
    the surface diff minimal.

- **D4 — Build artifacts (locked):**
  - Clean `dist/`, `build/`, `src/chatlytics_hermes.egg-info/` before
    building.
  - Add `dist/`, `build/`, `*.egg-info/`, `__pycache__/` to
    `.gitignore` if not already present (current `.gitignore` lists
    only `.smoke-cache/`). Build artifacts must NEVER be committed.
  - Build via `python -m build` (build 1.5.0 + twine 6.2.0 already
    installed via `pip install --user`).
  - Verify both sdist (`chatlytics_hermes-3.0.0.tar.gz`) and wheel
    (`chatlytics_hermes-3.0.0-py3-none-any.whl`) land in `dist/`.

- **D5 — Validation gates (each MUST pass; halt on first failure):**
  1. `twine check dist/*` — MUST report all OK
  2. `pip index versions chatlytics-hermes` — name must be available
     on PyPI (no existing 2.x or 3.x versions; the package was never
     publicly published)
  3. Local wheel-install dress rehearsal:
     - Scratch venv at `C:/Users/omern/AppData/Local/Temp/chatlytics-hermes-dress-19`
       (Windows-safe absolute path; cleaned if already exists)
     - `pip install dist/chatlytics_hermes-3.0.0-py3-none-any.whl`
     - `python -c "import chatlytics_hermes; print(chatlytics_hermes.__version__)"`
       MUST print `3.0.0`
     - Full pytest suite run against the INSTALLED wheel (not the
       source tree). Strategy: copy `tests/` to a scratch dir, run
       pytest with PYTHONPATH unset so the installed package is
       imported. All 120 tests MUST pass.

- **D6 — Scope guards (locked; PART A only):**
  - Do **NOT** run `twine upload` (real or TestPyPI)
  - Do **NOT** create the `v3.0.0` git tag
  - Do **NOT** `git push` anything
  - Do **NOT** modify source code beyond the version-string bumps
    in `__init__.py` (one-line `__version__` addition)
  - Do **NOT** touch the sibling JS repo (Phases 20-21)

- **D7 — Commits (locked):**
  - One commit per logical step, all prefixed `release(v3.0.0): ...`
  - `dist/` and `build/` artifacts are NOT committed (added to
    `.gitignore` first)
  - Order:
    1. infra-skip CONTEXT (this file)
    2. PLAN file (next step)
    3. `.gitignore` hardening
    4. version bumps (pyproject + plugin.yaml + `__init__.py`)
    5. CHANGELOG 3.0.0 entry
    6. README updates
    7. release-readiness summary doc

- **D8 — PART B (separate run, NOT this run):**
  - Real `twine upload dist/*` against PyPI
  - Post-publish install verification:
    `pip install chatlytics-hermes==3.0.0` in another fresh scratch
    venv + import check
  - `git tag -a v3.0.0 -m "v3.0.0 — first public PyPI release"`
  - `git push origin main && git push origin v3.0.0`
  - PART B is dispatched only after the operator reviews PART A's
    release-readiness summary and gives go/no-go.

## Code context

| File | Role |
|---|---|
| `pyproject.toml` | Project metadata + dependencies + version |
| `plugin.yaml` | Hermes plugin manifest + version |
| `src/chatlytics_hermes/__init__.py` | Plugin entry point; will gain `__version__` |
| `CHANGELOG.md` | Existing `[Unreleased]` section → becomes `[3.0.0]` (BREAKING) |
| `README.md` | Install path PyPI; Migration from 2.x section; Status: Stable |
| `.gitignore` | Currently `.smoke-cache/` only; needs `dist/`, `build/`, `*.egg-info/`, `__pycache__/` |
| `dist/` (build output, NOT committed) | sdist + wheel artifacts |
| `tests/` | 120-test suite; runs against source tree AND installed wheel |

## Specifics

- **PART A ends with go/no-go return.** Final return is a JSON block
  with `status: "human_needed"` (success) or `status: "blocker"`
  (any gate failure). The release-readiness summary file is the
  human-readable artifact the operator reviews.
- **PART B will be dispatched separately** after the operator
  confirms go. PART B's scope is real publish + tag + push.

## Deferred

- Real `twine upload` to PyPI — PART B
- Post-publish install verification — PART B
- `v3.0.0` git tag + push — PART B
- Sibling JS bundle (Phases 20-21) — separate phases

## Acceptance gates (PART A)

A release-readiness summary file at `19-RELEASE-READY.md` enumerates
the following checks; ALL must show `[x]` for PART A to return
`release_ready: true`:

- [ ] All 3 version strings bumped to `3.0.0` and match
- [ ] CHANGELOG `[3.0.0]` entry written with all breaking changes
- [ ] README updated for PyPI install path + Migration section
- [ ] `dist/chatlytics_hermes-3.0.0.tar.gz` exists
- [ ] `dist/chatlytics_hermes-3.0.0-py3-none-any.whl` exists
- [ ] `twine check dist/*` → OK
- [ ] `pip index versions chatlytics-hermes` → name available
- [ ] Scratch venv install of wheel succeeded
- [ ] `__version__` reads `3.0.0` from installed wheel
- [ ] Full pytest suite passes against installed wheel (120/120)
- [ ] Code review clean OR fix-pass landed

## Invariants (DO NOT REGRESS)

- 120/120 tests passing (source tree AND installed wheel)
- 21 tools registered
- All phases 13-18 changes preserved (no source edits beyond
  `__init__.py` `__version__` addition)
- Hermes pin `>=0.14,<0.15` unchanged
- httpx outbound + aiohttp embedded inbound architecture unchanged
- `{"success": bool, ...}` tool response shape unchanged
- `_error` sentinel contract (Phase 13) unchanged
- Strict JID regex (Phase 14) unchanged
- Adapter `send_*` collapse (Phase 15) unchanged
