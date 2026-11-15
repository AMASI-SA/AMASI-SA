# Read-only production diagnosis — order 299781816

Source: production commit 9afb38cf0dc0d8e8cd887f2c2cdb4a6be5a7ca68. No queries have yet run. Use the owner identity from the authenticated Mezan session; never query by order number alone. All reads must target the confirmed live production database, not preview.

## Initial reads

Common selector: `{"user_id":"<OWNER_ID>","order_number":"299781816"}`.

1. `unified_orders`: `_id,user_id,order_number,order_date,order_status,g47_salla_snapshot,raw_by_source.salla_direct.updated_at,raw_by_source.salla_direct.date,raw_by_source.salla_direct.created_at`.
2. `mezan_component_order_lifecycle_v1`: `_id,user_id,order_number,accepted,retry_required,cancelled,source_fingerprint,source_updated_at,state,snapshot_revision,generation,option_price_currency_revalidation,zero_price_currency_revalidation`. Retain whether audit fields exist, even if null/invalid.
3. `settings`, selector `{"user_id":"<OWNER_ID>"}`: full `g47_inventory` only (omit `_id`), preserving object type.
4. `mezan_component_consumption_plans_v1`, selector `{"user_id":"<OWNER_ID>","order_id":"299781816"}`: `_id,user_id,order_id,state,reconciliation_required,source_version`. Preserve document existence.
5. `order_review_workflows`, common selector without filtering completion mode: `user_id,order_number,completion_mode,review_completion_operation_id,stage,revision`.
6. `order_review_completion_operations`, selector `{"_id":"<WORKFLOW_OPERATION_ID>","user_id":"<OWNER_ID>","order_number":"299781816"}`: `_id,user_id,order_number,state,completion_mode,superseded_by,revision,workflow_fingerprint,business_snapshot.schema_version,business_snapshot.normalization_version,business_snapshot.facts.identity,business_snapshot.canonical_hash,business_snapshot.integrity_hash`. Do not substitute newest-by-date operation or filter away an invalid state before diagnosis.

## Ordered guards

Follow `revalidate_unchanged_component_snapshot` in `backend/fulfillment_v2_routes.py`:

- Requested revision must be an integer but not Boolean and equal current watermark revision; watermark must not be cancelled or require authoritative refresh.
- Previous lifecycle must have accepted exactly true, no retry/cancellation, and a source fingerprint.
- Watermark clock must be present and non-regressive against an existing prior clock.
- `component_provider_version(raw, created_event=True)` must exactly equal watermark after normalization. Precedence is raw.updated_at, then raw.date.updated, then raw.created_at/raw.date.created/raw.date fallback. Do not replace a nonempty old updated_at by a newer date.updated without evidence and a reviewed source-ingestion fix.
- Actual `get_order(MongoOrderRepository(...))` must construct the canonical order. Fingerprint includes eligibility and cancellation, not just item mapping.
- Legacy cohort requires the applicable old-order evidence and state legacy_operational_pre_g47; inventory enabled=false alone is insufficient. Nonlegacy requires reserved lifecycle, accepted plan, no reconciliation_required, source_version.value equal lifecycle generation.
- Only after preceding guards pass, differing fingerprints try zero-price proof followed by paid option-price proof.

## Additional proof reads only if needed

The workflow must be mezan_local_v1 and bind the exact existing operation. The operation must be completed, mezan_local_v1 and not superseded. Compare identity fields _id/user_id/order_number/revision/workflow_fingerprint to the immutable snapshot's typed identity. Workflow current revision equality is not itself a guard here.

Full proof requires the single order's complete raw_by_source.salla_direct, the linked operation's complete business_snapshot and identity, and the actual canonical DTO. A partial hash projection cannot verify snapshot integrity. The exchange_rate envelope is bound to both approved unknown hashes. Native KWD/SAR precision equivalence is necessary but not sufficient. After restoring only allowed option-price envelopes in memory, all normalized source facts and unknown-source fingerprints must equal approval, and restored component fingerprint must match the accepted lineage. Do not project typed arrays using fabricated object paths.

Keep private data out of durable evidence: retain only guard names, equality booleans, sanitized field paths, revisions and necessary nonsecret clocks. Browser sequential reads are not atomic; reread the watermark afterward and discard a mixed-revision diagnosis. No source refresh, operational_owner call, acceptance apply, receipt/Ready action, provider call or financial write is authorized by this read plan.
