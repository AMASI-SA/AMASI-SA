# WIP: created-order profitability worker

Do not merge or deploy this checkpoint. Based on draft PR #1571 head 7df10b54d26307d8991fcbc13c5bb3a9a675ecb6, not current production.

The private report path transfers its request-owned Salla matched cohort to a two-thread CPU executor after admission through ResourceGovernor. Public callers are copied synchronously before the first await; concurrent mutation by OS threads during that copy is not supported. Admission remains held until a cancelled worker finishes; release is separately shielded against repeated cancellation after a deterministic test exposed a leaked global permit. Mongo reads stay on the event loop, and the cost context is not an atomic database snapshot.

Local tests: 5 worker tests pass using a real ResourceGovernor with a controlled decision; another 50 existing tests pass with admission stubbed because this environment reports resource pressure. A synthetic calculator-only benchmark on 20,000 and 100,000 orders without product items showed maximum loop heartbeat gaps of 0.010 and 0.011 seconds, respectively; this is not an end-to-end report benchmark.

Before proceeding: independent review of ownership and cancellation, confirm all caller paths and nested governor behavior, add deterministic CI coverage for the worker tests, evaluate public snapshot blocking and resource-pressure behavior, and rebase onto current production. No production or provider write was performed.

Independent review found another event-loop bottleneck: actual _index_products on a synthetic 100,000-product catalog with one variant each blocked the loop about 0.74 seconds; the worker tests stub cost-context loading and do not cover this path. Production catalog size is unverified. A nested-governor deadlock was reproduced with a Snapchat admission holding one unit, a queued dashboard requiring two, and profitability waiting behind it; actual production nesting is unproven.

A local-only alternative uses an independent two-slot limiter per event loop and checks ResourceGovernor memory pressure before and after admission and after loading cost context. It passed a synthetic nested admission test and retains the slot through worker cancellation, but does not reserve shared global capacity; cross-event-loop admission and the indexing bottleneck remain unproven. This alternative was not accepted, merged, or deployed. Do not trade away the shared resource guard without a verified resource-safe design.
