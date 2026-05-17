# HERMES-06 -- VERIFICATION

**Phase:** HERMES-06 -- Release + smoke test
**Verified:** 2026-05-17
**Mode:** Dockerized clean-room (`python:3.13-slim` + `hermes-agent@v2026.5.16`)

## Smoke run

```bash
$ bash scripts/smoke.sh
... (apt-get + pip install hermes-agent + pip install -e .[dev]) ...
--- smoke step 1/3: import chatlytics_hermes.register ---
register OK: register
--- smoke step 2/3: hermes_agent.plugins entry-point discovery ---
entry-points OK: chatlytics in ['chatlytics']
--- smoke step 3/3: pytest tests/ ---
.............................................                            [100%]
45 passed in 1.84s
--- smoke PASS ---

$ echo $?
0
```

## Acceptance criteria

| AC | Check                                                                                         | Result        |
| -- | --------------------------------------------------------------------------------------------- | ------------- |
| 1  | `bash scripts/smoke.sh; echo $?`                                                              | `0`           |
| 2  | Smoke step 2 output                                                                           | `entry-points OK: chatlytics in ['chatlytics']` |
| 3  | `pytest tests/ -q`                                                                            | `45 passed`   |
| 4  | `grep -c "ChatlyticsAdapter(" README.md`                                                      | `0`           |
| 5  | `head -3 CHANGELOG.md`                                                                        | `## 2.0.0 (2026-05-17) -- BREAKING` |
| 6  | `python -c "import tomllib; d=tomllib.load(open('pyproject.toml','rb'))['project']; print(d['version'], d.get('entry-points',{}).get('hermes_agent.plugins'))"` | `2.0.0 {'chatlytics': 'chatlytics_hermes:register'}` |
| 7  | `git tag --list v2.0.0`                                                                       | `v2.0.0`      |
| 8  | `grep -rE "python -m build|twine upload" scripts/ src/`                                       | no executable matches; only doc strings in `.planning/` describing the lock |

All 8 PASS.

## Regression guard

- 44 prior tests (HERMES-01..05) all still green.
- 1 new test (`test_send_image_file_reads_off_event_loop`) covers the
  04-MED-02 `asyncio.to_thread` wrap.
- Bytes-path and HTTP-URL paths of `_resolve_media_url` unchanged.

## Operator lock

`grep -rE "python -m build|twine upload" .` returns matches ONLY inside
`.planning/phases/HERMES-06-release-smoke-test/` (06-CONTEXT.md, 06-01-PLAN.md,
06-01-SUMMARY.md) -- all are documentation strings asserting the
absence of those commands. There are zero executable invocations.

The v2.0.0 tag was created locally only (`git tag -a`). It was NOT
pushed. The push command is documented for operator action in the
SUMMARY and was emitted at run time:

```bash
git push origin v2.0.0   # OPERATOR ACTION
```
