---
phase: 20
phase_name: JS bundle update for v3.0 coordination (cross-repo)
mode: infra-skip
date: 2026-05-18
type: cross-repo
sibling_repo: C:/Users/omern/.claude/plugins/marketplaces/chatlytics-claude-code/
---

# HERMES-20 — Context (infra-skip, cross-repo)

## Domain (boundary)

**Phase 20 is a cross-repo bundle alignment.** The Python repo
(`D:/docker/chatlytics-hermes-split`) is used ONLY for autonomous
bookkeeping — CONTEXT.md, PLAN, SUMMARY, REVIEW, VERIFICATION land
under `.planning/phases/HERMES-20-*/` and are committed in the Python
repo's git history.

**All source/bundle/CHANGELOG/README edits happen in the sibling JS
repo at `C:/Users/omern/.claude/plugins/marketplaces/chatlytics-claude-code/`**
and are committed in that repo's separate git history.

The goal is to bring `chatlytics-claude-code` (the JS MCP bundle that
ships as a Claude Code plugin) in sync with `chatlytics-hermes 3.0.0`
just published to PyPI in Phase 19. The bump is MINOR (`1.1.x` →
`1.2.0`) — no JS API breaks; this aligns the JS plugin's bundled
behavior with the Python plugin's stricter Phase 14 JID rules and
fixes one drift bug (`chatlytics_send` bypasses `resolveChatId()`).

## Reality check before plan (one-time inspection of sibling repo)

Reality of the sibling repo as of phase start (verified by orchestrator
read):

- **Actual version in `package.json` + `servers/package.json`:**
  `1.1.0` (NOT `1.1.2` as the autonomous brief assumed). The
  CHANGELOG documents `1.1.0`, `1.1.1`, and `1.1.2` — but the
  `package.json` version field was never bumped past `1.1.0` during
  the 1.1.1 / 1.1.2 hotfixes (drift). Phase 20 reconciles by bumping
  straight to `1.2.0` everywhere.
- **`looksLikeJid()` regex (line 60 of `servers/chatlytics-mcp.js`):**
  ALREADY `/@(c\.us|g\.us|lid|newsletter)$/i` — exactly matches the
  Python plugin's Phase 14 rule. No tightening needed; the alignment
  the brief specified is already in place. The phase will VERIFY
  this regex matches the Python canonical regex and document the
  cross-repo invariant in the summary.
- **`chatlytics_send` drift bug (lines 115-135):** CONFIRMED. The
  handler builds the API payload with `chatId: to` directly, never
  calling `resolveChatId()`. The sibling `chatlytics_read` tool
  (lines 138-159) correctly calls `resolveChatId(chatId)` first.
  This is the drift bug to fix in Phase 20.
- **Tool count:** EIGHT tools registered (`chatlytics_send`,
  `_read`, `_search`, `_actions`, `_directory`, `_health`, `_login`,
  `_dispatch`). README already says "8 MCP tools" at line 8 —
  doc-vs-reality is already accurate. CHANGELOG 1.0.0 historical
  entry references 6 tools (true at the time); the 1.1.0 entry
  doesn't restate tool count, leaving a doc gap that 1.2.0 will
  close with an explicit count in the 1.2.0 CHANGELOG bullet.
- **Build script:** `npm --prefix servers run build` invokes
  `esbuild chatlytics-mcp.js --bundle --platform=node --target=node18
  --format=esm --outfile=chatlytics-mcp.bundle.js --packages=bundle`.
  Output is `servers/chatlytics-mcp.bundle.js` (~715 KB committed
  artifact per CHANGELOG 1.1.1 entry).
- **`McpServer` constructor version literal:** line 51 reads
  `new McpServer({ name: "chatlytics", version: "1.1.0" })`. This
  is a third version site (in addition to root + servers
  package.json) that must move to `1.2.0`.

So Phase 20's actual fix-pass is narrower than the brief assumed:
- Version: 3 sites (root pkg, servers pkg, McpServer literal) move
  `1.1.0` → `1.2.0`. The `1.1.2` CHANGELOG drift gets absorbed by
  jumping straight to `1.2.0`.
- `looksLikeJid` tightening: NO-OP (already strict). Documented as
  a verified invariant, not a code change.
- `chatlytics_send` resolveChatId fix: REAL CODE CHANGE — insert
  the same `await resolveChatId(...)` call used by `chatlytics_read`.
- Tool count doc: README is already correct; CHANGELOG 1.2.0 entry
  states the count for the record.
- Esbuild rebuild: MANDATORY after the source change.

