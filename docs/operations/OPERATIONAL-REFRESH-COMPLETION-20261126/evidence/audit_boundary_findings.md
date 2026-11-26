# Adjacent audit boundary checkpoints

Scope: coalesce only the duplicate end/start checkpoints between adjacent groups
of at most eight audit pages in an owned visible refresh. The first and final
checkpoints remain. Page queries, four-page prefetch, byte bounds, BSON/content
hash validation, ordered complete history, cancellation ownership, and all
non-visible reader behavior remain unchanged.

Production changes from this follow-up: **none**. This is local verified source
for the parent integrator's independent review and later release gates.

## Source and operation counts

Base source: `5de9d4ddb85e8f74852149e068c176b64bf0a8d6`, branch
`codex/operational-refresh-completion-20261126`.

The old reader called the completed group's checkpoint, then called another
checkpoint immediately before the next group without an intervening page read
or processing step. The new reader retains the first admission checkpoint and
every completion checkpoint. A boundary therefore freshly authorizes both the
completed group and the immediately following group.

For P valid nonempty pages, audit-page checkpoints fall from
`2 * ceil(P / 8)` to `ceil(P / 8) + 1`. Each checkpoint still invokes the fresh
authorization callback and renews the owned lease. Packed-state preparation,
manifest preparation and complete-history restoration keep their independent
before/after authorization gates.

The isolated synthetic fixture exercised both the actual serial and raw-prefetch
readers, the visible request/lease lifecycle, and the replacement-state store
path. Every immutable page contains one synthetic BSON event and there is one
tail event; financial and issue packets are populated using existing fixtures.
It asserts complete state equality, identical exact page queries and count,
unchanged stored input, zero nonlease writes and no orphan tasks.

| Pages | Audit checkpoints before | After | Whole unpack checkpoints before | After |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 2 | 2 | 8 | 8 |
| 8 | 2 | 2 | 8 | 8 |
| 9 | 4 | 3 | 10 | 9 |
| 16 | 4 | 3 | 10 | 9 |
| 17 | 6 | 4 | 12 | 10 |
| 64 | 16 | 9 | 22 | 15 |

Both reader paths produced identical checkpoint positions/counts in all twelve
observations. These are measured operation counts, not wall-clock timings.

The included hypothetical cost model uses five database operations per
successful owner-browser checkpoint: four fresh metadata reads plus one lease
renewal. The source-derived five-operation model is distinct from the fixture,
which observes the callback and lease renewal without simulating the four
production metadata reads. At an **assumed** 3 ms per database operation, the
64-page audit-gate model falls from 240 ms to 135 ms (35 fewer modeled DB
operations). No production page count, latency or improvement is established.
This does not claim to resolve the observed 16.287-second unpack stage.

Exact observations are retained in `measure_audit_boundaries_before.json` and
`measure_audit_boundaries_after.json`. The runnable counter is
`measure_audit_boundaries.py`; it requires only the existing isolated test
environment and uses no Mongo connection, sockets, simulated latency sleeps or
production data.

## Red/green verification

The new 32-case module first ran against unchanged production source. It exited
1 with **16 failed, 16 passed**, no errors/skips, in 1.661 seconds. Every failure
was the intended repeated internal checkpoint for 9/16/17/64 pages, on both
reader paths and with both packed/unpacked states. All denial/lost-lease cases
already passed. Exact results: `audit_boundaries_red.xml`.

After the two scoped source predicate changes, the focused suite exited 0 with
**119 passed, 11 deselected**, no errors/failures/skips, in 4.845 seconds. The
deselected cases require a real Mongo fixture or a socket fixture; none was
started in this local run. Exact results: `audit_boundaries_green.xml`.

Coverage includes fresh denial after page 8 before page 9 and after page 16
before page 17, successor lease preservation, existing final-boundary denial,
full BSON/history/tail parity, corruption and manifest rejection, maximum
eight-page authorization gap, unchanged four-read concurrency/byte capacity,
disconnect/deadline/repeated cancellation with joined I/O and CPU workers,
ordinary/strict-session fallback, and a CAS retry using the new revision's full
manifest. `git diff --check` also exited 0.

Commands from the repository root (the actual local interpreter was the existing
`/workspace/scratch/39c6dec7c37f/profit-test-venv/bin/python`):

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 PYTHONPATH=backend:backend/tests \
  python -B -m pytest --noconftest -q -p no:cacheprovider --asyncio-mode=auto --tb=line \
  --junitxml=docs/operations/OPERATIONAL-REFRESH-COMPLETION-20261126/evidence/audit_boundaries_red.xml \
  backend/tests/test_operational_balance_archive_checkpoint_boundaries.py

PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 PYTHONPATH=backend:backend/tests \
  python -B -m pytest --noconftest -q -p no:cacheprovider --asyncio-mode=auto --tb=short \
  --junitxml=docs/operations/OPERATIONAL-REFRESH-COMPLETION-20261126/evidence/audit_boundaries_green.xml \
  backend/tests/test_operational_balance_archive_checkpoint_boundaries.py \
  backend/tests/test_operational_balance_archive_visible_batches.py \
  backend/tests/test_operational_balance_archive_prefetch.py \
  backend/tests/test_operational_balance_archive_read_lifetime.py \
  -k 'not actual_mongo and not actual_motor and not opaque_capabilities and not real_socket'

PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 python -B \
  docs/operations/OPERATIONAL-REFRESH-COMPLETION-20261126/evidence/measure_audit_boundaries.py
```

The counter was run once before and once after the source changes, redirecting
its exact JSON output to the corresponding observation file. Both runs exited 0.
The before/after source hashes are included in those files; both were measured
while HEAD remained the base source above.

## Frozen source SHA-256

| Path | SHA-256 |
| --- | --- |
| `backend/operational_audit_prefetch.py` | `621951659b007b7ccb0e69cb1caa9f12ed6105e9db4288cfe4b4464a8ad6f2b3` |
| `backend/operational_balance_archive.py` | `40ffa6f7aa481d4bd24d63ff34ba7cb086a1b81e23d83131b430abe8518c65b6` |
| `backend/tests/test_operational_balance_archive_checkpoint_boundaries.py` | `4d9851ba0b1f054adb64b07e0c8c8891eabb13dd8ca5ee35f3b2921d0e45d92c` |

Next action: parent independent source/test review and durable task-branch
checkpoint. No approval/publication/financial refresh was executed by this
implementation task.
