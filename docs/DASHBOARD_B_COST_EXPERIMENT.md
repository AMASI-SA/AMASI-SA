# Cost/profit experiment: phase1 before phase2

Baseline PR1301 HEAD0a6ad622c3f3bb0777986191d355bedc644e5b54.
No parser experiment edits/re-run; no runtime adoption or Production access.

Phase1 measures the existing cost builder at10K/100K with real isolated
MongoDB8.0.12/Python3.11/Linux. Original, coarse observer and fine observer arms
run paired with warmup and three measured repetitions. Full returned JSON,
commands, inputs and record counts are checked. Input BSON sizes are measured
outside timed samples. No financial writes are permitted by the DB facade.

The synchronous outer order loop is measured separately from product indexing,
catalog/profile/resource/binding mapping, missing-product mapping/sort, profit
row mapping, sorting, final summaries and response construction. Fine mode adds
per-line product/cost lookup, line-cost calculation, bookkeeping/DTO assembly,
policy scaling/totals and profit accumulation. Profile/binding lookup arguments
are included in line-cost calculation; product resolution is the cost-lookup
span. These boundaries are stated rather than pretending to isolate every
dictionary access. Enclosing and child spans overlap and must not be summed.

Wall/thread CPU, record counts and largest synchronous span are captured.
Heartbeat observes scheduling delay separately. API-thread CPU across the
whole async function is not attributed as pure cost-loop CPU. Fine profiling
overhead is compared against coarse and original execution before conclusions.
There is no dedicated deep-copy call in the order loop; dictionary/DTO creation
is timed in its actual bookkeeping/mapping sections.

Phase1 completed on HEAD 824e4e010d8d59da701a322eaa5c7e0490b94ec8:
[CI 37951805439](https://github.com/AMASI-SA/AMASI-SA/actions/runs/37951805439),
33 passed, zero failed/skipped. At 100K the coarse median CPU was 1967.24ms for
the outer cost/profit loop, 1535.15ms indexing and 1088.43ms finalization.
Original whole-function CPU was 4902.67ms, coarse 4831.60ms and fine 6698.75ms.
Fine instrumentation is intrusive; its child times must not be interpreted as
uninstrumented absolute costs. This evidence selects the outer loop, not a
speculative attribution of all heartbeat delay to cost calculation.

Phase2 compares the original cost loop with cooperative 1/5/10ms budgets.
Existing computation-only sharing and parser5ms are FIXED in all four arms;
there is no new parser comparison or parser code change. Only the outer order
loop receives checkpoints. The accumulator, all statements, final rounding,
indexing and finalization stay unchanged. Six paired rounds plus warmup rotate
arm order on Linux/Python3.11/Mongo8.0.12, with an independent HTTP client and
10K multi-same, 100K single-same and 100K multi-different scenarios.

Cost slices measure active API-thread work, excluding time yielded to siblings.
Coarse synchronous observers are used equally in all arms. Fine per-line
observers are confined to the separate phase profile; a private catalog helper
adds a nested shallow DTO-enrichment timer. No deep-copy is introduced.

Correctness covers the original cost builder, full Dashboard mixed fixtures,
R1/mutation/R2 schedules, mutation after the final cost-input read, cancellation,
concurrent tenant-private accumulators, malformed exceptions, duplicates,
stable sort ties, first-image choice, currencies and rounding. Tiny budgets in
edge fixtures force actual yields independently of measured 1/5/10ms budgets.

Outer-order checkpoints follow all existing cost-input reads and preserve
accumulator/rounding/order semantics. Large single orders remain an explicit
possible overrun. Sibling Dashboard
reads can still interleave differently, so immutable cost-result equivalence
does not by itself prove arbitrary whole-Dashboard mutation equivalence.

Do not alter Resource Governor, Accounting, Shipping, Review, Android or parser.
No merge, prepare, deploy, Production test or worker/process implementation.
Production writes=0.
