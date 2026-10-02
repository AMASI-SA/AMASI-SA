# Exact-source CI failure evidence

Source `3bf348a236de0c04c6bea40926f19fe2111ec8e0`, tree `d7c79519a7abfbc20678ece621ba465313ce5602`. These are fresh job-log reads, not inferred from older CI. Full matrix is in WIRING-CI-MATRIX.json. Skipped downstream checks are not PASS. No new release intent or accounting fallback was introduced.

## MZ2 Track G SSOT contracts

[Run and failed job](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36813526517/job/110213532121). 1 failed / 158 passed; initial sale423 in report-isolation prerequisite.

```text
2026-10-01T04:06:54.4421476Z E   AssertionError: 423 != 200 : {"detail":{"code":"accounting_legacy_writer_disabled","message":"الكاتب القديم مقفل بعد تفعيل ميزان 2"}}
2026-10-01T04:06:54.4448583Z FAILED backend/tests/test_mz2_report_isolation.py::ReportIsolationTests::test_actual_refund_month_end_partial_and_final_payment_with_legacy_sentinels - AssertionError: 423 != 200 : {"detail":{"code":"accounting_legacy_writer_disabled","message":"الكاتب القديم مقفل بعد تفعيل ميزان 2"}}
2026-10-01T04:06:54.4449443Z 1 failed, 158 passed, 9 warnings in 27.77s
2026-10-01T04:06:54.8931056Z ##[error]Process completed with exit code 1.
```

## MZ2 Supplier Native Invoice V2

[Run and failed job](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36813526484/job/110213531550). 1 failed / 203 passed / 7 subtests; initial sale423 in closed-period refund prerequisite.

```text
2026-10-01T04:07:14.2100503Z E   AssertionError: 423 != 200 : {"detail":{"code":"accounting_legacy_writer_disabled","message":"الكاتب القديم مقفل بعد تفعيل ميزان 2"}}
2026-10-01T04:07:14.2102772Z FAILED backend/tests/test_mz2_closed_periods.py::ClosedPeriodTests::test_closed_entitlement_and_payment_no_partial_or_automatic_redating - AssertionError: 423 != 200 : {"detail":{"code":"accounting_legacy_writer_disabled","message":"الكاتب القديم مقفل بعد تفعيل ميزان 2"}}
2026-10-01T04:07:14.2104799Z 1 failed, 203 passed, 7 subtests passed in 49.83s
2026-10-01T04:07:14.7714699Z ##[error]Process completed with exit code 1.
```

## Mezan Release Readiness

[Run and failed job](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36813526617/job/110213532798). Source/reviewed-intent classification fails; retained release builds and host rehearsal skipped.

```text
2026-10-01T04:07:45.4526258Z # Subtest: explicit reviewed-intent mode ignores GitHub merge identity
2026-10-01T04:07:45.4528064Z ok 15 - explicit reviewed-intent mode ignores GitHub merge identity
2026-10-01T04:07:45.4926624Z     raise SystemExit("reviewed intent is not schema 2")
2026-10-01T04:07:45.4979390Z # prove that J differs from its governed source P by intent only.
2026-10-01T04:07:45.6832769Z scripts.emergent_deployment_adapter.DeploymentAdapterError: frontend source differs from reviewed intent: src/pages/EmployeesV2Management.jsx
2026-10-01T04:07:45.8584593Z scripts.emergent_deployment_adapter.DeploymentAdapterError: source A must change governed source without changing intent
2026-10-01T04:07:45.8758603Z ##[error]Process completed with exit code 1.
```

## MZ2 Accounting Module

[Run and failed job](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36813526628/job/110213532750). 18 tests / 2 failures at initial sale423; steps8–25 skipped. Separate frontend/backend contract jobs pass.

```text
2026-10-01T04:07:54.3778700Z Ran 11 tests in 0.023s
2026-10-01T04:07:58.3675876Z FAIL: test_new_sale_to_existing_settlement_service_and_balance_guard (test_mz2_receivable_workflow.WorkflowTests.test_new_sale_to_existing_settlement_service_and_balance_guard)
2026-10-01T04:07:58.3690842Z AssertionError: 423 != 200 : {"detail":{"code":"accounting_legacy_writer_disabled","message":"الكاتب القديم مقفل بعد تفعيل ميزان 2"}}
2026-10-01T04:07:58.3692421Z FAIL: test_partial_full_refund_uses_original_rate (test_mz2_receivable_workflow.WorkflowTests.test_partial_full_refund_uses_original_rate)
2026-10-01T04:07:58.3700053Z AssertionError: 423 != 200 : {"detail":{"code":"accounting_legacy_writer_disabled","message":"الكاتب القديم مقفل بعد تفعيل ميزان 2"}}
2026-10-01T04:07:58.3701050Z Ran 18 tests in 3.186s
2026-10-01T04:07:58.3701280Z FAILED (failures=2)
2026-10-01T04:07:58.8802268Z ##[error]Process completed with exit code 1.
```

## MZ2 A+B source integration

[Run and failed job](https://github.com/AMASI-SA/AMASI-SA/actions/runs/36813526625/job/110213532660). 3 failed / 254 passed / 237 subtests; all initial sale423. Later connected browser steps skipped; source-only clean-clone build and preflight pass.

```text
2026-10-01T04:09:54.5347058Z E   AssertionError: 423 != 200 : {"detail":{"code":"accounting_legacy_writer_disabled","message":"الكاتب القديم مقفل بعد تفعيل ميزان 2"}}
2026-10-01T04:09:54.5351713Z E   AssertionError: 423 != 200 : {"detail":{"code":"accounting_legacy_writer_disabled","message":"الكاتب القديم مقفل بعد تفعيل ميزان 2"}}
2026-10-01T04:09:54.5355356Z E   AssertionError: 423 != 200 : {"detail":{"code":"accounting_legacy_writer_disabled","message":"الكاتب القديم مقفل بعد تفعيل ميزان 2"}}
2026-10-01T04:09:54.5380673Z FAILED backend/tests/test_mz2_report_isolation.py::ReportIsolationTests::test_actual_refund_month_end_partial_and_final_payment_with_legacy_sentinels - AssertionError: 423 != 200 : {"detail":{"code":"accounting_legacy_writer_disabled","message":"الكاتب القديم مقفل بعد تفعيل ميزان 2"}}
2026-10-01T04:09:54.5382768Z FAILED backend/tests/test_mz2_receivable_workflow.py::WorkflowTests::test_new_sale_to_existing_settlement_service_and_balance_guard - AssertionError: 423 != 200 : {"detail":{"code":"accounting_legacy_writer_disabled","message":"الكاتب القديم مقفل بعد تفعيل ميزان 2"}}
2026-10-01T04:09:54.5384725Z FAILED backend/tests/test_mz2_receivable_workflow.py::WorkflowTests::test_partial_full_refund_uses_original_rate - AssertionError: 423 != 200 : {"detail":{"code":"accounting_legacy_writer_disabled","message":"الكاتب القديم مقفل بعد تفعيل ميزان 2"}}
2026-10-01T04:09:54.5385783Z 3 failed, 254 passed, 6 warnings, 237 subtests passed in 74.10s (0:01:14)
2026-10-01T04:09:56.0329185Z ##[error]Process completed with exit code 1.
```


