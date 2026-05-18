---
phase: 20
plan: 1
plan_name: chatlytics-claude-code 1.2.0 bundle alignment (cross-repo)
mode: infra-skip
date: 2026-05-18
status: ready
sibling_repo: C:/Users/omern/.claude/plugins/marketplaces/chatlytics-claude-code/
---

# HERMES-20 Plan 1 — chatlytics-claude-code 1.2.0 bundle alignment

All sibling-repo tasks (T1-T6) `cd` into the sibling repo first.
Commits land in the sibling repo's git history. T7 commits in the
Python repo only (`.planning/` artifact).

## Tasks (sequential; halt on any failure)

### T1 — Sibling repo baseline snapshot (sibling, NO COMMIT)
- `cd "C:/Users/omern/.claude/plugins/marketplaces/chatlytics-claude-code/"`
- `git status` confirms clean tree
- `git log --oneline -5` snapshot for the SUMMARY
- `grep -rn '"1\.1\.0"\|version: "1\.1\.0"' package.json servers/package.json servers/chatlytics-mcp.js`
  confirms 3 sites currently at `1.1.0`
- `grep -n 'looksLikeJid\|resolveChatId' servers/chatlytics-mcp.js`
  confirms regex and resolver positions match the CONTEXT decisions
- `grep -c 'server\.tool(' servers/chatlytics-mcp.js` confirms tool
  count is 8

**No commit.** This is a sanity gate before any edit.

### T2 — Version bumps (sibling repo, 1 commit)
Edit three files in the sibling repo:
- `package.json` — `"version": "1.1.0"` → `"version": "1.2.0"`
- `servers/package.json` — `"version": "1.1.0"` → `"version": "1.2.0"`
- `servers/chatlytics-mcp.js` line 51 —
  `new McpServer({ name: "chatlytics", version: "1.1.0" })` →
  `new McpServer({ name: "chatlytics", version: "1.2.0" })`

Post-edit sweep:
`grep -rn '"1\.1\.0"\|version: "1\.1\.0"' package.json servers/package.json servers/chatlytics-mcp.js`
must return nothing.

**Commit (in sibling repo cwd):**
`release(v1.2.0): bump version to 1.2.0 across root pkg, servers pkg, and McpServer constructor`

Note: the bundle is NOT rebuilt yet — that's T5 (after the source
fix in T4). Bumping version first means the rebuilt bundle in T5
already carries the new version literal.

### T3 — `looksLikeJid` invariant verification (sibling, NO COMMIT)
- Read line 60 of `servers/chatlytics-mcp.js`. Confirm regex is
  exactly `/@(c\.us|g\.us|lid|newsletter)$/i` (case-insensitive,
  four families, dollar-anchored, NO extra captures).
- Read the Python canonical regex from
  `D:/docker/chatlytics-hermes-split/src/chatlytics_hermes/tools.py`
  — search for `_JID_RE` or the regex literal
  `/@(c\.us|g\.us|lid|newsletter)$/i` equivalent (Python syntax:
  `re.compile(r"@(c\.us|g\.us|lid|newsletter)$", re.IGNORECASE)`).
- Document both regex literals + the byte-equal comparison in the
  SUMMARY under "Invariant verified".

**No commit.** Pure verification.

### T4 — Fix `chatlytics_send` to call `resolveChatId` (sibling repo, 1 commit)
Edit `servers/chatlytics-mcp.js` lines 123-130 (the `chatlytics_send`
handler body). Apply this exact diff pattern (mirrors `chatlytics_read`
at line 149):

```diff
   async ({ to, text, session }) => {
     try {
+      // Drift fix (v1.2.0): mirror chatlytics_read by pre-resolving
+      // bare names/phones to a JID via the search action. JID inputs
+      // short-circuit inside resolveChatId(). Ambiguous names throw
+      // a picker error with candidate list.
+      const resolved = await resolveChatId(to);
       const result = await callApi("POST", "/api/v1/actions", {
         action: "send",
-        params: { chatId: to, text },
+        params: { chatId: resolved, text },
         session: session || DEFAULT_SESSION || undefined,
       });
       return { content: [{ type: "text", text: JSON.stringify(result, null, 2) }] };
     } catch (e) {
       return { isError: true, content: [{ type: "text", text: e.message }] };
     }
   }
```

