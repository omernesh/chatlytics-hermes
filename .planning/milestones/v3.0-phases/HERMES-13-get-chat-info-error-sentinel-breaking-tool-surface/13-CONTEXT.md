---
phase: 13
phase_slug: get-chat-info-error-sentinel-breaking-tool-surface
phase_name: "`get_chat_info` `_error` sentinel (BREAKING tool surface)"
project_code: HERMES
milestone: v3.0
infra_skip: true
infra_skip_reason: "Scope is fix-locked per v3.0 ROADMAP HERMES-13. All implementation specifics (error code taxonomy, 404 disambiguation, test-update strategy, scope guards) are encoded by operator before phase launch. No grey areas need user discussion — gsd-discuss-phase would only paraphrase the locked decisions."
---

# HERMES-13 — `get_chat_info` `_error` sentinel (BREAKING tool surface) — CONTEXT

## Domain (Phase boundary from ROADMAP goal)

Disambiguate empty-success vs error on the `get_chat_info` surface.
v2.1 returned a bare `{}` dict for all four non-success paths (adapter
not connected, transport error, non-200 response, malformed JSON),
forcing operators to inspect logs to tell why a chat lookup came back
empty. v3.0 splits this into three explicit branches at both the
adapter and tool layers:

1. **Chat found** → `{success: true, chat: {...}}` (or adapter returns
   the dict directly).
2. **Chat-not-found legitimate empty** → `{success: true, chat: null}`
   (gateway responded 200 with no chat payload — adapter returns
   `None`). Reserved for the case where the gateway responds 200 with
   an empty `chat` field. Currently unreachable in practice, but the
   code path is defined for forward-compat.
3. **Transport / auth / server / validation error** →
   `{success: false, error: "<human msg>", _error: "<machine code>"}`.
   Adapter raises a typed exception (`ChatlyticsLookupError` carrying a
   `code`); the tool wrapper catches and maps to the failure dict.

Closes v2.1 deferred item 1 (sentinel `_error` key on `get_chat_info`).

## Decisions (encoded from operator-locked phase brief)

### D1 — Error code taxonomy (machine-readable, lowercase snake_case)

| Code               | Trigger                                              |
|--------------------|------------------------------------------------------|
| `transport_error`  | `httpx.RequestError` — network / timeout / DNS       |
| `auth_error`       | HTTP 401 / 403 from gateway                          |
| `server_error`     | HTTP 5xx from gateway                                |
| `validation_error` | HTTP 4xx other than 401/403 (e.g. 400, 404, 422)    |
| `unknown_error`    | Catch-all — non-JSON body on 2xx, unexpected raise   |

Pick the closest fit at the call site. The codes are an **extension**
of the v2.1 contract (all tool handlers still return
`{"success": bool, ...}`); error responses additionally include the
`_error` key.

### D2 — 404 disambiguation (the trickiest case)

Gateway responding **404 for an unknown `chatId`** is **NOT** a
"chat-not-found legitimate empty" — it is a `_error: "validation_error"`
because the JID was malformed or unknown to the gateway.

The **legitimate empty** branch (`{success: true, chat: null}`) is
reserved for the case where the gateway responds **200** with an empty
`chat` field. If no such response shape exists today, this branch is
currently unreachable; the code path stays defined for forward-compat.

### D3 — Test-update strategy

This is a **BREAKING tool surface change**. Existing v2.1 tests that
asserted the old `{}` shape MUST be **updated** (not deleted) with an
explicit comment referencing the v3.0 CHANGELOG entry for
`get_chat_info`.

Add at least **2 NEW assertions**:

- One for the **success-with-null branch** (gateway 200 with empty
  chat).
- One for the **error-with-`_error` branch** (e.g. 500 maps to
  `_error: "server_error"`).

Files known to touch `get_chat_info` shape:
- `tests/test_outbound.py` — AC-6 (`test_get_chat_info_returns_dict`)
  and AC-8 (uses `get_chat_info` in cross-cutting Bearer assertion).
