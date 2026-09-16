# Preview imported orders — partial repair

Operation: MZ2-FIN-CUTOVER-001 / P01. Preview only; Production unchanged.

## Confirmed findings

- Import jobs UI confirms user Excel import completed with no failures.
- Frontend order fixtures default on Preview and hide API results.
- Explicit mock=0 reveals only stored Preview seed orders.
- Inspected MongoOrderRepository and filter summary require raw_by_source.salla_direct; Excel imports retain source=excel. Do not fabricate provider payloads to bypass this contract.
- Settlement matching queries unified_orders independently; order-list exclusion does not establish the cause of unmatched settlement rows. Fresh matching after import remains unverified.

## Implemented slice

Default to environment API when neither explicit query nor stored demo preference selects fixtures. Explicit mock selection and production exclusion are preserved. No storage mutation.

Verification: node frontend/scripts/test-preview-order-mode.mjs failed on old default (true vs false), then passed all 8 cases after repair; git diff --check exit 0. No full build or live deployment established.

## Blocker and continuation

Repeated browser CDP timeouts prevent reading the independent cloud terminal, including after documented runtime recovery. No commands reached the cloud terminal. No shared /app changes, runtime writes, posting, merge, or Production deploy.

Restore independent terminal access; inspect actual imported row schema, implement explicit read-only Excel projection with provenance and operation restrictions, verify list/detail/summary/search parity and owner scoping, then test settlement matching. Apply source fix to isolated Preview and verify fresh runtime behavior. Existing original workbook and prior reviewer logout work remain untouched. Preview is NOT ready for complete accounting acceptance.
