# Isolated compatibility verification: PRs 1323, 1320 and 1321

Verification date: 2026-10-11 (Asia/Riyadh). No original PR branch was merged,
rebased or changed by the integration experiment. No deployment, restart,
production write, APK, or OTA occurred. The compatibility worktree is
`C:/Users/amasi/.codex/worktrees/receiving-joint-compat-20261011` on the local
branch `codex/receiving-joint-compat-20261011`.

## Exact integrated source

| Input | Full commit |
| --- | --- |
| Receiving fix, PR 1323 | `f8009739c9ac175c1d02439a330a7376eddd0aff` |
| Latest fetched invoice performance, PR 1320 | `f810ae8f65080afc99d56d751d7b9eb164c29ee0` |
| Requested ready/shipping source, PR 1321 | `c036f8a969ef4e2835e2ebcf623305d89ca22af6` |
| Integration HEAD | `d7e03954854a1d066306dfd7c307cc7f085ea90c` |
| Integration TREE | `6763e02e5b68658e7ea6d97b665f84a279cdd563` |

Both integration-only merges completed without conflicts. Source checks also
returned exit 0 for exact equality of invoice image/PDF files with PR 1320 and
assembly delivery/preparation/instruction files with PR 1321. The new receiving
attempt module remained byte-identical to PR 1323. No financial or accounting
test was edited to obtain these results; the existing PR 1320 financial-boundary
normalization and all its original assertions ran as supplied by that PR.

## Runtime evidence

All database tests used unique disposable databases on
`mongodb://127.0.0.1:27129/?replicaSet=supplierScan`. Synthetic provider doubles
were used; no production/provider writes were issued.

The joint runtime corpus passed **245 tests, zero skipped, zero failed**, exit 0,
in **244.92 seconds**. It exercised scan cancellation/fencing, lost responses,
sequential 10/50 pieces, actual scan-to-financial approval, native invoice
concurrent close/replay, rollback after journal insertion, legacy-write
exclusion, invoice/ledger rollback, image security/cache/deadlines, initialization
indexes, real assembly outbox delivery, material-evidence fencing and batched
instruction lookup on the integrated source.

Run from the integration worktree in PowerShell:

```powershell
$env:PYTHONPATH='backend;backend/tests'
$env:MZ2_TEST_MONGO_URI='mongodb://127.0.0.1:27129/?replicaSet=supplierScan'
$env:BUILD20_SCAN_TEST_MONGO_URL=$env:MZ2_TEST_MONGO_URI
$env:BUILD20_INVOICE_TEST_MONGO_URL=$env:MZ2_TEST_MONGO_URI
C:/Users/amasi/build37-test-venv/Scripts/python.exe -m pytest backend/tests/test_supplier_scan_lock.py backend/tests/test_supplier_scan_approval.py backend/tests/test_supplier_scan_recovery.py backend/tests/test_supplier_invoice_images.py backend/tests/test_supplier_invoice_save_profile.py backend/tests/test_supplier_native_invoice_v2.py backend/tests/test_supplier_invoice_financial_integrity.py backend/tests/test_supplier_display_financial_boundary.py backend/tests/test_supplier_invoice_display_readonly.py backend/tests/test_supplier_receiving.py backend/tests/test_assembly_completion_delivery.py backend/tests/test_assembly_search_instruction_batch.py backend/tests/test_preparation_piece_operations.py backend/tests/test_supplier_refresh_wrapper.py -q -ra --junitxml=docs/operations/SUPPLIER-RECEIVING-SCAN-LOCK/compatibility/joint-runtime.xml
```

Evidence directory within that worktree:
`docs/operations/SUPPLIER-RECEIVING-SCAN-LOCK/compatibility/`.
`joint-runtime.xml` and `joint-runtime.log` hold the complete joint result.
Warnings concern deprecated FastAPI `on_event` hooks; actual lifecycle execution
is covered, and warnings are not represented as failures.

The coordinator separately verified the current mobile queue/repository against
this integrated backend over local HTTP and the real replica set: 50 pieces,
lost-response recovery and queue reconstruction. This is cross-repository runtime
evidence, not a claim of Android hardware/app-process restart verification.

