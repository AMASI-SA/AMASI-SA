# Source-added item review recovery

Prior production source: `f75b9fcc9e5ac48d214feba005e12e472d1aafb9`.
Prior Android baseline: version code **56**. Retain its approved APK and release evidence separately; this document does not assert that either baseline has been redeployed.

## Preferred operational disable

Set `SOURCE_ADDITION_REVIEW_ENABLED=false` through the authorized deployment environment, then deploy/restart every backend instance so all requests use the disabled configuration. This is process environment configuration, not a database flag or an instant cross-instance kill switch. Requests already committed before disable remain approved. Drain old instances/in-flight requests before declaring disable complete.

The default is enabled when the variable is absent. Only `true`, `1`, `yes`, and `on` enable it (case-insensitive, surrounding whitespace ignored). Every other explicit value, including empty or misspelled values, disables it. Prefer explicit `false` and `true` operationally.

When disabled:

- Authenticated capability GET retains revision/fingerprint and returns `items: []`, hiding the action on web and mobile after capability refresh.
- POST rejects with HTTP 503 / `source_addition_review_disabled`, including requests using a previously loaded capability or retrying an already approved item. The first check runs before the owner transaction; checks also run inside the transaction and immediately before the workflow write.
- Existing approved additions remain in the ordinary assignment queue and retain Ready, component, configuration, warehouse, and source-freshness protections. Their existing preparation work can finish.
- Source ingestion and component reservations continue. Unreviewed additions remain unreviewed and can prevent whole-order completion until review is enabled again. This switch does not undo Salla additions or release their stock.

If an environment deployment/restart is unavailable, prepare a reviewed forward-disable source release that makes the same capability false while retaining compatibility logic. Govern every source release through repository protocol v5, including source A / intent B, CI, build evidence and post-publication identity verification. Never edit `/app` directly, reset the production checkout, or replace artifacts outside that protocol.

## Verification and re-enable

After the authorized deployment/restart, verify all serving instances have the intended configuration. Use an authorized reviewer to confirm empty capability rows and disabled rejection for a retained pre-disable capability; verify workflow revision/items/events do not change. Verify an already approved addition still appears in the normal queue, original pieces retain their identities/custody, and no extra assignment is created by the disabled request. A cached button may remain visible temporarily, but the server must reject it.

Re-enable with explicit `SOURCE_ADDITION_REVIEW_ENABLED=true` through the same governed operational path. Refresh the order capability before new approval. Existing approvals remain idempotent; enabling does not recreate units, files, or events. CI covers disabled read/write behavior, existing queue preservation, strict parsing, transaction recheck and re-enabled idempotency. Local execution was not performed for this change; passing GitHub CI remains required.

## Why a full code reset is conditional

Returning directly to the prior backend after feature data exists is not a reliable rollback:

- Baseline catalog queries exclude legacy in-progress workflows even when their added items were approved.
- Baseline partial-batch reconciliation may move the parent back to reviewed.
- Baseline Ready lacks explicit physical coverage for added instant-classified products and the marked-legacy completion/retry path.
- Component intake appends standard-schema plan lines and unit reservations before the review click. Baseline can replay an identical plan but may reject a later addition with `plan_conflict`.

Do not delete workflow items/markers, approval events, allocation rows, component reservations, or consumed units to make the baseline appear compatible. Physical work, stock consumption, files, and shipment effects cannot be undone by removing review metadata. Reverting the Android client to 56 alone does not reverse server data or close stale API callers; server disable remains authoritative.

A complete baseline return is only a candidate after a read-only audit establishes no affected state, or after an explicitly reviewed cohort-specific recovery/drain. Inspect source changes and component plans/units as well as workflow markers, approval events, preparation allocations/pieces and shipment effects. Zero review events alone is insufficient. Audit future source-event handling and concurrent writers before any destructive recovery. Keep compatibility code until those conditions are proven; no database restoration is authorized by this document.
