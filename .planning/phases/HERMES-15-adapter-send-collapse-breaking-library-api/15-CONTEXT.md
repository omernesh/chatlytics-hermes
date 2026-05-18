---
phase: 15
phase_slug: adapter-send-collapse-breaking-library-api
phase_name: "Adapter `send_*` collapse (BREAKING library API)"
project_code: HERMES
milestone: v3.0
infra_skip: true
infra_skip_reason: "Scope is fix-locked per v3.0 ROADMAP HERMES-15 + the operator's autonomous-launch brief. The unified signature (`async def send_image(chatId, resource: str | Path, ...)`), the auto-detection rule (Path → file, http(s):// str → URL, str path that exists → file, otherwise ValueError), the no-shim clean-break policy (delete `_file` variants entirely — no deprecation wrapper), and the test update plan are all encoded by the operator before launch. No grey areas need user discussion — gsd-discuss-phase would only paraphrase locked decisions."
---

# HERMES-15 — Adapter `send_*` collapse (BREAKING library API) — CONTEXT

## Domain (Phase boundary from ROADMAP goal)

Collapse the lower **adapter-layer** paired send methods so each media
type has exactly ONE entry point that auto-detects whether the
``resource`` argument is a URL or a local path.

**In-scope adapter methods (this is the actual surface area):**

- `send_image(chatId, image_url, ...)` + `send_image_file(chatId, image_path, ...)`
  → collapse to `send_image(chatId, resource: str | Path, ...)`

Audit of `adapter.py` (grep-confirmed) shows that ONLY `send_image`
has a `_file`-suffixed sibling. The other media methods
(`send_voice`, `send_video`, `send_document`, `send_animation`)
already accept `Union[str, bytes, bytearray]` and route through the
shared `_resolve_media_url` / `_send_media_payload` helpers. There is
no `send_voice_file` / `send_video_file` / `send_animation_file` /
`send_file_file` to collapse. There is no top-level `send_file` —
the document handler is named `send_document` (matching the upstream
``BasePlatformAdapter`` contract).

**Scope of "same collapse" for the other media types** therefore
becomes: harmonize their resource parameter to the canonical
``resource: str | Path`` shape (currently typed
``Union[str, bytes, bytearray]``) so the public adapter API is
internally consistent. The existing `bytes` / `bytearray` branch in
`_resolve_media_url` stays — bytes-upload is a legitimate non-URL
non-Path case and dropping it would break HERMES-04 acceptance
test 6 (`test_send_image_file_uploads_local_bytes` via the bytes
path) and any caller that already passes bytes.

**Tool surface stays at 21.** The tool layer at `tools.py:740-776`
(the `chatlytics_send_image` handler — the only branched tool) is
the unify point that already presents one face to MCP / Claude Code
users. After this phase it simplifies: instead of branching on
`mediaUrl` vs `filePath` to choose between two adapter methods, it
hands either input to the single `adapter.send_image(resource)`. The
other four media tools (`send_voice`, `send_video`, `send_file`,
`send_animation`) already call ONE adapter method each via
`_resolve_resource` — they do not need code-level changes beyond
trivial annotation alignment.

This is a **BREAKING library API** change. Direct callers of
`ChatlyticsAdapter.send_image_file(...)` see an `AttributeError` on
upgrade — by design, operator preference: clean break, no
deprecation wrapper. Tool callers (`chatlytics_send_image` from
MCP / Hermes) are unaffected because the tool layer keeps the same
schema and same external behavior.

Closes v2.1 deferred item 3.

## Decisions (encoded from operator-locked phase brief)

### D1 — Unified adapter signature

```python
async def send_image(
    self,
    chat_id: str,
    resource: Union[str, "Path", bytes, bytearray],
    caption: Optional[str] = None,
    reply_to: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    **kwargs: Any,
) -> "SendResult":
    ...
```

