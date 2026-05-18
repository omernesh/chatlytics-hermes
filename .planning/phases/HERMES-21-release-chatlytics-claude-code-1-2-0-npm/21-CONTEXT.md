---
phase: 21
phase_name: Release chatlytics-claude-code 1.2.0 (npm)
mode: infra-skip
part: A
date: 2026-05-18
type: cross-repo + release
sibling_repo: C:/Users/omern/.claude/plugins/marketplaces/chatlytics-claude-code/
---

# HERMES-21 PART A — Context (infra-skip, cross-repo, dry-run only)

## Domain (boundary)

**Phase 21 is split into two parts.**

- **PART A (this run):** Prepare the JS bundle for first public npm
  publish under the operator's `@chatlytics` org. Flip
  `"private": true` → `false`, rename root package to
  `@chatlytics/claude-code` (scoped), add `"files":` allowlist, add
  `"publishConfig": {"access": "public"}`, run `npm pack --dry-run`
  + `npm publish --dry-run --access=public`. **NO real publish. NO
  `v1.2.0` git tag. NO push.** End with explicit go/no-go summary.
- **PART B (separate run, after operator confirms):** Real
  `npm publish --access=public`, post-publish smoke
  `npm install @chatlytics/claude-code` verification, `v1.2.0`
  annotated tag, push tag + main IN THE SIBLING REPO.

**This run is PART A.** The Python repo
(`D:/docker/chatlytics-hermes-split`) is used ONLY for autonomous
bookkeeping — CONTEXT.md, PLAN, SUMMARY, REVIEW, NPM-READY land
under `.planning/phases/HERMES-21-*/` and are committed in the
Python repo's git history.

**All source/manifest/CHANGELOG/README edits happen in the sibling
JS repo at
`C:/Users/omern/.claude/plugins/marketplaces/chatlytics-claude-code/`**
and are committed in that repo's separate git history.

## Reality check before plan (one-time inspection of sibling repo)

Pre-flight verification (orchestrator already ran):

- **`npm whoami`** → `omernesh` (npm auth configured in `~/.npmrc`)
- **`npm view @chatlytics/claude-code`** → 404 Not Found (name
  available; first publish under `@chatlytics` org)
- **`servers/chatlytics-mcp.bundle.js`** mtime newer than
  `servers/chatlytics-mcp.js` (bundle is current; Phase 20 rebuilt)
- **Root `package.json`:** `name: "chatlytics-claude-code"`,
  `version: "1.2.0"`, `private: true`, MIT license, homepage +
  repository point at `https://github.com/omernesh/chatlytics-claude-code`
  (already public-correct URLs). NO `files:` allowlist. NO
  `publishConfig`. NO `engines.node` declared.
