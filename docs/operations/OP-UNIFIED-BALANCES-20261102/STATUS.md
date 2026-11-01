# OP-UNIFIED-BALANCES-20261102

Status: WIP. Native-payment normalization implemented and locally checked; full integration and release NOT ready.

## Authority and immutable inputs

The owner requested one combined release preserving both tasks and every later production update. Code-publication permission does not authorize financial corrections, actual refunds, opening rewrites, historical replay or automatic provider-policy activation.

- Latest production read in this continuation: `3d8720860fb524d729f6c83a8c56c5b4de3a2afb`, including customer partial-refund release #1686.
- Integration branch: `integration/operational-balances-unified-20261102`, Draft PR #1687. Parent before this application checkpoint: `1cd6e3aae5989fa31c4578a967373989c4e44819`.
- Provider #1685: `88c7872072e6e6a149a92153a732ae4c0551ae21`.
- Financial follow-up #1677: `df17ef3ff21e2515ed24ec61d5d8019cf9cb2fe5`, inheriting #1675 at `a8ffd5a81637bed8c4462f7bb4c2b059782cd520`.
- Common historical ancestor of production and follow-up: `3bac875cfbcbc7f66849edae83c478dd194d9c12`.
- Product #1678: `d5b301506912a481196af295b0812f3759d7a27f`; only a source-review workflow is committed there, not its required product runtime fixes.
- Local COD archive `mezan_cod_forecast_hardened_20261101.zip`, SHA256 `5001eaa156a758bd8d200b9e1738f6b3816d5eb5791abec0ab074276c6f13a74`, is not remotely committed. Its earlier blocked engine upload remains restricted. Do not retry or bypass that code write through another endpoint, encoding or execution route. This payment-source checkpoint does not modify/upload the engine or incorporate that archive.

Original task branches and production remain unchanged. Use latest production as the base; no reset, whole-file replacement from an older branch, squash/rebase of a frozen release or lease takeover.

## Owner-supplied live verification (previous release, not this work)

The owner supplied unchanged v5 verify output: verified=true, checks=3, source `d0532a057a33776ebe1b40f0d7cbf87a39bf7e4d`, source base `064ba0d0af06a29d947b63cf0c72a8da157264ed`, deployment `3d8720860fb524d729f6c83a8c56c5b4de3a2afb`, release `rg5-e8d8970b2ab29f4358b856dad1facc6e35e464d1091ad800c0e8df13d844a396`.

This proves the owner-observed prior publication of #1686, not combined-candidate acceptance. Separate guard status showing active=false remains unobserved. No /app access, lease manipulation or release preparation was performed here.

## Completed in this payment-source slice

1. Added pure `backend/operational_payment_evidence.py`. Exact aliases recognize native Tamara/Tabby installment names and card rails, without fuzzy routing. Payment party, observed paid amount and a dated captured transaction remain distinct.
2. Replaced only the payment-normalization block and shared alias definition in `backend/operational_balance_sources.py`. Native payment_methods/payment_actions observations are checked for consistent currency, precision, reference, amount and party. No database collection allowlist or writer was expanded.
3. Pass explicit plan_id from canonical payment or its single native payment-method row; contradictory or missing necessary proof is not guessed from an installment alias.
4. An existing complete explicit paid/captured status plus a genuine payment timestamp, matching reference and paid amount creates a capture. Native paid observations alone produce a visible estimate (when fee policy is valid) and a scoped diagnostic with order reference, Arabic reason, required action and missing fields. No order creation/update time or fulfillment state is invented as capture evidence.
5. Exact native reference replays are not summed; conflicting references, split payments, invalid currencies/amounts and contradictory pending-payment evidence fail closed. Unknown payment data does not erase independent product-cost evidence or previously confirmed facts.
6. Cancellation/refund hints are not executed monetary events. Their missing evidence remains explicit. No new provider refunds, old-bank rewrites, fees, openings or policy records are created by normalization.
7. `backend/operational_customer_returns.py` changes only original-party resolution. The published #1686 partial compensation, observed-refund information, physical-quantity separation and original-platform/manual-bank restrictions are retained. Reading a synthetic 170 capture/current total 150/refund 20 continues to work for both native Tamara and Tabby aliases without new deductions.

This is not a completed real capture/plan ingestion pipeline. Real native responses observed previously did not contain the complete dated capture contract. Therefore this slice alone cannot establish that live confirmed provider balances will now increase for every paid order. It does stop silently dropping recognized native methods and makes the missing evidence actionable. No live Mezan saved-state reconciliation was performed.

### Local verification

Python 3.13; exact production source archive plus the four changed application/test files; isolated offline dependencies; synthetic fixtures and read-only fake source database.

```bash
PYTHONPATH=<isolated-deps>:backend:backend/tests python -B -m pytest --noconftest -q \
 backend/tests/test_operational_balance_native_payment_evidence.py \
 backend/tests/test_operational_balance_sources.py \
 backend/tests/test_operational_balance_engine.py \
 backend/tests/test_operational_open_cost_exposure.py \
 --tb=short --junitxml=payment_selected.xml
```

