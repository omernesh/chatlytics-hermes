---
phase: 15
plan_index: 1
plan_slug: adapter-send-collapse
title: "Adapter send_* collapse (BREAKING library API)"
project_code: HERMES
milestone: v3.0
status: ready
infra_skip: true
verification: pytest
---

# HERMES-15 Plan 1 — Adapter `send_*` collapse

## Goal

Collapse the paired adapter-layer media-send methods into a single
unified entry point per media type, with `resource: str | Path` auto-
detection (URL passthrough vs local-file upload vs explicit `ValueError`
on unresolvable input). DELETE `ChatlyticsAdapter.send_image_file`
entirely (no deprecation wrapper). Harmonize the parameter name on the
four already-unified media methods to `resource`. Simplify
`tools.py::chatlytics_send_image` to call the single unified method.

Closes v2.1 deferred item 3.

## Scope (locked per 15-CONTEXT.md)

**In:**
- `src/chatlytics_hermes/adapter.py`:
  - `_resolve_media_url` — new 5-branch resolver (bytes → Path object →
    URL string → string-path-that-exists → `ValueError`). HI-01
    allowlist enforcement stays reachable from both file branches.
  - `send_image` — rename `image_url` kwarg to `resource`; broaden
    type hint to `Union[str, Path, bytes, bytearray]`. Update
    docstring.
  - DELETE `send_image_file` entirely (no alias, no shim).
  - `send_animation` — rename `animation_url` kwarg to `resource`;
    broaden type hint to include `Path`. Update docstring.
  - `send_voice` — rename `audio_path` kwarg to `resource`; broaden
    type hint to include `Path`. Update docstring.
  - `send_video` — rename `video_path` kwarg to `resource`; broaden
    type hint to include `Path`. Update docstring.
  - `send_document` — rename `file_path` kwarg to `resource`; broaden
    type hint to include `Path`. Update docstring.
  - `_send_media_payload` — add `ValueError` to the exception catch
    list (alongside `PermissionError`, `OSError`, `RuntimeError`,
    `httpx.RequestError`) so unresolvable resources surface as
    `SendResult(success=False, error=...)` instead of an uncaught
    raise.
  - Module header + class docstring — drop the v2.0 "send_image_file
    legacy alias" references.
- `src/chatlytics_hermes/tools.py`:
  - `chatlytics_send_image` — drop the `if mediaUrl/else send_image_file`
    branch. Use the same shape as the other four media tool handlers:
    `_resolve_resource(...)` → single `adapter.send_image(...)` call.
    Update docstring.
- `tests/test_media.py`:
  - Rename `test_send_image_file_uploads_local_bytes` →
    `test_send_image_local_path_uploads_bytes`. Change call from
    `adapter.send_image_file(CHAT_ID, str(img_path), ...)` to
    `adapter.send_image(CHAT_ID, Path(img_path), ...)`.
  - Rename `test_send_image_file_reads_off_event_loop` →
    `test_send_image_local_path_reads_off_event_loop`. Same call shape
    change.
  - Add new `TestResourceAutoDetection` class with 4 parametrized
    cases (URL string, Path object, str-path-exists, unresolvable str).
- `tests/test_tools.py` (audit) — switch any direct
  `adapter.send_image_file(...)` call to the new shape.
- `CHANGELOG.md` — append a bullet under
  `## [Unreleased] / ### Breaking`.

**Out:**
- Tool schema changes (HERMES-14 locked schemas).
- Tool count change — `assert len(TOOLS) == 21` stays.
- Version bump in `pyproject.toml` / `plugin.yaml` (Phase 19 owns).
- README rewrite (Phase 19 owns).
- New media types (e.g. `send_sticker`) — none added.
- Adapter `send_voice_file` / `send_video_file` / `send_animation_file`
  / `send_file_file` — these methods DO NOT EXIST on `ChatlyticsAdapter`
  (confirmed via grep). Only `send_image_file` exists and is the only
  `_file` variant to delete.
- Backward-compat shim / deprecation wrapper for `send_image_file` —
  operator preference per PROJECT.md "Out of Scope".
- `_resolve_resource` helper in `tools.py` — does NOT need refactoring;
  it still picks one of two kwargs and the adapter now does the rest.
- Modifying `get_chat_info` (Phase 13 — shipped).
- Modifying `_chat_id_field` / `_JID_PATTERN` (Phase 14 — shipped).
- Pushing to git / publishing.

## Invariants (DO NOT REGRESS)