- Parameter name **`resource`** (not `image_url`, not `image_path`,
  not `media`) — the canonical name encoded in the operator brief.
  Existing parameter names on the four already-unified methods
  (`audio_path`, `video_path`, `file_path`, `animation_url`) are
  renamed to `resource` for internal consistency. Callers using
  positional args are unaffected; keyword-arg callers see a rename
  but the brief is explicit that this is a clean BREAKING change.
- Type hint extends to include `Path` for explicit ergonomics. The
  existing `bytes` / `bytearray` cases STAY — see the in-scope
  audit above for the rationale.

### D2 — Auto-detection rule (uniform across all five media methods)

The rule lives in `_resolve_media_url`. Apply IN ORDER:

```python
# 1. bytes/bytearray → upload raw (preserved from v2.0/v2.1)
if isinstance(resource, (bytes, bytearray)):
    ...upload bytes...
# 2. Path object → local file (new branch)
elif isinstance(resource, Path):
    ...read & upload (allowlist enforced)...
# 3. str + http(s):// prefix → URL passthrough
elif isinstance(resource, str) and resource.startswith(("http://", "https://")):
    return resource
# 4. str + Path(resource).exists() → local file
elif isinstance(resource, str) and Path(resource).expanduser().exists():
    ...read & upload (allowlist enforced)...
# 5. otherwise → ValueError
else:
    raise ValueError(
        "resource must be a URL (http://, https://) or a local file path "
        "that exists"
    )
```

**Order matters.** bytes-first preserves the HERMES-04 bytes-upload
contract. Path-second handles explicit `Path()` objects without
triggering the str branches. URL-third is a fast string-prefix check.
Path-fourth requires a filesystem `exists()` call — only reached
when none of the above match.

