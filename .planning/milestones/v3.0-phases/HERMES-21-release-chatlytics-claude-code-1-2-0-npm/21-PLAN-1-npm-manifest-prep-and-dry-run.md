---
phase: 21
plan: 1
plan_name: NPM manifest prep + dry-run validation (PART A)
status: ready
date: 2026-05-18
part: A
sibling_repo: C:/Users/omern/.claude/plugins/marketplaces/chatlytics-claude-code/
---

# Phase 21 PART A — Plan 1: NPM manifest prep + dry-run validation

## Goal

Prepare the `chatlytics-claude-code` JS bundle (currently `1.2.0`,
`private: true`, name `chatlytics-claude-code`) for first public
npm publish under the operator's `@chatlytics` org. Validate the
manifest + tarball via `npm pack --dry-run` and
`npm publish --dry-run --access=public`. Halt before real publish
— PART B owns it.

## Strategy

Single linear task sequence (T1..T9), all in the sibling JS repo
except final `.planning/` SUMMARY + NPM-READY commits in the
Python repo. ONE sibling-repo commit total. Each task has a clear
gate; failure halts immediately with `blocker` status.

## Tasks

### T1 — Pre-flight re-verification (no commit)

Re-verify the conditions the orchestrator checked before context-write.
Catches any drift between pre-check and actual execution.

**Commands (cd sibling repo first):**
```bash
cd "C:/Users/omern/.claude/plugins/marketplaces/chatlytics-claude-code/"
npm whoami
npm view @chatlytics/claude-code 2>&1 | head -5  # expect 404
stat -c '%y' servers/chatlytics-mcp.bundle.js
stat -c '%y' servers/chatlytics-mcp.js
git status
git log --oneline -5
```

**Gates:**
1. `npm whoami` exits 0 with `omernesh`
2. `npm view @chatlytics/claude-code` reports 404 (name available)
3. Bundle mtime > source mtime (bundle current)
4. `git status` shows only `servers/package-lock.json` modified
   (Phase 20 artifact) — NO other unexpected dirty files
5. `git log --oneline -5` shows the 4 Phase 20 commits at HEAD
   (`dadc82a`, `7eafe94`, `b29818e`, `9a6a41a`)

**Failure mode:** Any gate failing → halt with `blocker`, write
the failing condition to NPM-READY.md `blockers_for_release`, skip
T2-T9.

---

### T2 — Apply root `package.json` flips (single edit, no commit yet)

Apply ALL D2 + D6 manifest edits to the root `package.json` in
ONE edit. The final manifest shape (after edit):

```json
{
  "name": "@chatlytics/claude-code",
  "version": "1.2.0",
  "private": false,
  "description": "Chatlytics Claude Code plugin — MCP server + skill for WhatsApp via Chatlytics REST API",
  "license": "MIT",
  "author": "Omer Nesher <omernesher@gmail.com>",
  "homepage": "https://github.com/omernesh/chatlytics-claude-code",
  "repository": {
    "type": "git",
    "url": "https://github.com/omernesh/chatlytics-claude-code.git"
  },
  "engines": {
    "node": ">=18"
  },
  "publishConfig": {
    "access": "public"
  },
  "files": [
    "servers/chatlytics-mcp.bundle.js",
    "servers/chatlytics-mcp.js",
    "servers/package.json",
    "skills/",
    "README.md",
    "CHANGELOG.md",
    "LICENSE",
    "QUICKSTART.md"
  ],
  "scripts": {
    "postinstall": "npm --prefix servers install",
    "setup": "npm --prefix servers install",
    "test": "npm --prefix servers test",
    "smoke": "npm --prefix servers test"
  }
}
```

**Note on `scripts.postinstall`:** This currently runs
`npm --prefix servers install` on every consumer install. With
the published bundle being zero-runtime-deps (esbuild
`--packages=bundle` inlined everything), this postinstall is
WASTEFUL for consumers. **BUT** removing it is a behavior change
beyond manifest-prep scope (Phase 21 PART A is manifest-only).
Document this as a known imperfection in NPM-READY.md for a
follow-up phase (could land in a v1.2.1 patch). Do NOT remove
the postinstall script in PART A.

**Gates:**
1. JSON is valid (parses with `node -e "JSON.parse(require('fs').readFileSync('package.json'))"`)
2. `private` is `false`
3. `name` is `"@chatlytics/claude-code"`
4. `files` array has exactly the 8 entries above
5. `publishConfig.access` is `"public"`
6. `engines.node` is `">=18"`

---

### T3 — `npm pack --dry-run` validation (no commit)