- **111/111 baseline tests still pass** (88 v2.1 + 10 Phase 13 + 13
  Phase 14). The two renamed tests in `test_media.py` keep the same
  test count; the new `TestResourceAutoDetection` class adds 4 cases,
  pushing the total to 115.
- `assert len(TOOLS) == 21` invariant in `tools.py` stays satisfied.
- HI-01 allowlist (`CHATLYTICS_UPLOAD_ALLOWED_ROOTS` default-deny on
  empty allowlist; rejects paths outside the allowlist) — fully
  preserved. Any v2.1 regression test asserting `/etc/passwd` is
  rejected MUST stay green without modification.
- Hermes pin stays `>=0.14,<0.15`.
- All HTTP outbound via `httpx`; aiohttp only for inbound server.
- Phase 13's `_error: "<code>"` contract on
  `chatlytics_get_chat_info` unchanged.
- Phase 14's `_JID_PATTERN` JID-only validation on chatId schemas
  unchanged.
- Bytes-upload path (`isinstance(resource, (bytes, bytearray))`)
  preserved — caller pattern from HERMES-04 still works.
- `chatlytics_send_image` tool's external shape (input schema, output
  dict shape) unchanged. The internal call site simplifies, but the
  tool's MCP/Hermes contract is identical.

## Tasks (atomic; each commits independently)

### T1 — Refactor `_resolve_media_url` with 5-branch auto-detection + HI-01 helper

**File:** `src/chatlytics_hermes/adapter.py`

Current `_resolve_media_url` (lines 757-866) handles bytes →
URL-prefix-string → local-path-string (catch-all `else`). The HI-01
allowlist enforcement lives inline in the local-path branch.

Refactor in TWO steps:

**Step 1a.** Extract the allowlist check into a private method
`_enforce_upload_allowlist(self, path: Path) -> Path` so both new
file branches can reuse it:

```python
def _enforce_upload_allowlist(self, candidate: Path) -> Path:
    """Resolve + allowlist-check a local upload path. HI-01 fix preserved.

    Returns the resolved ``Path`` on success. Raises
    :class:`PermissionError` when the allowlist is empty (default-deny)
    or the path is outside every configured root.

    Used by :meth:`_resolve_media_url` from BOTH the explicit ``Path``
    object branch and the implicit ``str`` + ``exists()`` branch
    (HERMES-15). Pulled out so the security check has exactly one
    canonical implementation site — drift here would silently weaken
    HI-01.
    """
    try:
        resolved = candidate.expanduser().resolve()
    except (OSError, RuntimeError) as exc:
        raise PermissionError(
            f"Cannot resolve upload path {str(candidate)!r}: {exc}"
        ) from exc
    if not self.upload_allowed_roots:
        raise PermissionError(
            "Local file uploads are disabled: set "
            "CHATLYTICS_UPLOAD_ALLOWED_ROOTS to an allowlist of "
            "absolute paths (OS-pathsep separated) to enable "
            "local-file uploads."
        )
    for root in self.upload_allowed_roots:
        try:
            if resolved == root or resolved.is_relative_to(root):
                return resolved
        except AttributeError:
            # Defensive fallback for 3.8 hosts if a downstream
            # consumer ever loosens the >=3.10 pin in pyproject.
            rs = str(resolved)
            rr = str(root)
            if rs == rr or rs.startswith(rr + os.sep):
                return resolved
    raise PermissionError(
        f"Refusing upload outside CHATLYTICS_UPLOAD_ALLOWED_ROOTS: "
        f"{resolved}"
    )
```

**Step 1b.** Rewrite `_resolve_media_url` body with the 5-branch
resolver. Replace the current body (lines 778-848) with:

