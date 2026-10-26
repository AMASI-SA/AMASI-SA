# WIP: created-order profitability worker

Do not merge or deploy this checkpoint. Based on draft PR #1571 head 7df10b54d26307d8991fcbc13c5bb3a9a675ecb6, not current production.

The private report path transfers its request-owned Salla matched cohort to a two-thread CPU executor after admission through ResourceGovernor. Public callers are copied synchronously before the first await; concurrent mutation by OS threads during that copy is not supported. Admission remains held until a cancelled worker finishes; release is separately shielded against repeated cancellation after a deterministic test exposed a leaked global permit. Mongo reads stay on the event loop, and the cost context is not an atomic database snapshot.

Local tests: 5 worker tests pass using a real ResourceGovernor with a controlled decision; another 50 existing tests pass with admission stubbed because this environment reports resource pressure. A synthetic calculator-only benchmark on 20,000 and 100,000 orders without product items showed maximum loop heartbeat gaps of 0.010 and 0.011 seconds, respectively; this is not an end-to-end report benchmark.

Before proceeding: independent review of ownership and cancellation, confirm all caller paths and nested governor behavior, add deterministic CI coverage for the worker tests, evaluate public snapshot blocking and resource-pressure behavior, and rebase onto current production. No production or provider write was performed.
