# Local backend gate results

Exact production base used for failure comparison: `1d9d65d8576d5bc852f52262b84af150356b9385`. All runs are synthetic / loopback only. No live DB writes. See environment-notes.md for platform/dependency differences.

| Workflow | Local result |
|---|---|
| fulfillment-v2 | 316 passed, 1 warning in 26.43s |
| g47-focused-integration | 125 passed, 5 warnings, 64 subtests passed in 200.63s (0:03:20) |
| prod-preparation-piece-operations | 146 passed in 12.86s |
| supplier-invoice-service-policy | 108 passed in 5.16s |
| auth-passkey-security | 1 failed, 14 passed in 1.94s |
| security-gate | 1 failed, 291 passed, 223 warnings, 29 subtests passed in 52.41s |
| build20-unified-qoyod | 1 failed, 66 passed in 3.87s |
| qoyod-rounding-lrm-tests | 8 failed, 69 passed, 2 warnings in 4.16s |
| qoyod-payment-freshness | 141 passed in 10.10s |
| qoyod-memory-bounds | 86 passed, 2 warnings in 8.49s |
| qoyod-credential-rotation-acceptance | 132 passed, 44 warnings in 23.24s |

Additional workflow unittest steps: fulfillment-production-tracking: 35 passed. security-toolchain-unittest: 26 run, 20 errors from Windows missing os.geteuid (6 passed). Identical toolchain error count reproduced on production base.

## Failures reproduced on unchanged base

Auth Passkey / Security: test_frontend_rebind_uses_authentication_not_duplicate_registration uses a forward-slash string replacement against a Windows backslash __file__, so it reads passkey_security.py instead of Login.jsx. Both candidate and exact production base produce the same failure.

Qoyod backlog: test_exact_invoice_with_failed_payment_stays_actionable_without_dup returns retry_allowed=False. Candidate and base: 1 failed, 66 passed.

Qoyod rounding group: eight pending eligibility/dedupe assertions fail identically on candidate and base. Candidate and base: 8 failed, 69 passed. Full failure identities follow.
- test_cross_tab_duplicate_prevented
- test_newest_trace_wins
- test_two_traces_same_status_dedupe
- test_single_trace_orders_unaffected
- test_eligible_delivered_not_evicted_by_completed_noise
- test_low_limit_still_finds_delivered_after_noise
- test_status_filter_semantics_unchanged
- test_shipping_and_delivering_are_in_delivery_but_shipped_is_not

These failures are not counted as passes. Ubuntu GitHub workflow evidence is separate. No source changes were made to suppress them. The local runner executes full selected Qoyod consumer files and does not use the rounding workflow two deselections; this is broader coverage. Security pip-audit/front-end build, CodeQL, and frontend workflow tests are handled separately by the parent task.

Logs, command metadata JSON and JUnit XML are stored beside this summary. Earlier dependency/harness attempts are retained with attempt1/prior-harness suffixes.
