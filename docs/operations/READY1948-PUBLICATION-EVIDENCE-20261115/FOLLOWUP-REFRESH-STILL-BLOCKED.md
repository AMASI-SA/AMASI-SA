# Ready lock after user refresh — follow-up, 2026-11-15

## Current result

**The publication is verified; the user's live Ready problem is still unresolved.** The user reports performing source refresh from the native app and Salla/Mezan, with the same lock. Their screenshots show the refresh spinner followed by the generic incomplete-approval warning.

A fresh ordinary web assembly search at 2026-11-15T20:42:25.593Z showed Ready 0/5, five disabled Ready buttons and five source-reconciliation-pending messages for order 299781816. No further source refresh or order mutation was performed by root. The Salla connector independently confirmed the correct Amasi store and the order's current in_progress status and KWD currency. No customer contact, personalization, payment amounts, credentials or raw payloads are retained here.

## What source inspection establishes

The native refresh helper and backend refresh wrapper consider ok/found/updated and matching order number sufficient for refresh success. They do not require component revalidation accepted=true. The existing narrow response carries auto_fulfillment.component_revalidation separately.

Source persistence writes a newer revision with component_pending=true, then attempts revalidation. Rejection can preserve the refreshed source while keeping Ready blocked. This explains how the observed result is possible; it does not identify the actual rejected predicate for this order.

Two independent read-only reviews traced the deployed B/M tree. Relevant layers include prior lifecycle acceptance/retry/cancellation, watermark and provider clocks, immutable approval linkage, plan/generation state, canonical order payment/totals/items equality, equivalent option-currency proof, permitted detail enrichment and fingerprint lineage. No evidence yet justifies relaxing those guards.

A concrete test coverage gap exists: the seven real-Mongo currency cases do not combine the richer detail-hydration shape with the actual repository path; the nineteen detail cases use direct mapping and a mocked workflow lookup rather than full refresh → persisted repository → revalidation → Ready. No new tests or speculative patch were added before obtaining a precise live failing condition.

## Access boundary and minimum pending read

Opening Emergent's existing View database action was rejected by automatic approval review. The page identified it as the database for the published website, but review rejected access to that private surface and its database controls. Root checked the retrieved prior authorization for this same order and attempted the same action once with that new evidence; review still declined because the earlier authorization was not user-authored in the current transcript. Root did not bypass the block through a terminal, another URL or credentials.

Production database manager was not opened. No Mongo URL was revealed, copied, sent or changed. The existing order UI remains available for ordinary scoped reads.

The pending request is explicit current permission to open the existing production database manager and read only reconciliation records for order 299781816, without changing records. Minimum diagnostic projection:

- current watermark: revision, component_pending, cancelled, requires_authoritative_refresh and source_updated_at;
- prior lifecycle: accepted, retry_required, cancelled, state, generation, snapshot_revision, source_updated_at, fingerprint/audit presence and comparison results;
- comparison of normalized provider clock against watermark and accepted lifecycle, without customer fields;
- only if those pass, immutable approval linkage/integrity checks and the first rejected proof condition or differing field paths.

Do not reuse historical revision6 or infer that the original preview database is production. Do not run another refresh just to obtain a diagnostic response. Existing review-detail and preparation-status GET routes are unsuitable substitutes because they have side effects. No Ready, receipt, label, payment or manual database writes are authorized by this read-only checkpoint.

## Source and release coordination

- Diagnostic source: B dd1f29970506f9db2454cdf3b708818b399ee620, exact tree 8b35611f0e66945a323b2365d7c6d456668d8b2e matching published M c626af288d3823694b4f1d0efcc3670e4ee74d81.
- Local checkout remained clean; no source, configuration or dependency changes were made.
- No release lease, deployment, Android build or application version was created in this follow-up.
- At 20:38:15 UTC, production ref still matched M. Provider PR1950 had resumed on predecessor M with frozen B4 983cb9307a8a23d086bc6cb3df962683870b01e2 and was running gates. Its 35 changed paths did not overlap the earlier Ready/notification source changes. Recheck current production and release coordination before freezing any corrective candidate; do not interfere with the provider owner's lease or workspace.
- Previous publication receipts remain valid. They prove deployed bytes and lease closure, not successful business revalidation of this order.

## Next safe action

After explicit authorization for the limited production-manager read, verify the existing connection's production context without exposing or transmitting credentials, obtain the minimum diagnostic fields above, identify the first actual failing predicate, then reproduce that case before changing source. If a new connection/credential transfer is requested by the manager, do not infer authorization for it from read-only access.

[Fresh post-refresh UI readback](after-user-refresh-readback.json)
