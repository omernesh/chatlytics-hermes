---
phase: 16
plan_index: 1
plan_slug: smoke-sh-wheel-caching
title: "smoke.sh wheel caching (additive, opt-in --cached)"
project_code: HERMES
milestone: v3.0
status: ready
infra_skip: true
verification: pytest + bash -n
---

# HERMES-16 Plan 1 — `smoke.sh` wheel caching

## Goal

Add opt-in `--cached` flag to `scripts/smoke.sh` that caches the
`hermes-agent` wheel between runs at `.smoke-cache/`, cutting docker
rebuild time on subsequent invocations. Default behavior remains
byte-for-byte identical to v2.1. Pin-hash invalidation auto-refreshes
the cache when the pinned hermes-agent tag changes. Cache-miss
fallback drops to a normal network install + repopulates the cache.

Closes v2.1 deferred item 4. Closes PR-MED-03's remaining open portion
(v2.1 added `--retries 3`; v3.0 adds the caching that obviates the
retry path for cache-hits).

## Scope (locked per 16-CONTEXT.md)

**In:**
- `scripts/smoke.sh`:
  - Add `HERMES_AGENT_PIN_TAG="v2026.5.16"` variable at the top
    (extracted from the embedded git+ install line so it can be
    hashed).
  - Add `--cached` flag to the argument parser (default off:
    `CACHED=0`).
  - Update the USAGE heredoc (`--help` output) with a `--cached`
    row in Modes and an Examples line.
  - When `CACHED=1` AND not `--fast`, wrap the existing docker
    `sh -c '...'` block with a cache-aware install path:
    - Compute current pin sha256 (`echo -n "$HERMES_AGENT_PIN_TAG"
      | sha256sum | cut -d' ' -f1`).
    - If `.smoke-cache/.pin-hash` mismatches → wipe
      `.smoke-cache/*` and re-download.
    - Mount `${REPO_DIR}/.smoke-cache` into the container at
      `/work/.smoke-cache` (already happens via the existing
      `-v ${REPO_DIR}:/work` mount — no new mount line needed; the
      cache dir is just a subpath of the existing mount).
    - Inside the container: if cache populated, run
      `pip install --no-index --find-links=/work/.smoke-cache/ hermes-agent`;
      on failure (cache miss / corrupted wheel), fall back to
      `pip install --retries 3 "hermes-agent @ git+...@${PIN_TAG}"`
      AND re-run `pip download hermes-agent==<resolved> -d /work/.smoke-cache/`
      so the next run is fast again.
    - On first cached run (cache empty): run
      `pip download "hermes-agent @ git+...@${PIN_TAG}" -d /work/.smoke-cache/`
      first, write the pin hash, then install from cache.
  - When `CACHED=1` AND `--fast`: documented no-op (the fast path
    uses host venv; never installs hermes-agent fresh). Flag
    accepted, prints a one-line "smoke.sh: --cached is a no-op in
    --fast mode" to stderr for clarity, continues normally.
  - Keep the existing default (non-cached) docker `sh -c '...'`
    block byte-identical to v2.1 — the cache path is a separate
    branch.

- `.gitignore` (CREATE at repo root):
  - Single entry: `.smoke-cache/`
  - Trailing newline.

- `README.md`:
  - Append a paragraph + example block to the Development section
    (around line 198, after the existing `scripts/smoke.sh`
    description) documenting `--cached`.

- `tests/test_smoke_cache.py` (CREATE):
  - Four lightweight `subprocess.run(["bash", ...])` tests:
    1. `test_smoke_sh_passes_bash_syntax_check` — `bash -n
       scripts/smoke.sh` exits 0.
    2. `test_help_text_documents_cached_flag` — `bash scripts/smoke.sh
       --help` stdout contains `--cached`.
    3. `test_unknown_flag_still_rejected` — `bash scripts/smoke.sh
       --bogus` exits non-zero (regression guard).
    4. `test_cached_and_fast_compose` — `bash scripts/smoke.sh
       --fast --cached --help` exits 0 (parser accepts the
       combination without short-circuiting).
  - Skip the whole module via `pytest.skip` at collection time if
    `shutil.which("bash") is None` (defensive — no such CI exists
    yet but the test file should not crash on a bash-less host).
  - Pure sync tests; no `async def`.

