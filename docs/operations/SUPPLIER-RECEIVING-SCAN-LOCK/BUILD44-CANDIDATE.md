# Build44 backend integration candidate

This candidate combines the fresh reviewed Production source with the receiving
fix only. **PR 1321 is excluded. PR 1320 is not included.** The historical
`JOINT-INTEGRATION.md` and `compatibility/` artifacts inherited from PR 1323
describe an earlier, separate experiment; their combined source is not this
candidate and must not be used as its release input.

## Provenance

| Role | Commit |
| --- | --- |
| Production input | `b41cd9cc36a738a0528dd89ddc7349b31db2a361` |
| Receiving input, PR 1323 | `15029b7623e25f6a1aa0749e51bfd3e384686764` |
| Local integration source HEAD | `872fbf878a249d7ca63a40c497dc13d35a29a950` |
| Local integration source TREE | `8da44af9839c08d0e06a48919b926fa822b6a3d2` |

Dedicated branch: `codex/build44-supplier-backend-integration`.
Dedicated worktree:
`C:/Users/amasi/.codex/worktrees/build44-supplier-backend-integration`.
The merge completed without conflicts; no accounting changes were necessary.
The original receiving, performance, ready/shipping and Production branches
were preserved.

Source protection checks returned the following results:

- Receiving routes and attempt implementation are byte-identical to PR 1323
  (`git diff --exit-code 15029b7 HEAD --` those two files: exit 0).
- Native accounting, preparation operations, assembly delivery, image/PDF,
  server and release-intent paths are identical to the Production input
  (`git diff --exit-code b41cd9c HEAD --` those paths: exit 0).
- Neither PR 1321 `c036f8a969ef4e2835e2ebcf623305d89ca22af6` nor PR 1320
  `f810ae8f65080afc99d56d751d7b9eb164c29ee0` is an ancestor of this candidate
  (`git merge-base --is-ancestor`: exit 1 for each, as required).

## Verification

All database operations use unique disposable databases on the isolated local
replica set; the application server, production systems and provider APIs are
not started or modified.

```powershell
$env:PYTHONPATH='backend;backend/tests'
$env:MZ2_TEST_MONGO_URI='mongodb://127.0.0.1:27129/?replicaSet=supplierScan'
$env:BUILD20_SCAN_TEST_MONGO_URL=$env:MZ2_TEST_MONGO_URI
$env:BUILD20_INVOICE_TEST_MONGO_URL=$env:MZ2_TEST_MONGO_URI
C:/Users/amasi/build37-test-venv/Scripts/python.exe -m pytest backend/tests/test_supplier_scan_lock.py backend/tests/test_supplier_scan_approval.py backend/tests/test_supplier_scan_recovery.py backend/tests/test_supplier_scan_retention.py backend/tests/test_supplier_receiving.py backend/tests/test_supplier_invoice_financial_integrity.py backend/tests/test_supplier_multi_supplier_lifecycle.py backend/tests/test_supplier_invoice_live_cost_session.py backend/tests/test_supplier_invoice_service_eligibility.py backend/tests/test_supplier_native_invoice_v2.py backend/tests/test_supplier_display_financial_boundary.py backend/tests/test_supplier_refresh_wrapper.py -q -ra --junitxml=docs/operations/SUPPLIER-RECEIVING-SCAN-LOCK/build44-candidate/backend-tests.xml
```

Result at the integration source identity above: **150 passed, zero skipped,
zero failed**, exit 0, in **138.87 seconds**. JUnit and the complete console
output are retained in `build44-candidate/backend-tests.xml` and
`build44-candidate/backend-tests.log`. Warnings concern existing-style FastAPI
`on_event` deprecations; they are not test failures. Financial
and accounting assertions are unchanged. The dedicated corpus covers scan
fencing/cancellation/timeout, exact recovery/retention, financial approval and
concurrent replay, journal rollback, service eligibility and native accounting.

Any subsequent documentation or verification-script commit must be recorded
separately from this tested production-code identity. The coordinator owns the
final combined candidate SHA and cross-repository script results.

## Handoff boundary

The added `scripts/verify_build44_supplier_candidate.py` / `.cjs` check runs
the Android candidate's actual scanner gate, durable queue and repository
against local real HTTP receiving routes and a unique isolated Mongo database.
It passed: 50 captures, lost saved-piece-26 response, offline GET, queue
reconstruction, GET-only recovery, 50 unique POST IDs/receipts, then one
121250-halala invoice. Repeated approval preserves the same invoice, two
ledger legs and two audit rows; all original request IDs remain recoverable.
See `build44-candidate/cross-repository-50.log`. Android runtime input is
`701401aca810b797c8e53d805215e24652d543e8`, tree
`6cc5b4c43b46785e64b9d23de952a4163c11b28e`. No accounting implementation changed.

These new test scripts and this report are the only changes after the 150-test
source above. PR metadata records the final descendant HEAD/TREE. Android
Release Guard and evidence-capacity commissioning remain blocked; this backend
candidate alone does not authorize any build or release. Physical Samsung
acceptance is pending.

The root coordinator owns Android integration, the current cross-repository
50-piece HTTP/replica check, remote checkpoint and Draft PR creation. This
candidate does not authorize merge, deploy, restart, production writes, release
intent changes, APK or OTA. Production changed: **no**.
