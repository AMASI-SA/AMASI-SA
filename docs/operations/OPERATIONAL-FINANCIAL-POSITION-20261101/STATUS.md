# Parallel report and supplier-invoice checkpoint — 2026-11-01

Repository: AMASI-SA/AMASI-SA. Task branch: fix/operational-supplier-invoice-cutover-20261101. Base: 3bac875cfbcbc7f66849edae83c478dd194d9c12.

Previous remotely verified supplier checkpoint: `861a061fee353c329227fe9f4a0a0f604ea22ad4`. Draft task PR: https://github.com/AMASI-SA/AMASI-SA/pull/1675. The PR head is the canonical current checkpoint; this record travels with the source change, so its own commit is not embedded self-referentially.

## Completed supplier correction

Recognize newly approved supplier invoices independently of customer order age/current cost availability. Preserve old customer-order exclusion, immutable receipt identities, payment/return links and SAR invoice contract. Expose operational balances and invoice provenance through a read-only supplier workspace. Surface generic confirmed receipt conflicts in summary and detail.

Verification: full operational backend suite659 passed including an isolated local Mongo replica set before the diagnostic-only follow-up; final targeted42 passed after that follow-up; final frontend15 suites202 tests passed with default CRA mock reset behavior; diff check and clean-base patch application passed. Independent baseline-to-candidate payment and return compatibility checks passed. The temporary local Mongo has been shut down. No production database was accessed for writes.

An initial combined frontend command incorrectly forced resetMocks=false for an unchanged suite and produced4 failures. The corrected default configuration passed all202; no product behavior was altered to mask the harness mismatch.

## Implemented report candidate

The user approved the three visual sections and requested parallel implementation while another conversation publishes product-cost calculation changes. Build a read-only report from the operational authority; preserve separate actual/estimated balances and independent profit coverage. New clarification: per-order shipping fee and collection responsibility for cash versus merchant card/bank receipts must be visible.

The candidate now implements:

- Pure `backend/operational_financial_position.py`, using the existing saved operational report as the balance authority. Exact decimal arithmetic; currencies remain separate. Includes banks/POS, cash, suppliers, employees, external parties, ads, employee custody, carriers, drivers, payment providers, and residual recorded obligations. Unreceived, received-uninvoiced, undelivered shipping, expected collection and provider-fee estimates remain separate from actual balances.
- Read-only `GET /operational-balances/financial-position` in `backend/operational_balance_routes.py`. Existing reports/mobile grants and real owner scope apply. Native accounting activation suppresses competing current balances. The optional cost reader uses the saved engine timestamp and only the three established source-boundary failures become explicit incomplete estimates.
- `backend/operational_financial_order_labels.py`: one bounded owner-and-canonical-ID lookup, narrow projection and no financial changes. Unknown public numbers remain null and are not guessed from internal identifiers.
- RTL report page, presentation/export helpers and CSS in `frontend/src/pages/operational/`, connected through `App.js`, `MezanV2NavigationShell.jsx` and `api.js`. Currency selection, manual read refresh, per-order/party/payment filters, report-only print and Excel export. Tenant/role changes and refresh errors clear stale amounts.
- Backend tests for projection, route and order-label boundaries; three frontend report suites. The operational CI command now explicitly includes `test_operational_supplier_invoice_cutover.py`, which was outside its prior filename glob.
- `frontend/testing/financial-position-preview/` is a synthetic-only local browser harness. It builds the real component and real adapter fixture into ignored `.evidence/financial-preview-site`; no production environment, data or application build is used.

## Verification of this candidate

All production code was frozen before these integrated checks:

- Full operational backend plus supplier cutover and shipping-calculator preservation: **734 passed, 18 subtests passed, zero skips/failures/errors**, exit 0. Isolated Mongo 8.0.12 replica set on `127.0.0.1:27305`; shutdown completed after the run. Evidence: `.evidence/financial-integrated-backend.xml` and `.log`.
- Combined frontend operational, supplier and navigation tests with default CRA mock reset behavior: **20 suites / 242 tests passed**, zero failures or skips, exit 0. Evidence: `.evidence/financial-integrated-frontend.json` and `.log`.
- A subsequent additional test-only ExcelJS bytes roundtrip was added. The three focused report suites then passed **26/26**, including actual XLSX serialization and re-open, exact large money strings, Arabic sheets, no formulas, currency exclusion and filtered order inclusion. No production code changed after the combined frontend run.
- Independent review reran all 58 new backend report/label/route tests and reviewed the authorization, date, currency, allocation and duplicate-counting boundaries.
- Browser verification of the real synthetic page: desktop table layout, SAR/USD isolation, pending bank-transfer filtering and all four payment scenarios were inspected. The displayed preview has synthetic names and amounts only. No application error appeared in captured browser logs.
- Browser download completion was not proven: two download-event waits timed out without a page error. The internal downloads URL was blocked by the browser URL policy and was not bypassed. The real ExcelJS serialization/reload test verifies workbook contents, not browser download completion.
- A 390px-frame preview harness exists for responsive QA, but its browser verification remains incomplete after an initial stale preview-server response and the error page's unsupported protocol block. Responsive CSS and report-only print behavior are covered by source/component checks; no physical-device or printed-PDF result is claimed.
- `git diff --check` passed. No governed production build/rehearsal was performed in this stage.

