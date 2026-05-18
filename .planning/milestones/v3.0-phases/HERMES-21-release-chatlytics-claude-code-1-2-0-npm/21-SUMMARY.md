---
phase: 21
phase_name: Release chatlytics-claude-code 1.2.0 (npm)
part: A
status: implemented
mode: infra-skip
type: cross-repo + release-prep
date: 2026-05-18
implemented_by: claude-opus-4-7-1m
sibling_repo: C:/Users/omern/.claude/plugins/marketplaces/chatlytics-claude-code/
sibling_repo_commits: 1
python_repo_commits: 3
package_name_before: chatlytics-claude-code
package_name_after: "@chatlytics/claude-code"
private_before: true
private_after: false
files_allowlist_added: true
publish_config_added: true
engines_added: true
author_added: true
npm_pack_dry_run: ok
npm_pack_files_count: 9
npm_pack_tarball_kb: 147.7
npm_publish_dry_run: ok
npm_view_status: 404_available
npm_whoami: omernesh
bundle_current: true
review_blockers: 0
review_high: 0
review_med: 0
review_low: 0
review_info: 1
fix_pass_invoked: false
phase_21_part_b_scope_guard_preserved: true
release_ready: true
next_action: operator go/no-go for PART B real npm publish
---

# HERMES-21 PART A — Summary

## Outcome

