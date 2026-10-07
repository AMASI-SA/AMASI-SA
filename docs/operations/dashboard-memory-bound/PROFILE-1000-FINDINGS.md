# Full-summary WIP profile: 1,000 orders / 1,000 products

2026-10-04. Isolated localhost:27261 only. Private fixture database was deleted
after profiling. No production source edits were made during this sample.

The existing full V2 summary benchmark ran under cProfile with one request,
1,000 seeded orders, 1,000 products, and 1 KiB item padding. Source and imported
calculator functions were the same WIP versions being benchmarked by the root
task. The root's 10k matrix may have overlapped on the local Mongo server;
therefore this profile's latency is diagnostic, not an uncontended benchmark.

The complete summary succeeded. Observations:

| Measurement | Result |
|---|---:|
| Endpoint wall time under profiler | 19.184 s |
| JSON encoding | 0.035 s |
| JSON bytes | 53,352 |
| Mongo read commands / returned documents | 58 / 2,825 |
| Sum of Mongo command durations | 0.141 s |
| Peak worker RSS | 118,173,696 bytes |
| Observed spill disk peak | 17,801,216 bytes |
| Spill cleanup | complete |

The profile records 12.33 million calls (10.16 million primitive). Its strongest
non-overlapping self-time result is **40,694 sqlite3.Connection.execute calls,
5.598 seconds**. SpillMap made 9,002 `__setitem__` calls but 22,238 `_write`
calls, showing additional writeback/eviction work. Sequence append made 3,639
calls; each currently also queries MAX(ordinal) before insertion.

Serialization is the next visible cost: 67,849 `_encode` calls (3.677 seconds
cumulative), 26,290 `_decode` calls (2.364 seconds cumulative), 688,143 recursive
`_pack` calls and 1,112,696 recursive `_unpack` calls. These cumulative times
overlap JSON encoding/decoding and must not be added to their child self times.
The final response JSON serialization is negligible compared with this internal
spill serialization.

Existing fee normalization also costs time: 12,470 normalize_payment_method
calls, 2.481 seconds cumulative; 730 match_settings calls, 2.633 seconds
cumulative. This is a secondary measured cost, not authorization to alter fee
logic. Async/coroutine cumulative timings and background monitor waits overlap;
do not sum the full top-function cumulative table as a wall-time decomposition.

## Minimum next change to test

1. Batch SQLite writes in an explicit request/batch transaction rather than
   per-statement autocommit, preserving disk limits, insertion order, cleanup,
   and visibility of failures. Re-run this same sample and parity tests first.
2. Remove redundant writeback for explicitly immutable/read-only spill maps
   used for lookup evidence/catalog records; keep mutable aggregation maps'
   eviction/flush contract. A blanket eviction-write removal is incorrect.
3. Keep per-sequence next ordinal as bounded namespace metadata instead of
   issuing a MAX query per append. Preserve replay order and namespace reuse.

Do not rewrite financial calculators based on this profile. If the first two
changes leave serialization dominant, evaluate a more compact safe tagged-JSON
encoding separately with type/parity tests. No large profiling run is needed
before measuring the targeted changes on this same fixture.

Evidence: PROFILE-1000.prof, PROFILE-1000-TOP.txt,
PROFILE-1000-CALLERS.txt, PROFILE-1000-RESULT.json.

## Independent direct-timer confirmation

cProfile's multithreaded caller table contains impossible cross-thread caller
relationships, so its caller attribution is not reliable for causal analysis.
A second fresh 1,000-order worker wrapped only the spill execute/encode/decode
boundaries with perf_counter counters; production source files remained untouched.
This confirms the same operation counts independently (PROFILE-1000-DIRECT.json):

| Boundary | Calls | Direct elapsed time |
|---|---:|---:|
| Mapping UPSERT including MAX ordinal subquery | 22,238 | 3.696 s |
| Sequence INSERT | 3,639 | 0.948 s |
| Sequence next MAX ordinal lookup | 3,639 | 0.528 s |
| Mapping SELECT by key | 8,186 | 0.375 s |
| Internal tagged encoding | 67,849 | 2.137 s |
| Internal tagged decoding | 26,290 | 1.412 s |

Endpoint/encoding wall time was 13.231 seconds. Mongo command durations summed
to 0.120 seconds, and final response JSON encoding was 0.010 seconds. Thus the
large cost is local SQLite/serialization work, not the Mongo round trips or
response JSON. The direct-timer counts support the proposed transaction and
redundant-writeback fixes; do not base changes on the corrupted caller tree.
The private fixture database was deleted again after this confirmation run.
