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

## Subsequent GitHub verification — real Mongo PASS

The earlier 70-pass / 4-skip local result above remains an accurate historical
record. A subsequent CI run executed all four Mongo tests: **74 passed, zero
failures/errors/skips, one pytest warning**. Compileall also succeeded.

- Workflow: `Mezan Special Orders Core`, run `36345453610`.
- Job: `108693597122`, conclusion `success`.
- Source: `ce792b863f1224c5dbf3a2e0dd15bdaeb7967ad0`.
- GitHub PR merge checkout: `97468105c13ac226918b62b257c447d2145688e5`.
- Both source and tested merge tree: `19392dd5404654cb720d3a8af2f2430140392767`.
- Python 3.11.16; MongoDB 7.0.43; exact six direct dependency pins were read from
  the repository. This is not installation of the entire application environment.
- Artifact `10939649265` was downloaded and its SHA-256 and XML counters verified.
- Full evidence, artifact hashes and scope limits: `CI_EVIDENCE.md`.

The warning concerns deprecated HTTPX usage in Starlette's test client, not a
failed test. Existing production dependency files were not altered to suppress it.

This real-Mongo result validates the new storage/index/concurrency contracts,
not actual existing Mezan fulfillment, MZ2 posting, labels, reports or Android.
Adapters remain unbound and the router remains unregistered. Documentation-only
checkpoint updates after the implementation commit do not imply new runtime tests.


## R2 — actual read adapters and canonical compatibility (activation remains off)

Implemented additions:
- `ExistingCatalogAdapter` reads the existing tenant-scoped `mezan_products_v2`
  cache and the canonical original-order reader. It preserves real choice IDs,
  free text, multi-select choices, product/variant IDs, SKU, barcode and galleries.
  Variant choices are validated against the catalog; an unknown/incomplete schema
  fails closed instead of exporting a product without required options. File-upload
  product options require a separate secure option-evidence binding and remain
  explicitly unsupported in this adapter. No catalog refresh/provider write occurs.
- Canonical `OrderSourceDTO` and `OrderItemSourceDTO` accept `mezan` additively;
  default is still `salla`. Canonical order purpose/original/local ID are explicit.
  Special metadata is omitted from ordinary Salla wire payloads to preserve
  backward compatibility with clients that reject unknown fields.
- `MongoOrderRepository(db, include_mezan=True)` is an explicit read opt-in, NOT a
  production activation. Existing application factories do not pass it and retain
  Salla-only behavior. Get/batch/list and numbered pending-review discovery support
  both real sources without inserting a fake local order into `unified_orders`.
- Local read queues derive current stage from `order_review_workflows`, not an
  asynchronous copied stage, while checking order identity and frozen-source
  integrity. Shared `in_progress` maps to canonical processing. Cursor ordering and
  pagination remain stable across the two sources and tenant-scoped.
- Current local option snapshots travel through the real OrderItem mapper, the
  real inventory specification helper and the real option-cost binding helper.
  All selected values remain present once in display/print data. The shared cost
  helper now recognizes each ID in a genuine multi-select value list; its existing
  single-value and name-based behavior is preserved.

R2 does not mount a route, enable a factory, issue a carrier label, or claim the
existing state-changing workflow/MZ2/Android integration is complete. Shared
review/preparation status mutations still need source-aware integration before
any local order may be created operationally. These are real implementation and
release blockers, not merely a request to run more tests.

R2 local evidence: 176 passed, 7 skipped (all real-Mongo cases), compileall and
`git diff --check` exit 0. Frozen ordinary-order baseline: 77 passed; the candidate
passed the same 77 plus 99 nucleus/source/HTTP tests. The local source fixture does
not include the frontend. Its one frontend-source assertion is scheduled in CI
with a complete repository checkout, not counted as a local pass.

An initial attempt ran the two static contract tests from the repository root,
producing two file-path failures on BOTH baseline and candidate. Rerunning from
`backend/` resolved the backend assertion; the frontend assertion cannot run on
this backend-only fixture. The test was not weakened; CI runs both from the
correct directory with the original frontend present. No production data used.


## R2 subsequent CI — verified, not live release acceptance

The R2 local result above (176 pass / 7 Mongo skips) is historical. Dedicated
GitHub run **36349065001**, job **108704030454** subsequently completed with
**184 passed, 1 deprecation warning**, zero failures/errors/skips. The frozen
ordinary-order baseline passed **78 tests**, and the same 78 pass in the candidate.
The full CI checkout also runs the frontend-source assertion unavailable in the
local backend-only fixture. All seven real-Mongo tests executed successfully.

Source `9e3c551fb987c78b85bfca3f6d1c3a69ea09467d` and the tested GitHub merge
`80e51a6b2ad91c6a6f25ba8ac47832e43ba979b6` both have tree
`b817bc4f45783e79642efca112e739d51fd8ebce`, independently read back.
Artifact **10941469074** was downloaded; its archive digest and both XML counters
were recomputed. Full identities, digests and remaining implementation blockers
are in **R2_CI_EVIDENCE.md**. This is not completion of shared fulfillment mutations,
MZ2 posting, actual labels, Android, reports or release acceptance.
