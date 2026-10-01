# Final integration verification — release BLOCKED

For the subsequent existing-writer wiring request, use [WIRING-VERIFICATION.md](WIRING-VERIFICATION.md) and its final checkpoint addendum. The results below retain their historical source identity. Neither this earlier checkpoint nor focused follow-up success establishes Release Readiness.

Source checkpoint: `fb688621c99cdf413604f514beffa274e58d73ea`; source tree: `5e2dc70ef0b81b7efabf1ffc6b3132cc7ce2a1aa`. Later evidence-only commits do not claim their own CI green from this source run. Exact final checkpoint identity is recorded in Issue1006, avoiding a self-referential commit hash.

`CHANGED-FILES.tsv` inventories added/deleted line counts from the production base to the source checkpoint (293 paths, before this evidence-only addendum). Git diff of the final checkpoint additionally includes this final evidence directory. There were no unresolved merge conflicts; all eleven source PRs listed in STATUS.json were already incorporated before this owner's repairs.

All local database runs use loopback Mongo8.0.12 and unique synthetic databases. Local Python3.13 differs from CI's pinned3.12; local Node22.23.2/Yarn1.22.22 match the requested source toolchain. No Production or Preview was connected.

## Tests and build

| Check | Result | Evidence |
| --- | --- | --- |
| Shipping + driver review + bank port + P02 + V2 ledger + onboarding/domains + native reports |193 passed,6 subtests, zero failures/skips; exit0 | `shipping-accounting.log`, `shipping-accounting.xml` |
| Supplier workflow selection after package-import repair |142 passed,82.37s; exit0 | `supplier-ci-selection-final.log` |
| G47 all six `test_g47*.py` modules + stock component consumption |126 passed,64 subtests, zero skips/failures,305.45s; exit0 | `g47-verified.log`, `g47-verified.xml` |
| Onboarding UI, session controller and transport after restoring six API exports |108 passed in10 suites; exit0 | `onboarding-final.log` |
| Real browser/HTTP/Mongo fixture |12 scenarios passed; exit0 | committed `evidence/browser-summary.json` and screenshots |
| Source-only Vite build |PASS,10.77s; exit0 | `source-build-final.log` |
| One-writer transition contract |1 passed,1.65s; exit0 | `one-writer-guard-final.log` |
| Missing-writer reproduction |3 existing acceptance tests fail423 at initial sale,3.18s; exit1 | `writer-blockers-focused.log`; source and limitations in WRITER-BLOCKERS.md |
| Full frontend |FAIL; final rerun details in STATUS.json/FRONTEND-REGRESSION.md | `frontend-full-final.log` |
| Full backend |NOT_PASS; accounting acceptance CI fails; downstream skipped steps are not passes. No claim of an unrestricted all-backend run. | `CI-MATRIX.json` and WRITER-BLOCKERS.md |
| Governed release build/readiness |NOT_PASS; no new release intent. Linux-only governed toolchain unavailable on local Windows; source build is not an authorized deployment artifact. | CI readiness failure retained |

Commands run from repository root (PowerShell; use `.venv/Scripts/python.exe` for Python):

```powershell
$env:PYTHONPATH='backend;backend/tests'
$env:PYTHON_DOTENV_DISABLED='1'
$env:MZ2_TEST_MONGO_URI='mongodb://127.0.0.1:27128/?replicaSet=mz2test'
$env:MZ2_TEST_STANDALONE_URI='mongodb://127.0.0.1:27129/?directConnection=true'
python -m pytest --noconftest -q --tb=short backend/tests/test_mz2_shipping_native.py backend/tests/test_mz2_driver_payment_review.py backend/tests/test_mz2_shipping_bank_port.py backend/tests/test_mz2_shipping_p02.py backend/tests/test_accounting_ledger_v2.py backend/tests/test_accounting_onboarding.py backend/tests/test_accounting_onboarding_domains.py backend/tests/test_track_g_native_reports.py
$tests=@(Get-ChildItem backend/tests/test_g47*.py | ForEach-Object FullName)
python -m pytest --noconftest -q --tb=short @tests backend/tests/test_stock_component_consumption.py
$env:PYTHONPATH='backend'
python -m pytest -q --tb=short backend/tests/test_mz2_supplier_payments_v2.py backend/tests/test_mz2_supplier_financial_port_integration.py backend/tests/test_mz2_supplier_identity.py backend/tests/test_accounting_ledger_v2.py backend/tests/test_mezan_supplier_management.py backend/tests/test_accounting_opening_categories.py backend/tests/test_accounting_onboarding.py
npx --yes --package=node@22.23.2 --package=yarn@1.22.22 yarn --cwd frontend test --watchAll=false --runInBand
```

