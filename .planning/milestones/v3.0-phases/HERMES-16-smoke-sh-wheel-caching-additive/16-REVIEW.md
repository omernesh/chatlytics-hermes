---
phase: 16
review_status: passed-with-nits
reviewed_by: gsd-code-review (inline, infra-only phase)
review_date: 2026-05-18
files_reviewed: 4
findings_blocker: 0
findings_high: 0
findings_med: 0
findings_low: 2
findings_info: 2
recommendation: ship-with-optional-nit-cleanup
---

# HERMES-16 — Code Review

## Scope

Phase 16 is **infra-only**. No source code in `src/chatlytics_hermes/`
was touched (verified via `git diff 0dffea0..HEAD --stat`). Review
focused on:

- `scripts/smoke.sh` (~125 lines added/modified)
- `tests/test_smoke_cache.py` (95 lines, new)
- `.gitignore` (1 line, new file)
- `README.md` (13 lines appended)

## Summary

**0 BLOCKER, 0 HIGH, 0 MED, 2 LOW, 2 INFO.** All Phase 13/14/15
contracts preserved (zero source code touched). 120/120 tests pass.
21-tool invariant intact. Default `bash scripts/smoke.sh` (no flags)
behaviorally byte-equivalent to v2.1 — only refactor is
`v2026.5.16` literal extracted to `$HERMES_AGENT_PIN_TAG` variable,
which is exercised in both the default block and the new cached
block (single source of truth for the pin).

**Verdict: SHIP.** Optional nit cleanup (LOW-01, LOW-02) can land in
Phase 18 (cosmetics sweep) — neither blocks Phase 16 close.

## Findings

### LOW-01 — `scripts/smoke.sh` header comment doesn't mention `--cached`

**File:** `scripts/smoke.sh` lines 20-23.

The module header comment block lists usage examples:

```
# Usage:
#   bash scripts/smoke.sh            # full dockerized smoke (release path)
#   bash scripts/smoke.sh --fast     # host-venv pytest only (local iteration)
#   bash scripts/smoke.sh --help     # show this help
```

The `--help` output (lines 58-82) was correctly updated to document
`--cached` with an example. The header comment was NOT — minor doc
drift. A reader skimming the file top sees the v2.1 surface; they
have to read the `--help` heredoc or grep for `CACHED=` to discover
the new flag.

**Severity:** LOW (cosmetic; `--help` is the authoritative source
and is correct).

**Fix:** Add one line under `--help`:
```
#   bash scripts/smoke.sh --cached   # cached docker smoke (HERMES-16)
```

**Recommendation:** DEFER to Phase 18 (cosmetics sweep). Not phase-
blocking.

### LOW-02 — Pre-existing smoke step numbering inconsistency carried forward

**File:** `scripts/smoke.sh` lines 175, 187, 190 (new cached block)
and lines 216, 228, 231 (pre-existing default block).

Both blocks echo "smoke step 1/3", "smoke step 3/4", "smoke step
4/4" — the count is inconsistent (should be 1/4..4/4). This is a
pre-existing v2.1 bug copied verbatim into the new cached block to
keep the two blocks structurally identical.

