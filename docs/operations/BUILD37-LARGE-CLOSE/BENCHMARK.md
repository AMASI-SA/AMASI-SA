# Isolated supplier close benchmark

Base: `d0a3ff5458dde6e87a7399493369ab58bef7656a`. Candidate is the containing checkpoint, not a deployed release.

Disposable localhost Mongo 8.0.12 replica set, UUID database per test. No Production requests or credentials. The fixture has eight products, 16 product/variant identities, three options, three services, different prices, and one requested invoice line per piece. It asserts that `price_changes` is empty: costs must come from the live definitions, not owner-authorized manual edits. Every piece's product/SKU/variant/options is compared before/after; invoice prices, services, unique piece links, total and all verification flags are asserted.

## Corrected comparable measurements

Single iteration per size. Durations include line tracing, tracemalloc and RSS sampling; not a Production SLA. Reads count Motor `find_one`/cursor `to_list` operations, not wire round trips. RSS is the whole test process, including imported modules.

| Pieces | Base close ms | Candidate close ms | Verify ms | Base/candidate reads | Cost reads | Product reads | Service reads | Piece reads | Price calculations | RSS peak MiB |
|---|---:|---:|---:|---|---:|---:|---:|---:|---:|---:|
|17|6038.4|4453.5|157.6|728/488|24|56|38|3|33|123.70|
|50|8324.4|6021.2|195.2|1091/488|24|56|38|3|64|125.06|
|100|7300.4|6445.7|195.2|1641/488|24|56|38|3|64|128.17|
|150|15233.9|7165.0|188.4|2191/488|24|56|38|3|64|130.99|
|200|19046.0|7165.5|196.9|2741/488|24|56|38|3|64|134.00|

Cost reads here mean cost profiles and option bindings; resource/service reads are separate. Counts are bounded by distinct setup/choice identities for this fixture, not constant for arbitrarily many distinct products. Iterating and validating every piece remains necessary. At 200 diverse pieces `find_one`=299, zero per-piece linkage `find_one`, and eight bulk writes: two service batches and six linkage batches. At 200 identical pieces price calculations=2 and close=4077.4ms.

200-piece candidate stages: live products/services/options/costs 2201.2ms; piece validation 76.7ms; grouping 153.2ms; assembly 1.7ms; existing financial posting 2292.8ms; linkage 661.7ms; flags/audit 14.8ms; persisted verification 197.7ms; commit 32.4ms; separate readback 62.0ms. Stage measurements do not include every pre-transaction/framework step; nested helper times are not additive.

## Integrity and concurrency

Light profiling (method counters and RSS only; no line tracing/tracemalloc): 200 diverse pieces 2926ms; two employees closing 200 pieces each 4950ms aggregate; artificial 10ms delay per instrumented Mongo operation 10070ms for 200 pieces. All three additional runs passed exact price/identity/linkage/verification assertions. This is latency-sensitivity evidence, not a Production measurement.

- All five sizes close and read back with all three verification flags true.
- Two concurrent closes of the same 200-piece session: one invoice, 8542.5ms aggregate close duration.
- Two independent 200-piece sessions for different employees in one merchant: two invoices, 13929.2ms aggregate duration. The shared owner serialization barrier is unchanged.
- Failure after persisted verification before commit: all nonempty collections exactly match their pre-close documents.
- Failure after the first 100-piece service batch: same complete rollback comparison, no partial invoice, orphan linkage or changed historical document.
- 85 focused tests passed on the final runtime changes, including native invoice/rollback, financial integrity, refresh, live cost, wrapper and read-scope contracts.

## Limits and corrected test issues

Earlier exploratory fixtures omitted option names while using name-based selections; their requested amounts could be treated as manual price edits. They are superseded by the corrected measurements above. A first two-session fixture violated the one-open-session-per-employee index; a subsequent synthetic employee lacked a required accounting page permission. Those fixtures were corrected without modifying either production guard. Both authorized employee sessions pass.

The native product debit contract requires INVENTORY_ASSET; it does not accept an EXPENSE product mapping. Services are non-stock resources. This does not certify a separate non-stock product workflow or inventory initialization. No accounting contract was changed.

Remote full CI, independent review and shipping diagnosis remain outstanding. No new Release Intent, Merge, Prepare, Deploy or APK action.
