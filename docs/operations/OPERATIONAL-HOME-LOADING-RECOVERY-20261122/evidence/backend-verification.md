# Backend catalog and order-batch verification

Recorded on 2026-11-22 in the controlled recovery checkout. These are summaries
of completed tool runs, not reconstructed raw pytest log files. The original
pytest output was captured in tool stdout; standalone raw logs were not written.
No production database, production HTTP endpoint, browser, provider or host
operation was used by the backend subagent.

## Source and ownership

Catalog source and tests are now in coordinator checkpoint
`35512ed7b09a5c6ef6e5a550a018559e3e5fe4af`:

- `backend/operational_balance_sources.py`
- `backend/tests/test_operational_catalog_read_scope.py`

The subsequent fairness slice, awaiting the coordinator's source checkpoint:

- `backend/order_engine/repository.py`
- `backend/order_engine/service.py`
- `backend/tests/test_order_batch_cpu_fairness.py`

The backend subagent made no commit or push. Frontend work was preserved and
was not included in these verification claims.

## Existing isolated test environment

All commands ran from the repository root. The pre-existing interpreter was:

```text
/workspace/scratch/81f2839b514c/courier-test-env/bin/python
```

No dependencies were installed. Python bytecode, pytest cache and dotenv
loading were disabled. Focused fixtures use in-memory databases or read-only
buffered cursor fakes. The optional real-Mongo integration suite did not run.

## Catalog red to green

Before changing the catalog source:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 PYTHONPATH=backend /workspace/scratch/81f2839b514c/courier-test-env/bin/python -B -m pytest --noconftest -p no:cacheprovider -q --tb=short backend/tests/test_operational_catalog_read_scope.py
```

Exit 1: **4 failed, 17 passed in 1.08s**. Failures were the intended efficiency
regressions, after the complete output assertions had succeeded:

- Both courier discovery modes fetched 2,660,647 bytes of synthetic setup data,
  identical to the deliberately unprojected reference.
- 128 accounts and bindings performed 16,384 account identity reads.
- 512 accounts and bindings performed 262,144 account identity reads.

After narrowing only the directory's shipping setup query and indexing ordinary
string account identities:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 PYTHONPATH=backend /workspace/scratch/81f2839b514c/courier-test-env/bin/python -B -m pytest --noconftest -p no:cacheprovider -q --tb=short -o asyncio_default_fixture_loop_scope=function backend/tests/test_operational_catalog_read_scope.py
```

Exit 0: **21 passed in 0.80s**.

Coverage includes complete courier output equivalence against an unprojected
reference, aliases, readiness and lifecycle fields, unchanged generic shipping
source reads, tenant scoping, duplicate setup rejection, exact account matching,
case and type behavior, malformed-value behavior, duplicate connection evidence,
BSON identifier formatting, output ordering and a linear operation-count budget
for ordinary string identities. Non-string identities retain the old comparison
path and can still have quadratic work.

Separate synthetic numeric measurement through the same current source:

| Fixture | Result |
| --- | --- |
| One courier, 300 synthetic history records in three history fields | Full setup 1,330,137 BSON bytes; directory 194 BSON bytes; output identical |
| 128 ordinary accounts and bindings | 128 returned; 256 account identity reads |
| 512 ordinary accounts and bindings | 512 returned; 1,024 account identity reads |

These are synthetic payload and operation counts, not production latency, RSS,
catalog sizes or a production root-cause finding.

## Catalog affected regression batch

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 PYTHONPATH=backend:backend/tests /workspace/scratch/81f2839b514c/courier-test-env/bin/python -B -m pytest --noconftest -p no:cacheprovider -q --tb=short -o asyncio_default_fixture_loop_scope=function backend/tests/test_operational_catalog_read_scope.py backend/tests/test_operational_balance_sources.py backend/tests/test_operational_balance_ad_funding.py backend/tests/test_operational_balance_workspace.py backend/tests/test_operational_context_snapshot_reader.py
```

Exit 0: **153 passed in 19.79s**.

An earlier attempt used only `PYTHONPATH=backend` and stopped during collection
with one `ModuleNotFoundError` for the existing sibling fixture
`test_operational_balance_supplier_workspace`. Adding `backend/tests` to the
import path resolved that environment issue. No dependency or source workaround
was used.

## Order-batch fairness red to green

The same command was used before and after the two order-engine changes:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 PYTHONPATH=backend:backend/tests /workspace/scratch/81f2839b514c/courier-test-env/bin/python -B -m pytest --noconftest -p no:cacheprovider -q --tb=short -o asyncio_default_fixture_loop_scope=function backend/tests/test_order_batch_cpu_fairness.py
```

