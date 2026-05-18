---
phase: 21
part: A
date: 2026-05-18
reviewer: claude-opus-4-7-1m
review_type: manifest-only
verdict: SHIP (PART A — operator gate for PART B)
blockers: 0
high: 0
med: 0
low: 0
info: 1
---

# HERMES-21 PART A — Code Review

## Surface

PART A's diff is a 2-file manifest-only change in the sibling JS
repo (commit `270b23e`):

- `package.json` (+24 / -5) — flips + allowlist + publishConfig + engines + author + URL normalization
- `servers/package-lock.json` (+472 / -3) — Phase 20 esbuild devDep tree + version bump capture

No source code changed. No bundle changed. No CHANGELOG changed.
No README changed. The MCP server's runtime behavior is identical
to the Phase 20 HEAD.

## Findings

### BLOCKER (0)

None.

### HIGH (0)

None.

### MEDIUM (0)

None.

### LOW (0)

None.

### INFO (1)

#### INFO-01 — `scripts.postinstall` is wasteful for published consumers

**File:** `C:/Users/omern/.claude/plugins/marketplaces/chatlytics-claude-code/package.json`
**Line:** `"postinstall": "npm --prefix servers install"`

**Observation:** The root `scripts.postinstall` runs
`npm --prefix servers install` automatically on every consumer
install. The published bundle (`servers/chatlytics-mcp.bundle.js`)
is built with esbuild `--packages=bundle`, which inlines both
runtime deps (`@modelcontextprotocol/sdk` and `zod`) into the
output JS. Consumers therefore do NOT need `servers/node_modules/`
populated at runtime. The postinstall installs ~94 packages of
dev tooling (esbuild + transitives) the consumer never uses,
slowing every `npm install` of `@chatlytics/claude-code` by
several seconds and bloating disk by ~50 MB.

**Severity:** INFO — does NOT block first publish. Affects
consumer install UX only; the published package is functionally
correct.

**Why not fixed in PART A:** PART A's scope is manifest-only
(per CONTEXT D2-D6 + scope guard). Removing `postinstall` is a
behavior change — even if safe, it crosses the PART A scope
boundary.

**Recommended remediation:**
- Option 1 (absorb in PART B): Delete the `postinstall` and
  `setup` scripts before the real publish in PART B. Single-line
  diff. Re-run `npm publish --dry-run --access=public` to confirm.
- Option 2 (defer to v1.2.1 patch): Ship v1.2.0 as-is. First
  consumers tolerate the slow install. Issue v1.2.1 within a
  week with the postinstall removed + CHANGELOG note.

**Recommendation:** Option 1 — fix it before first publish so
the npm registry never carries the wasteful version.

### Other observations (not findings)

- The `engines.node: ">=18"` declaration is conservative. esbuild
  targets `node18`, the bundle uses dynamic ESM imports, and the
  MCP SDK requires Node 18+. Aligned.
- `publishConfig.access: "public"` is correct for `@chatlytics/...`
  scoped publishes (npm defaults scoped to restricted).
- The `files:` allowlist is a whitelist (explicit-include). Tighter
  and safer than `.npmignore` (blacklist). Future additions
  (e.g., new skill directories) must be added explicitly here —
  this is a feature, not a bug.
- Tarball at 147.7 kB packed is well under the npm soft-cap
  (registry rejects > 100 MB hard, warns ~50 MB). Bundle
  compresses well due to whitespace-heavy MCP SDK code.
- `repository.url` normalization (`https://` → `git+https://`)
  was applied pre-commit to silence the npm warn. No behavior
  change; just hygiene.

## Verdict

**SHIP (PART A — operator gate for PART B).**

PART A delivers exactly what it was scoped to deliver: a manifest
that the npm registry will accept for first public publish under
the `@chatlytics` org, validated end-to-end by both
`npm pack --dry-run` and `npm publish --dry-run --access=public`,
with the package name confirmed available and the publisher
authenticated.

INFO-01 is a polish opportunity, not a blocker. Operator chooses
between absorbing it into PART B (recommended) or shipping as-is
and patching in v1.2.1.

Phase 21 PART B is unblocked from this side. Awaiting operator
go/no-go.