## Decisions

- **D1 — Version bump (locked, exact value `1.2.0`):**
  - `package.json` (root) `"version": "1.1.0"` → `"version": "1.2.0"`
  - `servers/package.json` `"version": "1.1.0"` → `"version": "1.2.0"`
  - `servers/chatlytics-mcp.js` line 51 `version: "1.1.0"` →
    `version: "1.2.0"` (the `new McpServer({...})` constructor
    literal — this is what the MCP handshake reports to clients)
  - All three sites equal exactly `1.2.0`. A sweep
    `grep -rn '"1\.1\.0"\|version: 1\.1\.0' package.json servers/package.json servers/chatlytics-mcp.js`
    confirms no orphans before commit.

- **D2 — `looksLikeJid` regex (locked, NO-OP code change):**
  Already `/@(c\.us|g\.us|lid|newsletter)$/i` at line 60. Python's
  Phase 14 regex (canonical source: `src/chatlytics_hermes/tools.py`
  `_JID_RE` constant in the Python repo) is the same regex literal.
  Phase 20 verifies the match and DOCUMENTS the cross-repo
  invariant in the SUMMARY. No source edit to the regex itself.

- **D3 — `chatlytics_send` `resolveChatId` fix (locked, REAL CODE CHANGE):**
  Insert `const resolved = await resolveChatId(to);` at the top of
  the handler body (inside `try`, before the `callApi` call) —
  mirroring the pattern at line 149 in `chatlytics_read`. Use
  `resolved` (not `to`) as the `chatId` field in the
  `params: { chatId: ..., text }` payload. The argument name `to`
  is preserved in the schema (no schema break — it's still the
  same input field name the LLM sees), only the internal value
  that hits the API changes from a literal pass-through to a
  resolved JID.
  - Behavior after fix: bare names / phones now go through the
    same name-resolution search path as `chatlytics_read` —
    single-match resolves, zero/multi-match throws a clear picker
    error. Existing JID inputs no-op through `resolveChatId`
    (line 67 short-circuits when `looksLikeJid(input)` is true)
    so JID-passing callers are unaffected.

