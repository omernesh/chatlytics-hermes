---
phase: 16
phase_slug: smoke-sh-wheel-caching-additive
phase_name: "`smoke.sh` wheel caching (additive)"
project_code: HERMES
milestone: v3.0
infra_skip: true
infra_skip_reason: "Scope is fix-locked per v3.0 ROADMAP HERMES-16 + the operator's autonomous-launch brief. The `--cached` flag spec, the `.smoke-cache/` directory layout, the pin-hash invalidation strategy, the cache-miss network fallback, the `.gitignore` update, and the README documentation update are all encoded by the operator before launch. The change is additive, non-breaking, and opt-in — default `smoke.sh` invocation must behave exactly as v2.1. No grey areas need user discussion — gsd-discuss-phase would only paraphrase locked decisions."
---

# HERMES-16 — `smoke.sh` wheel caching (additive) — CONTEXT

## Domain (Phase boundary from ROADMAP goal)

Cache the `hermes-agent` wheel between `scripts/smoke.sh` runs to cut
docker rebuild time. The current smoke flow installs
`hermes-agent @ git+https://github.com/NousResearch/hermes-agent.git@v2026.5.16`
from GitHub on every run, which dominates wall time (60-90 s on a warm
docker cache, almost all of which is the git+pip install). Caching the
wheel locally drops subsequent runs to a `pip install --no-index`
against the on-disk cache directory.

**This phase is additive, non-breaking, opt-in.** Default
`bash scripts/smoke.sh` invocation must behave exactly as v2.1 — no
functional change unless `--cached` is passed. The flag composes with
existing flags (`--fast`, `--retries N`) order-independently.

Closes v2.1 deferred item 4. Closes PR-MED-03's remaining open portion
(v2.1 added `--retries 3` to mask transient GitHub outages; v3.0 adds
the actual caching that obviates the retry path for cache-hits).

**Important file-location note:** `smoke.sh` lives at
`scripts/smoke.sh`, NOT at the repo root. The operator launch brief
referenced "smoke.sh at repo root" but the v2.1 location is
`scripts/smoke.sh` per ROADMAP HERMES-16 file list. The phase keeps
the existing path; no relocation.

## Decisions (encoded from operator-locked phase brief)

### D1 — `--cached` flag semantics

```
bash scripts/smoke.sh                 # v2.1 behavior, unchanged
bash scripts/smoke.sh --fast          # v2.1 host-venv pytest, unchanged
bash scripts/smoke.sh --cached        # NEW: docker mode + wheel cache
bash scripts/smoke.sh --fast --cached # NEW: composes; --cached is a no-op in --fast mode
bash scripts/smoke.sh --cached --fast # same as above (order-independent)
```

- `--cached` is **off by default**. Default invocation MUST NOT touch
  `.smoke-cache/`.
- First run with `--cached`: `pip download hermes-agent==<pin> -d .smoke-cache/`
  populates the cache, then `pip install --no-index --find-links=.smoke-cache/ hermes-agent`
  installs from cache.
- Subsequent runs with `--cached`: cache hit → `pip install --no-index --find-links=.smoke-cache/`
  only (no network).
- **Cache-miss fallback:** if the `--no-index` install fails (cache
  was wiped, wheel corrupted, pin changed), fall back to a normal
  network install via the existing
  `pip install --retries 3 "hermes-agent @ git+..."` line AND refresh
  the cache by re-running `pip download` so the next run is fast
  again.
- `--cached` in `--fast` mode is a documented no-op (the fast path
  uses the host venv and never installs hermes-agent fresh); the flag
  is accepted but does nothing in that mode. No error.

### D2 — Cache directory layout

```
.smoke-cache/                    # repo-relative; gitignored
├── .pin-hash                    # sha256 of the hermes-agent pin string
├── hermes_agent-0.14.0-py3-none-any.whl   # downloaded wheel
└── *.whl                        # transitive dep wheels from pip download
```

- **Location:** `.smoke-cache/` at repo root (sibling of `scripts/`,
  `tests/`, `src/`, etc.). NOT inside `scripts/`. Repo-relative so
  it follows the repo, not the user's home dir.
- **Files inside:** whatever `pip download` writes. Don't try to
  enumerate or filter — `pip download` writes the requested wheel +
  any transitive deps as wheels.