- `tests/test_tools.py` — check for any `get_chat_info`-named handler
  tests (none expected; tool layer doesn't currently expose
  `get_chat_info` as a registered tool — it's adapter-only).
- `tests/test_validation.py` — only docstring reference (line 10);
  no shape assertions.

### D4 — Scope guards (DO NOT TOUCH)

- **No JID regex tightening** — that is Phase 14.
- **No adapter `send_*` collapse** — that is Phase 15.
- **No version bump** in `pyproject.toml` / `plugin.yaml` — Phase 19
  owns release bumps.
- **CHANGELOG.md** — append a **single line** under an `## [Unreleased]`
  section noting the breaking change. Phase 19 finalizes the release
  entry.
- **No git push / publish.**

### D5 — Tool surface count unchanged

Stays at **21 tools**. Only `get_chat_info` semantics break. The
existing `assert len(TOOLS) == 21` invariant in `tools.py` MUST still
hold. Note: `get_chat_info` is **adapter-only** today — there is no
`chatlytics_get_chat_info` registered tool in the 21-tool registry, so
the "tool surface" wording in the ROADMAP refers to the **adapter +
caller contract**, not a new entry in the TOOLS tuple. The phase still
adds a tool wrapper (`chatlytics_get_chat_info`) ONLY if the operator
brief explicitly required it; the locked brief says **semantics break,
count unchanged**, so we keep the adapter-level contract change and do
NOT add a new tool. If a wrapper is needed later, that is a v3.1 minor.

**Resolution:** The phase modifies `adapter.get_chat_info` return
contract and updates callers. No new tool is registered. The 21-count
invariant stays satisfied.

## Code context (files touched + established patterns)

### Files to modify

| File | Change |
|------|--------|
| `src/chatlytics_hermes/adapter.py` | (a) Add `ChatlyticsLookupError(RuntimeError)` with `code: str` attribute. (b) Rewrite `get_chat_info` to return `dict | None` and raise `ChatlyticsLookupError` on transport/auth/server/validation errors. Update docstring. |
| `tests/test_outbound.py` | Update AC-6 to use the new adapter contract. AC-8 needs the cross-cutting `get_chat_info` call updated so the mocked 200 path returns a parseable chat. |
| `tests/test_validation.py` | No shape assertions touch `get_chat_info`; only line-10 docstring reference. May get a passing mention if convenient. |
| `tests/test_outbound.py` (new tests) | Add 4 new tests covering (1) success-with-chat, (2) success-with-null/empty, (3) `_error: "auth_error"` on 401, (4) `_error: "server_error"` on 500, (5) `_error: "validation_error"` on 404, (6) `_error: "transport_error"` on `httpx.RequestError`. The 2 NEW assertions called out in the brief are at minimum the null-branch test and the `_error`-branch test; pragmatically we add all four (one per code) plus the null branch since the cost is low and coverage matters. |
| `CHANGELOG.md` | Append single line under `## [Unreleased]`. |

### Established patterns

- All tool handlers in `src/chatlytics_hermes/tools.py` return
  `{"success": bool, ...}`. Error responses include `error: str`,
  `status_code: int?`, `raw_response: Any?`. The v3.0 extension adds
  `_error: "<code>"` to error responses ONLY.
- `_coerce_success_payload(status_code, payload)` in `adapter.py:188`
  is the canonical success-derivation predicate, shared by
  `_make_send_result`, `_standalone_send`, and tools `_post`/`_get`. It
  returns `(success: bool, error_msg: Optional[str])`. We will NOT add
  `_error` derivation to `_coerce_success_payload` for this phase
  because the rest of the surface stays on the v2.1 contract (only
  `get_chat_info` breaks). Phase scope is narrow.
- `ChatlyticsConnectError` is the existing typed exception pattern
  (raised by `connect()`). We mirror that for the new
  `ChatlyticsLookupError`.
- `httpx` is the outbound transport. `httpx.RequestError` covers
  transport errors (timeout, DNS, connection-refused, etc.).

### Call sites of `get_chat_info` (audit)

Only callers are tests and (potentially) future tool handlers. No
production caller relies on the v2.1 bare-`{}` return today —
`get_chat_info` was added in HERMES-02 as adapter contract surface but
no plugin code calls it except via the BasePlatformAdapter interface.

## Specifics (sequencing)

- **HERMES-14 (next phase)** tightens `chatId` schema validation. The
  error path established here MUST be uniform BEFORE Phase 14 starts
  rejecting `chatId`s at the schema layer, because Phase 14's
  validation errors will reuse the `_error: "validation_error"` code
  shape established in this phase. Sequencing: HERMES-14 happens AFTER
  HERMES-13 so the contract is consistent.
- **HERMES-15** then collapses adapter `send_*` methods. No coupling
  to this phase's contract change.

## Deferred

**None.** Scope is locked to the return-shape change. The wider
`_error` rollout across other tool handlers (if ever needed) is a v3.1
minor per the ROADMAP "Out of scope" section.
