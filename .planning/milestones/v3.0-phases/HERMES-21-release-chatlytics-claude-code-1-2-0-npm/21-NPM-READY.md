---
phase: 21
part: A
date: 2026-05-18
release_ready: true
sibling_commit: 270b23e
python_commits: [80c3734, fac30d9, "this commit"]
---

# Phase 21 PART A — NPM Publish Readiness Checklist

## Status

**RELEASE READY: YES** (pending operator go for PART B)

## Pre-publish validation gates

- [x] `package.json` (root) `private: false`
- [x] `package.json` (root) `name: "@chatlytics/claude-code"` (scoped)
- [x] `package.json` (root) `files: [...]` allowlist — 8 entries
- [x] `package.json` (root) `publishConfig: {access: "public"}`
- [x] `package.json` (root) `engines.node: ">=18"`
- [x] `package.json` (root) `author` field present
- [x] `package.json` (root) `repository.url` normalized (`git+https://`)
- [x] `servers/package.json` unchanged (internal sub-package; per D3)
- [x] `servers/package-lock.json` committed (Phase 20 carry-over)
- [x] `npm pack --dry-run` → exit 0, 9 files, 147.7 kB packed
- [x] `npm publish --dry-run --access=public` → exit 0
      (`+ @chatlytics/claude-code@1.2.0`)
- [x] `npm view @chatlytics/claude-code` → 404 (name available)
- [x] `npm whoami` → omernesh
- [ ] `@chatlytics` org accepts publish from omernesh — **UNVERIFIABLE
      in PART A**; granular token can't introspect orgs. Only
      verifiable by real publish attempt in PART B.
- [x] Bundle current (mtime `servers/chatlytics-mcp.bundle.js` >
      `servers/chatlytics-mcp.js`)
- [x] Code review clean (PART A only — 0 BLOCKER / 0 HIGH / 0 MED
      / 0 LOW / 1 INFO; INFO is non-blocking)

## File list (npm pack --dry-run output)

```
@chatlytics/claude-code@1.2.0
  7.4kB CHANGELOG.md
  1.1kB LICENSE
  5.9kB QUICKSTART.md
  3.3kB README.md
   932B package.json
731.6kB servers/chatlytics-mcp.bundle.js
 12.9kB servers/chatlytics-mcp.js
   454B servers/package.json
  3.6kB skills/chatlytics/SKILL.md

Total: 9 files | Packed: 147.7 kB | Unpacked: 767.1 kB
shasum: 2fd53432384097321fb842acd4c9cfd6c747c029
```

## Manifest diff summary (commit 270b23e)

```diff
 {
-  "name": "chatlytics-claude-code",
+  "name": "@chatlytics/claude-code",
   "version": "1.2.0",
-  "private": true,
+  "private": false,
   "description": "Chatlytics Claude Code plugin — MCP server + skill for WhatsApp via Chatlytics REST API",
   "license": "MIT",
+  "author": "Omer Nesher <omernesher@gmail.com>",
   "homepage": "https://github.com/omernesh/chatlytics-claude-code",
   "repository": {
     "type": "git",
-    "url": "https://github.com/omernesh/chatlytics-claude-code.git"
+    "url": "git+https://github.com/omernesh/chatlytics-claude-code.git"
   },
+  "engines": {
+    "node": ">=18"
+  },
+  "publishConfig": {
+    "access": "public"
+  },
+  "files": [
+    "servers/chatlytics-mcp.bundle.js",
+    "servers/chatlytics-mcp.js",
+    "servers/package.json",
+    "skills/",
+    "README.md",
+    "CHANGELOG.md",
+    "LICENSE",
+    "QUICKSTART.md"
+  ],
   "scripts": {
     "postinstall": "npm --prefix servers install",
     ...
   }
 }
```

## Known imperfection (REVIEW INFO-01)

`scripts.postinstall` runs `npm --prefix servers install` on every
consumer install. Bundle has zero runtime deps (esbuild bundles
everything), so this is wasteful (~50 MB of dev tooling installed
that consumers never use).

**Recommended remediation:** Absorb into PART B — delete the
`postinstall` + `setup` scripts before `npm publish --access=public`.
Single-line diff, re-run dry-run to confirm clean. Else defer to
a v1.2.1 patch within a week.

## PART B (operator-gated) commands

After operator confirms **GO** on PART B:

### Optional pre-publish polish (REVIEW INFO-01 fix)

```bash
cd "C:/Users/omern/.claude/plugins/marketplaces/chatlytics-claude-code/"
# Remove wasteful postinstall + setup scripts (single edit to package.json)
# Re-run dry-run to confirm
npm publish --dry-run --access=public
# Commit: chore(npm): drop wasteful postinstall before first publish
```

### Real publish

```bash
cd "C:/Users/omern/.claude/plugins/marketplaces/chatlytics-claude-code/"
npm publish --access=public
```

Expected output: `+ @chatlytics/claude-code@1.2.0`.

If `npm publish` fails with `EPUBLISH-FORBIDDEN` or
`EOTP` / `E403`, the `@chatlytics` org either does not exist or
the granular token lacks publish scope for it. Remediation:
- Create the org on the npm website: https://www.npmjs.com/org/create
  (free for public packages)
- OR upgrade the npm token to classic with `npm token create`
- Then retry `npm publish --access=public`

### Post-publish install verification

```bash
mkdir /tmp/chatlytics-npm-smoke && cd /tmp/chatlytics-npm-smoke
npm init -y
npm install @chatlytics/claude-code
ls node_modules/@chatlytics/claude-code/servers/chatlytics-mcp.bundle.js
# Should exist and be 731.6 kB
node -e "console.log(require('@chatlytics/claude-code/package.json').version)"
# Should print: 1.2.0
```

### Tag + push (back in sibling repo)

```bash
cd "C:/Users/omern/.claude/plugins/marketplaces/chatlytics-claude-code/"
git tag -a v1.2.0 -m "v1.2.0 — first public npm release as @chatlytics/claude-code"
git push origin main      # 9 commits (Phase 19 + Phase 20 + Phase 21 PART A + B if any)
git push origin v1.2.0    # the tag
```

### Verify on npmjs.com

Open https://www.npmjs.com/package/@chatlytics/claude-code in a
browser. Verify:
- Version is `1.2.0`
- README renders
- License is MIT
- Repository link points at GitHub
- "Last publish" timestamp is correct

## Blockers for release

(none)

## Next action

**Operator decision:** go/no-go on PART B.

Recommended verdict: **GO** — all PART A validation gates passed.

To spawn PART B, point Claude Code at this NPM-READY.md and the
PART B brief (which the operator authors based on the autonomous
brief's PART B description).
