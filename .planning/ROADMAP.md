# ROADMAP — chatlytics-hermes

## Milestones

- SHIPPED **v2.0** — Hermes plugin v2.0 (upstream-contract rebuild) — 2026-05-17
- SHIPPED **v2.1** — Critical safety fixes + tech debt resolution + live-loader integration — 2026-05-17
- SHIPPED **v3.0** — Breaking-change harmonization + first public release (PyPI + npm) — 2026-05-18
- PLANNING **v3.1** — TBD (run `/gsd:new-milestone` to scope)

## Phases

<details>
<summary>SHIPPED v3.0 — Breaking-change harmonization + first public release (Phases HERMES-13..21) — 2026-05-18</summary>

- [x] HERMES-13: `get_chat_info` `_error` sentinel (BREAKING tool surface) — 1/1 plans
- [x] HERMES-14: Strict JID regex on `chatId` schemas (BREAKING tool surface) — 1/1 plans
- [x] HERMES-15: Adapter `send_*` collapse (BREAKING library API) — 1/1 plans
- [x] HERMES-16: `smoke.sh` wheel caching (additive) — 1/1 plans
- [x] HERMES-17: Hermes 0.14 API audit doc (docs-only) — 1/1 plans
- [x] HERMES-18: Cosmetics sweep (nits) — 1/1 plans
- [x] HERMES-19: Release chatlytics-hermes 3.0.0 (PyPI) — 1/1 plans
- [x] HERMES-20: JS bundle update for v3.0 coordination (cross-repo) — 1/1 plans
- [x] HERMES-21: Release chatlytics-claude-code 1.2.0 (npm) — 1/1 plans

Full archive: `.planning/milestones/v3.0-ROADMAP.md`
Audit: `.planning/milestones/v3.0-MILESTONE-AUDIT.md` (passed)

</details>

<details>
<summary>SHIPPED v2.1 — Critical safety fixes + tech debt resolution + live-loader integration (Phases HERMES-07..12) — 2026-05-17</summary>

- [x] HERMES-07: Live-loader integration smoke (surfaces BL-01) — 1/1 plans
- [x] HERMES-08: Critical safety fixes (BL-01 + HI-01 + HI-03) + async lifecycle hardening — 1/1 plans
- [x] HERMES-09: Observability + log hygiene — 1/1 plans
- [x] HERMES-10: Input validation + UX alignment — 1/1 plans
- [x] HERMES-11: Test infra cleanup — 1/1 plans
- [x] HERMES-12: Release v2.1.0 — 1/1 plans

Full archive: `.planning/milestones/v2.1-ROADMAP.md`
Audit: `.planning/milestones/v2.1-MILESTONE-AUDIT.md` (passed)

</details>

<details>
<summary>SHIPPED v2.0 — Hermes plugin v2.0 (upstream-contract rebuild) (Phases HERMES-01..06) — 2026-05-17</summary>

- [x] HERMES-01: Upstream contract scaffolding — 1/1 plans
- [x] HERMES-02: Outbound text + control parity — 1/1 plans
- [x] HERMES-03: Inbound transport migration — 1/1 plans
- [x] HERMES-04: Media + UX polish + cron — 1/1 plans
- [x] HERMES-05: Full Chatlytics tool surface — 1/1 plans
- [x] HERMES-06: Release + smoke test — 1/1 plans

Full archive: `.planning/milestones/v2.0-ROADMAP.md`
Audit: `.planning/milestones/v2.0-MILESTONE-AUDIT.md` (passed)

</details>

### PLANNING v3.1 — TBD

Run `/gsd:new-milestone` to scope.

## Backlog

(Items deferred during v3.0 close — candidates for v3.1.)

- **HERMES-21 tech debt** — Remove `scripts.postinstall` in sibling chatlytics-claude-code `package.json`. Currently runs `npm --prefix servers install` on every consumer install, but the published bundle is fully bundled (esbuild `--packages=bundle`) with zero runtime deps. Wasteful, not broken. Suggest v1.2.1 patch.
- **HERMES-17 env-leak workaround** — Add `monkeypatch.delenv(CHATLYTICS_API_KEY|API_URL|SESSION)` to `tests/conftest.py` so pytest is immune to orchestrator-shell env-var leakage. Cosmetic.
- **Tool surface expansion** — v3.0 kept the count at 21 tools per architectural invariant. New tools require a v3.1 minor.
- **Hermes `0.15` pin bump** — Not yet possible (0.15 doesn't exist; see `.planning/HERMES-API-AUDIT.md`). Picked up when upstream ships 0.15.