Run `npm pack --dry-run` from the sibling repo root with the
manifest changes IN PLACE (uncommitted in working tree). Capture
full output.

**Commands:**
```bash
cd "C:/Users/omern/.claude/plugins/marketplaces/chatlytics-claude-code/"
npm pack --dry-run 2>&1 | tee /tmp/npm-pack-dry-run.txt
```

**Gates:**
1. Exit code 0
2. Output contains `@chatlytics/claude-code@1.2.0` (proves scoped
   name + version reached npm's manifest parser)
3. File list contains:
   - `package.json`
   - `README.md`
   - `LICENSE`
   - `CHANGELOG.md`
   - `QUICKSTART.md`
   - `servers/chatlytics-mcp.bundle.js`
   - `servers/chatlytics-mcp.js`
   - `servers/package.json`
   - `skills/...` (one or more files)
4. File list MUST NOT contain:
   - `servers/node_modules/...`
   - `servers/test/...`
   - `servers/package-lock.json`
   - `SUBMISSION.md`
   - `.planning/...` (would never appear — outside repo, but defensive)
   - `node_modules/...` (none at root)
   - `.git/...`
5. Tarball size sane (< 1 MB packed; the bundle alone is ~715 KB
   gzip-compressible to ~150-200 KB)

**Capture:**
- File count from output (`X files`)
- Tarball size in KB from output (`X.X kB` packed)

**Failure mode:** Any gate failing → halt with `blocker`. Common
failures: forgot to add `LICENSE` to `files:` (it's auto-included
by npm anyway, but verify), accidental glob mistake (e.g.,
`servers/` without trailing-slash includes everything), invalid
JSON.

---

### T4 — `npm publish --dry-run --access=public` validation (no commit)

Run the actual publish-dry-run. This is the manifest end-to-end
validator (no auth required for dry-run). Captures the same file
list plus the full publish manifest npm would send.

**Commands:**
```bash
cd "C:/Users/omern/.claude/plugins/marketplaces/chatlytics-claude-code/"
npm publish --dry-run --access=public 2>&1 | tee /tmp/npm-publish-dry-run.txt
```

**Gates:**
1. Exit code 0
2. Output reports `+ @chatlytics/claude-code@1.2.0` at the end
3. NO errors about missing required fields (`name`, `version`,
   `description` — already present)
4. NO errors about scope access (`--access=public` should
   silence the "scope is restricted by default" warning)
5. File list matches T3 file list (sanity — same files via
   different code path)

**Failure mode:** Any gate failing → halt with `blocker`. Common
failures: `publishConfig.access` typo, version conflict (would
require a real network check that may not run in `--dry-run`),
malformed `files` glob.

**Note:** `npm publish --dry-run` does NOT contact the registry
auth endpoint, so it CANNOT verify `@chatlytics` org membership.
That's PART B's gate.

---

### T5 — Verify @chatlytics name still 404 after manifest changes (no commit)

Re-check the name registration ONE MORE TIME after T2-T4 finish,
in case someone else publishes between checks. Tight race window
(seconds) but cheap to verify.

**Commands:**
```bash
npm view @chatlytics/claude-code 2>&1 | head -5
```

**Gates:**
1. Still 404 (name still available)

**Failure mode:** If the name was claimed → halt with `blocker`,
note in NPM-READY.md, abort PART A entirely. Operator needs to
either pick a different name OR contact npm support if it's a
typosquat.

---

### T6 — Commit manifest changes IN SIBLING REPO

ONE commit containing:
- `package.json` (all D2 + D6 flips)
- `servers/package-lock.json` (Phase 20 artifact carry-over)

NOTHING ELSE. NO bundle change, NO source change, NO CHANGELOG
update (the 1.2.0 entry already has the "Out of scope (Phase 21)"
note from Phase 20; that note will get reconciled in a follow-up
or absorbed at PART B time).

**Commands:**
```bash
cd "C:/Users/omern/.claude/plugins/marketplaces/chatlytics-claude-code/"
git add package.json servers/package-lock.json
git status   # confirm only these two files staged
git commit -m "chore(npm): prepare 1.2.0 for first public publish under @chatlytics org

- Flip private: true -> false on root package.json
- Rename root package: chatlytics-claude-code -> @chatlytics/claude-code (scoped)
- Add publishConfig.access: public (required for scoped public packages)
- Add engines.node: >=18 (matches esbuild target=node18)
- Add author field
- Add files: allowlist (whitelist what ships in the tarball)
  - servers/chatlytics-mcp.bundle.js, servers/chatlytics-mcp.js
  - servers/package.json (for file-tree consistency)
  - skills/, README.md, CHANGELOG.md, LICENSE, QUICKSTART.md
  - EXCLUDED: servers/node_modules/, servers/test/, package-lock.json, SUBMISSION.md

- Commit servers/package-lock.json refresh from Phase 20 esbuild install
  (version field bumped 1.1.0 -> 1.2.0 + devDep tree captured)

NO real npm publish in this commit. NO v1.2.0 tag. NO push.
This is PART A of Phase 21 (manifest prep + dry-run validation).
PART B (operator-gated) runs the real npm publish + tag + push.

Dry-run validation passed:
- npm pack --dry-run -> ok
- npm publish --dry-run --access=public -> ok
- npm view @chatlytics/claude-code -> 404 (name available)
- npm whoami -> omernesh"
```

**Gates:**
1. Commit succeeds
2. `git log --oneline -1` shows new commit
3. `git status` clean
4. `git show --stat HEAD` shows EXACTLY 2 files: `package.json`
   and `servers/package-lock.json`

**Failure mode:** Pre-commit hooks (none expected in this repo,
but defensive) fail → fix and re-commit. NEVER `--amend` (per
CLAUDE.md git safety protocol).

---

### T7 — Post-commit re-validation (no commit)

Re-run both dry-runs AFTER the commit lands, to prove the
committed state validates (not just the working tree). Catches
the case where `git add` missed something subtle.

**Commands:**
```bash
npm pack --dry-run 2>&1 | tail -20
npm publish --dry-run --access=public 2>&1 | tail -20
```

**Gates:**
1. Both still exit 0
2. File lists unchanged from T3/T4

---

### T8 — Write Python repo SUMMARY + REVIEW + NPM-READY

In the Python repo, write three artifacts to
`.planning/phases/HERMES-21-*/`:

**a) `21-SUMMARY.md`** — frontmatter (phase/part/status/date/sibling
commits/Python commits/etc.) + Outcome / Task results T1-T7 /
Acceptance gates / Phase 21 PART B scope guard / Next action
sections. Mirror Phase 20 SUMMARY shape.