Source-only build used Node22.23.2 directly with `frontend/node_modules/vite/bin/vite.js build` from `frontend`. It deliberately does not claim the governed release contract was satisfied. Browser fixture commands/configuration are in `scripts/testing/mz2_onboarding_ab/README.md`; it requires a local compiled fixture and localhost HTTP origin. The run used Edge headless.

## Failure history and evidence boundaries

- Supplier identity sink initially failed its required native identity test; restored the delivered guard, then supplier/ledger40 passed and broader supplier/accounting141 passed. No inactive-identity allowance beyond verified reversal was added.
- The first full G47 rerun used the wrong standalone environment-variable name:123 passed,1 failed,2 skipped. Correcting the command yielded126 passed/zero skipped in the final combined run; no production code changed to hide those failures.
- Browser initially failed missing provider policy, then unselected policy. The synthetic fixture now seeds a native owner-scoped effective policy and the browser explicitly selects it using delivered UI. This exposed missing six transport exports; restoring them from Track G made the12 scenarios pass. No server guard was removed.
- An exploratory direct Jest command lacked the repository's React test transform and executed zero tests. It is not counted as a feature failure or successful verification; repository `yarn test` supplied the actual108-test result.
- Source checkpoint257ef6 had a new supplier-test import error in CI (`PYTHONPATH=backend`); fb6886 fixes only that import. The whole142-test supplier selection then passed locally and its exact-checkpoint CI passed.
- Salla P0 at257ef6 failed the capability-TTL test (0 simulated POST vs1). Its50ms wall-clock expiry and direct source/test paths are unchanged from the production base. This is evidence of timing sensitivity, not a proven root cause. The next fb6886 Salla P0 workflow passed without changing it.
- Exact fb6886 A+B CI has254 passed/3 failed/237 subtests; all three failures are the already documented initial-sale423. Its source build and preflight jobs passed. Supplier Native has203 passed/1 failed, Track G has158 passed/1 failed, and Accounting Module's18-test workflow stops on2 failures at the same initial sale. Later skipped accounting steps are not counted as passes.
- Release Readiness fails its source/intent classification: reviewed intent does not match `EmployeesV2Management.jsx`, followed by `source A must change governed source without changing intent`. Backend preflight and release-contract unit tests pass; retained A/B build, verification and host rehearsal are skipped. No release intent, branch history or release guard was changed to bypass this boundary.
- Driver-proof tests with substituted proof do not close the production proof adapter. No payroll-native capability is inferred from Legacy-active payroll fixtures. Source and runtime proof are separated in the audit.

## Acceptance and next safe action

All source tracks are integrated; no B/C1/D/G merge remains. Existing native port repairs are complete within the authorized scope. [SSOT audit](MZ2_ONLY_SSOT_AUDIT.md) is NOT_PASS, complete business UAT is NOT_PASS, and Smoke B remains BLOCKED_BY_ENVIRONMENT. Keep PR1222 draft; do not merge or release.

Review [writer blockers](WRITER-BLOCKERS.md), [frontend regression](FRONTEND-REGRESSION.md) and CI snapshot. Future native-writer implementation requires separate explicit scope. Preserve the protected Legacy barrier and exact V2 contracts. Production writes0; Deploy/Opening Post/Activation=NO.
