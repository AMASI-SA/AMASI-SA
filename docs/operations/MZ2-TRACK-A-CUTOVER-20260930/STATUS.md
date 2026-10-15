# TRACK_A_CUTOVER_CORE_READY_FOR_INTEGRATION

Source-only backend work. No release candidate, release intent, deployment or live accounting action.

- Branch: `codex/mz2-cutover-core-20260930`.
- Approved shared base: `1d9d65d8576d5bc852f52262b84af150356b9385`.
- Base tree: `82ed0d05eee935d22c7a4050f21e66cdb9f4f551` (merge #1194).
- API contract: Issue #1006 comment `5900935996`; clarification comments `5900990152`, `5901180648`; definitive source contract in `API_CONTRACT.md`.
- Target cutover: `2026-10-01T00:00:00+03:00`, Asia/Riyadh. No live schedule or activation created.

## Implemented

1. Owner-scoped setup sessions, revision CAS, stable request idempotency, audit, save/resume and explicit section states. Missing facts are never zero. All metadata save/preview/review effects stay in `mz2_onboarding_sessions`, including while financial writes are paused.
2. Typed opening lines reuse the existing V2 compiler. Exact account/entity mappings, provider bank evidence, separate employee/supplier/ad balances, explicit zeros, immutable source bytes, currency/FX snapshots, and per-account inventory value reconciliation are checked at preview/review/handoff.
3. Canonical handoff creates/previews/reviews the existing V2 opening draft inside one owner transaction. It does not post. Failure rolls back draft/evidence/session; identical retry returns the same draft. Financial pause/missing controls still reject before the callback with HTTP423.
4. Readiness separates source completeness from live permission: Smoke B proof and owner authorization remain hard holds; financial pause, writer transition and active opening verification are surfaced. Physical inventory approval is not claimed from value equality.
5. Existing order-creation cutover core is reused unchanged. Tests cover before/equal/after the exact target, missing/invalid timestamps, provider/Salla/bank/COD ingress and existing replay/Store Delivery paths. No recognition fallback or fence relaxation.
6. Explicit economic classifications added: supplier advance asset, prepaid-expense asset, accrued-expense liability. No payable/advance netting and no amortization scheduler. Supplier advance now appears separately in V2 financial position reporting.

## Files and scope

- New backend core: `accounting_onboarding.py`, `accounting_onboarding_contract.py`, `accounting_onboarding_identities.py`, `accounting_opening_categories.py`.
- Existing backend: `accounting_financial_accounts.py` (categories and internal canonical lifecycle composition), `accounting_mz2_reports.py` (supplier advance asset mapping), `financial_provider_apps.py` (new metadata routes while preserving existing protected paths).
- Tests: `test_accounting_onboarding.py`, `test_accounting_opening_categories.py`.
- Documentation: this directory only.
- No frontend, opening-inventory quantity/service/UI, Qoyod, startup, write-control, atomic writer, transition policy, release guard or release-intent changes.

## Validation

Final isolated run: **228 tests PASS + 240 subtests PASS, 0 failures/errors/skips**. See `TEST_RESULTS.json` for suite counts, exact command, environment and tested source hashes. Suites cover new HTTP contracts, financial accounts and real Mongo opening lifecycle, V2 ledger, MZ2 permissions/report isolation/write control/receivable workflow, cutover boundaries, P02, Store Delivery accounting and G47 opening inventory.

All DB fixtures used synthetic data in dedicated loopback-only MongoDB 8.0.12 processes. No Production/Preview DB access. Existing Pydantic v1-validator deprecation warnings (5) remain; not suppressed or changed.

Independent read-only source review found two input-boundary issues during implementation (operator objects in provider-bank IDs; extreme inventory decimals). Both fixed; HTTP rejection and stable-error regressions included. Independent fix review: 20 pure-memory assertions PASS, no remaining concrete defect found in audited paths. Existing route paths/financial wrappers, metadata write surface and atomic handoff reviewed.

This is local source validation, not GitHub Release Readiness/CI or a release rehearsal. No release intent generated.

## Schema / operational impact

`mz2_onboarding_sessions` is new metadata storage, created only on an explicit future setup request. Deterministic owner+request `_id` uses built-in uniqueness; updates CAS owner/version/status with audit and request hash in the same document. No startup migration, index creation, seed or backfill added. Reviewed/handoff snapshots cannot be edited through this API.

Existing ledger writer, journal collections, account/evidence stores, opening inventory SSOT and transition contract are retained. New category slugs use the current generic V2 account contract; no data migration is needed. Existing financial post remains the only posting path and includes activation effects, so it must remain unavailable in Track B's live UI until independent approval.

## Closed gaps

- Durable non-financial setup while paused, instead of attempting a financial draft for every UI save.
- Explicit zero/absence/N/A distinctions and independent entity account coverage.
- Supplier advance, prepaid expense and accrued expense semantics.
- Exact supplier/employee/external/ad/courier/driver identity validation.
- Reviewed evidence/mapping/FX snapshots and transactional canonical handoff.
- Planned inventory valuation reconciliation per account rather than only grand total.

## Deferred / integration work

- Track B frontend/domain persistence and inventory quantities remain Track B scope. It must reconcile its preserved checkpoint to the approved shared base and bind this API.
- Automated periodic prepaid-expense amortization is deferred; correct opening asset is supported.
- Live inventory quantity approval remains distinct from planned financial reconciliation and belongs to the existing inventory service/Track B.
- Existing account creation and evidence upload remain behind financial pause. Sessions may save incomplete references, but cannot claim complete preview until genuine accounts/evidence exist. Any live preparation route needs separate authorization; no bypass was added.

## Hard live blockers

Smoke B remains `BLOCKED_BY_ENVIRONMENT`. No equivalent Production proof was fabricated; no probe endpoint was added. Separate owner authorization is still required before any live post, financial enablement, opening approval, activation or cutover. P02 and G47 remain locked/unactivated. Source-only completion does not resolve these live gates.

Production/Preview DB reads/writes: **0/0 for each environment**. No Merge/Deploy/Publish, no release lease, no write-control change, no live opening data, no activation. No changes to #1195 or its metadata.
