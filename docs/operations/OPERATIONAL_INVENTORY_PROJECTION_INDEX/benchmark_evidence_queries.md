# Isolated receipt-evidence query benchmark

This is a synthetic read benchmark, not a Production index change or physical
receipt writer test. `benchmark_evidence_queries.py` creates and drops only a
UUID database with prefix `inventory_projection_benchmark_test_` on loopback
27462, Replica Set `operationalphysical`. It does not start/restart MongoDB,
connect to Salla, or change application index initialization.

## Dataset and validation

- MongoDB 8.0.12, 100,000 documents, five owners, 1,000 locations per owner,
  20 receipt documents per location; growth run doubles histories to 200,000
  documents / 40 receipts per location without changing active stock references.
- Two current occupancy references per location. Their proofs include two direct
  receipts and an opening-adoption witness for one reference: three relevant
  documents per location. Current direct status cycles through posted, pending,
  and rejected. All three statuses and the retained old direct receipt plus its
  opening-adoption witness are asserted present in the actual loader result.
- Location-history baseline reproduces the prior owner/location query and its
  20,001 read bound. Optimized measurements invoke the real
  `_inventory_evidence_queries` and `_load_inventory_evidence` functions.
- Each sample retrieves/deserializes full returned documents; optimized sample
  time additionally includes evidence-preservation assertions. Three samples,
  reported median, sequential local workload. This is not a Production latency
  SLA. No fixed cache state or host load guarantee is claimed.
- `explain` uses `executionStats` without hints, limits 20,001 per query, and
  aggregates all batches. The JSON includes keys/documents examined, returned,
  execution time, winning indexes, and first-batch winning plan. The explain
  command alone uses a tolerant UTF-8 decoder for local server diagnostic text;
  receipt reads use the normal strict decoder.

## Index proposal — isolated experiment only

Existing indexes are the three indexes created by
`ensure_inventory_receipt_indexes` plus MongoDB `_id_`. They have no location or
receipt-ID selector suffix. The candidate pair is:

1. `(user_id, location_id, id)` for current direct receipt identity.
2. `(user_id, location_id, source_type, adopted_receipt_ids)` for opening adoption.

The second index is multikey on adopted receipt IDs. It deliberately has no
status predicate, so pending/rejected evidence cannot disappear. Both indexes
are owner-prefixed and cover each branch of the OR selector. No uniqueness
change is proposed. Index installation must receive separate review; no
permanent hint or production index installation is included in this patch.

## 100,000-document results

Median milliseconds, with total documents examined in parentheses:

| Query / indexes | 10 locations | 100 locations | 1,000 locations |
|---|---:|---:|---:|
| Previous location-history / existing | 84.60 (20,000) | 113.75 (20,000) | 1,092.81 (200,000) |
| Current-reference query / existing | 272.05 (20,000) | 102.12 (20,000) | 1,048.02 (200,000) |
| Previous location-history / candidate | 94.55 (20,000) | 108.19 (20,000) | 433.03 (38,000) |
| Current-reference query / candidate | 6.44 (30) | 13.99 (300) | 137.89 (3,000) |

The optimized query alone reduces returned historical data (20,000 to 3,000 for
1,000 locations) but **does not solve index scan cost**. Its 10-location samples
were slower without candidate indexes. Do not claim unconditional latency
improvement from the read-code patch. With the candidate pair, MongoDB chose
both intended indexes automatically for all three tested location counts.
The old location-only query still selected a broad existing index in some
batches even when candidates existed.

## Growth: 200,000 documents

| Query / indexes | 10 locations | 100 locations | 1,000 locations |
|---|---:|---:|---:|
| Previous location-history / existing | 185.21 (40,000) | 200.17 (40,000) | 3,578.36 (400,000) |
| Current-reference query / existing | 537.49 (40,000) | 209.61 (40,000) | 2,207.72 (400,000) |
| Previous location-history / candidate | 181.99 (40,000) | 187.20 (4,000) | 717.80 (40,000) |
| Current-reference query / candidate | 5.97 (30) | 18.96 (300) | 131.77 (3,000) |

Both candidate indexes were again selected automatically for the optimized query
at all three sizes. Doubling unrelated histories did not increase documents
examined for the optimized/candidate combination. All 24 measurements completed;
all result-count, current-negative-status, and opening-witness assertions passed.
Both uniquely named fixture databases were dropped on completion.

## Safety and rollout limits

The benchmark asserts evidence inclusion, not full stock acceptance. Eligibility,
opening compatibility, reservation/consumption and concurrency are covered by
separate acceptance tests. Unreferenced historical receipts are not balances;
occupancy remains the sole physical source. Ambiguous direct/adoption witnesses
remain visible to the verifier. Reading fewer irrelevant histories must not
remove the bounded-read reconciliation failure or introduce a fail-open fallback.

Before an index rollout, independently review index storage/write overhead,
representative owner/receipt/adoption-array distributions, rolling index-build
procedure, and explain plans on approved non-Production snapshots. Verify the
chosen plans after deployment; do not use this synthetic distribution as proof
of every Production planner choice.

An initial script attempt exceeded MongoDB's 63-character database-name limit
and failed before fixture creation. The corrected script uses a 20-character
UUID suffix and subsequently runs the complete benchmark. No result from the
failed setup is counted as a performance sample.