## Additional helper failure retained, not weakened

The explicit, non-pytest-collected helper
`python backend/tests/test_preparation_piece_operations.py` passed four real-Mongo
checks (physical exactly-once, consume rollback, ready rollback, virtual/operational
handling) then failed `supplier_v2_real_close_rollback`: it expects HTTP 423
`accounting_legacy_writer_disabled`, but receives HTTP 403
`accounting_actor_unavailable`.

The identical failure was reproduced in a separate, unchanged detached worktree
at the exact PR 1321 commit above, with the same command and isolated Mongo URI.
Thus it is a pre-existing helper limitation, not a new integration regression.
Its fixture sets `v2_active`, omits the persisted actor required by native posting,
and expects the legacy writer path even though close selects native posting.
No actor check, financial assertion, production code, or test was softened.
Logs: `overlap-runtime.log` and `overlap-exact-1321.log` (both exit 1).
The collected, current native HTTP rollback and exactly-once tests passed in
the 245-test corpus; the obsolete helper itself is **not** marked passed.

## Performance retained on the integrated source

Performance scripts were rerun after the joint pytest corpus completed. Results
are local synthetic measurements, not production or device latency guarantees.

| Measurement | Before | Integrated source |
| --- | ---: | ---: |
| 50-piece assembly search median, seven warm samples | 123.177 ms / 50 instruction queries | 10.281 ms / 1 query |
| Last ready piece with synthetic 20-second provider delay | 20.165 s / 1 provider call in request | 0.127 s / 0 provider calls in request |
| PDF, 50 distinct images, synthetic 20-ms CDN delay | 1241.93 ms | 471.99 ms; warm reprint 188.07 ms |
| PDF, 50 repetitions of one image | 1346.57 ms / 50 fetches | 176.25 ms / 1 fetch; warm reprint 148.00 ms / 0 fetches |
| Save sequence, index calls including replay | 36 | 12 |
| 50-piece close, one sampled sequence | 320.663 ms | 274.617 ms |

Single-piece close was 219.745 ms before versus 234.184 ms integrated in these
single samples. Do not infer an across-the-board invoice-save latency win from
these numbers. The repeatable structural result is elimination of repeated
index initialization; totals, integrity and one-invoice replay assertions passed.
The save-profile rerun passed **6 tests, zero skipped**, exit 0, in 25.15 seconds.

```powershell
C:/Users/amasi/build37-test-venv/Scripts/python.exe scripts/benchmark_supplier_invoice_pdf.py
C:/Users/amasi/build37-test-venv/Scripts/python.exe backend/tests/benchmark_assembly_search_instructions.py
C:/Users/amasi/build37-test-venv/Scripts/python.exe backend/tests/benchmark_assembly_delivery.py docs/operations/SUPPLIER-RECEIVING-SCAN-LOCK/compatibility/assembly-delivery.json
C:/Users/amasi/build37-test-venv/Scripts/python.exe -m pytest backend/tests/test_supplier_invoice_save_profile.py -q -s --junitxml=docs/operations/SUPPLIER-RECEIVING-SCAN-LOCK/compatibility/save-profile.xml
```

All four commands exited 0. Raw artifacts: `pdf-benchmark.json`,
`assembly-search.log`, `assembly-delivery.json`, `save-profile.log` and
`save-profile.xml` in the evidence directory.

## Retention

The journal currently retains all terminal and pending evidence indefinitely;
`expires_at` expires ownership, never evidence. There is no TTL or deletion
routine. The new retention regression aged confirmed/rejected rows 400 days,
ran startup and reconciliation, verified installed indexes have no TTL,
recovered/replayed the original identity, rejected its use for another piece,
and proved one receipt event and one reserved piece remain. Result: **1 passed,
zero skipped**, exit 0, in 4.67 seconds (`retention.xml`, `retention.log`).

See [RETENTION.md](RETENTION.md) for the explicit policy and unbounded-storage
tradeoff. Any future purge/archive requires a separate evidence-preserving
design; this change supplies no cleanup that could remove deduplication proof.

This report establishes tested source compatibility at the identities above.
It does not authorize merging or deploying any PR, and it does not declare the
entire Android operational acceptance FINAL PASS.
