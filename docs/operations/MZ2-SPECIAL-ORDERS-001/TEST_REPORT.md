# R1 verification — not release acceptance

Actual local command, exit 0:

```sh
PYTHONPATH=backend python -m pytest -q backend/mezan_special_orders/tests
python -m compileall -q backend/mezan_special_orders
```

Result: **70 passed, 4 skipped**. All four skips are real-Mongo tests requiring a
separately supplied isolated Mongo test URI. No failures were hidden or converted
into passes. Compileall exited 0.

Environment: Python 3.13.5; Pydantic 2.13.4; FastAPI 0.128.2; HTTPX 0.28.1;
pytest 9.0.2. This is not the full repository's locked environment. The dedicated
GitHub workflow reads its six exact dependency pins from backend/requirements.txt
and uses a disposable Mongo 7 service. Its result must be checked independently.

The local checkout contains only this new package, not the complete application.
Source/catalog, evidence, shared workflow and MZ2 ledger providers in tests are
explicit synthetic test doubles. HTTP tests exercise the actual new router;
MemoryStore tests do not establish Mongo durability or existing-engine parity.

Coverage includes all purposes, required/new options, immutable originals,
permission/tenant isolation, integer monetary precision, FX guards, idempotency,
concurrent commands, pending/rejected/verified bank receipt distinction, COD and
remittance separation, refunds, cost-unit coverage and reversal, cost-allocation
rounding, provisional reports, Salla remainder calculation, disabled default gates,
private financial projections and dependency/error sanitization.

Not run: full repository baseline/candidate regression; real Salla, MZ2 or
fulfillment integration; actual supplier PDFs/shipping labels; Android; Preview
or Production UAT; release build or rollback drill. No runtime/financial writes.

Do not describe the 70 tests as an end-to-end test of Mezan or proof that all
preparation stages now support local orders. The production adapters are unbound.