- **`servers/package.json`:** `name: "chatlytics-mcp"` (NOT
  scoped — it's an internal sub-package), `version: "1.2.0"`,
  `private: true`, `type: "module"`. NOT independently published
  by `npm publish` from the repo root — npm only publishes the
  root manifest. `servers/` is bundled INTO the published tarball
  via the root `files:` allowlist, not as a separate package.
- **Uncommitted change in sibling repo:** `servers/package-lock.json`
  has a benign diff capturing the `1.1.0` → `1.2.0` version bump
  plus the esbuild devDep tree that was installed during Phase 20's
  rebuild. This should be committed as part of Phase 21 PART A's
  bookkeeping (single commit, with the manifest flips).
- **Sibling repo branch:** `main`, 8 commits ahead of `origin/main`
  (PART B will push). The 4 Phase 20 commits are at the head.

## Decisions

- **D1 — Publish root only (locked):**
  Only the **root** `package.json` becomes a published npm package.
  `servers/package.json` stays `private: true` and is NOT renamed —
  it's an internal sub-package whose contents ship inside the root
  tarball via the root `files:` allowlist. `servers/` is the build
  context for esbuild; the consumer only sees the built bundle and
  the file tree the root manifest's `files:` allowlist selects.

- **D2 — Root manifest flips (locked, exact values):**
  - `"private": true` → `"private": false`
  - `"name": "chatlytics-claude-code"` → `"name": "@chatlytics/claude-code"`
  - ADD `"publishConfig": {"access": "public"}` (REQUIRED for
    scoped public packages; npm defaults scoped to restricted)
  - ADD `"engines": {"node": ">=18"}` (the esbuild target is
    `node18` per `servers/package.json` build script — match the
    runtime minimum)
  - ADD `"files":` allowlist (whitelist):
    ```json
    "files": [
      "servers/chatlytics-mcp.bundle.js",
      "servers/chatlytics-mcp.js",
      "servers/package.json",
      "skills/",
      "README.md",
      "CHANGELOG.md",
      "LICENSE",
      "QUICKSTART.md"
    ]
    ```
    Rationale per item:
    - `servers/chatlytics-mcp.bundle.js` — the actual runnable MCP
      server (esbuild output, ~715 KB, ZERO runtime deps after
      bundling — `--packages=bundle` inlines `@modelcontextprotocol/sdk`
      and `zod`)
    - `servers/chatlytics-mcp.js` — source for transparency / debug
      / forks (small file, ~13 KB)
    - `servers/package.json` — needed for the file tree consistency
      so `node servers/chatlytics-mcp.bundle.js` resolution + future
      regeneration is reproducible
    - `skills/` — Claude Code skill markdown that ships with the
      plugin (advertised feature)
    - `README.md`, `CHANGELOG.md`, `LICENSE` — standard
    - `QUICKSTART.md` — consumer onboarding doc
  - **EXCLUDED (NOT in the allowlist):** `servers/node_modules/`
    (would balloon tarball; not needed because esbuild bundles deps),
    `servers/test/` (live-API smoke tests not useful to consumers),
    `servers/package-lock.json` (lockfile for dev environment, not
    needed by consumers), `SUBMISSION.md` (internal claude-plugins
    marketplace doc), `.planning/` (NA — outside the package),
    `node_modules/` (NA — none at root)

- **D3 — `servers/package.json` (locked, MOSTLY UNCHANGED):**
  Stays `private: true`, stays `name: "chatlytics-mcp"` (not
  scoped). Not modified in PART A unless we discover a manifest
  issue at validation time. Reason: it's not the publish root, and
  the published consumer never runs `npm install` inside
  `servers/` (because the bundle has zero runtime deps after
  esbuild `--packages=bundle`). Touching its private flag or name
  is out of scope.

- **D4 — Repository / homepage / bugs URLs (locked, NO CHANGE):**
  Root manifest already has:
  ```
  "homepage": "https://github.com/omernesh/chatlytics-claude-code"
  "repository": {"type": "git", "url": "https://github.com/omernesh/chatlytics-claude-code.git"}
  ```
  These are already public-correct GitHub URLs. PART A does NOT
  touch them. (Optionally PART A could add a `"bugs"` field, but
  npm doesn't reject manifests missing `bugs` — leave for a
  follow-up cleanup phase.)

- **D5 — License (locked, NO CHANGE):**
  Root manifest already has `"license": "MIT"` (SPDX-valid). The
  `LICENSE` file exists at the repo root. No change.

- **D6 — Author field (locked, ADD IF MISSING):**
  Check `"author"` field. If absent, add
  `"author": "Omer Nesher <omernesher@gmail.com>"`. npm doesn't
  require `author` but it's good public-publish hygiene.

- **D7 — `bin` field (locked, DEFER):**
  PART A does NOT add a `"bin":` entry. Consumers run the bundle
  via their MCP client's stdio invocation (e.g., the Claude Code
  marketplace plugin manifest invokes
  `node node_modules/@chatlytics/claude-code/servers/chatlytics-mcp.bundle.js`).
  A future v1.3.x could add `"bin": {"chatlytics-mcp": "servers/chatlytics-mcp.bundle.js"}`
  to enable `npx chatlytics-mcp`, but that's a consumer-UX
  decision beyond PART A scope.

- **D8 — `package-lock.json` capture (locked):**
  The uncommitted `servers/package-lock.json` diff (Phase 20
  artifact) gets committed in the PART A sibling-repo commit
  alongside the manifest flips. Single commit message:
  `chore(npm): prepare 1.2.0 for first public publish under @chatlytics org`.

- **D9 — Validation gates (locked, ALL MUST PASS):**
  Each gate runs from the sibling repo root unless noted:
  1. `npm whoami` → `omernesh` (PRE-CHECKED already; re-verify)
  2. `npm view @chatlytics/claude-code` → 404 / not found
     (PRE-CHECKED already; re-verify after rename to catch
     race conditions where someone publishes between checks)
  3. `npm pack --dry-run` → exits 0; tarball file list contains
     ONLY the `files:` allowlist + the mandatory
     `package.json`, `README.md`, `LICENSE` (npm always includes
     these regardless of allowlist); MUST NOT contain
     `node_modules/`, `.git/`, `.env`, `servers/node_modules/`,
     `servers/test/`, `SUBMISSION.md`, `package-lock.json`
  4. `npm publish --dry-run --access=public` → exits 0; output
     shows the scoped name `@chatlytics/claude-code@1.2.0` and
     the file list matches `npm pack` output
  5. Bundle currency: `stat servers/chatlytics-mcp.bundle.js` mtime
     newer than `stat servers/chatlytics-mcp.js` mtime
     (PRE-CHECKED already; re-verify)

- **D10 — `@chatlytics` org publish rights (DEFERRED to PART B):**
  The npm token is a granular access token (per STATE.md token
  scope note). `npm org ls @chatlytics omernesh` returns 403 —
  the token can't introspect orgs. Org membership / publish
  rights can ONLY be verified by attempting a real publish.
  PART A's dry-run does NOT exercise auth or org membership
  (manifest-only validation). This is captured in the go/no-go
  summary as an explicit "known unverified item" — PART B's
  real publish is the actual gate. If PART B fails with
  `EPUBLISH-FORBIDDEN` or similar, that's the operator's
  signal to either upgrade the token's scope or create the
  `@chatlytics` org first.