- **`.pin-hash` file:** stores the sha256 of the pin string used at
  download time. On cache lookup, compute the current pin's sha256 and
  compare. Mismatch → wipe `.smoke-cache/` contents and re-download.
- The wheel directory is reusable across docker runs because we
  bind-mount the repo at `/work` in the existing smoke.sh docker
  invocation — the cache is visible to the container at
  `/work/.smoke-cache/`.

### D3 — Pin-hash invalidation

The hermes-agent pin currently lives **embedded in smoke.sh** at
line 94 as a string literal:

```
"hermes-agent @ git+https://github.com/NousResearch/hermes-agent.git@v2026.5.16"
```

The pin is NOT in `pyproject.toml` (that has `>=0.14,<0.15` — a range,
not a specific commit). The pin-hash check must hash the **smoke.sh
git-tag string** (e.g. `v2026.5.16`), not the pyproject range, because
the cache is keyed to the actual wheel that gets downloaded.

**Implementation:** extract the tag to a shell variable near the top
of smoke.sh (e.g. `HERMES_AGENT_PIN_TAG="v2026.5.16"`), compute its
sha256 in bash (`echo -n "$HERMES_AGENT_PIN_TAG" | sha256sum | cut -d' ' -f1`),
compare to `.smoke-cache/.pin-hash` contents, wipe + re-download on
mismatch.

This variable refactor is the minimum touch needed to make the pin
checksummable without parsing strings out of smoke.sh's pip-install
line. The pin VALUE doesn't change in this phase.

### D4 — `.gitignore` creation

The repo has no top-level `.gitignore` (only `.pytest_cache/.gitignore`
auto-generated by pytest). The operator brief says "needs
`.smoke-cache/` added" — interpret this as **create** a top-level
`.gitignore` with `.smoke-cache/` as the (likely sole, initially)
entry. Keep it minimal — just `.smoke-cache/` plus a couple of
universally-ignored Python artifacts that the existing git workflow
already implicitly ignores (the `.pytest_cache/`, `__pycache__/`,
`*.egg-info/` patterns visible in `git status --untracked` output).

**Decision:** create `.gitignore` with `.smoke-cache/` only. Do NOT
sprawl the gitignore into a general Python ignore template — that's
beyond the phase scope. The other untracked dirs visible in
`git status` (`.pytest_cache/`, `__pycache__/`, `*.egg-info/`) are
already excluded from commits by virtue of operator never having
added them; adding them here would be in-scope-creep.

### D5 — README documentation

The existing README has a "Development" section (line 189) that
documents `bash scripts/smoke.sh`. Add a brief paragraph there
describing `--cached` with a single example invocation:

```markdown
For faster local iteration, pass `--cached` to cache the
`hermes-agent` wheel between runs at `.smoke-cache/`:

    bash scripts/smoke.sh --cached

The first cached run downloads the wheel; subsequent runs install from
the local cache (no network). Cache invalidates automatically when the
pinned `hermes-agent` tag changes.
```

One paragraph + one code block. Do not document the implementation
details (pin-hash, fallback path) — those live in smoke.sh comments.

### D6 — Test addition (lightweight)

Add `tests/test_smoke_cache.py` covering:

1. **Bash syntax check** — `bash -n scripts/smoke.sh` exits 0
   (script parses cleanly with the new flag added).
2. **Help text includes `--cached`** — `bash scripts/smoke.sh --help`
   stdout contains the string `--cached` (basic discoverability).
3. **Unknown flag still rejected** — `bash scripts/smoke.sh --bogus`
   exits non-zero (regression guard on the existing flag-parsing
   error path).
4. **`--fast --cached` composes** — `bash scripts/smoke.sh --fast --cached --help`
   exits 0 (verifies flag parser accepts the combination without
   short-circuiting). The actual run isn't exercised — too heavy.

The test file uses `subprocess.run` with `shell=False`. Skip via
`pytest.importorskip("subprocess")` not needed (stdlib). Tests must
NOT execute the docker path or the host-venv pytest path — pure
argument-parsing verification only.

**Test file lives in `tests/test_smoke_cache.py`** — there is no
existing `tests/test_smoke*.py` file (audited).

### D7 — Scope guards (DO NOT TOUCH)

- **No source code changes** in `src/chatlytics_hermes/`. This is
  infra-only.
- **No version bump** in `pyproject.toml` / `plugin.yaml`. Phase 19
  owns release bumps.