- **D4 — CHANGELOG 1.2.0 entry (locked shape):**
  Heading: `## 1.2.0 — 2026-05-18`
  Subsections (in order):
  - **Coordination** — bundle aligned with chatlytics-hermes 3.0.0
    on PyPI (link to https://pypi.org/project/chatlytics-hermes/3.0.0/)
  - **Fixed** — `chatlytics_send` was bypassing `resolveChatId()`
    (drift bug from prior versions). Now consistent with
    `chatlytics_read`: bare names / phones get resolved via the
    `search` action before send. Existing JID-passing callers
    unaffected. Ambiguous names return the same picker error the
    `chatlytics_read` tool returns.
  - **Verified** — `looksLikeJid()` regex
    (`/@(c\.us|g\.us|lid|newsletter)$/i`) confirmed identical to
    chatlytics-hermes 3.0.0's Phase 14 canonical JID rule. Phone
    numbers and display names are rejected at JID-detection time
    in BOTH plugins, ensuring uniform behavior across the Python
    Hermes plugin and the JS MCP bundle.
  - **Internal** — esbuild bundle regenerated
    (`servers/chatlytics-mcp.bundle.js`). Version constants
    aligned across `package.json`, `servers/package.json`, and
    the `McpServer` constructor literal (drift between `1.1.0`
    package.json and `1.1.2` CHANGELOG reconciled by jumping to
    `1.2.0`). 8 tools registered (no change from 1.1.x —
    `chatlytics_send`, `_read`, `_search`, `_actions`,
    `_directory`, `_health`, `_login`, `_dispatch`).

- **D5 — README updates (locked, minimal):**
  - No tool-count change needed (already correct at "8 MCP tools").
  - No version bump in README (the README doesn't reference the
    bundle version; CHANGELOG is the source of truth).
  - Optional: add a one-line "v1.2.0 coordinates with
    chatlytics-hermes 3.0.0 on PyPI" note in the Versioning
    section. Decided AT PLAN TIME based on the diff complexity.

- **D6 — Esbuild rebuild (locked, MANDATORY):**
  `npm --prefix servers run build` produces a regenerated
  `servers/chatlytics-mcp.bundle.js`. The rebuilt bundle MUST be
  committed alongside the source change in the SAME commit so the
  git history shows source + bundle move together (matches the
  1.1.1 / 1.1.2 commit pattern).

- **D7 — Validation gates (locked):**
  - `npm pack --dry-run` from the sibling repo root succeeds
    (validates the manifest + tarball without needing npm auth).
  - `npm test` from `servers/` succeeds OR is skipped with a
    documented reason (current smoke test hits a live Chatlytics
    endpoint and needs `CHATLYTICS_API_URL` + `CHATLYTICS_API_KEY`
    in env — if not available, the smoke run is logged as
    SKIPPED with rationale in the SUMMARY, NOT auto-failed).
  - Manual verification of resolve-then-send: after the bundle
    rebuild, document the codepath in the SUMMARY (no live API
    call required — the regression bar is "the source-level diff
    matches the `chatlytics_read` pattern exactly").

## Scope guard (HARD STOP — Phase 21 owns these)

- DO NOT flip `"private": true` → `false` in either package.json
  (Phase 21 owns the publish flip)
- DO NOT rename the package to `@chatlytics/claude-code`
  (Phase 21 owns the scoped rename)
- DO NOT add the `"files":` allowlist to any package.json
  (Phase 21 owns it)
- DO NOT run `npm publish` or `npm publish --dry-run`
  (Phase 21 owns publish; `npm pack --dry-run` is the Phase 20
  validation surface)
- DO NOT create a `v1.2.0` git tag in the sibling repo
  (Phase 21 owns the tag)
- DO NOT modify the Python repo's source code
  (Python work is done in Phases 13-19; Phase 20 only modifies
  `.planning/` in the Python repo + sibling JS repo source)

## Code context (paths the plan references)

### Sibling JS repo (where work happens)
- `C:/Users/omern/.claude/plugins/marketplaces/chatlytics-claude-code/package.json`
  — root manifest (version `1.1.0` → `1.2.0`)
- `C:/Users/omern/.claude/plugins/marketplaces/chatlytics-claude-code/servers/package.json`
  — servers manifest (version `1.1.0` → `1.2.0`, build script
  source-of-truth)
- `C:/Users/omern/.claude/plugins/marketplaces/chatlytics-claude-code/servers/chatlytics-mcp.js`
  — MCP server source (line 51 version literal; lines 58-61
  `looksLikeJid` regex; lines 66-112 `resolveChatId`; lines
  115-135 `chatlytics_send` handler to fix)
- `C:/Users/omern/.claude/plugins/marketplaces/chatlytics-claude-code/servers/chatlytics-mcp.bundle.js`
  — esbuild output (regenerate)
- `C:/Users/omern/.claude/plugins/marketplaces/chatlytics-claude-code/CHANGELOG.md`
  — `1.2.0` entry (top of file)
- `C:/Users/omern/.claude/plugins/marketplaces/chatlytics-claude-code/README.md`
  — minor versioning note (optional per D5)

### Python repo canonical source (NOT MODIFIED — referenced for invariant)
- `D:/docker/chatlytics-hermes-split/src/chatlytics_hermes/tools.py`
  — Phase 14 canonical JID regex (the source-of-truth that
  `looksLikeJid` must match)

## Specifics (cross-repo dispatch protocol)

Sub-agents executing Phase 20 PLAN tasks MUST:

1. `cd "C:/Users/omern/.claude/plugins/marketplaces/chatlytics-claude-code/"`
   before any source / build / commit operation.
2. Commit ALL JS source / bundle / CHANGELOG / README changes IN
   THE SIBLING REPO'S git history (not in the Python repo).
3. Verify the sibling repo's `git log` shows the new commits
   AFTER each task (`git log --oneline -5` in the sibling cwd).
4. NEVER `cd` back into the Python repo for source edits — only
   for `.planning/` artifact updates.

The Python repo gets commits ONLY for `.planning/phases/HERMES-20-*/`
artifact files (CONTEXT.md, PLAN, SUMMARY, REVIEW, VERIFICATION).

## v3.0-so-far invariants in the Python repo (DO NOT REGRESS)

- 120/120 tests still pass against `v3.0.0` source AND the PyPI
  artifact
- `v3.0.0` annotated tag exists locally + on `origin`
- `chatlytics-hermes 3.0.0` is LIVE on
  https://pypi.org/project/chatlytics-hermes/3.0.0/
- Phase 20 does NOT touch any of the above

## Deferred (Phase 21)

- `npm publish --access=public` (real publish)
- `"private": true` → `false` flip on both package.json
- Package rename `chatlytics-claude-code` → `@chatlytics/claude-code`
- `"files":` allowlist on root package.json
- `.npmignore` creation
- `v1.2.0` annotated tag + push in sibling repo
- npm-side post-publish install verification