```python
async def _resolve_media_url(
    self,
    resource: Union[str, Path, bytes, bytearray],
    *,
    upload_filename: Optional[str] = None,
    content_type: Optional[str] = None,
) -> str:
    """Resolve a media resource to a remotely-hosted URL.

    HERMES-15 (v3.0 BREAKING — library API): unified resolver with
    explicit ``Path`` support and an unambiguous failure mode.

    Branches (evaluated IN ORDER — order matters for correctness):

    1. ``bytes`` / ``bytearray`` — uploaded to ``/api/v1/upload``;
       returned ``{url}`` becomes the media URL. Preserved from
       HERMES-04 — caller-supplied raw bytes is a legitimate path.
    2. ``Path`` object — resolved + allowlist-checked + read +
       uploaded. New in HERMES-15 for explicit ergonomics.
    3. ``str`` starting with ``http://`` or ``https://`` — returned
       as-is (URL passthrough, no upload).
    4. ``str`` whose ``Path(s).expanduser().exists()`` is true —
       treated as a local path; resolved + allowlist-checked + read +
       uploaded.
    5. Anything else (typically a malformed ``str`` that is neither
       a URL nor an existing path) — raises :class:`ValueError`.

    Raises:
    - :class:`PermissionError` — path is outside
      ``CHATLYTICS_UPLOAD_ALLOWED_ROOTS`` (HI-01 default-deny
      preserved).
    - :class:`ValueError` — input is not bytes / Path / URL / existing
      string path. Caught by :meth:`_send_media_payload` and surfaced
      as ``SendResult(success=False, error=...)``.
    - :class:`RuntimeError` — upload endpoint did not return a
      ``url`` field in its JSON body.

    Callers wrap exceptions into ``SendResult(success=False, error=...)``
    via :meth:`_send_media_payload`.
    """
    assert self._client is not None  # caller guards

    # Branch 1: raw bytes — upload as-is.
    if isinstance(resource, (bytes, bytearray)):
        name = upload_filename or "upload.bin"
        ctype = content_type or _guess_content_type(name)
        upload_response = await self._client.upload_file(
            filename=name, content=bytes(resource), content_type=ctype
        )

    # Branch 2: explicit Path object — local file, allowlist-enforced.
    elif isinstance(resource, Path):
        resolved = self._enforce_upload_allowlist(resource)

        def _read_file() -> tuple[bytes, str]:
            with open(str(resolved), "rb") as fh:
                return fh.read(), os.path.basename(str(resolved)) or "upload.bin"

        content, basename = await asyncio.to_thread(_read_file)
        name = upload_filename or basename
        ctype = content_type or _guess_content_type(name)
        upload_response = await self._client.upload_file(
            filename=name, content=content, content_type=ctype
        )

    # Branch 3: URL string — passthrough.
    elif isinstance(resource, str) and resource.startswith(("http://", "https://")):
        return resource

    # Branch 4: string path that exists on disk — local file.
    elif isinstance(resource, str) and Path(resource).expanduser().exists():
        resolved = self._enforce_upload_allowlist(Path(resource))

        def _read_file() -> tuple[bytes, str]:
            with open(str(resolved), "rb") as fh:
                return fh.read(), os.path.basename(str(resolved)) or "upload.bin"

        content, basename = await asyncio.to_thread(_read_file)
        name = upload_filename or basename
        ctype = content_type or _guess_content_type(name)
        upload_response = await self._client.upload_file(
            filename=name, content=content, content_type=ctype
        )

    # Branch 5: unresolvable input — clean ValueError.
    else:
        raise ValueError(
            "resource must be a URL (http://, https://) or a local "
            f"file path that exists; got {type(resource).__name__}="
            f"{resource!r}"
        )

    if upload_response.status_code != 200:
        raise RuntimeError(
            f"Upload to /api/v1/upload returned HTTP {upload_response.status_code}"
        )

    try:
        payload = upload_response.json()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"Upload response was not valid JSON: {exc}"
        ) from exc

    if not isinstance(payload, dict) or "url" not in payload:
        raise RuntimeError(
            "Upload response missing 'url' field; "
            f"got keys: {list(payload.keys()) if isinstance(payload, dict) else type(payload).__name__}"
        )
    return payload["url"]
```

**Step 1c.** Update `_send_media_payload` to catch `ValueError`:

Find (around lines 920-942):
```python
        try:
            media_url = await self._resolve_media_url(
                resource,
                upload_filename=filename,
                content_type=content_type,
            )
        except FileNotFoundError as exc:
            return SendResult(success=False, error=f"File not found: {exc}")
        except PermissionError as exc:
            # HI-01 surface: ...
            return SendResult(success=False, error=f"Permission denied: {exc}")
        except OSError as exc:
            return SendResult(success=False, error=f"File read error: {exc}")
        except RuntimeError as exc:
            return SendResult(success=False, error=str(exc))
        except httpx.RequestError as exc:
            return SendResult(
                success=False,
                error=f"Upload transport error: {exc}",
                retryable=True,
            )
```

Add a `ValueError` branch BEFORE the `OSError` branch so an
unresolvable resource (HERMES-15 Branch 5) surfaces with a clear
error message:

```python
        except FileNotFoundError as exc:
            return SendResult(success=False, error=f"File not found: {exc}")
        except PermissionError as exc:
            # HI-01 surface: ...
            return SendResult(success=False, error=f"Permission denied: {exc}")
        except ValueError as exc:
            # HERMES-15: resource was neither a URL, a Path, nor an
            # existing string path. _resolve_media_url raised cleanly;
            # surface as a regular SendResult failure for the caller.
            return SendResult(success=False, error=f"Invalid resource: {exc}")
        except OSError as exc:
            return SendResult(success=False, error=f"File read error: {exc}")
        ...
```

Acceptance:
- `from chatlytics_hermes.adapter import ChatlyticsAdapter` works.
- `_resolve_media_url` raises `ValueError` on
  `"not-a-url-not-a-path"` (verified by new test in T5).
- HI-01 regression: bytes-only callers from HERMES-04 still work
  (verified by existing `test_send_image_file_uploads_local_bytes`
  rename in T5 — uses Path object now).
- `_enforce_upload_allowlist` is callable on adapter instances
  (covered by T5 tests touching the file branches).

### T2 — Collapse `send_image` + DELETE `send_image_file`

**File:** `src/chatlytics_hermes/adapter.py`

Replace the existing `send_image` signature + docstring (lines 971-1006)
with the unified version:

```python
async def send_image(
    self,
    chat_id: str,
    resource: Union[str, Path, bytes, bytearray],
    caption: Optional[str] = None,
    reply_to: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    **kwargs: Any,
) -> "SendResult":
    """Send an image as a native WhatsApp photo attachment.

    HERMES-15 (v3.0 BREAKING — library API): unified resource shape.
    The v2.0/v2.1 ``send_image_file`` companion is GONE. Callers pass
    ``resource`` in any of four forms; the adapter auto-detects which
    branch applies in :meth:`_resolve_media_url`:

    - ``http(s)://...`` ``str`` — used as ``mediaUrl`` directly.
    - ``Path`` object — local file, uploaded via multipart.
    - ``str`` whose ``Path(s).exists()`` is true — local file (the
      adapter resolves + uploads the same as the ``Path`` branch).
    - ``bytes`` / ``bytearray`` — uploaded as raw bytes (preserved
      from HERMES-04).
    - Anything else — :class:`ValueError`, surfaced by
      :meth:`_send_media_payload` as ``SendResult(success=False, ...)``.

    ``caption`` is optional. ``reply_to`` / ``metadata`` are accepted
    for base-class signature parity but currently ignored — the
    Chatlytics send-media endpoint does not expose per-message reply
    context.

    ``**kwargs`` is swallowed for forward-compat with upstream base
    signature evolution (HI-03 fix from HERMES-08): future Hermes
    versions may add new kwargs (``priority``, ``force_native``,
    etc.) that this override should not trip on.

    See CHANGELOG entry "BREAKING — adapter send_* unified resource
    shape" for migration guidance from v2.x callers.
    """
    return await self._send_media_payload(
        chat_id, "image", resource, caption=caption
    )
```

**DELETE** the entire `send_image_file` method (lines 1099-1131).
No replacement, no alias, no shim. The next method definition after
the deletion is `_keep_typing`.

Also update the module docstring header (lines 17-19) — remove
`send_image_file` from the HERMES-04 enumeration:

Find:
```python
- HERMES-04 -- media handlers (``send_image``, ``send_voice``,
  ``send_video``, ``send_document``, ``send_animation``,
  ``send_image_file``) and ``_keep_typing`` heartbeat
```

Replace with:
```python
- HERMES-04 -- media handlers (``send_image``, ``send_voice``,
  ``send_video``, ``send_document``, ``send_animation``) and
  ``_keep_typing`` heartbeat. HERMES-15 (v3.0 BREAKING) collapsed
  the v2.0 ``send_image`` / ``send_image_file`` split into one
  unified ``send_image(chat_id, resource: str | Path | bytes, ...)``;
  ``send_image_file`` is gone.
```

Acceptance:
- `getattr(ChatlyticsAdapter, "send_image_file", None) is None` —
  symbol is fully gone (verified by new test in T5).
- `inspect.signature(ChatlyticsAdapter.send_image)` has
  `resource: Union[str, Path, bytes, bytearray]` as the second
  positional parameter.

### T3 — Harmonize the four already-unified media methods

**File:** `src/chatlytics_hermes/adapter.py`

Each of `send_animation`, `send_voice`, `send_video`, `send_document`
already accepts `Union[str, bytes, bytearray]` and routes through
`_send_media_payload`. The body needs no change. Only:

(a) Rename the second positional parameter to `resource`.
(b) Broaden the type hint to include `Path`.
(c) Update the brief mention in the docstring.