Result: **189 passed**, zero failures/errors/skips, including **95 new parameterized payment cases** and 94 existing regressions. Counts overlap earlier runs and are not additive. An intermediate boundary test exposed a falsey malformed status slug being ignored; explicit type checks corrected it, and the final run passed. This is local unit/source/reducer acceptance only, not real-Mongo transactions, the full application, merged #1685/#1677, UI, release build or live-balance acceptance.

The new tests are in the existing Operational Balance Web glob. Fresh exact-head CI, including existing real-Mongo customer return and atomic paths, is required after the checkpoint. No workflow or dependency changes were made for this application slice.

Reviewed Git blob identities:
- payment evidence module: `c48fbc24f048a842553d16aa86f02da5fe8124a8`.
- source adapter: `5e7790e512f8060fde3f869d61cc932c44935989`.
- customer return module: `df8abe189731183e06e6703dbd68444f3360171e`.
- new tests: `fd13b8c46345918c80da9f96dfdf8ef09e6d1a83`.

## Earlier diagnosis and compatibility findings retained

The full preceding record is preserved at `1cd6e3aae5989fa31c4578a967373989c4e44819` in this same path. Its diagnostic file remains in diagnostics/published_payment_payroll_checks.py: its 15 passing assertions reproduce old published defects, and must NOT be treated as corrected-source acceptance tests. Earlier follow-up payroll tests had 64 local passes; not combined acceptance.

- Production payroll starts at Riyadh cutoff day plus one; the inclusive first-day correction is in #1677 and still unpublished. Preserve reviewed owner activation and opening coverage; do not silently backfill day one.
- PR1685 order_view validates original captured amount against current already-reduced Salla total. Its 170/150 failure must be repaired before that code is combined. This slice preserves #1686's readable order; it has NOT imported/fixed the whole #1685 return implementation.
- Explicit payment plan propagation is implemented here, but proving the plan from a real authoritative source remains required.
- Preserve provider_postsettlement_refund_review_required until statement-linked actual refunded fee/tax and partial/multi-order settlement allocations are implemented and tested. Preserve posting_allowed=false in the pure statement arithmetic reviewer.
- Hunk-level combination remains necessary for engine, sources, routes, service, returns, operational UI and tests. Original individual-branch CI does not validate combined behavior.

## Complete owner scope / activation inventory

| Scope | Existing implemented work | Still required before complete combined acceptance |
|---|---|---|
| Alerts | Aggregation inherited from production #1676; scoped payment reason/reference/action in this slice | Actual source, carrier/rate, product cost, provider fee, advertising start-day/FX and salary evidence remediation, never hide missingness or replace with zero |
| Products/suppliers | Supplier cutoff fix in #1675/#1677; production open-cost feature | Exact allowed statuses at piece level, original SAR costs, received supplier attribution, no duplicate opening-covered debts; #1678 requirements not implemented by its workflow |
| Shipping/drivers/COD | Cutoff delivery and bank/POS review in #1677; local-only forecast | Current carrier reassignment/actual fulfiller, fee vs custody vs forecast separation, remaining forecast source checkpoint restriction, combined Mongo/browser checks |
| Provider balances | Opt-in component model and read-only reviewer in #1685; native method/explicit-plan normalization here | Authoritative actual capture/plan/void/refund mapping; post-settlement credits and allocations; approved merchant policies; gross/net opening proof and read-only balance differences |
| Customers/external people | Latest production #1686 preserved; existing loan/return/exchange flows | Four debt/cash directions, before/after balances, creation vs settlement, exchanges/returns/shipping and cross-path deduplication on combined source |
| Payroll/openings | Inclusive cutoff day and reviewed owner UI in #1677 | Integrate unchanged rule, examine opening coverage, approve only explicit missing amounts; no silent activation or financial correction |
| Unified report | Source in #1675/#1677 | Complete common-period profit and forecast authority; print/download/Excel/mobile/browser acceptance |
| Release | Owner verified previous #1686 release | Current combined tests/security/build; new source A and intent-only B; inactive lease check; one authorized publication, all three identity probes and active=false |

## Next safe action

Read current integration head and this record, verify application blob hashes, inspect exact-head Operational Balance Web/Security/Readiness results, and correct observed failures without weakening guards. Continue authoritative payment evidence acquisition and compatibility with the preserved follow-up/provider sources. Keep all original branches and the prior release intact. A pre-activation read-only comparison of saved balances/openings and explicit owner approval is required before financial correction.

Before any /app or release work, read production_release_guard.py status and honor active leases. Remove the temporary source-review workflow before freezing final release source. The pending v5 combined release must not reuse previous intent or generated artifacts.

Production changed: **NO**. Live balances, provider policies, salary activation, openings, stock, bank/provider APIs, /app, leases and deployment: **untouched**. The application checkpoint remains Draft/WIP and is not permission to publish separately.
