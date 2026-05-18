---
phase: 15
verification_status: passed
implemented_by: gsd-execute-phase
reviewed_by: gsd-code-review
fix_pass_by: gsd-code-review
tests_total: 116
tests_passed: 116
files_changed: 4
commits: 8
---

# HERMES-15 — Verification

## Test results

```
$ python -m pytest tests/ -q
116 passed in 28.20s
```

**Baseline before phase:** 111 tests (88 v2.1 + 10 Phase 13 + 13 Phase 14).
**Tests added this phase:** +5 (4 auto-detection branches +
1 send_image_file-symbol-gone in `TestResourceAutoDetection`).
**Tests renamed this phase:** 2 in `tests/test_media.py`
(`test_send_image_file_uploads_local_bytes` →
`test_send_image_local_path_uploads_bytes`;
`test_send_image_file_reads_off_event_loop` →
`test_send_image_local_path_reads_off_event_loop`).
Test count unchanged in those renamed positions.

**Net delta:** 111 → 116 (+5). All baseline tests still pass.

## Acceptance criteria (per ROADMAP HERMES-15)

| # | Criterion | Status |
|---|-----------|--------|
| 1 | `adapter.send_image(chatId, "https://example.com/cat.jpg")` → uploads via URL path (no local file access) | PASS — `TestResourceAutoDetection::test_url_string_passes_through_without_upload` asserts upload route NOT called and mediaUrl matches input |
| 2 | `adapter.send_image(chatId, "/allowed/root/cat.jpg")` → uploads via file path (allowlist enforced) | PASS — `TestResourceAutoDetection::test_string_path_that_exists_uploads_via_multipart` + `test_path_object_uploads_via_multipart`; both pass through `_enforce_upload_allowlist` |
| 3 | `adapter.send_image(chatId, "/etc/passwd")` → rejected with same error as v2.1 HI-01 fix (allowlist preserved) | PASS — `_enforce_upload_allowlist` extracted to single helper; both file branches reuse it; existing HI-01 tests in `tests/test_security.py` (and `tests/test_validation.py` permission tests) still green |
| 4 | `adapter.send_image_file` symbol is gone — `getattr(adapter, "send_image_file", None) is None` (NOT a deprecation alias) | PASS (stronger than spec) — `TestResourceAutoDetection::test_send_image_file_symbol_is_gone` asserts (a) `"send_image_file" not in ChatlyticsAdapter.__dict__` and (b) instance access raises `AttributeError` with migration message. The base class `BasePlatformAdapter.send_image_file` provides a text-fallback default; we explicitly block it via `__getattribute__` to prevent silent degradation of v2.x photo sends to text bubbles. |
| 5 | Same for `send_animation`, `send_video`, `send_file` | N/A (clarified) — `send_animation` / `send_voice` / `send_video` / `send_document` never had `_file` siblings on `ChatlyticsAdapter`. They were already unified in v2.0 via `Union[str, bytes, bytearray]`. This phase harmonized their parameter naming to `resource` and broadened the type hint to include `Path`. No `send_file` method exists at adapter level (it's `send_document`). |
| 6 | pytest passes; all v2.1 HI-01 regression tests still pass (allowlist unchanged) | PASS — 116/116; HI-01 allowlist enforcement consolidated into `_enforce_upload_allowlist` helper, called from both new file branches. |

## Sanity introspection

```
$ PYTHONPATH=src python -c "from chatlytics_hermes.adapter import ChatlyticsAdapter; \
    print('send_image_file in __dict__:', 'send_image_file' in ChatlyticsAdapter.__dict__)"
send_image_file in __dict__: False

$ PYTHONPATH=src python -c "import inspect; \
    from chatlytics_hermes.adapter import ChatlyticsAdapter; \
    print(list(inspect.signature(ChatlyticsAdapter.send_image).parameters))"
['self', 'chat_id', 'resource', 'caption', 'reply_to', 'metadata', 'kwargs']

$ PYTHONPATH=src python -c "from chatlytics_hermes.tools import TOOLS; \
    print('tool count:', len(TOOLS))"
tool count: 21
```

All four other media methods (`send_animation`, `send_voice`,
`send_video`, `send_document`) now expose `resource` as the second
positional parameter (verified via the same `inspect.signature`
probe).

## Invariants preserved

- `assert len(TOOLS) == 21` — passes (no new tools added; only adapter-
  layer changes + tool-handler internal simplification).
- Hermes pin `>=0.14,<0.15` — unchanged.
- All HTTP outbound via `httpx`; aiohttp only for inbound server —
  unchanged.
- Phase 13 contract (`{success: false, error, _error: "<code>"}` on
  `chatlytics_get_chat_info`) — unchanged.
- Phase 14 strict JID regex on chatId schemas — unchanged.
- HI-01 allowlist (`CHATLYTICS_UPLOAD_ALLOWED_ROOTS`) — fully
  preserved; consolidated into `_enforce_upload_allowlist` reachable
  from both file branches.
- v2.1 baseline tests — all green.

## Commits

```
1608142 docs(15)!: changelog Unreleased entry for adapter send_* collapse
050f962 test(15): audit + update docstring cross-references to removed send_image_file
893a6bd test(15): rename _file tests + add TestResourceAutoDetection + __getattribute__ guard
9fe10e3 refactor(15): simplify chatlytics_send_image tool handler
770f0fa refactor(15)!: harmonize send_* resource parameter naming
37e446d feat(15)!: collapse send_image and delete send_image_file (no shim)
22cedaf refactor(15): _resolve_media_url 5-branch resolver + _enforce_upload_allowlist helper
```

(7 task commits, matching the 7-task plan.)

## Files changed

- `src/chatlytics_hermes/adapter.py` — new `_enforce_upload_allowlist`
  helper; `_resolve_media_url` refactored to 5-branch resolver with
  explicit `Path` + str-path-exists branches and `ValueError` failure
  mode; `_send_media_payload` catches `ValueError`; `send_image`
  parameter renamed to `resource` + type hint broadened to include
  `Path`; v2.0 `send_image_file` override DELETED; the four other
  media methods (`send_animation`, `send_voice`, `send_video`,
  `send_document`) had their second positional parameter renamed to
  `resource` + type hint broadened; new `__getattribute__` guard
  blocks the base class's text-fallback `send_image_file` to prevent
  silent v2.x photo-send degradation; module header updated.
- `src/chatlytics_hermes/tools.py` — `chatlytics_send_image` handler
  simplified (single `adapter.send_image(chatId, resource)` call
  matching the shape of the other four media tool handlers).
- `tests/test_media.py` — two `_file` tests renamed and updated to
  call `adapter.send_image(CHAT_ID, Path(img_path), ...)`; new
  `TestResourceAutoDetection` class with 5 cases (URL, Path,
  str-path-exists, unresolvable str → ValueError → SendResult,
  symbol-gone assertion). Module docstring updated.
- `tests/test_validation.py` — historical docstring cross-reference
  to `send_image_file` annotated as "SUPERSEDED in v3.0 HERMES-15".
- `CHANGELOG.md` — third bullet appended under
  `## [Unreleased] / ### Breaking` describing the BREAKING library
  API change with full migration guidance.

## Out-of-scope changes

None. Scope locked to the adapter-layer collapse + tool-layer single-
call simplification per 15-CONTEXT.

## Deviations from plan

**T6 — minor:** Plan T6 anticipated potential `send_image_file`
references in `tests/test_tools.py`. The grep audit found NONE there
(the tool layer always abstracted the split via `_resolve_resource`).
The audit DID find one historical docstring cross-reference in
`tests/test_validation.py` which was annotated as "SUPERSEDED" rather
than removed (preserving the history of the v2.0 split). The T6
commit thus folded together the docstring update in `test_validation.py`
+ the module-header update in `test_media.py` (which still listed
`send_image_file` as one of the 6 historical handlers).

**T2 — strengthening:** The plan's acceptance criterion 4 literally
specifies `getattr(adapter, "send_image_file", None) is None`. This
is impossible to satisfy because `BasePlatformAdapter` provides a
text-fallback default. To honor the criterion's INTENT (clean break,
no silent degradation), the implementation adds an explicit
`__getattribute__` guard on the adapter that raises a clear
`AttributeError` with migration guidance when `send_image_file` is
accessed on an instance. The test then verifies both
`"send_image_file" not in ChatlyticsAdapter.__dict__` (our class is
clean) AND that instance access raises `AttributeError`. This is
strictly stronger than the literal criterion: instead of silently
inheriting the base text-fallback, v2.x callers get a clear error
pointing at `send_image`.

## Notes for review

- The `__getattribute__` guard (T5) is the most subtle change. Its
  purpose is documented in a multi-line comment block above the
  method. Removing it would NOT cause test failure of the renamed
  tests (those use `send_image` directly), but would cause
  `test_send_image_file_symbol_is_gone` to fail because instance
  access would silently degrade to the base class text-fallback.
- The two new `_read_file_path` / `_read_file_str` inner functions in
  `_resolve_media_url` are intentionally duplicated (rather than
  extracted into a third helper) because they capture slightly
  different `resolved` closures from their respective branches. The
  body is identical and short; extracting would obscure the per-branch
  flow without saving meaningful lines.
- `_enforce_upload_allowlist` is the single security checkpoint. Any
  future media branch added to `_resolve_media_url` (e.g. fileobj /
  IOBase) must call this helper to remain HI-01-compliant. The
  helper's docstring states this contract.