Verify the diff:
- `grep -A 3 'async ({ to, text, session })' servers/chatlytics-mcp.js`
  shows the new `const resolved = await resolveChatId(to);` line
- `grep -B 1 'chatId: resolved' servers/chatlytics-mcp.js` confirms
  the payload now uses `resolved` not `to`
- The handler still catches errors via the existing `try/catch`
  so `resolveChatId` throws (zero/multi match) become `isError: true`
  responses with the resolver's actionable message — same UX as
  `chatlytics_read`.

**Commit (in sibling repo cwd):**
`fix(v1.2.0): chatlytics_send now resolves bare names/phones via resolveChatId (drift bug — was bypassing the resolver while chatlytics_read used it)`

### T5 — Rebuild esbuild bundle (sibling repo, 1 commit)
- `cd "C:/Users/omern/.claude/plugins/marketplaces/chatlytics-claude-code/"`
- If `servers/node_modules/` is missing, run `npm --prefix servers install`
  first (one-time, takes ~30s).
- Run the build: `npm --prefix servers run build`
  Expected: esbuild writes `servers/chatlytics-mcp.bundle.js`
  (size roughly unchanged from prior ~715 KB; the resolveChatId
  call adds ~one line of bundled output).
- Verify the new bundle carries the version bump and the fix:
  - `grep '"chatlytics".*"1.2.0"' servers/chatlytics-mcp.bundle.js`
    (MCP constructor literal moved through the bundle)
  - `grep 'await resolveChatId' servers/chatlytics-mcp.bundle.js`
    confirms the new call is present in the bundled output
    (will appear in both the `read` and `send` handlers now —
    minimum 2 hits)

**Commit (in sibling repo cwd):**
`build(v1.2.0): rebuild esbuild bundle with version bump + chatlytics_send resolver fix`

The committed bundle change includes whatever esbuild produces;
no manual edits to the bundle file.

### T6 — CHANGELOG 1.2.0 entry (sibling repo, 1 commit)
Prepend to `CHANGELOG.md` (above the `## 1.1.2 — 2026-05-15` heading)
the entry shaped per CONTEXT D4:

```markdown
## 1.2.0 — 2026-05-18

### Coordination

- Bundle aligned with **chatlytics-hermes 3.0.0** on PyPI
  (https://pypi.org/project/chatlytics-hermes/3.0.0/), the first
  public PyPI publish of the sibling Python Hermes plugin. The JS
  bundle and the Python plugin now share the same JID-handling
  contract end-to-end.

### Fixed

- **`chatlytics_send` was bypassing `resolveChatId()`** — a drift
  bug carried over from `1.0.0`. The handler now mirrors
  `chatlytics_read`: bare names and phone numbers are resolved
  to a JID via the `search` action before the API call. Existing
  JID-passing callers are unaffected (the resolver short-circuits
  on JID input). Ambiguous names return the same actionable
  picker error the `chatlytics_read` tool returns.

### Verified

- **`looksLikeJid()` regex** (`/@(c\.us|g\.us|lid|newsletter)$/i`)
  confirmed identical to chatlytics-hermes 3.0.0's Phase 14
  canonical JID rule. Phone numbers and display names are rejected
  at JID-detection time in BOTH plugins, ensuring uniform behavior
  across the Python Hermes plugin and the JS MCP bundle. No code
  change — alignment was already in place since `1.1.0`; this
  entry documents the cross-repo invariant for the record.

### Internal

- Esbuild bundle regenerated (`servers/chatlytics-mcp.bundle.js`).
- Version constants aligned across `package.json` (root),
  `servers/package.json`, and the `McpServer` constructor literal
  in `servers/chatlytics-mcp.js`. Drift between `1.1.0` in
  `package.json` and `1.1.2` in the CHANGELOG (artifact of the
  hotfix commits in `1.1.1` / `1.1.2` not bumping `package.json`)
  is reconciled by jumping straight to `1.2.0` everywhere.
- **8 tools registered** (no change from `1.1.x`):
  `chatlytics_send`, `chatlytics_read`, `chatlytics_search`,
  `chatlytics_actions`, `chatlytics_directory`,
  `chatlytics_health`, `chatlytics_login`, `chatlytics_dispatch`.

### Out of scope (Phase 21)

- npm publish (`@chatlytics/claude-code` first-ever publish)
- `"private": true` → `false` flip
- Package rename to scoped `@chatlytics/claude-code`
- `"files":` allowlist + `.npmignore`
- `v1.2.0` git tag
```

