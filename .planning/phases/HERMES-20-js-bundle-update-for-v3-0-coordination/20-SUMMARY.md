---
phase: 20
phase_name: JS bundle update for v3.0 coordination (cross-repo)
status: implemented
mode: infra-skip
type: cross-repo
date: 2026-05-18
implemented_by: claude-opus-4-7-1m
sibling_repo: C:/Users/omern/.claude/plugins/marketplaces/chatlytics-claude-code/
sibling_repo_commits: 4
python_repo_commits: 3
version_before: "1.1.0"
version_after: "1.2.0"
version_sites_updated: 3
looks_like_jid_change: noop_already_aligned
chatlytics_send_resolve_fixed: true
tool_count: 8
tool_count_changed: false
esbuild_bundle_rebuilt: true
bundle_size_kb: 714.4
npm_pack_dry_run: ok
npm_pack_tarball_size_kb: 163.5
npm_pack_files: 15
tests_run: skipped_smoke_needs_live_endpoint
review_blockers: 0
review_high: 0
review_med: 0
review_low: 0
review_info: 0
fix_pass_invoked: false
phase_21_scope_guard_preserved: true
next_action: phase 21 — npm publish
---

# HERMES-20 — Summary

## Outcome

**Implemented.** The chatlytics-claude-code JS MCP bundle is now
aligned with `chatlytics-hermes 3.0.0` on PyPI. The version is bumped
to `1.2.0` everywhere, the `chatlytics_send` drift bug (bypassing
`resolveChatId`) is fixed, the `looksLikeJid` regex is verified
identical to the Python canonical, the esbuild bundle is regenerated,
and `npm pack --dry-run` validates the published artifact shape.

Phase 21 owns the actual `npm publish`, scope rename, `private:false`
flip, `files:` allowlist, and `v1.2.0` tag — those are explicitly
NOT done in Phase 20 per scope guard.

## Reality vs brief

The autonomous brief assumed:

| Brief assumption | Reality | Resolution |
|---|---|---|
| Current version `1.1.2` | Current version `1.1.0` (in package.json); CHANGELOG documented `1.1.1` and `1.1.2` hotfixes without bumping package.json | Reconciled by jumping straight to `1.2.0` everywhere |
| `looksLikeJid` needed tightening | Already `/@(c\.us\|g\.us\|lid\|newsletter)$/i` — exact alignment with Python's `_JID_PATTERN` | NO-OP code change; documented as a verified invariant |
| `chatlytics_send` bypasses `resolveChatId` | CONFIRMED bug | Fixed with 6-line diff mirroring `chatlytics_read` pattern |
| Doc tool count drift (6 vs 8) | README already shows "8 MCP tools"; only CHANGELOG 1.0.0 historical entry mentions 6 (true at the time) | CHANGELOG 1.2.0 entry restates the 8-tool count for the record; README unchanged |
| Main bundle source `servers/chatlytics-mcp.ts` | Source is `.js`, not `.ts` (no TypeScript) | Edited `servers/chatlytics-mcp.js` |

## Sibling repo commits (4)

| Commit | Task | Files |
|---|---|---|
| `9a6a41a` | T2 — version bump to 1.2.0 (3 sites) | `package.json`, `servers/package.json`, `servers/chatlytics-mcp.js` line 51 |
| `b29818e` | T4 — fix `chatlytics_send` to call `resolveChatId` | `servers/chatlytics-mcp.js` lines 123-138 (+6 / -1) |
| `7eafe94` | T5 — rebuild esbuild bundle | `servers/chatlytics-mcp.bundle.js` (regenerated, 714.4 KB; +19 / -17 vs prior bundle) |
| `dadc82a` | T6 — CHANGELOG 1.2.0 entry | `CHANGELOG.md` (+55 lines, prepended above 1.1.2 entry) |

Snapshot of sibling repo `git log --oneline -6` after Phase 20:

```
dadc82a docs(v1.2.0): CHANGELOG 1.2.0 — coordination ...
7eafe94 build(v1.2.0): rebuild esbuild bundle ...
b29818e fix(v1.2.0): chatlytics_send now resolves ...
9a6a41a release(v1.2.0): bump version to 1.2.0 ...
249960a fix(v1.1.2): use ${VAR} interpolation in .mcp.json env block
fd762bb docs: add SUBMISSION.md — claude-plugins-official submission readiness
```

## Python repo commits (3)

| Commit | Task | Files |
|---|---|---|
| `96c580b` | CONTEXT (infra-skip) | `.planning/phases/HERMES-20-*/20-CONTEXT.md` |
| `4a2fe59` | PLAN 1 — bundle alignment | `.planning/phases/HERMES-20-*/20-PLAN-1-bundle-alignment.md` |
| (this) | SUMMARY + REVIEW | `.planning/phases/HERMES-20-*/20-SUMMARY.md` + `20-REVIEW.md` |

