# Primary source scheduling evidence

This is a local synthetic scheduling experiment, not a Production Mongo query
measurement and not proof of Production recovery.

At the reviewed source boundary, the primary batch contains 32 unique source
collections. Alphabetical scheduling puts `unified_orders` in position 32.
`load_collections` admits four reads at a time and only computes each read's
remaining budget after its semaphore admission. The budget is the minimum of
15 seconds and the remaining overall refresh budget; queued time consumes the
overall budget. The overall refresh deadline remains 85 seconds.

## Reproduction

The experiment uses the actual `load_collections` implementation and the exact
32 primary collection names. Synthetic readers await 16 ms for each of the
31 smaller collections and 112 ms for orders. Each read is limited to 150 ms;
the remaining shared budget is 190 ms. No application database or financial
writer is called.

These durations are a 1:100 scheduling model of 1.6-second smaller reads, an
11.2-second orders read, the unchanged 15-second per-read limit, and 19 seconds
left after a hypothetical 66-second prelude. The prelude and source durations
are assumptions for reproduction, not observed Production measurements.

| Scheduling | Orders queue, ms | Total, ms | Result |
|---|---:|---:|---|
| Alphabetical, run 1 | 116.44 | 195.81 | Orders timed out |
| Alphabetical, run 2 | 117.37 | 191.01 | Orders timed out |
| Alphabetical, run 3 | 114.37 | 190.96 | Orders timed out |
| Orders first, run 1 | 0.36 | 164.21 | All 32 completed |
| Orders first, run 2 | 0.24 | 165.49 | All 32 completed |
| Orders first, run 3 | 0.28 | 169.27 | All 32 completed |

All six runs observed at most four concurrent reads, joined all 32 started
readers, and left zero active readers. Prioritizing orders overlaps the longer
read with the smaller reads without increasing any source or request limit.

## Change and regression coverage

Only the primary collection sort key changes: orders first, followed by the
other collections in their existing alphabetical order. Query predicates,
projections, 20,001-row overflow detection, completeness admission, concurrency,
read deadlines, and financial commit rules are unchanged.

`test_operational_balance_source_scheduling.py` first failed against the old
code because orders did not enter the first four-reader wave. It uses event
barriers rather than a tight timing assertion. The fixed test verifies all
32 unique reads complete and join, the four-reader maximum, original 15-second
default, and original ordering of the other collections. A second test verifies
that an orders failure still cancels and joins every started sibling and never
admits partial primary evidence.

Production still requires request-local stage and cursor evidence to determine
whether its failure is queue starvation, first-row latency, cursor transfer,
saved-state preparation, or another stage. This change removes a demonstrated
scheduling hazard; it does not establish the Production database root cause.