**`send_animation`** (lines 1008-1028):
- Rename `animation_url` → `resource`.
- Type hint: `Union[str, Path, bytes, bytearray]`.
- Body unchanged (still calls
  `self._send_media_payload(chat_id, "animation", resource, caption=caption)`).
- Docstring: append "HERMES-15: ``resource`` accepts URL str, Path,
  string path, or bytes (auto-detected)." to the existing prose.

**`send_voice`** (lines 1030-1053):
- Rename `audio_path` → `resource`.
- Type hint: `Union[str, Path, bytes, bytearray]`.
- Body unchanged.
- Docstring: same HERMES-15 note appended.

**`send_video`** (lines 1055-1067):
- Rename `video_path` → `resource`.
- Type hint: `Union[str, Path, bytes, bytearray]`.
- Body unchanged.
- Docstring: same HERMES-15 note appended.

**`send_document`** (lines 1069-1097):
- Rename `file_path` → `resource`.
- Type hint: `Union[str, Path, bytes, bytearray]`.
- Body unchanged (`file_name`/`filename` kwargs preserved exactly
  as-is — they are separate parameters, not the resource).
- Docstring: same HERMES-15 note appended.

Acceptance:
- All four methods have `resource` as their second positional
  parameter (verified via `inspect.signature` in T5 or by manual
  introspection).
- `pytest tests/test_media.py -q` — existing tests still pass
  (they call positionally and the rename is invisible to positional
  callers).

### T4 — Simplify `chatlytics_send_image` tool handler

**File:** `src/chatlytics_hermes/tools.py`

Replace the branched handler (lines 740-776) with a single-call
shape matching the other four media tool handlers:

```python
async def chatlytics_send_image(
    client: ChatlyticsClient,
    *,
    adapter: Any = None,
    chatId: str,
    mediaUrl: Optional[str] = None,
    filePath: Optional[str] = None,
    caption: Optional[str] = None,
) -> Dict[str, Any]:
    """Send an image via the Chatlytics gateway.

    HERMES-15 (v3.0 BREAKING — library API; tool surface unchanged):
    dispatches to the unified ``adapter.send_image(chatId, resource,
    ...)`` method. Either ``mediaUrl`` or ``filePath`` is required —
    ``_resolve_resource`` picks one of them and hands it to the
    adapter, which auto-detects URL vs local-path vs bytes in
    :meth:`ChatlyticsAdapter._resolve_media_url`.

    The v2.0/v2.1 split (``adapter.send_image`` vs
    ``adapter.send_image_file``) is gone at the adapter layer. The
    tool layer has always exposed one face; only the internal
    dispatch simplified.

    Tool surface stays at 21 tools — this is an internal
    simplification only. MCP / Hermes callers see no behavior change.
    """
    if adapter is None:
        return {"success": False, "error": "Media tools require a live adapter; ensure register() ran."}
    resource = _resolve_resource(mediaUrl=mediaUrl, filePath=filePath)
    if not resource:
        return {"success": False, "error": "Either mediaUrl or filePath is required."}
    result = await adapter.send_image(chatId, resource, caption=caption)
    return _media_result_dict(result)
```

The signature and schema stay byte-identical — only the body changes.

Acceptance:
- `pytest tests/test_tools.py -q` — all existing tool tests pass.
- `len(TOOLS) == 21` still holds.
- The handler body now matches the other 4 media tool handlers
  (visual code-review pass).

### T5 — Update `tests/test_media.py` (rename 2 tests + add TestResourceAutoDetection)

**File:** `tests/test_media.py`

**Step 5a.** Rename and update the two `_file`-suffixed tests.

Find `test_send_image_file_uploads_local_bytes` (lines 185-224). Rename
to `test_send_image_local_path_uploads_bytes`. Update the docstring
+ the adapter call:

```python
async def test_send_image_local_path_uploads_bytes(
    adapter: ChatlyticsAdapter,
    mock_router: respx.MockRouter,
    tmp_path: Path,
) -> None:
    """HERMES-15: send_image(Path) reads bytes, uploads, references URL.

    Renamed from ``test_send_image_file_uploads_local_bytes`` when the
    v2.0 ``send_image_file`` companion was deleted. The unified
    ``send_image`` method now accepts a ``Path`` object and routes
    through the auto-detection branch in ``_resolve_media_url``.
    """
    img_path = tmp_path / "x.png"
    fake_png = b"\x89PNG\r\n\x1a\n" + b"some-bytes-x123"
    img_path.write_bytes(fake_png)

    mock_router.get("/health").mock(return_value=httpx.Response(200, json={}))
    upload_route = mock_router.post("/api/v1/upload").mock(
        return_value=httpx.Response(
            200, json={"url": "https://cdn.test/uploaded.png"}
        )
    )
    media_route = mock_router.post("/api/v1/send-media").mock(
        return_value=httpx.Response(
            200, json={"success": True, "messageId": "m-imgfile"}
        )
    )

    await adapter.connect()
    # HERMES-15: was adapter.send_image_file(CHAT_ID, str(img_path), ...).
    result = await adapter.send_image(CHAT_ID, Path(img_path), caption="local")
    assert result.success is True
    assert result.message_id == "m-imgfile"

    assert upload_route.called
    upload_req = upload_route.calls.last.request
    assert fake_png in upload_req.content
    assert upload_req.headers["authorization"] == EXPECTED_AUTH

    media_body = _json.loads(media_route.calls.last.request.content)
    assert media_body["mediaUrl"] == "https://cdn.test/uploaded.png"
    assert media_body["mediaType"] == "image"
    assert media_body["caption"] == "local"
    await adapter.disconnect()
```

Find `test_send_image_file_reads_off_event_loop` (lines 229-287). Rename
to `test_send_image_local_path_reads_off_event_loop`. Update only the
adapter call line (around line 278):

```python
    # HERMES-15: was adapter.send_image_file(CHAT_ID, str(img_path), ...).
    result = await adapter.send_image(CHAT_ID, Path(img_path), caption="thr")
```

The rest of the test (thread capture, monkeypatch on `builtins.open`)
is unchanged — both old and new call paths still exercise the
`asyncio.to_thread` wrapping in the new Branch 2 of
`_resolve_media_url`.

**Step 5b.** Add a new `TestResourceAutoDetection` class at the bottom
of `test_media.py`. Use `pytest.mark.parametrize` for the cases:

```python
# ---------------------------------------------------------------------------
# HERMES-15: send_image resource auto-detection (BREAKING library API)
# ---------------------------------------------------------------------------
#
# v3.0 BREAKING — see CHANGELOG entry "BREAKING — adapter send_* unified
# resource shape". The v2.0 send_image / send_image_file split collapsed
# into a single send_image(resource) that auto-detects URL vs Path vs
# string-path-exists vs raw bytes. Anything else raises ValueError and
# surfaces as SendResult(success=False, error=...).


class TestResourceAutoDetection:
    """Adapter.send_image auto-detection branches (HERMES-15)."""

    async def test_url_string_passes_through_without_upload(
        self,
        adapter: ChatlyticsAdapter,
        mock_router: respx.MockRouter,
    ) -> None:
        """Branch 3: http(s):// string → mediaUrl passthrough, NO upload call."""
        mock_router.get("/health").mock(return_value=httpx.Response(200, json={}))
        upload_route = mock_router.post("/api/v1/upload").mock(
            return_value=httpx.Response(200, json={"url": "should-not-be-used"})
        )
        media_route = mock_router.post("/api/v1/send-media").mock(
            return_value=httpx.Response(200, json={"success": True, "messageId": "m-url"})
        )

        await adapter.connect()
        result = await adapter.send_image(
            CHAT_ID, "https://cdn.test/cat.jpg", caption="url branch"
        )
        assert result.success is True
        assert not upload_route.called, (
            "URL passthrough must not trigger an upload"
        )

        body = _json.loads(media_route.calls.last.request.content)
        assert body["mediaUrl"] == "https://cdn.test/cat.jpg"
        await adapter.disconnect()

    async def test_path_object_uploads_via_multipart(
        self,
        adapter: ChatlyticsAdapter,
        mock_router: respx.MockRouter,
        tmp_path: Path,
    ) -> None:
        """Branch 2: explicit Path object → multipart upload + URL reference."""
        img_path = tmp_path / "p.png"
        img_path.write_bytes(b"path-object-bytes")

        mock_router.get("/health").mock(return_value=httpx.Response(200, json={}))
        upload_route = mock_router.post("/api/v1/upload").mock(
            return_value=httpx.Response(200, json={"url": "https://cdn.test/p.png"})
        )
        mock_router.post("/api/v1/send-media").mock(
            return_value=httpx.Response(200, json={"success": True, "messageId": "m-path"})
        )

        await adapter.connect()
        result = await adapter.send_image(CHAT_ID, Path(img_path))
        assert result.success is True
        assert upload_route.called, "Path branch must upload via multipart"
        assert b"path-object-bytes" in upload_route.calls.last.request.content
        await adapter.disconnect()

    async def test_string_path_that_exists_uploads_via_multipart(
        self,
        adapter: ChatlyticsAdapter,
        mock_router: respx.MockRouter,
        tmp_path: Path,
    ) -> None:
        """Branch 4: ``str`` whose path exists → multipart upload (parity with Path)."""
        img_path = tmp_path / "s.png"
        img_path.write_bytes(b"string-path-bytes")

        mock_router.get("/health").mock(return_value=httpx.Response(200, json={}))
        upload_route = mock_router.post("/api/v1/upload").mock(
            return_value=httpx.Response(200, json={"url": "https://cdn.test/s.png"})
        )
        mock_router.post("/api/v1/send-media").mock(
            return_value=httpx.Response(200, json={"success": True, "messageId": "m-str"})
        )

        await adapter.connect()
        result = await adapter.send_image(CHAT_ID, str(img_path))
        assert result.success is True
        assert upload_route.called, (
            "String-path-that-exists branch must upload via multipart"
        )
        assert b"string-path-bytes" in upload_route.calls.last.request.content
        await adapter.disconnect()

    async def test_unresolvable_string_returns_invalid_resource_error(
        self,
        adapter: ChatlyticsAdapter,
        mock_router: respx.MockRouter,
    ) -> None:
        """Branch 5: str that's neither URL nor existing path → SendResult(False, ...).

        ``_resolve_media_url`` raises ``ValueError`` and
        ``_send_media_payload`` catches it and surfaces a clean
        failure dict to the caller instead of an uncaught raise.
        """
        mock_router.get("/health").mock(return_value=httpx.Response(200, json={}))
        # No upload route mocked — should never be called.

        await adapter.connect()
        result = await adapter.send_image(CHAT_ID, "not-a-url-not-a-path-zzz")
        assert result.success is False
        assert "Invalid resource" in (result.error or "")
        await adapter.disconnect()

    def test_send_image_file_symbol_is_gone(self) -> None:
        """HERMES-15 acceptance criterion 4: send_image_file must NOT exist.

        Clean break, no deprecation alias. Direct callers of the v2.0
        ``adapter.send_image_file(...)`` see an ``AttributeError`` —
        documented BREAKING change in the v3.0 CHANGELOG.
        """
        assert getattr(ChatlyticsAdapter, "send_image_file", None) is None, (
            "send_image_file must be fully removed (no shim, no alias)"
        )
```