**b) `21-REVIEW.md`** — short review entry. PART A is manifest-only;
code review surface is the 2-file diff in the sibling repo
(`package.json` + `package-lock.json`). Findings expected: 0
BLOCKER / 0 HIGH / possibly 1 MED-or-LOW about the postinstall
script wastefulness (decision: documented in NPM-READY.md as a
known imperfection, deferred to a v1.2.1 patch phase or PART B
absorption — not a blocker for PART A's scope).

**c) `21-NPM-READY.md`** — the explicit go/no-go checklist
(operator deliverable). Format below.

**21-NPM-READY.md template:**

```markdown
---
phase: 21
part: A
date: 2026-05-18
release_ready: true|false
sibling_commit: <SHA>
---

# Phase 21 PART A — NPM Publish Readiness Checklist

## Status

**RELEASE READY: YES** (pending operator go for PART B)

## Pre-publish validation gates

- [x] `package.json` (root) `private: false`
- [x] `package.json` (root) `name: "@chatlytics/claude-code"`
- [x] `package.json` (root) `files: [...]` allowlist (8 entries)
- [x] `package.json` (root) `publishConfig: {access: "public"}`
- [x] `package.json` (root) `engines.node: ">=18"`
- [x] `package.json` (root) `author` field present
- [x] `servers/package.json` unchanged (internal sub-package; per D3)
- [x] `npm pack --dry-run` → exit 0, N files, X.X kB packed
- [x] `npm publish --dry-run --access=public` → exit 0
- [x] `npm view @chatlytics/claude-code` → 404 (name available)
- [x] `npm whoami` → omernesh
- [ ] `@chatlytics` org accepts publish from omernesh
      — UNVERIFIABLE in PART A (granular token can't introspect
      orgs; only verifiable by real publish in PART B)
- [x] Bundle current (mtime check)
- [x] Code review clean (PART A — manifest only)

## File list (npm pack --dry-run output)

<paste file list from T3>

## Manifest diff summary

<paste git diff package.json from T6 commit>

## Known imperfections (deferred to future patch)

- `scripts.postinstall` still runs `npm --prefix servers install`
  on consumer install. Bundle has zero runtime deps after esbuild
  `--packages=bundle`, so this is wasteful. Remove in v1.2.1 patch
  or absorb into PART B if operator wants. NOT a blocker for PART A.

## PART B (operator-gated) commands

After operator confirms go:

\`\`\`bash
cd "C:/Users/omern/.claude/plugins/marketplaces/chatlytics-claude-code/"

# 1. Real publish
npm publish --access=public

# 2. Post-publish install verification (scratch dir)
mkdir /tmp/chatlytics-npm-smoke && cd /tmp/chatlytics-npm-smoke
npm init -y
npm install @chatlytics/claude-code
ls node_modules/@chatlytics/claude-code/servers/chatlytics-mcp.bundle.js  # should exist

# 3. Tag + push (back in sibling repo)
cd "C:/Users/omern/.claude/plugins/marketplaces/chatlytics-claude-code/"
git tag -a v1.2.0 -m "v1.2.0 — first public npm release as @chatlytics/claude-code"
git push origin main
git push origin v1.2.0
\`\`\`

## Blockers for release

(empty if release_ready=true)

## Next action

**Operator decision:** go/no-go on PART B (real `npm publish`).
Recommend GO — all validation gates passed.
```

