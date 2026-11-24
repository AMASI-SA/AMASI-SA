# Bounded audit-page reads during an authorized refresh

## Observed problem and scope

The production release D automation samples reported about 10.9–12.5 seconds
in snapshot unpacking, compared with about 57–64 milliseconds for the initial
saved-state query. The full mutation reader hydrates immutable historical
audit pages sequentially. The production page count and individual page
latencies were not available, so those samples do not prove how much time this
change will save. A later automatic cycle succeeded, but finished close to its
unchanged overall deadline.

This patch overlaps at most four raw audit-page reads inside the existing
leased refresh context for the exact database object and owner. Only an exact
Motor database, or the existing exact `AcceptanceConfigDatabase` around an
immediate exact Motor database, is eligible. The original wrapper remains in
the query path. A session, opaque or custom database/collection adapter, another
owner, another database, an unleased request, or a nonstandard document class
keeps the original sequential reader.

Every page is decoded with the collection's original BSON codec and validated
in manifest order. Page identity, owner, starting position, event count, content
hash, manifest and final total remain mandatory. The full decoded audit is
still returned to the unchanged mutation operation. No history is omitted and
no financial engine, copied-state isolation, revision comparison or commit
behavior changes.

## Resource ownership and cancellation

- Read-ahead is fixed at four documents. Each raw BSON document is checked
  against MongoDB's 16 MiB maximum, and the admitted group is bounded at 64 MiB
  of raw encoded bytes. This is a raw-buffer bound, not a total-process memory
  claim: the complete decoded history, decoding overhead and existing snapshot
  copies still consume memory.
- A completed read task is released immediately after its result is obtained.
  That page's raw bytes are released after validation, before validating the
  next page. Validated buffers are not retained until the end of the group.
- The existing fresh owner/lease authorization cadence of at most eight pages
  remains unchanged. No next group is admitted after a failed validation or
  failed checkpoint. At most the current already-authorized group can have
  been read ahead when corruption is detected.
- Every admitted unfinished read remains owned. Error, disconnect, deadline
  and repeated caller cancellation cancel and join all those reads before the
  request can release its lease. The existing joined CPU preparation helper
  also owns the page decoder and validator.
- A driver-side PyMongo deadline is established before Motor dispatch. It is
  the minimum of a finite 15-second per-read ceiling, the remaining request
  deadline, the source-read budget and any shorter inherited PyMongo deadline.
  Cleanup therefore cannot wait on an unbounded socket after asyncio
  cancellation. The global scheduled-refresh deadline and CPU priority are
  unchanged, and no deadline context escapes the read.

## Synthetic performance evidence

The prototype used a new disposable loopback Mongo instance, synthetic
immutable pages of 131,739 BSON bytes each, full decoded-output equality and
two runs for each configuration. The table reports the mean/median of those
two runs; it is not production performance or an estimate of seconds saved.

| Pages | Additional synthetic latency per page | Sequential | Four-page prefetch |
| --- | --- | --- | --- |
| 64 | None | 78.743 ms | 79.216 ms |
| 128 | None | 223.295 ms | 164.976 ms |
| 64 | 10 ms | 762.873 ms | 274.623 ms |
| 128 | 10 ms | 1,719.433 ms | 546.439 ms |

These results demonstrate the intended benefit when page I/O latency matters,
and also show no meaningful gain for the smaller zero-added-latency case.
The final implementation retains the original packet-decoding and checkpoint
path; the prototype's extra preparation around unpacked packets is not part
of this patch. Final tests cover the actual integrated refresh and mutation
reader rather than only the prototype.

## Verification

The raw-buffer lifetime regression first failed against the original
four-task list because completed task results retained already-validated
buffers. It now checks weak references to BSON buffer carriers during each
following validation, the four-buffer maximum and zero retained buffers on
completion.

The focused run passed **142 tests in 9.56 seconds, with no skips**:

```bash
env -u MZ2_TEST_MONGO_URI -u DEMAND_READ_TEST_MONGO_URI \
  PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 \
  PYTHONPATH=backend:backend/tests \
  DEMAND_READ_TEST_MONGOD=/workspace/scratch/56d90f85c1cc/mongo-test-tools/mongod7 \
  /workspace/scratch/56d90f85c1cc/test-venv/bin/python -B -m pytest \
  --noconftest -p no:cacheprovider -q --tb=short --asyncio-mode=auto \
  backend/tests/test_operational_balance_archive_prefetch.py \
  backend/tests/test_operational_balance_archive_visible_batches.py \
  backend/tests/test_operational_balance_archive_read_lifetime.py \
  backend/tests/test_operational_balance_snapshot_encoding.py \
  backend/tests/test_operational_read_runtime.py
```

The fixture starts and removes its own random-loopback Mongo replica and
disposable test database. It does not load application credentials. Coverage
includes real Mongo full-state BSON parity for both supported database types;
packed financial snapshots and issues; typed BSON audit events; ordinary
serial behavior; malformed and corrupt pages/manifests; authorization and
lease loss; absence of financial operation or CAS after failed validation;
joined I/O and CPU cancellation; raw-byte capacity; opaque/slotted adapter
rejection; an actual stalled Mongo-wire socket; diminishing Motor executor
budgets; expired requests dispatching no query; and no timeout-context leak.

The final test-only CI adapter accepts the Stock gate's explicitly supplied,
matching `OPERATIONAL_TEST_MONGO_URI` / `MZ2_TEST_MONGO_URI` capability only
after checking loopback host, explicit port, no credentials and no database
path. The generic fixture is unchanged. All **50 cases in the new suite passed
in 3.37 seconds, with no skips**, under that exact environment, with both
`DEMAND_READ_TEST_MONGO_URI` and `DEMAND_READ_TEST_MONGOD` removed in the test
process. This run used another newly owned local replica.

The independent reviewer checked the final source and reran both environments:
**142/142 passed in 9.15 seconds** for the five-suite gate and **50/50 passed in
3.49 seconds** under the Stock-style environment, with no skips, failures or
errors. All three source/test hashes were unchanged before and after review,
and the whitespace/diff check passed. No blocking findings remained.

Frozen source/test SHA-256 values:

- Prefetch helper: `078f63365380f789d9b27000c561f50f279151bda3f61492c372ce0d0b4c70f0`
- Archive integration: `a8e08fdd573fae56f184e1c10070bde994845f55bdb1ac9c81ba3dd46b833000`
- Focused tests: `e48776c93ddfa8f015388dfe688fe2ad03b08982d23fd4237e2b5debe0bc6b8e`

## Release verification required

Root owns combined verification and governed publication. After deployment,
compare actual automatic-refresh unpack, copy, total duration and memory at
the same production scope, and verify a successful scheduled financial
commit. The raw-buffer cap does not resolve or explain platform-wide memory
pressure, and this patch does not make a claim about cold financial-report
CPU time or other workers' retained memory.
