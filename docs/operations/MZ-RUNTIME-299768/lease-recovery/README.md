# Native advertising scheduler ownership

The global timer previously slept for 15 seconds after a cycle completed. Its 25-minute lease was not renewed during that cycle. A controlled test admitted a second owner at expiry while the first process was still alive. This was reproduced locally, not observed on the store.

Acquisition, renewal and terminal recording now use the MongoDB server clock and a token unique to each acquisition. An active cycle renews while targets or Snapchat accounts make progress. If no target or account finishes for the existing 25-minute stale-job window, the owner cancels and joins its reporting coroutines without releasing the lease early. An uncertain renewal allows at most one retry of the lease write while enough confirmed time remains. A lost owner cannot renew or finalize the new owner's record. Provider-side requests already submitted can have an unknown outcome.

The [real-Mongo test](../../../../backend/tests/test_ads_auto_sync_global_lease_mongo.py) runs in the native ad scheduler workflow. The [process probe](probe.py) was independently run with a disposable MongoDB 8.0.12 instance, 2.5-second test lease and synthetic provider cycle. [Results](results.json) include one winner among 15 first acquisitions, renewal beyond the first expiry and successful takeover by a new OS process after a real SIGKILL. Adjusted client clocks and malformed stored deadlines could not claim a valid lease.

Run from the repository root, with the baseline commit available locally:

```sh
PYTHONPATH=backend python docs/operations/MZ-RUNTIME-299768/lease-recovery/probe.py /path/to/mongod
```

Limits: the probe covers global report ownership. It does not prove exactly-once external credential refresh or advertising fact publication after network partitions, nor early safe takeover from an older scheduler during a mixed-version rollout. The checks do not start operational or accounting workers.