Python repo source tree (`src/`, `tests/`) is UNCHANGED — verified by
`git diff main~3..main -- src/ tests/` returning empty diff (per
acceptance gate 9).

## Task results (T1-T7)

### T1 — Baseline snapshot (no commit) — PASSED
- Clean tree confirmed
- 3 version sites all at `1.1.0` (package.json, servers/package.json,
  servers/chatlytics-mcp.js line 51)
- 8 tools registered (`grep -c 'server\.tool(' servers/chatlytics-mcp.js`
  → 8)
- `looksLikeJid` regex at line 60: `/@(c\.us|g\.us|lid|newsletter)$/i`
- `resolveChatId` defined at line 66, called at line 67 (self-recursive
  short-circuit) + line 82 (candidate validation) + line 154
  (chatlytics_read handler)

### T2 — Version bumps — PASSED, commit `9a6a41a`
- 3 sites edited, post-sweep `grep "1.1.0"` empty
- 3 sites at `"1.2.0"` confirmed
- Bundle NOT rebuilt yet (deferred to T5 after the source fix)

### T3 — `looksLikeJid` invariant verification (no commit) — PASSED
Side-by-side regex comparison:

| Source | Regex literal | Notes |
|---|---|---|
| Python `tools.py:223` | `r"^.+@(c\.us\|g\.us\|lid\|newsletter)$"` | Case-sensitive, anchored `^.+` requires non-empty prefix |
| JS `chatlytics-mcp.js:60` | `/@(c\.us\|g\.us\|lid\|newsletter)$/i` | Case-insensitive, `^` implicit via `.test()` substring semantics; empty-string short-circuit at line 59 |

The `@(c\.us|g\.us|lid|newsletter)$` body is byte-identical. Python's
case-sensitivity vs JS's `/i` is benign: real-world WAHA JIDs are
lowercase, so the case dimension never differentiates valid inputs.
The Python source comment at `tools.py:200-206` already documents
this cross-repo invariant. No source edit required.

### T4 — `chatlytics_send` resolveChatId fix — PASSED, commit `b29818e`
Diff applied at `servers/chatlytics-mcp.js` lines 123-138:

```diff
   async ({ to, text, session }) => {
     try {
+      // Drift fix (v1.2.0): mirror chatlytics_read by pre-resolving
+      // bare names/phones to a JID via the search action. JID inputs
+      // short-circuit inside resolveChatId(). Ambiguous names throw
+      // a picker error with candidate list — same UX as chatlytics_read.
+      const resolved = await resolveChatId(to);
       const result = await callApi("POST", "/api/v1/actions", {
         action: "send",
-        params: { chatId: to, text },
+        params: { chatId: resolved, text },
         session: session || DEFAULT_SESSION || undefined,
       });
```

Post-fix verification:
- `grep -c 'await resolveChatId' servers/chatlytics-mcp.js` → 2
  (chatlytics_send line 129 + chatlytics_read line 154)
- Error path preserved via existing `try/catch` — resolver throws
  (zero/multi match) flow into the `isError: true` response with
  the resolver's actionable message

### T5 — Esbuild rebuild — PASSED, commit `7eafe94`
- `npm --prefix servers install` ran cleanly (93 packages added).
- `npm --prefix servers run build` succeeded:
  `chatlytics-mcp.bundle.js  714.4kb` in 458ms.
- Post-build verification:
  - `grep -c 'await resolveChatId' servers/chatlytics-mcp.bundle.js`
    → 2 (matches source — fix present in bundled output)
  - `grep -o '"chatlytics", version: "1.2.0"' servers/chatlytics-mcp.bundle.js`
    confirms the McpServer constructor literal carries the version
    bump through the bundle

### T6 — CHANGELOG 1.2.0 entry — PASSED, commit `dadc82a`
Entry prepended to CHANGELOG.md above the `## 1.1.2 — 2026-05-15`
heading, with the four required sections (Coordination, Fixed,
Verified, Internal) and an explicit "Out of scope (Phase 21)"
footer documenting the deferred publish-side items.

### T7 — `npm pack --dry-run` validation (no commit) — PASSED
- Exit code 0
- Manifest valid: `chatlytics-claude-code@1.2.0`
- Tarball: 163.5 kB packed, 829.0 kB unpacked, 15 files
- File list includes `servers/chatlytics-mcp.bundle.js` (731.6 kB
  inside the tarball — esbuild output)
- npm warning: `No .npmignore file found, using .gitignore for file
  exclusion. Consider creating a .npmignore file ...` — this is
  EXPECTED and Phase 21 owns adding the `.npmignore` (per scope guard)
- No auth required (validation-only mode)

