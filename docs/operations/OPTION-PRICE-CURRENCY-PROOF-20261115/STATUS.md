# Option-price currency representation recovery

## Scope and checkpoint

- Task: `OPTION-PRICE-CURRENCY-PROOF-20261115`.
- Repository: `AMASI-SA/AMASI-SA`.
- Branch: `codex/option-price-currency-proof-20261115`.
- Starting source: `7ef45017e5221635ce3c80c3788f73d9c50b1e08`.
- Implementation commit: `a9f563890c2be4c36b026eb8c081cb9dcacddd75` (local).
- Remote PR: https://github.com/AMASI-SA/AMASI-SA/pull/1933 (Draft).
- First remote source: `51ee523d81806990498860851d14e9cbc0240040`, tree
  `b2e79ae261171cf58e59a9c8a1171ac2d7944b02`.
- State: the seven real-Mongo currency cases passed. One neighboring test
  fixture needed correction; a new exact-source CI run and new v5 intent are
  required before release.
- Owner authorization on 2026-11-15: explicitly approved pushing, merging and
  publishing this fix ("تمام وادمج وانشر"). The earlier push refusal is resolved
  by this new explicit instruction. Publication still requires the normal
  verification and v5 release gates.
- Production changed: **no**. No merge, release intent, release lease, deployment,
  order mutation, supplier receipt, or financial write was performed.
- The prior hold is lifted for this fix. This task does not authorize unrelated
  notification work to be included in either repository's release.

## Implemented behavior

`backend/component_option_currency_recovery.py` proves the narrow case where
approved nonzero KWD option prices later arrive as SAR. It binds the original
order exchange-rate envelope to the immutable approved business snapshot,
checks the completed local approval and its lineage, and compares every source
fact and unknown source-field fingerprint after restoring only the permitted
option envelopes in a temporary copy. Original source values, the approved
snapshot, accounting amounts, selections, quantities, stock, and receipts remain
unchanged.

The proof uses native minor units (KWD 0.001, SAR 0.01), rejects non-native
precision and exact half-unit boundaries, and requires the SAR representation
to lie strictly within half a fils of the approved KWD amount at the bound
order rate. This is an operational equivalence policy; it does not reconstruct
a unique historical SAR option price or assert Salla's tie-breaking policy.
Other currencies/directions or any independent financial/source change fail
closed. Repeated representation switches, the prior zero-price recovery, and
return to the original representation keep the original approval lineage.

`backend/fulfillment_v2_routes.py` calls this proof from the existing unchanged
snapshot revalidation path. The existing owner transaction, revision fence,
cancellation, authoritative-refresh and stock gates remain in place. Successful
proof records an audit on the component lifecycle. No scheduler or background
job was added.

`backend/preparation_piece_operations.py` reports
`assembly_source_reconciliation_pending` when the canonical in-progress order
is awaiting source reconciliation. Missing supplier receipt or mandatory
employee custody keeps its own blocking reason. The source lookup replaces an
existing read; this does not add a query per piece or change Ready eligibility.
The web client recognizes the source-pending reason and keeps its action frozen.

## Verification at this checkpoint

- Local focused backend run: **93 passed, 102 subtests passed**, exit 0.
  Command: pytest with `--noconftest -p no:cacheprovider` for
  `test_assembly_source_pending_message.py`, `test_preparation_piece_operations.py`,
  and `test_component_option_currency_recovery.py`; dotenv/bytecode disabled.
- Independent read-only review found no material blocker in FX binding, exact
  source comparison, native rounding bounds, approval lineage, transaction
  fencing, receipt safety, or query count. The reviewer independently executed
  **35 tests and 102 subtests**, exit 0.
- Web regression: **58 passed, zero skipped**, exit 0. The new diagnostic and
  removal of the misleading unprepared-product prefix were each observed
  failing before the fix. The card and actual page alerts now show only the
  source-pending reason for this code; pressing the frozen action never calls
  the Ready mutation. Existing receipt reasons retain their wording.
- Seven real-Mongo integration tests exercise source refresh, read-only dry run,
  received/unreceived readiness, immutable financial/business state, zero-price
  lineage, repeated transitions, and rejection of real changes or stale gates.
  They are included in `Review Completion Resumable`, with an executed-test
  minimum and the existing no-skip/no-failure evidence gate.
- The local environment could not start a disposable Mongo server. No substitute
  or production database was used. CI run `43656826678` ran the seven new
  integration cases against its disposable replica set: all seven passed, with
  zero skips, failures, or errors. Android recovery also passed 329 tests.
- The first CI source was not accepted overall: the main group had 808 passed,
  one failed, and 457 passed subtests. The failure was
  `test_legacy_addition_ready_retry_after_address_correction[True-in_progress-in_progress-True]`.
  Its obsolete `source_status` mock no longer supplied canonical source data
  after the source-message read change. The fixture now inserts the same
  parametrized status in scoped `unified_orders`; no production fallback or
  assertion was changed. Local RED was reproduced (exit 1); the complete
  source-addition file plus source-message tests then passed 25 tests (exit 0).
- Retained first-run JUnit artifact: `14138252213`, SHA256
  `0438d874bb1f90fa173d84fc6921545dff4addbe29ecbd5e768e4108f8d9f65c`.
  The remaining 18 first-source workflows passed. The candidate intent from
  readiness run `43656826800` is superseded by this fixture/status source
  change and must not be frozen or deployed.

## Remaining work and next safe action

1. Advance PR1933 with the narrow fixture correction and this checkpoint; record
   the new full source A SHA in the PR. Require all exact-source CI, including
   the seven new Mongo cases and the corrected neighboring retry regression.
2. Review the new readiness artifact, freeze intent-only B, and require both
   clean-clone deployment adapter rehearsals and all B gates. A normal merge
   must have the exact B tree. Never reuse the superseded first-source intent.
3. The original `/app` guard currently reports an active release owned by
   `codex-campaign-operating-period-1932`, prepared at
   `2026-11-14T22:10:56.977380+00:00`, deployment commit
   `7ef45017e5221635ce3c80c3788f73d9c50b1e08`. Do not clear that lease or change
   `/app` or the production branch during its handoff. Recheck read-only status
   before the main merge/publication. This task has made no lease change.
4. Mobile PR https://github.com/AMASI-SA/amasi-mobile/pull/357 is based directly
   on published Android81 (`ef3984ee423d2e2645155fc33d154c6376d83a76`), excludes
   PR356, and prepares Android82 / 1.0.67 with the existing APK profile. Current
   candidate is `2d6b97656c84d9cc45a6adb2e907db6a57d028c7`; exact CI and export
   must finish before normal merge and one EAS build. It changes the diagnostic
   message only and does not remove the backend source hold.
5. After verified deployment, perform a scoped dry run of the
   existing revalidation path against the current revision of the affected
   order. Apply only if the full approved-source proof passes. The complete live
   proof has **not** been executed in this task; an FX-envelope match alone is
   not sufficient to claim the existing order will pass.
6. Preserve supplier-receipt and all other normal Ready requirements. A piece
   without receipt remains blocked even after source reconciliation succeeds.

The repository-wide continuity ledger is Issue #1006. Fresh read on 2026-11-15
still shows 2500 comments and a body last updated on 2026-11-12. This task-specific
status and its new PR are the active continuation records. The saved review
archive preserves the earlier pre-authorization checkpoint.