Backend command:

```bash
OPERATIONAL_TEST_MONGO_URI='mongodb://127.0.0.1:27305/?replicaSet=operationalprojection' MZ2_TEST_MONGO_URI='mongodb://127.0.0.1:27305/?replicaSet=operationalprojection' PYTHONPATH=backend:backend/tests .venv/bin/python -B -m pytest --noconftest -q backend/tests/test_operational_balance*.py backend/tests/test_operational_supplier_invoice_cutover.py backend/tests/test_dashboard_shipping_ssot.py backend/tests/test_mz2_shipping_calculator.py --tb=short --junitxml=.evidence/financial-integrated-backend.xml
```

Frontend combined command, from `frontend`:

```bash
CI=true node node_modules/react-scripts/bin/react-scripts.js test --watch=false --runInBand --testMatch '**/MezanSuppliersV2.test.jsx' '**/OperationalSupplierAccount.test.jsx' '**/mezanSuppliersV2.test.js' '**/SupplierPaymentWorkspace.test.jsx' '**/src/pages/operational/**/*.test.{js,jsx,ts,tsx}' '**/MezanV2NavigationShell.test.jsx' '**/MezanV2NavigationShell.employees.test.jsx' --json --outputFile=../.evidence/financial-integrated-frontend.json
```

## Prerequisite and CI evidence

Current prerequisite: PR1674, head667d94a3b01b7a59f58b753dfbf7d52c80764e02, depends on PR1673, heada767d9123a00ea8b03519b77b8f7ef4177070d24. Both were draft/open and unmerged on the latest read. Remote production base remained3bac875c. Live identity remained sourceb6cf6d7e1e2239a99dacae926941ff1b1aed8736 and releaserg5-9798b8ac1749a5c77c871f58c3f816157ccf4e19ef611bedf422edd5fb02ab96; no evidence yet of the prerequisite being published.

CONTRACT.md defines the report API and the exact write boundaries. The report does not invent profit from balances. Its incomplete source and period coverage must remain visible until verified.

At supplier checkpoint `861a061fee353c329227fe9f4a0a0f604ea22ad4`, 12 of 13 returned PR workflows succeeded, including Operational Balance Web and release-readiness checks. Security Gate failed on inherited dependency audits: backend `pyasn1` 0.6.4 and frontend `follow-redirects`/`minimatch`. Exact pending source remediation was independently verified in PR1673 and inherited byte-for-byte by PR1674: pyasn1 0.6.5 in both backend requirement files; follow-redirects 1.16.2, minimatch 11.0.3 and glob 14.0.3 in package resolutions and applicable Yarn-lock entries. These fixes are not in this branch yet. Fresh combined-head CI, including Security Gate and real dependency compatibility tests, remains required after integration. A prior green workflow is not proof for a later source commit.

## Remaining financial gaps and exact delivery behavior

1. Existing source and reducer cutoffs exclude orders created before operational startup even when delivery occurs afterward. Neither this read-only report nor the pending cost-estimate PR repairs that recognition path. Reconcile actual post-start delivery events against opening evidence before adding any prospective recognition; never replay old sales, bank or provider effects indiscriminately.
2. Driver cash creates driver collection responsibility; carrier COD creates carrier receivables. Shipping payables are independent. Do not sum application earnings and the central shipping obligation for the same delivery.
3. Driver bank/card review approval belongs to native accounting and does not itself create an operational bank/POS movement. Show pending/confirmed evidence and unavailable receiving-account linkage; never fabricate operational cash or driver custody. Merchant collection displayed in details is informational and is not added again to balances.
4. Delivery endpoints do not synchronously refresh the operational snapshot. The worker runs active owners sequentially and waits 30 seconds after each completed cycle. Successful delivery evidence, canonical synchronization, worker processing and the next UI read all affect visibility. This is a code-path finding, not a measured production latency guarantee. The new report refresh button only reads the latest saved snapshot.
5. No complete pre-start expected-COD source exists yet. Supported current estimates can be shown, but full forecast and projected monetary-position totals remain null. Cost buckets must remain disjoint; the prerequisite's pre-start post-cutover invoice reconciliation issue cannot be hidden without matching receipt-component proof.
6. Complete profit evidence for one common sales period/cohort is not available. Current balances and supplier invoice approval totals are not profit or cohort COGS. Profit rows and final profit remain null with explanations until sales, costs, consumed advertising, salaries, operating/financing and applicable tax coverage are established.

## Publication and continuity

Production changed: no. Source merge: no. Release intent/build/lease/deployment: not started. Publication is deferred until after the prerequisite release and combined verification.

Issue1006 is recorded as full (2500 comments) by existing repository operational status documents. Keep this task document and its eventual task PR as the continuation record. Do not publish or clear another task's lease.

Next safe action: fetch and verify the current task PR head and current production/prerequisite refs; establish prerequisite publication from release identity, then integrate its exact source into an isolated candidate. Resolve supplier/cost overlap and the financial gaps above, complete browser export/compact/print verification, rerun combined tests and CI, and only then enter the separate v5 source/intent release workflow. Until then keep PR1675 draft and unmerged. A successful source checkpoint is not production acceptance.