**Implemented.** The chatlytics-claude-code root `package.json` is
flipped to public, renamed to `@chatlytics/claude-code` (scoped under
the operator's `@chatlytics` org), and given a `files:` allowlist
that ships exactly 9 files at 147.7 kB packed. Both
`npm pack --dry-run` and `npm publish --dry-run --access=public`
pass cleanly. The `@chatlytics/claude-code` name is still 404 on
the registry. The npm session is authenticated as `omernesh`.

PART A is **release-ready**. PART B (operator-gated) runs the real
`npm publish`, post-publish install verification, `v1.2.0`
annotated tag, and pushes tag + main in the sibling repo.

## Reality vs brief (PART A)

| Brief assumption | Reality | Resolution |
|---|---|---|
| `servers/package.json` may also need flipping | `servers/` is NOT a separate npm publish target; npm only publishes the root manifest. The `servers/` tree ships INSIDE the root tarball via the root `files:` allowlist. | LEFT `servers/package.json` UNCHANGED (still `private: true`, still `name: "chatlytics-mcp"` — internal sub-package). Per D3. |
| `.npmignore` should be created | npm honors `files:` over `.npmignore` when both exist; creating `.npmignore` would be redundant. | Did NOT create `.npmignore`. The Phase 20 npm warning about its absence is silenced once `files:` is present. Per D11. |
| `engines.node` may need to be added | Was absent; npm warns at publish time if missing. | ADDED `"engines": {"node": ">=18"}` to match the esbuild `target=node18` build setting. |
| Phase 20 left a dirty `servers/package-lock.json` | Yes — Phase 20's `npm install` populated the lockfile but didn't commit it. | COMMITTED in T6 alongside the manifest flips (single commit). |
| `repository.url` may need normalization | `https://...` form triggers npm warning auto-correcting to `git+https://...`. | Pre-corrected to `git+https://...` in the same commit. Silences warning. |

## Sibling repo commits (1)

| Commit | Task | Files |
|---|---|---|
| `270b23e` | T6 — manifest flips + lockfile carry-over | `package.json` (+24/-5), `servers/package-lock.json` (+472/-3) |

Snapshot of sibling repo `git log --oneline -5` after PART A:

```
270b23e chore(npm): prepare 1.2.0 for first public publish under @chatlytics org
dadc82a docs(v1.2.0): CHANGELOG 1.2.0 — coordination with chatlytics-hermes 3.0.0...
7eafe94 build(v1.2.0): rebuild esbuild bundle with version bump...
b29818e fix(v1.2.0): chatlytics_send now resolves bare names/phones via resolveChatId...
9a6a41a release(v1.2.0): bump version to 1.2.0 across root pkg...
```

Sibling repo is now **9 commits ahead** of `origin/main` (4 from
Phase 20 + 1 from Phase 21 PART A + 4 prior Phase 19 / 1.1.x). PART
B pushes all 9 plus the `v1.2.0` tag.

## Python repo commits (3)

| Commit | Task | Files |
|---|---|---|
| `80c3734` | T0 — CONTEXT (infra-skip) | `.planning/phases/HERMES-21-*/21-CONTEXT.md` |
| `fac30d9` | T0.5 — PLAN 1 | `.planning/phases/HERMES-21-*/21-PLAN-1-npm-manifest-prep-and-dry-run.md` |
| (this) | T8 — SUMMARY + REVIEW + NPM-READY | 3 files in `.planning/phases/HERMES-21-*/` |

## Task results (T1-T7)

### T1 — Pre-flight re-verification — PASSED
- `npm whoami` → `omernesh`
- `npm view @chatlytics/claude-code` → 404 (name available)
- Bundle mtime (13:43) newer than source (13:42) — current
- `git status` (sibling) showed only `servers/package-lock.json`
  dirty (expected — Phase 20 artifact)
- `git log --oneline -5` (sibling) confirmed 4 Phase 20 commits
  at HEAD

### T2 — Apply root `package.json` flips — PASSED
- JSON valid (parses)
- `name`: `"@chatlytics/claude-code"` (was `"chatlytics-claude-code"`)
- `private`: `false` (was `true`)
- `files`: 8-entry allowlist added
- `publishConfig.access`: `"public"` added
- `engines.node`: `">=18"` added
- `author`: `"Omer Nesher <omernesher@gmail.com>"` added
- `repository.url`: normalized `https://...` → `git+https://...`

### T3 — `npm pack --dry-run` — PASSED
- Exit 0
- 9 files in tarball:
  1. `CHANGELOG.md` (7.4 kB)
  2. `LICENSE` (1.1 kB)
  3. `QUICKSTART.md` (5.9 kB)
  4. `README.md` (3.3 kB)
  5. `package.json` (932 B)
  6. `servers/chatlytics-mcp.bundle.js` (731.6 kB)
  7. `servers/chatlytics-mcp.js` (12.9 kB)
  8. `servers/package.json` (454 B)
  9. `skills/chatlytics/SKILL.md` (3.6 kB)
- Packed size: **147.7 kB** (gzip-compressed)
- Unpacked size: 767.1 kB
- **EXCLUSIONS VERIFIED:** No `servers/node_modules/`, no
  `servers/test/`, no `servers/package-lock.json`, no
  `SUBMISSION.md`, no `.git/`, no `node_modules/`

### T4 — `npm publish --dry-run --access=public` — PASSED
- Exit 0
- Final line: `+ @chatlytics/claude-code@1.2.0`
- File list identical to T3
- One initial warning about `repository.url` normalization →
  pre-fixed before final commit, second dry-run clean

### T5 — Name still 404 — PASSED
- `npm view @chatlytics/claude-code` → still 404 after T2-T4
  manifest changes (no race-condition publish from another
  account)

### T6 — Sibling repo commit — PASSED
- Commit `270b23e` on `main`
- 2 files changed (only `package.json` + `servers/package-lock.json`)
- Working tree clean post-commit

### T7 — Post-commit revalidation — PASSED
- `npm pack --dry-run` still exit 0, same 9 files, same 147.7 kB
- `npm publish --dry-run --access=public` still exit 0, same
  `+ @chatlytics/claude-code@1.2.0` final line
- No new warnings (repository.url normalization fix held)

## Acceptance gates (10/10)

| # | Gate | Result |
|---|---|---|
| 1 | T1 pre-flight gates passed | PASS |
| 2 | Manifest valid JSON, all required flips present | PASS |
| 3 | `npm pack --dry-run` exit 0 + clean file list | PASS |
| 4 | `npm publish --dry-run --access=public` exit 0 | PASS |
| 5 | Name still 404 after manifest changes | PASS |
| 6 | Sibling repo commit succeeds (1 commit, 2 files) | PASS (`270b23e`) |
| 7 | Post-commit revalidation passes | PASS |
| 8 | Python repo bookkeeping artifacts written + committed | PASS (this SUMMARY commit) |
| 9 | Telegrams sent (implemented + dry-run + review) | PASS |
| 10 | Phase 21 PART B scope guard preserved | PASS — no `npm publish`, no `v1.2.0` tag, no push |

## Phase 21 PART B scope guard (verified preserved)

The following are STILL OPEN for PART B (operator-gated):

- [ ] `npm publish --access=public` (real publish; PART A used `--dry-run` only)
- [ ] Post-publish `npm install @chatlytics/claude-code` smoke test
      in scratch directory
- [ ] `git tag -a v1.2.0 -m "..."` in sibling repo
- [ ] `git push origin main` (9 commits) in sibling repo
- [ ] `git push origin v1.2.0` in sibling repo
- [ ] `@chatlytics` org publish-rights verification (only via
      real publish attempt; granular token can't introspect orgs)
- [ ] npm page render check at
      https://www.npmjs.com/package/@chatlytics/claude-code

## Known imperfection (deferred to follow-up patch)

- `scripts.postinstall` still runs `npm --prefix servers install`
  on every consumer install. The published bundle has zero runtime
  deps (esbuild `--packages=bundle` inlines `@modelcontextprotocol/sdk`
  + `zod`), so this postinstall is wasteful (it installs dev/test
  deps the consumer never uses). Removing it is a behavior change
  beyond PART A's manifest-prep scope. Recommend absorbing into
  PART B or scheduling a v1.2.1 patch phase.

## Invariants preserved

- **Python repo:** 120/120 tests still pass; `v3.0.0` tag on
  `origin`; `chatlytics-hermes 3.0.0` LIVE on PyPI; `src/` and
  `tests/` UNCHANGED in PART A
- **Sibling JS repo:** Bundle (`servers/chatlytics-mcp.bundle.js`)
  UNCHANGED (mtime + sha — verified by diff in T6 commit:
  only `package.json` + lockfile)
- **8 tools registered** (no count change)
- **`looksLikeJid` regex** UNCHANGED
- **`resolveChatId` call sites** UNCHANGED (2 — send + read)
- **`servers/package.json`** UNCHANGED (still `private: true`,
  still `name: "chatlytics-mcp"`)

## Cross-repo coordination invariant

Going forward (after PART B publishes), the public surface of the
two coordinated plugins is:

```
PyPI:  chatlytics-hermes 3.0.0      (Python)
npm:   @chatlytics/claude-code 1.2.0 (JS — pending PART B)
```

Both ship from the operator's accounts:
- PyPI: omernesh@gmail.com (token in `~/.pypirc[pypi]`)
- npm: omernesh (token in `~/.npmrc`, granular access)

## Next action

**Operator decision required:** go/no-go on Phase 21 PART B (the
real `npm publish --access=public` + post-publish verification +
`v1.2.0` tag + push tag + push main IN THE SIBLING REPO).

All PART A validation gates passed — recommended verdict: **GO**.
Spawn PART B in a new run.
