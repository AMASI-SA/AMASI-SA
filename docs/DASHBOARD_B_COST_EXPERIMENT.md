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

Phase2 is deliberately not implemented until phase1 artifacts identify the
largest synchronous region. A potential outer-order checkpoint follows all
existing cost-input reads and preserves accumulator/rounding/order semantics;
large single orders remain an explicit possible overrun. Sibling Dashboard
reads can still interleave differently, so immutable cost-result equivalence
does not by itself prove arbitrary whole-Dashboard mutation equivalence.

Do not alter Resource Governor, Accounting, Shipping, Review, Android or parser.
No merge, prepare, deploy, Production test or worker/process implementation.
Production writes=0.