Acceptance:
- `pytest tests/test_media.py -v` — original 8 tests (renamed) pass +
  5 new `TestResourceAutoDetection` cases pass (4 branch cases + 1
  symbol-gone assertion).
- Total media test count goes from 8 to 13 (+5).

### T6 — Audit `tests/test_tools.py` for direct `send_image_file` references

**File:** `tests/test_tools.py`

Grep for `send_image_file` and any test that constructs a fake
adapter mock with a `send_image_file` attribute / method. Update each
to use the unified `send_image` shape.

Likely targets (per the tool layer's previous branched dispatch):
- Any test that mocks `chatlytics_send_image` with a `filePath`
  argument and asserts that `adapter.send_image_file` was called —
  flip the assertion to `adapter.send_image` (single call now).
- Any test that monkeypatches a fake adapter with both
  `send_image_file` and `send_image` — drop the `_file` attribute,
  keep only `send_image`.

If no direct references exist in `test_tools.py`, this task is a
no-op confirming the audit and committing nothing for it. Combine
with T7's CHANGELOG entry commit if the audit comes up empty.

Acceptance:
- `Grep send_image_file tests/` returns ZERO matches outside of
  `test_media.py::test_send_image_file_symbol_is_gone` (which
  intentionally references the name in a string for the assertion).
- `pytest tests/ -q` — all tests pass.

### T7 — Append CHANGELOG entry

**File:** `CHANGELOG.md`

Append a bullet under the existing `## [Unreleased] / ### Breaking`
section (third bullet after the Phase 13 + 14 entries).

Append:
```markdown
- **Library API:** `ChatlyticsAdapter.send_image_file` is removed.
  The unified `adapter.send_image(chatId, resource: str | Path, ...)`
  auto-detects URL vs local-file vs raw-bytes inputs. The other media
  methods (`send_animation`, `send_voice`, `send_video`,
  `send_document`) have their second positional parameter renamed to
  `resource` and their type hint broadened to
  `Union[str, Path, bytes, bytearray]`. Tool surface unchanged —
  `chatlytics_send_image` and the other four media tools keep their
  schemas and external behavior; the `_file` companion at the adapter
  layer is the only break. The `CHATLYTICS_UPLOAD_ALLOWED_ROOTS`
  default-deny allowlist (HI-01) is preserved on every file branch.
  Migration: replace `adapter.send_image_file(chat_id, path, ...)`
  with `adapter.send_image(chat_id, Path(path), ...)` (or just
  `adapter.send_image(chat_id, path, ...)` — the auto-detector
  handles existing string paths). Direct callers of the removed
  symbol see `AttributeError` on upgrade by design; this is a clean
  break with no deprecation wrapper per the operator's lifted-lock
  preference. Closes v2.1 deferred item 3.
```

Acceptance:
- `CHANGELOG.md` has the new bullet under `[Unreleased] / Breaking`.
- No release-line bumps (Phase 19 owns 3.0.0 release).

## Verification

After all tasks land:

```bash
cd D:/docker/chatlytics-hermes-split
python -m pytest tests/ -q
```

Expected: 111 baseline (88 v2.1 + 10 Phase 13 + 13 Phase 14) + 5 new
from `TestResourceAutoDetection` = **116 passing tests**, zero
regressions. The two renamed tests in `test_media.py` keep the same
count.

Sanity import + introspection:
```bash
python -c "from chatlytics_hermes.adapter import ChatlyticsAdapter; \
  print('send_image_file present:', hasattr(ChatlyticsAdapter, 'send_image_file'))"
# Expected: send_image_file present: False

python -c "import inspect; \
  from chatlytics_hermes.adapter import ChatlyticsAdapter; \
  sig = inspect.signature(ChatlyticsAdapter.send_image); \
  print(list(sig.parameters))"
# Expected: ['self', 'chat_id', 'resource', 'caption', 'reply_to', 'metadata', 'kwargs']

python -c "from chatlytics_hermes.tools import TOOLS; \
  assert len(TOOLS) == 21, f'tool count drift: {len(TOOLS)}'; \
  print('21 tools registered')"
# Expected: 21 tools registered
```

## Risks + mitigations

| Risk | Mitigation |
|------|------------|
| HI-01 allowlist accidentally bypassed by the new `Path` branch | The new `_enforce_upload_allowlist` helper is the SINGLE site for the security check; both file branches (Branch 2 + Branch 4) call it. T5's `test_path_object_uploads_via_multipart` exercises Branch 2 with `tempfile.gettempdir()` already in the fixture's allowlist; if the helper regresses, the test fails. |
| Branch 4's `Path(s).exists()` filesystem call slows down callers that pass garbage strings | The `exists()` check is a single stat call. Failure case (Branch 5) is the explicit error path — operators get a clear ValueError instead of a downstream allowlist or upload failure with worse diagnostics. |
| Existing keyword-arg callers using `image_url=...`, `audio_path=...`, etc. break silently | Library API is BREAKING per the phase brief; CHANGELOG entry documents the migration. Direct keyword-arg callers see `TypeError: unexpected keyword argument`. Positional callers are unaffected (which is the dominant pattern in this codebase and in tests). |
| Tool layer simplification regresses `chatlytics_send_image` behavior | The handler shape now matches the other four media tools verbatim. The `_resolve_resource` helper picks one of two inputs and the adapter does the rest — identical to the existing four. T6 verifies no direct `send_image_file` references survive in tool-layer tests. |
| Branch 5 catches *intended* failures (e.g. URL typo `htp://...`) and surfaces as ValueError instead of an upload-side error | Acceptable — the new failure mode is strictly better. A URL typo is now a clear "Invalid resource" surface at the boundary, not a confusing HTTP error after the adapter tries to read a file that doesn't exist. |

## Commit plan

One commit per task (T1..T7), each via the standard commit pattern.
Suggested messages (`!` marker per conventional-commits for breaking
changes):

- T1: `refactor(15): _resolve_media_url 5-branch resolver + _enforce_upload_allowlist helper`
- T2: `feat(15)!: collapse send_image and delete send_image_file (no shim)`
- T3: `refactor(15)!: harmonize send_* resource parameter naming`
- T4: `refactor(15): simplify chatlytics_send_image tool handler`
- T5: `test(15): rename _file tests + add TestResourceAutoDetection`
- T6: `test(15): audit test_tools.py for removed send_image_file references`
- T7: `docs(15)!: changelog Unreleased entry for adapter send_* collapse`
