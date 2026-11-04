## Delivered COD recognition verification

The owner requires external courier COD receivables and shipping payables to become confirmed at delivery, with subsequent transfers settling the receivable. Driver cash becomes custody, while bank/card selection creates no driver custody. The deployed source already implements this contract; this PR adds explicit regression coverage without modifying runtime behavior.

Validation: full operational acceptance on retained isolated loopback Mongo: **615 passed, 0 skipped**, 5 existing Pydantic deprecation warnings. Coverage includes delivery transition, partial settlement, replay, late collection evidence and noncash driver choices. Test databases retained. No Production writes, activation or deployment.

Live blocker: MZ2 shipping-cod page reports no approved shipping prices. General shipping settings do not establish approved operational contracts. Operational source collection also excludes orders created at/before system start; in-flight orders require separate cutover reconciliation. Bank/card driver selection does not automatically add operational bank liquidity; settlement/receipt recording remains separate.

Evidence and continuation: docs/operations/MZ2-DELIVERED-COD-20261024/STATUS.json and evidence/. Production base f75b9fcc9e5ac48d214feba005e12e472d1aafb9. No new release intent is needed for this tests-only review.
