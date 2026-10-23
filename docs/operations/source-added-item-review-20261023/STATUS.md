# Source-added item review

Task: source-added-item-review-20261023. Candidate implementation, not deployed.
Base: `571e1704fc823c681c61b2f31de1db4f5ea5f55a` on `hotfix/prod-snap-meta-final`.
Task branch: `codex/source-added-item-review-20261023`.

## Approved behavior

Merchant adds a new product line in Salla while the order is in progress.
Web and Android order details show `تمت المراجعة` for that new line. Explicit
approval admits only that line to the shared reviewed/unassigned product catalog
for ordinary later batching with other products. Parent stays in progress;
existing reviewed snapshots, stock units, assignments and physical pieces remain.
Approval itself creates no preparation file, assignment or Salla write.

The server validates live permissions, source identity/fingerprint, current
component lifecycle, unchanged original lines, local completion proof or legacy
review evidence, and whole positive quantity. Workflow append and audit event are
one owner-serialized transaction; repeated clicks acknowledge the prior approval.
Source ingestion appends reservations only for genuinely new lines with unchanged
original content/warehouses and newer source version. Existing-line edits/deletes
are not new-line additions. Completion/instant routing cannot bypass pending
review or physical preparation of additions.

## Changed surfaces

- Source addition HTTP service and existing review router registration.
- Component stock append path and regression tests.
- Reviewed catalog, local policy, preparation stage reconciliation.
- Ready physical coverage and instant routing guards.
- Web order page, shared review hook/service/button and tests.
- Existing real-Mongo CI adds new regression modules.

## Verification so far

- Component and real-Mongo compatibility: 39 passed, 1 pre-existing skip.
- Source review HTTP: all 10 real-Mongo tests passed, including actual original
  and added-product mobile preparation-file/PDF and physical-piece creation. Only
  external image download is mocked; duplicate approval, stale source, tenant and
  permission checks, rollback, original piece preservation and normal assignment
  are exercised.
- Catalog/Ready/adjacent suites: 147 passed, 17 Mongo-only skipped, 3 environment
  proxy/PDF-client cases deselected. Baseline Ready counterexamples fail.
- Web targeted UI/service/shipping identity: 28 passed.
- Android dedicated 16 checks and existing 38 item tracking checks passed.
- No production data or production configuration changed.

## Android companion

PR https://github.com/AMASI-SA/amasi-mobile/pull/295
Commit `272bd187390dc57cf51403f4b8db96e2cae85ab4`.
Stacked on PR294, exact baseline `a8542ff3cbb6a762d1ba03447c6e2ed391509ba9`.
Full project TypeScript, complete historical auth checks, CI, device acceptance,
and APK build are not yet verified. Android source was prepared from an exact
subset; remote commit correctly uses the full baseline tree and parent.

## Next safe action

Finish real assignment integration checks, inspect CI on exact remote commits,
and review both candidate PRs before any merge/release. No release guard lease,
APK, OTA, merge or deployment performed. Final checkpoint SHA is recorded in the
PR and repository continuation ledger Issue #1006.