- **D11 — `.npmignore` (locked, NO CREATE):**
  The `files:` allowlist is the authoritative whitelist. When
  both `files:` (in package.json) and `.npmignore` exist, `files:`
  wins. Creating `.npmignore` would be redundant and could
  confuse future maintainers. PART A relies on `files:` only.
  (Phase 20's `npm pack --dry-run` emitted a warning about
  `.npmignore` absence; that warning is silenced once `files:`
  is present.)

- **D12 — Telegram cadence (locked):**
  Bot token in `~/.claude/.telegram-bot-token`, chat
  `-1003808579173`, HTML parse mode. Send after: execute land,
  code-review verdict, fix-pass (only if invoked), dry-run
  result. NO telegram on plan write or context write (matches
  CLAUDE.md "no routine in-flight progress" rule).

## Scope guard (HARD STOP — PART B owns these)

- DO NOT run `npm publish` without `--dry-run` (PART B owns the
  real publish)
- DO NOT create the `v1.2.0` git tag (PART B owns it)
- DO NOT push anything to the sibling repo's `origin/main` (PART
  B owns push tag + push main)
- DO NOT install the package from npm to smoke-test (PART B owns
  post-publish install verification)
- DO NOT modify ANY source code beyond the manifest fields
  enumerated in D2-D6 (no behavior changes; PART A is
  manifest-prep only)
- DO NOT touch the Python repo's source or tests (Python work
  done in Phases 13-19)

## Code context (paths the plan references)

### Sibling JS repo (where work happens)

- `C:/Users/omern/.claude/plugins/marketplaces/chatlytics-claude-code/package.json`
  — root manifest (THE publish manifest; all D2 + D6 edits land here)
- `C:/Users/omern/.claude/plugins/marketplaces/chatlytics-claude-code/servers/package.json`
  — UNCHANGED per D3
- `C:/Users/omern/.claude/plugins/marketplaces/chatlytics-claude-code/servers/package-lock.json`
  — uncommitted diff from Phase 20 (committed alongside manifest
  flips per D8)
- `C:/Users/omern/.claude/plugins/marketplaces/chatlytics-claude-code/servers/chatlytics-mcp.bundle.js`
  — bundle (NOT modified; verified current per D9 gate 5)

### Python repo (bookkeeping only)

- `D:/docker/chatlytics-hermes-split/.planning/phases/HERMES-21-*/21-CONTEXT.md`
  (this file)
- `D:/docker/chatlytics-hermes-split/.planning/phases/HERMES-21-*/21-PLAN-1-*.md`
  (next step)
- `D:/docker/chatlytics-hermes-split/.planning/phases/HERMES-21-*/21-SUMMARY.md`
- `D:/docker/chatlytics-hermes-split/.planning/phases/HERMES-21-*/21-REVIEW.md`
- `D:/docker/chatlytics-hermes-split/.planning/phases/HERMES-21-*/21-NPM-READY.md`
  (go/no-go checklist for operator)

## Specifics (cross-repo dispatch protocol)

Sub-agents executing PART A PLAN tasks MUST:

1. `cd "C:/Users/omern/.claude/plugins/marketplaces/chatlytics-claude-code/"`
   before any source / manifest / npm operation.
2. Commit ALL JS manifest / lockfile changes IN THE SIBLING REPO'S
   git history (not in the Python repo). ONE commit total for
   PART A, message:
   `chore(npm): prepare 1.2.0 for first public publish under @chatlytics org`.
3. NEVER `cd` back into the Python repo for source edits — only
   for `.planning/` artifact updates.
4. NEVER run `npm publish` without `--dry-run` (HARD STOP — log
   that command would have run, but do not execute it).
5. NEVER run `git tag v1.2.0` in the sibling repo (PART B).
6. NEVER `git push` in the sibling repo (PART B).

The Python repo gets commits ONLY for `.planning/phases/HERMES-21-*/`
artifact files.

## v3.0-so-far invariants (DO NOT REGRESS)

- Python repo: 120/120 tests pass; `v3.0.0` annotated tag on
  `origin`; `chatlytics-hermes 3.0.0` LIVE on PyPI
- Sibling JS repo: 4 Phase 20 commits at HEAD of `main`; 8
  commits total ahead of `origin/main`; `servers/chatlytics-mcp.bundle.js`
  carries the `chatlytics_send` resolveChatId fix + `version: "1.2.0"`
- PART A does NOT regress ANY of the above (manifest-only edits;
  bundle unchanged; no source change)

## Deferred (PART B)

- Real `npm publish --access=public`
- Post-publish: `npm install @chatlytics/claude-code` in scratch
  dir to verify the published artifact loads
- `git tag -a v1.2.0 -m "..."` in sibling repo
- `git push origin main && git push origin v1.2.0` in sibling repo
- `@chatlytics` org publish-rights verification (only verifiable
  via real publish attempt)
- npm page render verification at
  https://www.npmjs.com/package/@chatlytics/claude-code
