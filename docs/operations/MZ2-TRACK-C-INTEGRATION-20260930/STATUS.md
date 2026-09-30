# TRACK_C — A+B source integration

Integration complete; final source CI and clean-clone validation in progress.
This is not a release, deployment, financial handoff or live-cutover approval.

## Source identity and ordered merges

- Branch: `codex/mz2-cutover-integration-20260930`.
- Approved base: `1d9d65d8576d5bc852f52262b84af150356b9385`, tree `82ed0d05eee935d22c7a4050f21e66cdb9f4f551`.
- Track A: `2c7591f6764b28519beae3f09446fd42104bff2d`, tree `64a6318ee0955d8e43bf6a14b66fc6f164adf7d7`.
- Track B: `4d0e74c32592e99bfe9ef820e51b695d93adb34e`, tree `0b9813d116f07aba006f722d6dfc78491dc7c6ee`.
- First merge: `ae12e305c4ae556b99e04458baf08f94e3d8c4d9`, parents base then A; tree exactly A.
- Second merge: `c9a9cc4defd75fbd672ca6dc05a18d0ca1e5314a`, parents first merge then B; tree `18a7a12e8a82688d099d2bc49f6cbdc205112a61`.
- No textual conflicts. Neither source branch was changed. Base/#1194 remains an ancestor.
- Draft PR: https://github.com/AMASI-SA/AMASI-SA/pull/1198 . No production merge.

## Narrow integration corrections

1. Readiness displays financial valuation separately from physical quantity approval;
   Smoke B and `ready_for_live_post` are visible.
2. Explicit advertising profile selection must match canonical account `external_ref`;
   blank restored profile does not invent an identity.
3. Replacing section evidence updates unchanged/sibling lines and provider bindings;
   separate FX evidence is preserved.
4. Long real session hashes wrap on mobile instead of widening the page.

No new endpoint, accounting writer, domain persistence store or release intent.
The real client uses `/api/accounting-module/onboarding` and `definitions.financial_base`.

## Fresh local verification

- Track A+B backend: **241 passed + 240 subtests**, zero failures/errors/skips.
- Track B frontend: **89 passed / 12 suites**, zero failures/skips.
- Real connected browser: **12 scenarios passed**, real FastAPI/router + disposable
  local Mongo; no mocked transport. All 16 stages navigable at 390px, RTL, no
  horizontal overflow, zero unexpected page/console errors, zero external requests.
- Fingerprint of every non-session Mongo collection unchanged after all UI work.
  No post/transition/approval/activation/handoff endpoint called by browser tests.
- Full additional backend matrix, including exact-base comparisons, is in
  [local-gates/SUMMARY.md](local-gates/SUMMARY.md). Known baseline Qoyod failures are
  **not passes**; Windows-only failures are separately labeled.
- Reproducible connected test fixture: `scripts/testing/mz2_onboarding_ab/`.
  A dedicated CI workflow reruns A+B and connected browser tests, plus otherwise
  path-filtered Auth/Preparation/Supplier regressions on Linux.

## Explicit gap audit

| Area | A: Ready source | B: Deferred | C: Blocks live cutover |
|---|---|---|---|
| Financial onboarding | Create/get/list/save/resume, CAS, idempotency, explicit zero, N/A evidence, preview/review lock | None silently substituted | Real complete evidence/account/entity coverage required |
| Shipping contracts | Financial courier/driver receivable and payable facts use Track A identities | **A: UI-only intentionally deferred**: delivery cost, VAT, COD tiers, commission and effective dates remain volatile local drafts; no shipping-policy adapter | Required approved policy/courier identity cannot be replaced with UI text; P02 enabling is a separate gate |
| Physical opening inventory | Product/variant/component/unit/location/preparation fields are UI drafts; financial per-account valuation is persisted | No quantity import/approval adapter implemented in this task | **Quantity opening remains incomplete**: exact resource/variant/component IDs, registered unit, location allocations and preparation state must pass existing `opening_inventory_service` draft/import/preview/approval lifecycle under separate authorization; financial equality is not stock proof |
| Advertising | Wallet and payable independently use real accounts/profile mappings and original evidence | Funding reference and notes are UX-only; not opening accounting facts | Missing actual account/profile/evidence/FX mapping blocks review |
| Prepaid/accrued | Prepaid opening asset and accrued liability independently supported | Monthly amortization scheduler deferred; does not block source cutover | Actual opening evidence remains required |
| Evidence/account creation | Incomplete metadata can save while paused | No bypass or synthetic live account creation | Existing canonical evidence upload/account creation remain permission/pause-gated; verified original bytes and exact owner scope required |
| Smoke B | Readiness reports `BLOCKED_BY_ENVIRONMENT` and `ready_for_live_post=false` | No source mechanism to mark passed | Hard hold before live post/activation |
| P02 / G47 | P02 LOCKED; both activation permissions false | No enabling in integration | Physical G47 approval/activation and P02 authorization remain separate |
| Deployment drift | Approved GitHub base used, preserving #1194 | Not repaired here | Runtime `48066a05c2952718fda92f741d41bd802ffc64f3` / tree `294d74d0962b9d55f678f96f5abe13356c61a955` differs from GitHub; separate deployment gate |

## Safety / CI distinction

`release/release-intent-v5.json` is byte-identical to approved base. Preview and
Production DB reads/writes during integration: **0/0**. No control change,
replay/backfill, post, activation, deploy or publish.

The existing Mezan Release Readiness CI at merge commit passed backend preflight
and frontend build. It automatically generated an ephemeral candidate-intent
artifact; the tracked file stayed unchanged. This artifact is not adopted or
used for release. The full Emergent adapter rehearsal was **SKIPPED**, not PASS.
Final local clean-clone source validation does not generate an intent.

Remaining finalization: collect final-head source CI, clean-clone build evidence,
and final SHA/tree; preserve baseline failures as explicit limitations rather
than editing unrelated financial behavior.