Before the runtime changes, exit 1: **7 failed, 1 warning in 2.06s**. The real
mappers ran under a controlled thread-CPU clock, charging 6ms per complete unit
against the existing 5ms parser budget. Before checkpoints:

- A queued callback ran only after 101 repository hydration units.
- It ran only after 103 DTO attempts, or 100 invalid DTO attempts.
- In the real 205-order assembly board, callbacks ran after 100 repository and
  100 DTO units, at the existing batch boundary.
- Scheduled cancellation could not interrupt either synchronous mapping batch;
  the new cleanup expectations failed for that reason.

After the runtime changes, exit 0: **7 passed, 1 warning in 1.83s**. In the same
controlled fixtures the queued callback runs after one complete unit. Full
repository rows, complete serialized DTOs, duplicate/error handling, board
response, totals, pagination, group counts, Mongo read counts and stored data
remain equal. Cancellation stops later units. Repeated cancellation joins one
gated cursor-close operation before returning the original cancellation.

The checkpoint is after a whole repository cursor advance and hydration. Only
cancellation at that new CPU checkpoint triggers the owned close; cancellation
during the pre-existing `__anext__` await is deliberately unchanged. There is no
blanket finally that races a still-running Motor executor read.

## Order-batch affected regression batch and skips

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 PYTHONPATH=backend:backend/tests /workspace/scratch/81f2839b514c/courier-test-env/bin/python -B -m pytest --noconftest -p no:cacheprovider -q --tb=short -o asyncio_default_fixture_loop_scope=function backend/tests/test_order_batch_cpu_fairness.py backend/tests/test_order_engine_repository.py backend/tests/test_order_engine_service.py backend/tests/test_order_engine_mapper.py backend/tests/test_fulfillment_read_performance.py backend/tests/test_review_local_assembly.py
```

Exit 0: **122 passed, 41 skipped, 1 warning in 3.48s**.

The 41 skips are the real-Mongo local assembly integration tests. Their fixture
requires an explicit `MZ2_TEST_MONGO_URI` and reports
`MZ2_TEST_MONGO_URI required; no production fallback`. That isolated Mongo
environment was not configured here; these tests are not claimed as passing.
The warning is the existing `fitz` import deprecation. The passing tests include
the actual assembly-board read over an in-memory 205-order cohort.

`git diff --check` completed with exit 0 after both slices.

## Independent review

The separate reviewer inspected both actual diffs and their focused tests and
found no concrete blocker or material output mismatch. Review was read-only;
the reviewer did not execute tests.

Limits recorded by review:

- The catalog's linear matching claim applies to ordinary exact-string
  identities; legacy fallback intentionally remains unchanged.
- The 5ms budget is cooperative active-thread CPU accounting between whole
  orders, not a hard per-order or HTTP latency bound.
- Cursor-close ownership is tested with controlled fakes; this subagent did not
  run a real Motor cleanup integration test.
- Cancellation during an already pending cursor advance remains unchanged.

## Deferred work and production acceptance

No assembly route lifecycle wrapper, client polling assumption, driver timeout,
projection rewrite, truncation, admission policy or financial change was added.
The assembly board still validates the full selected cohort before returning
counts and a page. The exact `/assembly/orders` caller was not found in this
frontend checkout, so `poll_seconds: 15` is only a response hint and does not
prove overlapping client polling.

The coordinator explicitly stopped further runtime changes for this candidate.
Production availability is not established by these tests. Production
acceptance must independently verify the actual home, auth and health surfaces
and record any remaining service-wide pressure. The existing diagnostics hook
requires the already-configured internal diagnostics token; no token was
created, printed or retrieved by this subagent.
