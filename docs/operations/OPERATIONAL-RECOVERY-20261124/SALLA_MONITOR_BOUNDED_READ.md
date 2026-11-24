# Salla webhook monitor: bounded metadata reads

## Observed failure and scope

The release operator reproduced `GET /api/salla/webhook-monitor` HTTP 500 and
immediately captured a `pymongo.errors.NetworkTimeout` at `_event_snapshot`:
the capture aggregation's `to_list(length=200)`. The limit applied to grouped
output, not to historical documents sorted and fetched. The old group carried
the latest full payload and nested processing results while counting all
captured deliveries. Concurrent health checks remained responsive.

This patch changes only this diagnostic read and its index lifecycle. It does
not replay webhooks, change queues, edit captured history, or write financial
entries. Local synthetic timings below are not production timings.

## Reader and index

- An authenticated owner must have exactly one unambiguous merchant binding.
  Missing, malformed, or conflicting bindings return 409 with `status=unknown`
  before capture reads. A unique disconnected binding may still read its
  authorized history. The binding is checked again before returning data.
- One metadata-only compound index contains merchant, event, last receipt
  time, first receipt time, and delivery count. The aggregate requires this
  index, selects only the 16 approved events for the bound merchant, and counts
  every historical delivery without a pre-group limit.
- Sorting by event then descending last receipt time preserves the previous
  per-event `$first`/`$last` selection, including missing last receipt times.
  The oldest `first_received_at` is not substituted for that existing rule.
- At most one document per observed event is fetched afterward, tied to the
  aggregate's last receipt timestamp. Only seven order-reference paths and
  four boolean processing-status fields are projected; payloads and arbitrary
  nested sync results are neither grouped nor returned.
- Queue counts retain their owner-and-merchant scope and use the already
  existing merchant/state index. Every database read has a 1,500 ms server
  budget; the complete request has a five-second application deadline.
- Missing, building, hidden, or otherwise unavailable indexes have no
  collection-scan fallback. Database errors and request timeouts return clear
  503 `status=unknown`, never an empty successful monitor or zeroed counts.
  Logs contain fixed stages and exception types, not provider error bodies.

## Owned initialization and failure containment

`_local_startup` saves a one-shot initializer in
`app.state.salla_monitor_index_task`; it does not await the build before normal
web readiness or worker startup. The operation has a 15-second Mongo budget
and 16-second application deadline. It logs starting, ready, or unavailable.
Failure leaves only the monitor unavailable and is consumed by the owned task;
there is no retry loop, GET schema work, process-local ready cache, or financial
deployment readiness override. Other replicas use the same required index.

Shutdown cancels and joins the task before closing Mongo. The initializer is
deliberately absent from the background runner's critical task list: that
runner exits on `FIRST_COMPLETED`, so a completed one-shot initializer would
otherwise restart the worker.

## Verification

An isolated Mongo 7 prototype used 8,000 synthetic captures, 6,000 belonging to
the selected merchant, with 4 KB payloads. The complete aggregate had
`GROUP → PROJECTION_COVERED → IXSCAN`, examined 6,000 keys and **zero documents**,
and produced the exact all-history delivery totals. Aggregate execution was
6 ms inside Mongo and 8.6 ms wall time in that local experiment.

The final five-suite acceptance run passed **75 tests in 7.87 seconds**, with no
skips, against a newly created loopback replica set and disposable databases.
It covered the new monitor contracts, existing queue visibility, inbox unit
and real-Mongo delivery invariants, and lifecycle contracts. The four new
real-Mongo cases verify covered complete counts, single-document latest reads,
missing/hidden/pending index failure behavior, successful initialization, and
the original timestamp-selection behavior.

An independent reviewer reran the same frozen source and five suites against
another newly owned replica: **75 passed in 6.58 seconds**, with no skips and
unchanged source/test hashes. The review found no blocking scope, lifecycle,
count-completeness, or output-safety issue.

The remaining contracts verify tenant isolation, numeric/string merchant
conflicts, no GET writes or index creation, metadata-only output, explicit
unknown on Mongo/network/request timeouts, cancellation acknowledgment,
binding changes during a read, and responsive health during index timeout.

The command requires an explicit disposable loopback replica set in
`MZ2_TEST_MONGO_URI`, uses `PYTHON_DOTENV_DISABLED=1` and `--noconftest`, and never
reads a production environment file:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 PYTHONPATH=backend:backend/tests \
python -B -m pytest --noconftest -p no:cacheprovider -q --tb=short \
  backend/tests/test_salla_webhook_monitor_reads.py \
  backend/tests/test_salla_webhook_monitor_queue.py \
  backend/tests/test_salla_webhook_inbox.py \
  backend/tests/test_salla_webhook_inbox_mongo.py \
  backend/tests/test_salla_orders_v3_lifecycle.py
```

After integration, the existing Salla recovery CI workflow must include
`test_salla_webhook_monitor_reads.py` beside its monitor queue suite. The release
operator must verify index readiness, actual monitor success and complete
totals, response timing, visible failure messaging, and other-app health in
production. No production performance or readiness claim is made here.
