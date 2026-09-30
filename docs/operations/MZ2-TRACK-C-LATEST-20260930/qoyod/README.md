# Qoyod exact-baseline comparison and temporal test isolation

Baseline: `fab6ce6bb4f533683d414ffec2289b0b976f386f`, pristine detached Production worktree. Candidate: latest Production + full A/B source and prior four integration fixes; root records final linear source identity separately.

Both original runs used the exact existing workflow's six test files, environment, order and two deselections: **8 failed, 67 passed, 2 deselected** in each. Failure-by-failure IDs and their equality are in `comparison.json`. There are zero candidate-only failures. Logs and JUnit are retained.

Cause is **temporal test isolation**, not suite ordering or shared state. Both affected test modules seed `order_date=2026-07-05` but call `list_pending_orders(days=60)` without its supported `now` argument. At the actual September clock the July fixture lies outside the requested real-order-date interval, so the correct runtime returns no candidates. Running the affected tests independently retains the same eight failures (`isolated-controls.log`). No Qoyod runtime behavior was changed.

The narrowly scoped `isolated_pending_clock` autouse fixture injects July 12 through the existing public `now` parameter only in those two historical fixture modules. It does not patch production code, date filtering, assertions, or global time. Production used this exact candidate-owned fixture via external `-p qoyod_pending_clock_harness`; its tracked files remained pristine. Candidate used the same plugin and fixture. Both now run **all 77 tests PASS, zero failures/errors/skips/deselections**. The two former frozen-worker exclusions pass unchanged against latest Production, so their exclusions have been removed. Exact runtime directory diff against latest Production is empty.

CI now has a two-source matrix. It retains the candidate-owned clock plugin, checks out exact Production for the baseline job, and runs the same 77 tests against each source. Baseline runs original baseline assertions/test files; candidate only adds fixture imports. Each job retains JUnit and pytest output.

## Reproduction

Environment: `PYTHONPATH=backend:<candidate>/backend/tests` (semicolon on Windows), the existing workflow's synthetic QOYOD_TOKEN_ENC_KEY and QOYOD_API_BASE; Python from the isolated existing task venv. No live database or HTTP writes; these tests use mock clients/DBs.

Same file order for every full run:

```text
backend/tests/test_qoyod_rounding_lrm.py
backend/tests/test_qoyod_manual_send_plan_b.py
backend/tests/test_plan_b_payment_path_fix.py
backend/tests/test_pending_cross_tab_dedupe.py
backend/tests/test_plan_b_cross_trace_dedupe.py
backend/tests/test_pending_limit_and_status_filter_ordering.py
```

Original: `python -m pytest -q --tb=short <six files> --deselect backend/tests/test_qoyod_manual_send_plan_b.py::test_worker_respects_frozen_flag --deselect backend/tests/test_qoyod_manual_send_plan_b.py::test_freeze_toggle_stops_worker --junitxml=<source>-original.xml`

Isolated: `python -m pytest -p qoyod_pending_clock_harness -q --tb=short <six files> --junitxml=<source>-isolated.xml`

All four commands exited as expected: original 1/1; isolated 0/0. Two preexisting datetime.utcnow deprecation warnings remain; no test is skipped or xfailed.