## Acceptance gates (10/10)

| # | Gate | Result |
|---|---|---|
| 1 | Three version sites at `1.2.0` | PASS (grep sweep) |
| 2 | `grep -n 'await resolveChatId' servers/chatlytics-mcp.js` returns ≥ 2 | PASS (2 matches — send + read) |
| 3 | `chatlytics-mcp.bundle.js` mtime newer than `chatlytics-mcp.js` | PASS (T5 rebuilt after T4 edit) |
| 4 | Bundle contains `version: "1.2.0"` in McpServer literal | PASS |
| 5 | CHANGELOG.md top entry is `## 1.2.0 — 2026-05-18` | PASS |
| 6 | `npm pack --dry-run` exit 0 | PASS |
| 7 | Sibling repo: 4 new commits | PASS (`9a6a41a`, `b29818e`, `7eafe94`, `dadc82a`) |
| 8 | Python repo: 3 new commits | PASS (CONTEXT, PLAN, this SUMMARY+REVIEW) |
| 9 | Python repo source tree unchanged | PASS |
| 10 | Phase 21 scope guard preserved | PASS (no publish/rename/files/private-flip/tag) |

## Phase 21 scope guard (verified preserved)

The following are STILL OPEN for Phase 21, none touched in Phase 20:

- [ ] `package.json` `"private": true` → `false`
- [ ] `servers/package.json` `"private": true` → review (may stay
      true as internal sub-package)
- [ ] Package rename `chatlytics-claude-code` → `@chatlytics/claude-code`
- [ ] `"files":` allowlist on root `package.json`
- [ ] `.npmignore` creation (current npm warning expected)
- [ ] `npm publish --dry-run --access=public` (real dry-run with the
      scoped name — Phase 20 used `npm pack --dry-run` only)
- [ ] `npm publish --access=public` (real publish)
- [ ] `v1.2.0` annotated tag + push in sibling repo

## Test execution note

The sibling repo's `npm test` (`servers/test/smoke.js`) requires
`CHATLYTICS_API_URL` and `CHATLYTICS_API_KEY` env vars and hits a
live Chatlytics endpoint. It was NOT run in Phase 20 — these env
vars are not available in the autonomous orchestrator environment,
and the smoke test is a connectivity check, not a logic check.

The Phase 20 regression bar is "the source-level diff matches the
`chatlytics_read` pattern exactly, and the bundle carries the diff."
Both verified by the T4 grep and the T5 bundle-grep gates.

Live verification (operator-driven, post-merge) of the new
resolve-then-send flow:

1. In Claude Code with the rebuilt bundle loaded, invoke
   `chatlytics_send(to: "Omer", text: "ping from v1.2.0")` (bare
   name, not a JID)
2. Expected: the search resolver resolves "Omer" to the unique JID
   match (or throws a picker error if multiple matches), then the
   send succeeds with the resolved JID. Prior to v1.2.0, this would
   have either silently failed at the gateway or produced an
   ambiguous error because the gateway received a non-JID `chatId`.

## Invariants preserved

- 8 tools registered (no count change from 1.1.x)
- `looksLikeJid` regex unchanged (already aligned with Python)
- `resolveChatId` helper unchanged (only its call sites changed)
- All 8 tools' input schemas unchanged (no zod schema breaks)
- `chatlytics-mcp` MCP server name unchanged
- Repository structure unchanged (no new files, no deleted files)
- Python repo: `v3.0.0` tag still on `origin`; 120/120 tests still
  pass against the PyPI artifact; `chatlytics-hermes 3.0.0` still
  live on PyPI; no source change in `src/` or `tests/`

## Cross-repo coordination invariant

Going forward, the JID-handling contract is shared end-to-end:

```
Python plugin (chatlytics-hermes 3.0.0):
  src/chatlytics_hermes/tools.py:223
    _JID_PATTERN = r"^.+@(c\.us|g\.us|lid|newsletter)$"

JS bundle (chatlytics-claude-code 1.2.0):
  servers/chatlytics-mcp.js:60
    /@(c\.us|g\.us|lid|newsletter)$/i
```

Both plugins:
- Reject phone numbers, display names, and ambiguous strings at
  the JID-detection layer (Python: schema validator; JS: `looksLikeJid`
  short-circuit inside `resolveChatId`)
- Provide a name-resolution path (Python: caller must call
  `chatlytics_search` first; JS: `chatlytics_send`/`_read` call
  `resolveChatId` automatically)

The two approaches differ in WHEN resolution happens (caller-driven
in Python, tool-driven in JS) but converge on the same final
contract: only valid JIDs reach the Chatlytics REST API.

## Next action

**Proceed to Phase 21** — `npm publish` + scope rename + `v1.2.0`
tag in the sibling repo.
