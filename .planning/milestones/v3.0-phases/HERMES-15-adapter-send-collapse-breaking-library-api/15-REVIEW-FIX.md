---
phase: 15
fix_pass_status: complete
fixes_applied: 3
fixes_deferred: 3
tests_total: 116
tests_passed: 116
reviewed_by: gsd-code-review
---

# HERMES-15 — Code Review Fix-Pass

## Fixes applied (3)

### MED-01 — Replaced `__getattribute__` with `_RemovedMethod` descriptor

**Commit:** `795e9a5`

`ChatlyticsAdapter.__getattribute__` was deleted. A new module-level
`_RemovedMethod` descriptor class shadows the inherited
`BasePlatformAdapter.send_image_file`:

```python
class _RemovedMethod:
    def __init__(self, message: str) -> None: self._message = message
    def __get__(self, obj, objtype=None) -> Any:
        raise AttributeError(self._message)
    def __set_name__(self, owner, name) -> None: self._name = name


class ChatlyticsAdapter(BasePlatformAdapter):
    send_image_file = _RemovedMethod(
        "ChatlyticsAdapter.send_image_file was removed in v3.0 ..."
    )
```

**Why this is better:**

- Every other attribute lookup (`self._client`, `self.api_key`,
  `self.send`, etc.) now goes through the C-level slot directly —
  no Python comparison per access.
- The descriptor IS present in `ChatlyticsAdapter.__dict__`, which
  matches `inspect.signature` / IDE / docs-tool expectations
  ("there's something there; touching it raises").
- Error message + behavior preserved exactly — instance access
  still raises `AttributeError` with the migration text.

### LOW-01 — Deduplicated `_read_file_*` inner functions into `_read_file_sync`

**Commit:** `795e9a5`

`_resolve_media_url` Branch 2 and Branch 4 used identical inner
functions (`_read_file_path` / `_read_file_str`). Replaced with a
single module-level `_read_file_sync(path: str) -> Tuple[bytes, str]`
helper used by both branches via `asyncio.to_thread(_read_file_sync,
str(resolved))`. Saves ~10 lines, behavior identical.

### LOW-03 — Test path now guaranteed-nonexistent

**Commit:** `795e9a5`

`test_unresolvable_string_returns_invalid_resource_error` previously
passed the literal string `"not-a-url-not-a-path-zzz"` and would
have hit Branch 4 (not the intended Branch 5) if a developer
happened to have a file by that name in their CWD. Replaced with
`tmp_path / f"definitely-not-a-real-path-{uuid.uuid4().hex}"` plus
an `assert not Path(...).exists()` invariant guard. Test now
exercises Branch 5 deterministically regardless of filesystem
state.

## Test fix (collateral)

The `test_send_image_file_symbol_is_gone` assertion that probed
`"send_image_file" not in ChatlyticsAdapter.__dict__` was inverted
by the MED-01 descriptor change — the descriptor IS now in
`__dict__` (that's the whole point of using a descriptor). The
test was updated to instead probe the load-bearing contract:

1. `getattr(adapter, "send_image_file", None) is None` — the
   ROADMAP HERMES-15 acceptance criterion 4 literal text.
2. Direct attribute access raises `AttributeError` with migration
   message — the load-bearing user-facing contract.

This is INFO-02 from the original REVIEW resolved.

## Fixes deferred (3)

The remaining findings are cosmetic-only and have no shipping
impact. Deferred to Phase 18 (cosmetics sweep) if they're addressed
at all:

- **LOW-02** — Branch 4 `exists()` syscall on every non-URL string.
  Real-world impact negligible; revisit only if production shows
  the syscall is hot. Premature optimization to short-circuit
  obvious-garbage strings.

- **LOW-04** — `Path("https://...")` misuse produces a slightly
  misleading "Permission denied" error. The misuse is unusual
  given the documented signature; the error IS correct (the
  URL-as-path is denied), just literal.

- **INFO-01** — In-body branch-order rationale could be repeated
  in the per-branch comments. The docstring already covers it
  comprehensively.

## Test results after fix-pass

```
$ python -m pytest tests/ -q
116 passed in 27.83s
```

Same 116/116 count. The two MED-01 / LOW-01 refactors are
behavior-preserving. The LOW-03 test fix tightens the test contract
without changing what's being tested.

## Verification of fixes

```
$ PYTHONPATH=src python -c "
> from chatlytics_hermes.adapter import ChatlyticsAdapter, _RemovedMethod
> print('descriptor in __dict__:', 'send_image_file' in ChatlyticsAdapter.__dict__)
> print('is _RemovedMethod:', isinstance(ChatlyticsAdapter.__dict__['send_image_file'], _RemovedMethod))
> "
descriptor in __dict__: True
is _RemovedMethod: True
```

## Commit summary

```
795e9a5 fix(15): code-review fix-pass — MED-01 descriptor, LOW-01 read helper, LOW-03 stable test path
```

Single fix-pass commit covers all three applied findings (they
touch the same two files and are conceptually coherent: "clean up
review nits in the HERMES-15 implementation").

## Verdict

**READY TO SHIP** — all required fixes landed; deferred items are
cosmetic-only and explicitly tracked for Phase 18. Tests green at
116/116. Invariants preserved (HI-01 allowlist, 21-tool count,
Phase 13/14 contracts, bytes-upload path).
