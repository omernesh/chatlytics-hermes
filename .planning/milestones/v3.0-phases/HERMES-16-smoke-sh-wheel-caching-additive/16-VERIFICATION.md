---
phase: 16
verification_status: passed
implemented_by: gsd-execute-phase
tests_total: 120
tests_passed: 120
files_changed: 4
commits: 6
---

# HERMES-16 — Verification

## Test results

```
$ python -m pytest tests/ -q --no-header
120 passed in 27.81s
```

**Baseline before phase:** 116 tests (88 v2.1 + 10 Phase 13 + 13 Phase 14 + 5 Phase 15).
**Tests added this phase:** +4 (`tests/test_smoke_cache.py` —
`TestResourceAutoDetection`-style class with 4 lightweight subprocess
checks of smoke.sh argument parsing).

**Net delta:** 116 → 120 (+4). All baseline tests still pass.

## Acceptance criteria (per ROADMAP HERMES-16)

| # | Criterion | Status |
|---|-----------|--------|
| 1 | `bash scripts/smoke.sh` (no args) — behaves exactly as v2.1 (`--retries 3`, no caching) | PASS — default docker block is byte-equivalent. Only refactor: `v2026.5.16` literal extracted to `$HERMES_AGENT_PIN_TAG` and passed via `docker run -e HERMES_AGENT_PIN_SPEC=...`. Same retry count, same install line shape. |
| 2 | `bash scripts/smoke.sh --cached` (first run) — downloads to `.smoke-cache/`, then installs from cache, runs tests | PASS (by static inspection) — new cached docker block at smoke.sh:108-191. `pip download` populates the cache when empty, writes `.pin-hash`, then `pip install --no-index --find-links=` installs from cache. Live execution deferred (network + docker; the pytest suite verifies argument parsing only per scope). |
| 3 | `bash scripts/smoke.sh --cached` (second run) — installs from cache only (network calls down by ≥ 90%) | PASS (by static inspection) — cache-populated branch skips `pip download` (only runs when cache dir is empty / pin-hash mismatched). Cached `pip install --no-index` makes no network calls. |
| 4 | `bash scripts/smoke.sh --cached --fast` — works (composes with v2.1's `--fast` flag) | PASS — `test_cached_and_fast_compose_in_help` verifies parser accepts both. Runtime behavior: `--cached` is a documented no-op in `--fast` mode (host venv reused); a one-line stderr notice fires for clarity. |
| 5 | Cache miss (e.g., delete .smoke-cache/wheel, re-run) — falls back to network gracefully | PASS (by static inspection) — cached docker block wraps `pip install --no-index` in `if ! ... ; then` and on failure falls through to `pip install --retries 3` network install AND re-runs `pip download` to refresh the cache so the next run is fast again. |

## Sanity introspection

```
$ bash -n scripts/smoke.sh
(exit 0 — syntax clean with --cached added)

$ bash scripts/smoke.sh --help | grep -c -- '--cached'
5

$ bash scripts/smoke.sh --bogus 2>&1; echo $?
smoke.sh: unknown argument: --bogus
Run 'bash scripts/smoke.sh --help' for usage.
2

$ git check-ignore .smoke-cache/foo; echo $?
.smoke-cache/foo
0

$ PYTHONPATH=src python -c "from chatlytics_hermes.tools import TOOLS; print(len(TOOLS))"
21
```

## Invariants preserved

- `assert len(TOOLS) == 21` — passes (no new tools; infra-only phase).
- Hermes pin `>=0.14,<0.15` — unchanged.
- All HTTP outbound via `httpx`; aiohttp only for inbound server —
  unchanged.
- Phase 13 contract (`{success: false, error, _error: "<code>"}` on
  `chatlytics_get_chat_info`) — unchanged.
- Phase 14 strict JID regex on chatId schemas — unchanged.
- Phase 15 `send_*` collapse + `_enforce_upload_allowlist` — unchanged.
- v2.1 `--fast` flag still works — host-venv pytest short-circuit at
  smoke.sh:101-105 unchanged.
- v2.1 `--retries 3` on pip install calls still in place — both in
  the default docker block and in the new cached block's network
  fallback path.
- Default `bash scripts/smoke.sh` (no flags) — falls through to the
  v2.1 default docker block (cached block guarded by `if CACHED=1
  AND FAST=0`).

## Commits

```
e7f3db8 test(16): add tests/test_smoke_cache.py for --cached arg parsing
9dcb85c docs(16): document scripts/smoke.sh --cached in README Development
18154e9 feat(16): cache hermes-agent wheel at .smoke-cache/ on --cached
28249cf feat(16): add --cached flag parser + help text to smoke.sh
119ae9e refactor(16): extract hermes-agent pin tag to HERMES_AGENT_PIN_TAG var
059c6ad chore(16): gitignore .smoke-cache/ wheel-cache directory
```

(6 task commits, matching the 6 implementation tasks in the plan;
T7 was verification-only with no separate commit.)

## Files changed

- `.gitignore` — CREATED at repo root with single entry
  `.smoke-cache/`.
- `scripts/smoke.sh` — (1) new `HERMES_AGENT_PIN_TAG` +
  `HERMES_AGENT_PIN_SPEC` variables at top; (2) `--cached` flag in
  argument parser; (3) updated `--help` USAGE heredoc documenting
  `--cached` with example; (4) one-line stderr warning when
  `--cached` is passed with `--fast`; (5) new ~90-line cached docker
  block (`if CACHED=1 AND FAST=0`) that handles pin-hash
  invalidation, `pip download` cache population, `pip install
  --no-index` cache install, and network-install + cache-refresh
  fallback on cache-miss; (6) default docker block now references
  `${HERMES_AGENT_PIN_SPEC}` env var (passed via `docker run -e
  ...=...`) instead of the inline pin literal — same effective
  install line.
- `README.md` — new paragraph + code block under Development section
  documenting `--cached`.
- `tests/test_smoke_cache.py` — CREATED. Four sync subprocess tests
  verifying bash syntax, `--help` mentions `--cached`, `--bogus`
  still rejected, and `--fast --cached --help` composes. Uses
  `shutil.which("bash")` to resolve the bash executable explicitly
  (avoids subprocess picking up an unconfigured WSL `bash.exe` on
  Windows hosts).

## Out-of-scope changes

None. Scope locked to the wheel caching feature per 16-CONTEXT.

## Deviations from plan

**T2 — minor:** The plan's acceptance criterion said `grep -c
'v2026.5.16' scripts/smoke.sh` should return exactly 1. Actual is 2:
one in the variable assignment at line 42, one in the module header
comment at line 5 ("from the v2026.5.16 tag"). The header comment
is documentation, not active code — leaving it as-is preserves the
phase's documentary history. The intent of the acceptance criterion
(no inline pin in the install command) is satisfied: the install
line now references `${HERMES_AGENT_PIN_SPEC}` only.

**T6 — defensive strengthening:** The plan used bare
`["bash", ...]` in subprocess calls. On the Windows host this
resolved to a WSL `bash.exe` that hangs without a configured WSL
distro (4/4 tests timed out). Switched to
`shutil.which("bash")` to get the same bash a developer terminal
uses (Git Bash at `C:\Program Files\Git\usr\bin\bash.EXE`). Bumped
the subprocess timeouts from 5/10s to 30s as a defensive belt-and-
suspenders measure (actual runtime is ~0.1s per call). The skip
sentinel (`pytestmark = pytest.mark.skipif(_BASH is None, ...)`) is
preserved.

## Notes for review

- The new cached docker block (smoke.sh:108-191) was authored to be
  byte-identical to the default block at smoke.sh:201-237 in its
  plugin-install + pytest + entry-point-discovery steps. Only the
  hermes-agent install path differs (cache-then-network vs
  network-only). This keeps the cached path a faithful drop-in
  rather than a divergent flow.
- The `printf '%s'` for sha256 computation (line 117) deliberately
  uses `printf` instead of `echo -n` because `echo -n` is not
  portable across `/bin/sh` variants — `printf` is POSIX.
- The cache-population check `ls -A | grep -v "^\.pin-hash$"`
  (smoke.sh:159) treats a directory containing only `.pin-hash` as
  empty — this happens after pin-hash mismatch + `rm -rf "$CACHE_DIR"`
  + `mkdir -p` recreates the dir, then we re-populate. Avoids a
  spurious cache-hit attempt against an empty wheel dir.
- `tests/test_smoke_cache.py` does NOT exercise the actual docker
  cache flow — that would require docker + ~60s + network on every
  pytest run. The scope is argument-parsing verification only, per
  16-CONTEXT D6.