- **No push, no publish.**
- **No CI integration** — there is no CI yet; ROADMAP explicitly
  defers CI cache integration.
- **No pre-built docker base image** — heavier solution, ROADMAP
  defers to v3.1 if needed.
- **Existing `--fast`, `--retries N` behavior unchanged.** The current
  `--retries 3` flag is hard-coded into the pip-install calls inside
  the docker `sh -c '...'` block; this phase does NOT promote it to a
  configurable flag. The brief says `--retries N` "composes with
  `--cached`" — interpretation: the existing implicit `--retries 3`
  stays in place on the fallback path; no new `--retries` CLI flag is
  added in this phase.
- **No changes to the pin** (`v2026.5.16`). Just extract to a
  variable for hashing.

### D8 — v3.0-so-far invariants (DO NOT REGRESS)

- 116/116 tests passing baseline (from Phase 15)
- 21 tools registered
- Phase 13 `_error` sentinel contract preserved
- Phase 14 strict JID regex preserved
- Phase 15 `send_*` collapse preserved
- Existing `--fast` flag still works the same way (host-venv pytest
  short-circuit)
- Existing `--retries 3` on pip install calls still in place
- Default `bash scripts/smoke.sh` (no flags) still produces the same
  output as v2.1 — same docker image, same install line, same
  pytest invocation

## Code context (files touched + established patterns)

### Files to modify / create

| File | Change |
|------|--------|
| `scripts/smoke.sh` | Add `--cached` to the argument parser. Add `HERMES_AGENT_PIN_TAG` variable. Extend the docker `sh -c '...'` block (or wrap it) with a cache-aware install path when `--cached` is on. Document the flag in the existing USAGE heredoc. |
| `.gitignore` | CREATE at repo root with `.smoke-cache/` entry. |
| `README.md` | Append `--cached` paragraph + example to the Development section (line 189-198 area). |
| `tests/test_smoke_cache.py` | CREATE. Four lightweight subprocess-based tests covering bash syntax, --help mentioning --cached, --bogus still rejected, --fast --cached composes. |

### Established patterns

- **smoke.sh argument parser** — already loops over `$@` with a
  `case` block (lines 39-70). Add a `--cached) CACHED=1 ;;` arm in
  the same shape.
- **Existing USAGE heredoc** — `bash scripts/smoke.sh --help` prints
  a heredoc (lines 47-62). Add a `--cached` row in the Modes section
  and an Examples line.
- **Default-off flags** — `FAST=0` at line 39, set to 1 in the
  parser. Mirror with `CACHED=0`.
- **Conditional docker step** — when `--cached` is on, change the
  docker `sh -c '...'` block to mount `.smoke-cache/` and adjust the
  pip-install line. Keep the default (non-cached) docker block
  byte-identical to v2.1 to satisfy the "default unchanged"
  invariant.
- **Path resolution** — `REPO_DIR` is computed at line 35 via
  `cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd`. Use
  `${REPO_DIR}/.smoke-cache` for the cache dir so the script works
  when invoked from any cwd.

### Reference: existing test patterns

- `tests/conftest.py` — shared fixtures; doesn't need to know about
  smoke tests.
- `tests/test_register.py`, `tests/test_outbound.py` etc — async test
  patterns. `test_smoke_cache.py` is fully synchronous (subprocess
  calls). Use plain `def test_*` (no `async def`); pytest-asyncio's
  `asyncio_mode = "auto"` is harmless for sync tests.
- Existing tests don't shell out — this is the first test file that
  invokes `bash`. Tests must skip on Windows-CI-without-bash if bash
  isn't on PATH (no such CI exists yet, but `pytest.skip` if
  `shutil.which("bash") is None` is the right defensive shape).

## Specifics (sequencing)

- **Phase 13, 14, 15** — landed; not in this phase's path.
- **Phase 16 (this)** — opt-in non-breaking smoke.sh caching.
  Default behavior unchanged. Cache lives at `.smoke-cache/` with
  `.pin-hash` for invalidation. Tests cover argument parsing only,
  not actual cache flow execution.
- **Phase 17 (next)** — Hermes 0.14 API audit doc (docs-only).
  Independent.

## Deferred

**None** — scope is locked to the wheel caching feature per the
operator brief. No CI integration, no pre-built docker base image,
no promotion of `--retries` to a CLI flag, no broader gitignore
template, no relocation of smoke.sh.
