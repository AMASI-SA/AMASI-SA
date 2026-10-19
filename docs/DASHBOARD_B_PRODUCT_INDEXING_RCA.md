# Product indexing RCA — instrumentation only

Baseline b7d8deb3b59b258c58b484d614f635c27f885fa8, Draft PR1301.
The preceding advertising experiment measured a largest contiguous indexing
peak of1766.81ms for1ms advertising at100K/multi-different. This is a peak,
not a median and not a new measurement from this RCA.

## Scope

Trace dashboard_v2_routes._index_products -> index_current_catalog_products
-> enrich_current_salla_cost and name normalization. Test-only AST timers keep
source statement order. Coarse preparation/product traversal/name-alias return
phases; fine enrichment, ID/SKU/name/variant indexing and nested shallow-copy,
variant lookup/raw-cost recovery phases. Child spans overlap parents and must
never be summed with them. Calibrate original/coarse/fine observer overhead.

Capture real isolated MongoDB8.0.12 normalized catalog inputs at10K/100K from
Dashboard with parser5/cost5/ads1 fixed. Three measured direct-call samples after
warmup with alternating mode order, thread CPU/wall/heartbeat/GC observations.
GC is included in wall/CPU, not additive or a proven causal decomposition.
Discard previous return maps outside each next timing. Input hashing, JSON
sizing/output serialization and rich-variant fixture construction are excluded.
Also profile10K products with2current/2raw variants (one new variant each),
explicitly synthetic and not a Production distribution. Record output map sizes.

Exact ordered JSON, object-reference relationships, duplicate last-wins IDs/SKUs,
first-object name aliases, malformed exception parity and shallow sharing tests.
Full Dashboard JSON/read-count equivalence; existing mixed filters/permissions/
tenants and independent-read mutation schedules. No optimization, yield or new
DB read boundary inside indexing; no runtime adoption or full load rerun.

## Next step

After measurement, propose only a separate1/5/10ms ordered cooperative design.
Both product and name-group passes must preserve original accumulators/order;
large individual variants/name groups may need finer checkpoints in a future
approved experiment. No per-batch dedup/sort or re-grouping. Do not implement it
here. Final evidence and handoff recorded on Issue1006 and PR1301.

No Governor, Accounting, Shipping, Review, Android, parser/cost/ads candidate edits.
No merge/prepare/deploy/Production tests. Production writes=0.