**Severity:** LOW (cosmetic; doesn't affect smoke pass/fail).

**Fix:** Renumber both blocks to 1/4, 2/4, 3/4, 4/4.

**Recommendation:** DEFER to Phase 18. Not phase-blocking; not
introduced by Phase 16 (only propagated).

### INFO-01 — `CURRENT_PIN_HASH` env var is intentional, not dead

**File:** `scripts/smoke.sh` line 142.

`docker run -e CURRENT_PIN_HASH="${CURRENT_PIN_HASH}"` passes the
pin hash into the container. Used inside the container at lines 158
and 170 to write `.pin-hash` after a successful download. Verified
in-scope.

**Severity:** INFO (no action; review note documenting that the
env var is load-bearing).

### INFO-02 — `PIN_HASH_FILE` declared in two scopes (host + container)

**File:** `scripts/smoke.sh` lines 120 and 150.

Host scope (line 120) declares
`PIN_HASH_FILE="${CACHE_DIR}/.pin-hash"` — used at lines 126-127
for the host-side staleness check (read stored hash, compare to
current, wipe cache if mismatched).

Container scope (line 150) re-declares
`PIN_HASH_FILE="$CACHE_DIR/.pin-hash"` — used at lines 158 and 170
for in-container hash file writes after `pip download`.

The two declarations are necessary (host bash and container `sh -c`
are separate processes with separate variable scopes; the single-
quoted heredoc doesn't expand host vars). Slight repetition but the
alternative (passing PIN_HASH_FILE via another `-e`) buys nothing
because `CACHE_DIR` differs between host (`${REPO_DIR}/.smoke-cache`)
and container (`/work/.smoke-cache`).

**Severity:** INFO (no action; review note documenting the
intentional duplication).

## Invariants verified

- `assert len(TOOLS) == 21` — passes.
- Hermes pin `>=0.14,<0.15` — unchanged (only smoke.sh's docker
  install line uses the variable; pyproject.toml constraint
  untouched).
- 116 baseline tests + 4 new tests = 120/120 passing.
- Default `bash scripts/smoke.sh` (no flags) — falls through to the
  v2.1-equivalent docker block (the only change is the install line
  references `${HERMES_AGENT_PIN_SPEC}` env var instead of the inline
  literal; same effective install command).
- `bash scripts/smoke.sh --fast` — host-venv pytest short-circuit at
  line 101-105 unchanged.
- HI-01 allowlist (`CHATLYTICS_UPLOAD_ALLOWED_ROOTS`) — not touched
  (no source code changes).
- Phase 13 `_error` sentinel — not touched.
- Phase 14 strict JID regex — not touched.
- Phase 15 `send_*` collapse — not touched.

## Security check

- **Cache directory traversal:** `.smoke-cache/` is repo-relative
  via `${REPO_DIR}/.smoke-cache`. `REPO_DIR` is derived from
  `BASH_SOURCE[0]` (the script's own location), not from user
  input. Path injection not possible.
- **Pin-hash file integrity:** `.pin-hash` is a sha256 hex string
  the script writes itself; not externally writable in normal use.
  If an attacker DID write a malformed file, the comparison at line
  128 would either match (no-op) or mismatch (cache wiped — safe).
  Worst case: cache forced to repopulate from network. No code
  execution path.
- **Docker `-e` env var injection:** `HERMES_AGENT_PIN_SPEC` and
  `CURRENT_PIN_HASH` are derived from the in-script
  `HERMES_AGENT_PIN_TAG` constant; not user-supplied. Safe.
- **`pip install --no-index --find-links=`:** Restricts pip to the
  local cache directory — prevents accidental network installs
  during the cached path. Defense in depth.
- **`pip download` from git+ tag:** Same source as the default path
  (`hermes-agent @ git+...@v2026.5.16`); same trust boundary.

## Performance check

- First `--cached` run: same wall time as default (~60-90s; the
  `pip download` cost is equivalent to `pip install` from git).
  Plus a small ~1s overhead for cache populate + hash write.
- Subsequent `--cached` runs: `pip install --no-index` from a local
  cache; eliminates the ~30-60s git clone + build step. Net win.
- Cache invalidation: O(1) string comparison of two sha256 hashes;
  negligible.

## Test coverage

- 4 new tests in `tests/test_smoke_cache.py`:
  1. `test_smoke_sh_passes_bash_syntax_check` — `bash -n` clean.
  2. `test_help_text_documents_cached_flag` — `--help` mentions
     `--cached`.
  3. `test_unknown_flag_still_rejected` — `--bogus` → non-zero +
     stderr "unknown".
  4. `test_cached_and_fast_compose_in_help` — `--fast --cached
     --help` exits 0.
- The actual docker cache flow is NOT exercised in pytest (would
  require docker + ~60s + network on every test run). This is the
  documented scope per 16-CONTEXT.md D6 ("Do NOT run the actual
  smoke flow in pytest — too heavy. Just verify the script's
  argument parsing accepts the new flag without breaking existing
  flags.").

## Recommendation

**SHIP Phase 16 as-is.** Two LOW nits (header comment + pre-
existing step-numbering bug) are optional cleanup candidates for
Phase 18 (cosmetics sweep). Neither blocks the phase or affects
any acceptance criterion.

No BLOCKER, HIGH, or MED findings. No fix-pass required.