**Commit Python repo (single commit for all 3 files):**

```bash
cd D:/docker/chatlytics-hermes-split
git add .planning/phases/HERMES-21-release-chatlytics-claude-code-1-2-0-npm/
git commit -m "docs(21A): summary + review + NPM-READY for PART A dry-run

PART A complete. Manifest prepared in sibling repo (1 commit). All
validation gates passed:
- npm pack --dry-run ok
- npm publish --dry-run --access=public ok
- npm view @chatlytics/claude-code -> 404 available
- npm whoami -> omernesh

Awaiting operator go/no-go for PART B (real npm publish + tag + push)."
```

**Gates:**
1. Three new files in `.planning/phases/HERMES-21-*/`
2. Python repo commit succeeds
3. `git status` clean in both repos

---

### T9 — Telegrams (no commit, post-everything)

Send the required cadence telegrams in order (after each gate
landed, NOT batched at the end — per CLAUDE.md "trigger
immediately when event lands"). For PART A's brief, the telegrams
are:

1. After T6 (sibling commit landed): "implemented" telegram
2. After T7 (post-commit dry-run revalidation): "dry-run" telegram
3. After T8 (review artifact written): "review" telegram

**Telegram bodies (HTML mode, chat -1003808579173):**

a) After T6: implemented
```
✅ <b>chatlytics-claude-code</b> — Phase 21 PART A implemented (manifest flipped to scoped public, files: allowlist added, 1 commit sibling + N Python). Awaiting review.
```

b) After T7: dry-run
```
🧪 <b>chatlytics-claude-code</b> — Phase 21 dry-run READY. npm pack: <N> files, <X.X> kB. npm publish --dry-run --access=public: ok. @chatlytics/claude-code 404 (available). whoami: omernesh.
```

c) After T8: review
```
🔍 <b>chatlytics-claude-code</b> — Phase 21 PART A review: BLOCKER=0 HIGH=0 MED=0 LOW=0. SHIP (PART A only — operator gate for PART B real publish).
```

**Gates:**
1. Each telegram returns 200 from `api.telegram.org`
2. Send commands captured in execution log

---

## Acceptance gates (Plan-level)

All must pass:

1. T1 pre-flight gates passed
2. T2 manifest validates (JSON parseable, all required flips present)
3. T3 `npm pack --dry-run` exit 0 + clean file list
4. T4 `npm publish --dry-run --access=public` exit 0
5. T5 name still 404
6. T6 sibling repo commit succeeds (1 commit, 2 files)
7. T7 post-commit revalidation passes
8. T8 Python repo bookkeeping artifacts written (SUMMARY +
   REVIEW + NPM-READY) and committed (1 commit, 3 files)
9. T9 3 telegrams sent
10. Phase 21 PART B scope guard preserved (no real publish, no
    tag, no push)

## Estimated commits

- Sibling repo: **1** (T6)
- Python repo: **2** (this CONTEXT commit already landed; SUMMARY/REVIEW/NPM-READY in T8) — context was T0 outside this plan, so plan-attributable Python commits = **1**

## Estimated runtime

- T1-T5: < 1 minute (dry-runs are fast; no network publish)
- T6-T7: < 30 seconds (commit + re-validate)
- T8: 2-3 minutes (3 artifact files)
- T9: < 30 seconds (3 telegrams)
- Total: ~5 minutes

## Halt conditions (recap from Plan-level)

- `npm whoami` not `omernesh`
- `@chatlytics/claude-code` already exists on npm
- `npm pack --dry-run` exit non-zero OR file list contains
  excluded files
- `npm publish --dry-run --access=public` exit non-zero
- Bundle stale (mtime older than source)
- JSON manifest invalid after edit

Any halt → write blocker reason to NPM-READY.md
`blockers_for_release`, return immediately with `status: blocker`.