**Commit (in sibling repo cwd):**
`docs(v1.2.0): CHANGELOG 1.2.0 — coordination with chatlytics-hermes 3.0.0, chatlytics_send drift fix, looksLikeJid invariant`

### T7 — `npm pack --dry-run` validation (sibling, NO COMMIT)
- `cd "C:/Users/omern/.claude/plugins/marketplaces/chatlytics-claude-code/"`
- `npm pack --dry-run` from root
  - Expect: prints a tarball file list + summary line with size
  - Verify: NO auth required (this is a manifest validation only)
  - Verify: exit code 0
- Capture the output for the SUMMARY (record the file count + total
  tarball size). The actual `npm publish` is Phase 21.

**No commit.** Validation only.

### T8 — Phase 20 SUMMARY + REVIEW (Python repo, 1 commit)
- `cd D:/docker/chatlytics-hermes-split/`
- Write `.planning/phases/HERMES-20-js-bundle-update-for-v3-0-coordination/20-SUMMARY.md`
  with frontmatter (phase, mode, sibling_repo_commits, npm_pack output, etc.)
  and a section for each task (T1-T7) reporting actual outcome
- Write `.planning/phases/HERMES-20-js-bundle-update-for-v3-0-coordination/20-REVIEW.md`
  with inline orchestrator review (BLOCKER/HIGH/MED/LOW/INFO counts)
- Both files committed in the Python repo only

**Commit (in Python repo cwd):**
`docs(20): phase 20 summary + review (cross-repo bundle alignment)`

## Acceptance gates (autonomous)

1. Sibling repo `package.json`, `servers/package.json`, and
   `servers/chatlytics-mcp.js` McpServer constructor all read
   `version: 1.2.0` (or `"1.2.0"`).
2. `grep -n 'await resolveChatId' servers/chatlytics-mcp.js`
   returns ≥ 2 matches (read handler + send handler).
3. `servers/chatlytics-mcp.bundle.js` mtime is newer than
   `servers/chatlytics-mcp.js` (rebuild happened after edit).
4. `grep '"chatlytics-mcp"' servers/chatlytics-mcp.bundle.js` returns
   matching constructor with `1.2.0` literal in the bundle.
5. CHANGELOG.md top entry is `## 1.2.0 — 2026-05-18`.
6. `npm pack --dry-run` exits 0 in sibling repo root.
7. Sibling repo `git log` shows EXACTLY 4 new commits
   (T2 version, T4 resolveChatId fix, T5 bundle rebuild, T6 CHANGELOG).
8. Python repo `git log` shows EXACTLY 3 new commits
   (CONTEXT before this plan, PLAN itself, SUMMARY+REVIEW after).
9. Python repo source tree IS UNCHANGED
   (`git diff main~3..main -- src/ tests/` is empty —
   only `.planning/` files modified).
10. Phase 21 scope guard preserved (no `npm publish`, no `private:false`
    flip, no `@chatlytics/` rename, no `files:` allowlist, no
    `v1.2.0` tag — these MUST still be open work for Phase 21).