The `exists()` check matches the operator brief verbatim
("`isinstance(resource, str)` and it's a valid local path
(`Path(resource).exists()`)"). It DOES catch malformed input like
`"some-garbage-string"` and surface it as a clear ValueError instead
of letting the allowlist check do that later with a less obvious
error.

### D3 — Clean break: no `_file` variants, no deprecation aliases

Per PROJECT.md "Out of Scope" line ("Backward-compat shims for the
removed adapter methods (HERMES-15) — operator preference: clean
break") and ROADMAP HERMES-15 acceptance criterion 4
(`getattr(adapter, "send_image_file", None) is None` — **NOT a
deprecation alias**):

- DELETE `ChatlyticsAdapter.send_image_file` entirely. No shim. No
  `warnings.warn(DeprecationWarning, ...)`. No alias attribute. The
  symbol is **gone**.
- Update class docstring + module header to drop the
  ``send_image_file`` references that document the v2.0 split.

### D4 — Path allowlist preserved (DO NOT REGRESS HI-01)

The `CHATLYTICS_UPLOAD_ALLOWED_ROOTS` allowlist enforcement in
`_resolve_media_url` (HERMES-08 HI-01 fix) is **load-bearing
security** and must keep functioning unchanged. The new `Path` object
branch and the new `str + exists()` branch must both flow into the
same allowlist check that lives at `adapter.py:802-836`. Currently
the check sits in the `else` branch (treating everything non-bytes /
non-URL as a path). The refactor must preserve the same check for
the new Path-explicit and exists()-implicit branches.

### D5 — Tool-layer simplification (opportunistic)

`tools.py:740-776` (`chatlytics_send_image`) has explicit
`if mediaUrl: adapter.send_image(...) else: adapter.send_image_file(...)`
branching. After D3 there is only ONE method to call:

```python
resource = _resolve_resource(mediaUrl=mediaUrl, filePath=filePath)
if not resource:
    return {"success": False, "error": "Either mediaUrl or filePath is required."}
result = await adapter.send_image(chatId, resource, caption=caption)
return _media_result_dict(result)
```

This matches the shape of the other four `chatlytics_send_*` media
tool handlers exactly. The other four are already in this shape and
don't need changes. The `_resolve_resource` helper itself
(`tools.py:726-737`) does NOT need refactoring — it picks one of
two kwargs and the adapter now does the rest.

Do this refactor **only at the `chatlytics_send_image` site**. Do
not aggressively rewrite `_resolve_resource` or touch other media
tool handlers — that would sprawl beyond the phase boundary.

### D6 — Test updates

`tests/test_media.py` references:

- `await adapter.send_image_file(CHAT_ID, str(img_path), caption="local")`
  in `test_send_image_file_uploads_local_bytes` (line 208)
- `await adapter.send_image_file(CHAT_ID, str(img_path), caption="thr")`
  in `test_send_image_file_reads_off_event_loop` (line 278)

**Update strategy:**

- Rename the test functions to drop `_file` and pass `Path(img_path)`
  (Path-object branch) for the canonical post-collapse signature
  exercise. The test function names should reflect the unified API
  (e.g. `test_send_image_local_path_uploads_bytes`,
  `test_send_image_local_path_reads_off_event_loop`).
- The signature changes from `adapter.send_image_file(CHAT_ID, str(img_path), ...)`
  to `adapter.send_image(CHAT_ID, Path(img_path), ...)`.
- Add NEW tests covering the four auto-detection branches:
  1. URL string (`"https://..."` → URL passthrough; no upload call)
  2. Path object (`Path(...)` → upload via multipart)
  3. String path that exists (`str(tmp_path / "x.png")` where the file
     exists → upload via multipart, same outcome as Path object)
  4. String that's neither URL nor existing path
     (`"not-a-url-not-a-path"` → `ValueError` raised at the adapter
     boundary, caught by `_send_media_payload`, surfaced as
     `SendResult(success=False, error=...)`)

These new tests live in `tests/test_media.py` in a new class
`TestResourceAutoDetection` (consistent with HERMES-14's pattern of
adding a focused test class for the new contract).

### D7 — `voice` / `sticker` / other variants

- `send_voice` exists and accepts `Union[str, bytes, bytearray]`
  already. No `_file` sibling to collapse. Apply the type-annotation
  alignment (`resource: Union[str, Path, bytes, bytearray]`) and
  rename the kwarg to `resource`.
- No `send_sticker` method exists at the adapter or tool layer.
  Skipped (out of scope — stickers were never added to v2.0/v2.1).

### D8 — Scope guards (DO NOT TOUCH)

- **Tool surface count** stays at **21**. `len(TOOLS) == 21` test
  must still pass. No new tools, no removed tools.
- **Tool schemas** — already locked by HERMES-14. Do NOT modify any
  `chatlytics_send_image_schema` / `chatlytics_send_voice_schema` /
  etc.
- **Version bump** in `pyproject.toml` / `plugin.yaml` — Phase 19
  owns release bumps.
- **CHANGELOG.md** — append a bullet under
  `## [Unreleased] / Breaking` next to the Phase 13 + 14 entries.
  Phase 19 finalizes the release notes.
- **No git push / no publish.**
- **HI-01 allowlist tests** — these MUST continue to pass.
  `tests/test_security.py` and any HERMES-08-era regression tests
  asserting `/etc/passwd` is rejected must remain green without
  modification.
- **Phase 13 `_error` sentinel** — not affected by this phase.
- **Phase 14 JID regex** — not affected by this phase.

### D9 — Tool count and contract invariants

- Tool surface stays at **21 tools** — `assert len(TOOLS) == 21` still
  holds.
- All tool handlers still return `{"success": bool, ...}`.
- `chatlytics_send_image` tool still returns the same dict shape it
  did before; the change is internal (single adapter call instead of
  branched two).

## Code context (files touched + established patterns)

### Files to modify

| File | Change |
|------|--------|
| `src/chatlytics_hermes/adapter.py` | (1) `_resolve_media_url` gets the new 5-branch resolver with explicit `Path` and `str-path-exists` handling, raising `ValueError` otherwise. (2) `send_image` signature: rename `image_url` → `resource`, broaden type hint to include `Path`. (3) DELETE `send_image_file` entirely. (4) `send_animation` / `send_voice` / `send_video` / `send_document`: rename their `*_url` / `*_path` kwarg to `resource`, broaden type hint to include `Path`. Body unchanged. (5) Update module header + class docstring to drop v2.0 split references. |
| `src/chatlytics_hermes/tools.py` | `chatlytics_send_image` simplified: drops the `if mediaUrl ... else ...` branch and calls single `adapter.send_image(chatId, resource, caption=caption)` like the other four media tool handlers. Update docstring to reflect the unified call. |
| `tests/test_media.py` | Rename `test_send_image_file_uploads_local_bytes` → `test_send_image_local_path_uploads_bytes`; call `adapter.send_image(CHAT_ID, Path(img_path), caption=...)`. Same for `test_send_image_file_reads_off_event_loop`. Add new `TestResourceAutoDetection` class covering the 4 detection branches. |
| `tests/test_tools.py` | If any test references `adapter.send_image_file` directly, update to `adapter.send_image` with the new shape. (Audit during execute.) |
| `tests/test_security.py` (or wherever HI-01 regression lives) | Audit only — should not need changes; the allowlist runs on the same code path. Verify under `pytest` run. |
| `CHANGELOG.md` | Add bullet under `## [Unreleased] / Breaking`: "Library API: `ChatlyticsAdapter.send_image_file` removed. Use `adapter.send_image(chatId, resource: str \| Path)` with the auto-detection rule. Tool surface (`chatlytics_send_image` etc.) unchanged." |

### Established patterns

- **Single helper, multiple call sites** — `_resolve_media_url` is
  already the single resolver for all media methods. Tighten it here
  and all five media handlers inherit the new behavior.
- **`SendResult(success=False, error=...)` on resolver errors** —
  `_send_media_payload` already catches `PermissionError`,
  `OSError`, `RuntimeError`, `httpx.RequestError`. Add `ValueError`
  to that catch list so the new "not-a-URL-not-a-path" rejection
  surfaces as a clean `SendResult` instead of an uncaught raise.
- **HI-01 allowlist** — flows through the same `_resolve_media_url`
  local-path branch. The refactor MUST keep the allowlist check
  reachable from BOTH the new Path-object branch AND the new
  str-path-exists branch. Easiest: extract the allowlist check into
  an inline helper `def _enforce_allowlist(path_str: str) -> Path`
  and call it from both branches.

### Reference: tool-layer call sites (do not touch their schemas)

`tools.py` tool handlers for the five media tools, currently:

- `chatlytics_send_image` (lines 740-776) — branched
  (mediaUrl→send_image vs filePath→send_image_file). **This one
  simplifies.**
- `chatlytics_send_voice` (lines 779-794) — already unified via
  `_resolve_resource` + `adapter.send_voice`. **No change.**
- `chatlytics_send_video` (lines 797-812) — same. **No change.**
- `chatlytics_send_file` (lines 815-833) — calls
  `adapter.send_document`. **No change.**
- `chatlytics_send_animation` (lines 836-851) — same. **No change.**

## Specifics (sequencing)

- **Phase 13** (`_error` sentinel) — landed; not in this phase's path.
- **Phase 14** (strict JID regex) — landed; not in this phase's path.
- **Phase 15 (this)** — adapter-layer breaking change. Tool surface
  STAYS at 21 — only the internal adapter API breaks. Tool layer
  simplification is opportunistic at one call site, not required
  beyond `chatlytics_send_image`.
- **Phase 16 (next)** — `smoke.sh` wheel caching. Independent.

## Deferred

**None** — scope is locked to the adapter-layer collapse per the
operator brief. No backward-compat wrappers, no broader media API
restructuring, no new media types.