**Out:**
- Modifying anything in `src/chatlytics_hermes/`. This is infra-only.
- Version bumps in `pyproject.toml` / `plugin.yaml` (Phase 19 owns).
- Pushing to git / publishing.
- CI integration (ROADMAP defers — no CI exists).
- Pre-built docker base image (ROADMAP defers).
- Promoting `--retries N` to a CLI flag (the existing implicit
  `--retries 3` on pip-install lines stays in place; the brief's
  "composes with --retries N" wording refers to that existing
  behavior, not a new flag).
- Changing the pinned hermes-agent tag (`v2026.5.16` stays).
- Adding `__pycache__/`, `.pytest_cache/`, `*.egg-info/`, etc. to
  the new `.gitignore` — scope is just `.smoke-cache/`.
- Caching plugin transitive dependencies (the brief targets the
  hermes-agent wheel specifically — `pip download hermes-agent`
  pulls its deps as a side effect, but we don't try to cache the
  plugin's own dev dependencies).

## Invariants (DO NOT REGRESS)

- **116/116 baseline tests still pass** (88 v2.1 + 10 Phase 13 + 13
  Phase 14 + 5 Phase 15). New tests in `test_smoke_cache.py` add 4
  cases, pushing total to **120**.
- `assert len(TOOLS) == 21` invariant in `tools.py` stays satisfied.
- Default `bash scripts/smoke.sh` (no flags) produces byte-identical
  pip-install + pytest output as v2.1.
- Existing `--fast` flag still short-circuits to host-venv pytest.
- Existing `--help` text is preserved (only ADDED to, not rewritten).
- Existing `--retries 3` on pip-install calls inside the docker block
  stays in place on the fallback path.
- Hermes pin stays `v2026.5.16` (only extracted to a variable;
  value unchanged).
- HI-01 allowlist (`CHATLYTICS_UPLOAD_ALLOWED_ROOTS`) — not
  affected by this phase (no source code touched).
- Phase 13 `_error` sentinel — not affected.
- Phase 14 strict JID regex — not affected.
- Phase 15 `send_*` collapse — not affected.

## Tasks (atomic; each commits independently)

### T1 — Create `.gitignore` at repo root

**File:** `.gitignore` (CREATE)

Write minimum content:

```
.smoke-cache/
```

(Single line + trailing newline. Repo intentionally has no top-level
gitignore today; this phase adds it solely for `.smoke-cache/`. The
existing untracked dirs visible in `git status` — `.pytest_cache/`,
`__pycache__/`, `*.egg-info/` — are already implicitly excluded by
operator never having added them; adding them here would be
scope-creep per 16-CONTEXT D4.)

**Commit message:**
```
chore(16): gitignore .smoke-cache/ wheel-cache directory
```

**Acceptance:**
- `cat .gitignore` shows `.smoke-cache/`.
- `git check-ignore .smoke-cache/foo` exits 0 (proves the rule
  matches a path inside the cache dir).
- `git status` no longer shows `.smoke-cache/` as untracked after
  the dir is populated (verified in T4).

### T2 — Extract pin tag to variable in smoke.sh (prep for hashing)

**File:** `scripts/smoke.sh`

Find (line 33-37 region, after `set -euo pipefail`):

```bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
```

Insert after `REPO_DIR=...`:

```bash
# The hermes-agent git tag this smoke run pins against. Extracted to
# a variable (rather than embedded inline in the pip install line) so
# HERMES-16's --cached mode can sha256 it for cache invalidation.
# Bumping this tag here automatically invalidates an existing
# .smoke-cache/ on the next --cached run.
HERMES_AGENT_PIN_TAG="v2026.5.16"
HERMES_AGENT_PIN_SPEC="hermes-agent @ git+https://github.com/NousResearch/hermes-agent.git@${HERMES_AGENT_PIN_TAG}"
```

Then find (line 94 region) inside the docker `sh -c '...'` block:

```bash
    pip install --quiet --no-cache-dir --retries 3 \
        "hermes-agent @ git+https://github.com/NousResearch/hermes-agent.git@v2026.5.16"
```

Replace with a templated form that the outer bash will substitute
into the heredoc. Two safe approaches:

(a) Switch from single-quoted `sh -c '...'` to a `cat <<EOF` heredoc
    fed into `sh -c` — but that risks shell-quoting bugs.

(b) **Preferred (minimal-change):** keep `sh -c` single-quoted but
    pass the spec via `-e PIN_SPEC=...` environment variable on the
    `docker run` invocation, then reference `${PIN_SPEC}` inside the
    container script. This is the smallest delta from v2.1.

Apply (b). Modify the `docker run` line (lines 84-87):

Before:
```bash
MSYS_NO_PATHCONV=1 docker run --rm \
  -v "${REPO_DIR}:/work" \
  -w /work \
  python:3.13-slim sh -c '
```

After:
```bash
MSYS_NO_PATHCONV=1 docker run --rm \
  -v "${REPO_DIR}:/work" \
  -w /work \
  -e HERMES_AGENT_PIN_SPEC="${HERMES_AGENT_PIN_SPEC}" \
  python:3.13-slim sh -c '
```

And inside the container script change the pip install line (line 94):

Before:
```bash
    pip install --quiet --no-cache-dir --retries 3 \
        "hermes-agent @ git+https://github.com/NousResearch/hermes-agent.git@v2026.5.16"
```

After:
```bash
    pip install --quiet --no-cache-dir --retries 3 "${HERMES_AGENT_PIN_SPEC}"
```

(The single-quoted heredoc still resolves `${HERMES_AGENT_PIN_SPEC}`
because the container's `sh -c` evaluates it — the docker `-e` flag
exports it into the container's environment. Verified: this is the
standard idiom for passing build-time pins into a single-quoted
container script without escaping nightmares.)

**Acceptance:**
- `bash -n scripts/smoke.sh` — syntax clean.
- `grep -c 'v2026.5.16' scripts/smoke.sh` returns exactly 1 (the
  single variable assignment; the inline reference is gone).
- `bash scripts/smoke.sh --help` still works (the help block
  short-circuits before the docker invocation).
- Default `bash scripts/smoke.sh` (no flags) — if docker is
  available, runs the same install line as before (verified by
  inspection of the substituted env var).

**Commit message:**
```
refactor(16): extract hermes-agent pin tag to HERMES_AGENT_PIN_TAG var

Prep for --cached mode: the pin string needs to be sha256'd for
cache invalidation. Extract to a variable, pass into the docker
container via -e HERMES_AGENT_PIN_SPEC=... so the existing single-
quoted sh -c block doesn't need escaping changes. No behavior
change: same install line, same retry count, same pin value.
```

### T3 — Add `--cached` flag parser + help text

**File:** `scripts/smoke.sh`

Modify the argument parser (lines 39-70):

Before:
```bash
FAST=0
for arg in "$@"; do
  case "$arg" in
    --fast)
      FAST=1
      ;;
    -h|--help)
      cat <<'USAGE'
Usage: scripts/smoke.sh [--fast]

Modes:
  (default)   Full dockerized smoke: fresh python:3.13-slim, pip install
              hermes-agent from git+ tag, install plugin editable, run
              pytest + live-loader. ~60-90s on a warm docker cache.
  --fast      Skip docker; run pytest tests/ against the host venv.
              Assumes hermes-agent + plugin already installed locally.
              Used for local iteration -- NOT a substitute for the full
              smoke before tagging a release. ~10-20s.

Examples:
  bash scripts/smoke.sh                 # release-gate smoke
  bash scripts/smoke.sh --fast          # quick local pytest
USAGE
      exit 0
      ;;
    *)
      echo "smoke.sh: unknown argument: $arg" >&2
      echo "Run 'bash scripts/smoke.sh --help' for usage." >&2
      exit 2
      ;;
  esac
done
```

After:
```bash
FAST=0
CACHED=0
for arg in "$@"; do
  case "$arg" in
    --fast)
      FAST=1
      ;;
    --cached)
      CACHED=1
      ;;
    -h|--help)
      cat <<'USAGE'
Usage: scripts/smoke.sh [--fast] [--cached]

Modes:
  (default)   Full dockerized smoke: fresh python:3.13-slim, pip install
              hermes-agent from git+ tag, install plugin editable, run
              pytest + live-loader. ~60-90s on a warm docker cache.
  --fast      Skip docker; run pytest tests/ against the host venv.
              Assumes hermes-agent + plugin already installed locally.
              Used for local iteration -- NOT a substitute for the full
              smoke before tagging a release. ~10-20s.
  --cached    Cache the hermes-agent wheel at .smoke-cache/ between
              runs. First --cached run populates the cache via
              pip download; subsequent runs install with --no-index
              (no network). Cache invalidates automatically when the
              pinned hermes-agent tag changes. Falls back to a normal
              network install if the cache install fails. No-op in
              --fast mode. ~60-90s first run; ~15-25s subsequent runs.

Examples:
  bash scripts/smoke.sh                       # release-gate smoke
  bash scripts/smoke.sh --fast                # quick local pytest
  bash scripts/smoke.sh --cached              # cached docker smoke
  bash scripts/smoke.sh --cached --fast       # cached flag is a no-op in --fast
USAGE
      exit 0
      ;;
    *)
      echo "smoke.sh: unknown argument: $arg" >&2
      echo "Run 'bash scripts/smoke.sh --help' for usage." >&2
      exit 2
      ;;
  esac
done

# --cached is a no-op in --fast mode (host venv never installs hermes-
# agent fresh). Print a one-line note for clarity, continue normally.
if [ "$CACHED" = "1" ] && [ "$FAST" = "1" ]; then
  echo "smoke.sh: --cached is a no-op in --fast mode (host venv reused)" >&2
fi
```

**Acceptance:**
- `bash -n scripts/smoke.sh` — syntax clean.
- `bash scripts/smoke.sh --help` stdout contains `--cached`.
- `bash scripts/smoke.sh --cached --help` exits 0 (parser handles
  both flags before help short-circuits).
- `bash scripts/smoke.sh --bogus` still exits non-zero with the
  existing "unknown argument" message.
- `bash scripts/smoke.sh --fast --cached --help` exits 0.

**Commit message:**
```
feat(16): add --cached flag parser + help text to smoke.sh

Default off (CACHED=0). Composes with --fast (no-op + warning) and
with the existing flag parser. No-op in --fast mode because the
host-venv path never installs hermes-agent fresh. Cache flow itself
lands in the next commit -- this commit just wires the flag
through the parser so the flag is discoverable and existing flags
keep working.
```

### T4 — Wire the cache flow into the docker path

**File:** `scripts/smoke.sh`

Add a new block AFTER the `--cached`-in-`--fast` warning and BEFORE
the `# --- Fast path: ...` comment, that handles the cache-aware
docker invocation when `CACHED=1` and `FAST=0`:

```bash
# --- Cached docker path: populate .smoke-cache/, install with --no-index --

if [ "$CACHED" = "1" ] && [ "$FAST" = "0" ]; then
  CACHE_DIR="${REPO_DIR}/.smoke-cache"
  PIN_HASH_FILE="${CACHE_DIR}/.pin-hash"

  # Compute current pin sha256 (portable; sha256sum is in busybox + GNU).
  CURRENT_PIN_HASH=$(printf '%s' "$HERMES_AGENT_PIN_TAG" | sha256sum | cut -d' ' -f1)

  # Invalidate cache if pin changed.
  if [ -d "$CACHE_DIR" ] && [ -f "$PIN_HASH_FILE" ]; then
    STORED_PIN_HASH=$(cat "$PIN_HASH_FILE")
    if [ "$STORED_PIN_HASH" != "$CURRENT_PIN_HASH" ]; then
      echo "smoke.sh: hermes-agent pin changed (was ${STORED_PIN_HASH:0:12}, now ${CURRENT_PIN_HASH:0:12}); wiping cache" >&2
      rm -rf "$CACHE_DIR"
    fi
  fi

  mkdir -p "$CACHE_DIR"

  echo "--- smoke --cached: docker + .smoke-cache/ (pin hash ${CURRENT_PIN_HASH:0:12}) ---"

  MSYS_NO_PATHCONV=1 docker run --rm \
    -v "${REPO_DIR}:/work" \
    -w /work \
    -e HERMES_AGENT_PIN_SPEC="${HERMES_AGENT_PIN_SPEC}" \
    -e CURRENT_PIN_HASH="${CURRENT_PIN_HASH}" \
    python:3.13-slim sh -c '
      set -euo pipefail

      apt-get update -qq >/dev/null 2>&1
      apt-get install -y -qq --no-install-recommends git ca-certificates >/dev/null 2>&1

      CACHE_DIR=/work/.smoke-cache
      PIN_HASH_FILE="$CACHE_DIR/.pin-hash"

      # Populate cache if empty (first run or post-invalidation).
      if [ -z "$(ls -A "$CACHE_DIR" 2>/dev/null | grep -v "^\\.pin-hash$" || true)" ]; then
        echo "--- smoke --cached: cache empty, pip download hermes-agent ---"
        pip download --quiet --no-cache-dir --retries 3 \
          -d "$CACHE_DIR" "${HERMES_AGENT_PIN_SPEC}"
        printf "%s" "$CURRENT_PIN_HASH" > "$PIN_HASH_FILE"
      fi

      echo "--- smoke --cached: pip install --no-index from cache ---"
      if ! pip install --quiet --no-cache-dir --no-index \
          --find-links="$CACHE_DIR" hermes-agent ; then
        echo "smoke.sh: cache install failed; falling back to network install + refreshing cache" >&2
        pip install --quiet --no-cache-dir --retries 3 "${HERMES_AGENT_PIN_SPEC}"
        # Refresh cache so the next --cached run is fast again.
        rm -rf "$CACHE_DIR"/*.whl "$CACHE_DIR"/*.tar.gz 2>/dev/null || true
        pip download --quiet --no-cache-dir --retries 3 \
          -d "$CACHE_DIR" "${HERMES_AGENT_PIN_SPEC}"
        printf "%s" "$CURRENT_PIN_HASH" > "$PIN_HASH_FILE"
      fi

      pip install --quiet --no-cache-dir --retries 3 -e ".[dev]"

      echo "--- smoke step 1/3: import chatlytics_hermes.register ---"
      python -c "from chatlytics_hermes import register; print(f\"register OK: {register.__name__}\")"

      echo "--- smoke step 2/3: hermes_agent.plugins entry-point discovery ---"
      python -c "
from importlib.metadata import entry_points
eps = entry_points(group=\"hermes_agent.plugins\")
names = sorted({ep.name for ep in eps})
assert \"chatlytics\" in names, f\"chatlytics not found in entry-points group; got: {names}\"
print(f\"entry-points OK: chatlytics in {names}\")
"

      echo "--- smoke step 3/4: pytest tests/ ---"
      pytest tests/ -q

      echo "--- smoke step 4/4: live-loader integration ---"
      pytest tests/test_live_loader.py -q --no-header --tb=short
      echo "live-loader: chatlytics platform + 21 tools registered"

      echo "--- smoke PASS (cached) ---"
    '
  exit $?
fi
```

The existing `# --- Fast path: ...` and `# --- Default path: ...`
sections stay untouched below this new block. When `CACHED=0`,
control falls through to them exactly as in v2.1. When `CACHED=1
FAST=0`, this new block handles the run and `exit $?` short-
circuits before the default block. When `CACHED=1 FAST=1`, the
fast-path block (next section) takes over (the warning already
printed; `--cached` no-ops as documented).

**Acceptance:**
- `bash -n scripts/smoke.sh` — syntax clean.
- `bash scripts/smoke.sh --cached --help` — exits 0, shows updated
  help.
- Default `bash scripts/smoke.sh` (no flags) — falls through to
  the v2.1 default block (no behavioral diff, no .smoke-cache/
  touched).
- `bash scripts/smoke.sh --fast` — falls through to the fast
  block (no .smoke-cache/ touched).
- `bash scripts/smoke.sh --cached` — would invoke the new cached
  block (not executed in pytest — too heavy; verified by static
  inspection that the block is reachable).
- `grep -c '.smoke-cache' scripts/smoke.sh` returns ≥ 5 (the
  cache logic).

**Commit message:**
```
feat(16): cache hermes-agent wheel at .smoke-cache/ on --cached

First --cached run: pip download hermes-agent to .smoke-cache/,
install from cache. Subsequent --cached runs: install --no-index
only (no network). Pin-hash invalidation auto-wipes the cache when
HERMES_AGENT_PIN_TAG changes. Cache-miss fallback: drops to a
normal network install AND refreshes the cache so the next run is
fast again. Default (non-cached) docker block unchanged.
```

### T5 — Add `--cached` documentation to README.md

**File:** `README.md`

Find (around lines 198-204):

```markdown
`scripts/smoke.sh` runs the package against a fresh `python:3.13-slim`
container -- it installs hermes-agent + this plugin in a clean Python
environment, asserts the `chatlytics` entry point is discoverable, then runs
the full test suite. Use this to validate a release before tagging.
```

Append AFTER this paragraph (before the next `## Architecture notes`
section):

```markdown

For faster local iteration, pass `--cached` to cache the
`hermes-agent` wheel between runs at `.smoke-cache/`:

```bash
bash scripts/smoke.sh --cached
```

The first cached run downloads the wheel; subsequent runs install
from the local cache (no network). The cache invalidates automatically
when the pinned `hermes-agent` tag changes. If the cached install
fails (corrupted wheel, missing dep), the script falls back to a
normal network install and refreshes the cache.
```

(Note: ensure the existing fenced code block's closing backticks are
preserved — append AFTER the closing of the existing block. The
above adds one new paragraph + one new fenced code block.)

**Acceptance:**
- `grep -c '\\-\\-cached' README.md` returns ≥ 2 (one in prose,
  one in the example command).
- Markdown rendering is sane (one new paragraph + one new code
  block, no broken fences).
- `grep -c '\\.smoke-cache' README.md` returns ≥ 1.

**Commit message:**
```
docs(16): document scripts/smoke.sh --cached in README Development

One-paragraph + one-example addition under the Development section.
Documents the user-visible flag; implementation details (pin-hash,
fallback path) live in smoke.sh comments per scope.
```

### T6 — Create `tests/test_smoke_cache.py`

**File:** `tests/test_smoke_cache.py` (CREATE)

```python
"""HERMES-16: scripts/smoke.sh argument-parsing smoke test.

Verifies that the v3.0 ``--cached`` flag landed cleanly in the bash
script's argument parser WITHOUT regressing the existing ``--fast``
flag, the ``--help`` text, or the unknown-flag error path.

Notes:

- We do NOT exercise the actual cached install flow (would require
  docker + ~60s minimum + network) -- pytest stays a unit / static
  guard.
- Tests are skipped on hosts without ``bash`` on PATH (e.g. a
  hypothetical bash-less Windows CI). v2.1's existing smoke.sh has
  always assumed bash availability for the same reason.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SMOKE_SH = REPO_ROOT / "scripts" / "smoke.sh"

# Skip entire module if bash isn't available.
pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="bash not on PATH; skipping smoke.sh argument-parsing tests",
)


def _run(*args: str, timeout: float = 10.0) -> subprocess.CompletedProcess:
    """Invoke ``bash scripts/smoke.sh`` with the given args."""
    return subprocess.run(
        ["bash", str(SMOKE_SH), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(REPO_ROOT),
    )


def test_smoke_sh_passes_bash_syntax_check() -> None:
    """``bash -n scripts/smoke.sh`` must parse cleanly with --cached added."""
    result = subprocess.run(
        ["bash", "-n", str(SMOKE_SH)],
        capture_output=True,
        text=True,
        timeout=5.0,
    )
    assert result.returncode == 0, (
        f"smoke.sh failed bash syntax check: {result.stderr}"
    )


def test_help_text_documents_cached_flag() -> None:
    """``--help`` output must mention --cached so the flag is discoverable."""
    result = _run("--help")
    assert result.returncode == 0, (
        f"smoke.sh --help should exit 0; got {result.returncode}: {result.stderr}"
    )
    assert "--cached" in result.stdout, (
        f"smoke.sh --help should document --cached; got:\n{result.stdout}"
    )


def test_unknown_flag_still_rejected() -> None:
    """Regression guard: unknown flags still exit non-zero with a message."""
    result = _run("--bogus-flag-that-does-not-exist")
    assert result.returncode != 0, (
        "smoke.sh should reject unknown flags with non-zero exit"
    )
    assert "unknown argument" in result.stderr.lower() or "unknown" in result.stderr.lower(), (
        f"smoke.sh should print an unknown-argument message; got stderr:\n{result.stderr}"
    )


def test_cached_and_fast_compose_in_help() -> None:
    """``--fast --cached --help`` must exit 0 (parser handles both flags)."""
    result = _run("--fast", "--cached", "--help")
    assert result.returncode == 0, (
        f"smoke.sh --fast --cached --help should exit 0; "
        f"got {result.returncode}: stderr={result.stderr!r}"
    )
    # The --cached-in-fast-mode warning may also fire here; that's
    # fine and not asserted (stderr-noise tolerant).
```

**Acceptance:**
- `pytest tests/test_smoke_cache.py -q` — 4 passed.
- `pytest tests/ -q` — 120 passed (116 baseline + 4 new).
- The module is skipped (not failed) on hosts without bash.

**Commit message:**
```
test(16): add tests/test_smoke_cache.py for --cached arg parsing

Four lightweight subprocess tests verifying:
  1. bash -n syntax check passes with --cached added
  2. --help text documents --cached (discoverability)
  3. unknown flag still rejected (regression guard on existing parser)
  4. --fast --cached --help composes cleanly

Pure argument-parsing verification; does NOT exercise the actual
docker cache flow (too heavy for pytest). Skips the whole module
if bash is not on PATH.
```

### T7 — Run full pytest + cross-verify invariants

**No file change.** Verification-only commit gate.

Run:
```bash
python -m pytest tests/ -q --no-header
```

Expected: **120 passed** (116 v3.0-Phase-15 baseline + 4 new
`test_smoke_cache.py` tests).

Cross-verify (via short python snippets in commit message body or
just by inspection):
- `from chatlytics_hermes.tools import TOOLS; len(TOOLS) == 21`
- `bash -n scripts/smoke.sh` exits 0
- `git check-ignore .smoke-cache/foo` exits 0
- Default `bash scripts/smoke.sh --help` works
- `bash scripts/smoke.sh --cached --help` works
- `bash scripts/smoke.sh --fast --help` works

If all green, write `16-VERIFICATION.md` (handled by the verify
step of the autonomous workflow; not a separate commit here).

**Acceptance:**
- pytest 120/120 passing.
- All v3.0-so-far invariants preserved.

## Risks & mitigations

| Risk | Mitigation |
|------|-----------|
| Single-quoted `sh -c '...'` block doesn't expand `${HERMES_AGENT_PIN_SPEC}` | Pass via `docker run -e VAR=...` so the container's `sh` resolves it at runtime — standard idiom, no shell-quoting escapes. |
| `pip download` pulls Linux-incompatible wheels on Windows host | `pip download` is run INSIDE the container (linux/amd64 python:3.13-slim) — wheels are arch-correct. |
| `.smoke-cache/.pin-hash` orphaned if user manually rm's wheels but leaves the hash | Cache-miss fallback handles this — `pip install --no-index` fails on empty cache, fallback runs `pip download` + rewrites the hash. |
| Existing default `bash scripts/smoke.sh` accidentally takes the cached branch | The cached block is guarded by `if [ "$CACHED" = "1" ] && [ "$FAST" = "0" ]`. Default `CACHED=0` falls through unchanged. Verified by T2 acceptance (default flow stays byte-identical) + T7 pytest. |
| `pip download` for a git+ spec writes a `.tar.gz` instead of a `.whl` | `pip install --no-index --find-links=` handles both formats. The cache lookup is filename-agnostic. |
| `bash` not on PATH on a future Windows CI runner | `test_smoke_cache.py` skips itself; smoke.sh has always needed bash, so this is consistent with the existing posture. |
| `.gitignore` already exists and gets clobbered | T1 verifies file doesn't exist at repo root first; if it somehow does, append `.smoke-cache/` rather than overwriting. (Audited 2026-05-18: no top-level .gitignore exists.) |

## Deferred

**None** — scope is locked. No CI integration, no pre-built docker
base image, no promotion of `--retries` to a CLI flag, no broader
gitignore template, no relocation of smoke.sh.
