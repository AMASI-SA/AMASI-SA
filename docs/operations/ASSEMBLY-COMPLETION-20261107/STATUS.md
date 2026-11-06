# Assembly completion delivery — 2026-11-07

Task branch: `codex/fulfillment-completion-hotfix-20261106` in AMASI-SA/AMASI-SA.
Base: `3bd31d56a93ce30f43d514008f545eb2ca3563c3`.

Owner requested an urgent minimal fix and publication because finishing the last
assembly piece appeared not to advance Salla to completed. Production read-only
inspection established that the assembly worker is running and status dispatch
lagged local completion by approximately 45–128 seconds in the reported examples.
One outbox subsequently rejected a source fingerprint after dispatch. Its workflow
fingerprint was unchanged; the exact source difference is not established. Do not
disable evidence checks or replay Ready to recover that record.

The queue awaited each entire delivery sequentially, including provider/label
waits up to 90 seconds, and stopped polling new work until that batch finished.
The source fix retains at most four active delivery tasks, polls available slots
every five seconds, and orders first attempts before older retries. Existing
durable claims, provider read-before-write, source/component checks, attempt
journal and label proof remain unchanged. Shutdown cancels and joins child tasks.

Fresh local evidence uses only an isolated loopback Mongo replica set:

- Existing delivery baseline: 15 passed in 116.836 seconds.
- New scheduler regression before change: 1 failure and 2 timeouts. A blocked
  provider prevented new completion dispatch; old retries excluded the new order.
- After change: all 3 scheduler tests passed in 0.450 seconds. They cover arrival
  during a blocked provider, four-task bound and shutdown, and first-attempt order.
- `git diff --check`: passed. Wider affected integration validation is pending.

Changed source: `backend/assembly_completion_delivery.py`.
Regression: `backend/tests/test_assembly_completion_scheduling.py`.

Production changed by this task: **no**. No business data/provider write, lease,
intent freeze, merge or publication has occurred. `/app` latest checked guard was
inactive and tracked files clean. Unrelated local Qoyod work and Emergent files are
preserved. This checkpoint is not release acceptance.

Next: run affected completion/status integration suites, inspect final diff and
CI; freeze a new v5 source/intent pair, rehearse on Linux, then use fresh production
fetch and the prepare/prepublish gates before the authorized publication. Require
new Deployment Succeeded and three matching official verification probes. Existing
source-fingerprint failures require fresh evidence and remain excluded from any
automatic repair or data migration in this scheduling patch.
