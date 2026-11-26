# Implemented copier lifetime regression

The parent approved the minimal lifetime fix after reviewing the isolated
experiment retained in `report_copy_lifetime_findings.md` and its exact JSON.
This implementation does not replace any additional bundle or other copy call.
Production publication by this implementation task: **none**.

## Scope

Only the final region of `backend/operational_report_copy.py` changed. Its entry
checkpoint, measured clone call, exit checkpoint and return are wrapped in
`try/finally`; the finalizer assigns only `clone = None`. This breaks the private
recursive function/cell cycle on success, error and cancellation. The memo,
visit counter, traversal, custom hooks, BSON fallback and diagnostics phase
remain unchanged. An escaped custom-hook memo is not cleared.

The independent boundary optimization was already frozen separately. No archive,
query, source formula, historical record, call-site or authorization change was
made by this copier implementation.

## Red and green results

The new dedicated module is
`backend/tests/test_operational_balance_report_copy_lifetime.py`.

Against the unchanged copier, the seven new cases exited 1 with **5 failed,
2 passed**, no errors/skips, in 1.026 seconds. The five failures were the intended
weakref retention after success, hook error, interior cancellation, exit
cancellation, and release of an intentionally escaped custom-hook memo. Entry
cancellation and the stdlib escaped-memo control passed. Results are retained in
`report_copy_lifetime_red.xml`.

After the scoped source change, the new tests plus the existing report-copy and
real-reducer state-copy suites exited 0 with **74 passed**, no failures, errors,
skips or deselections, in 3.810 seconds. Results are retained in
`report_copy_lifetime_green.xml`. The source diff whitespace check exited 0.

The lifetime cases disable cyclic GC while observing an acyclic original input
through a weakref. Successful copies remain alive and independent while their
original marker is released. Failure/cancellation cases return no partial copy.
Original frames and handled exception tracebacks end before the observation;
cleanup collection occurs only afterward. Cancellation uses the real preparation
scope stop event and checkpoint implementation.

Existing parity/lifecycle tests cover all report views, financial/history input
immutability, exact BSON/Python types, aliases and cycles across fallback tuples,
custom hooks/subclasses, atomic memo overrides, memo restoration, real reducer
copy counts, and demand cancellation waiting for its owned worker to finish.

## Commands

The actual interpreter was the existing isolated environment below. No network,
database, production read, financial write, installation or port was used.

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 PYTHONPATH=backend:backend/tests \
  /workspace/scratch/39c6dec7c37f/profit-test-venv/bin/python -B -m pytest \
  --noconftest -q -p no:cacheprovider --tb=line \
  --junitxml=docs/operations/OPERATIONAL-REFRESH-COMPLETION-20261126/evidence/report_copy_lifetime_red.xml \
  backend/tests/test_operational_balance_report_copy_lifetime.py

PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 PYTHONPATH=backend:backend/tests \
  /workspace/scratch/39c6dec7c37f/profit-test-venv/bin/python -B -m pytest \
  --noconftest -q -p no:cacheprovider --asyncio-mode=auto --tb=short \
  --junitxml=docs/operations/OPERATIONAL-REFRESH-COMPLETION-20261126/evidence/report_copy_lifetime_green.xml \
  backend/tests/test_operational_balance_report_copy_lifetime.py \
  backend/tests/test_operational_balance_report_copy.py \
  backend/tests/test_operational_balance_engine_state_copy.py

git diff --check -- backend/operational_report_copy.py
```

## Frozen hashes and limits

| File | SHA-256 |
| --- | --- |
| `backend/operational_report_copy.py` | `5a67158416030aa0ffec203f53d4c675c1cf1aaa612ca7d4e9afd538fcc4b990` |
| `backend/tests/test_operational_balance_report_copy_lifetime.py` | `a435fd0ac23eee16d8aeb73b93d421c76fbc4088af8b82c5f8078c7a069d916a` |

The prior prototype's measured JSON and script remain unchanged. They describe
the exact pre-change source hash and are reproducible at the checkpoint that
contains that source, not by silently regenerating a prototype from the now
modified function. No optional timing rerun was performed for this patch.

This fixes an unnecessary retained graph for existing callers. It does not
establish improved production RSS, full-collector timing, or a successful new
financial calculation. The orders/shipping agent was notified that the source
hash was frozen and its separate collector timing window was available.

Next action: parent independent review and durable checkpoint of the two source/
test files plus this note and the two JUnit artifacts; then the parent-owned
combined verification/release gates.
