# R1 CI evidence — MZ2-SPECIAL-ORDERS-001

## Verified outcome

GitHub Actions run **36345453610**, job **108693597122**, completed successfully.
The actual job log reports: `74 passed, 1 warning in 1.07s`.
The downloaded JUnit XML independently reports **74 tests, 0 failures, 0 errors,
0 skipped**. Compileall also exited successfully.

Workflow: https://github.com/AMASI-SA/AMASI-SA/actions/runs/36345453610
Artifact: https://github.com/AMASI-SA/AMASI-SA/actions/runs/36345453610/artifacts/10939649265

## Exact tested identity

- Source commit: `ce792b863f1224c5dbf3a2e0dd15bdaeb7967ad0`.
- Source parent: `1cbeccd84d58fb3397dcd42822d5feb92de06275`.
- PR test merge: `97468105c13ac226918b62b257c447d2145688e5`.
- Source and test-merge tree: `19392dd5404654cb720d3a8af2f2430140392767`.
- The test-merge tree was fetched from GitHub and equals the source tree.
- The GitHub-generated test merge is not a merge into the production branch.

## Environment and command

Python 3.11.16, MongoDB 7.0.43. Direct pins: FastAPI 0.140.0, HTTPX 0.28.1,
Motor 3.3.1, Pydantic 2.13.4, PyMongo 4.6.3, pytest 9.0.3. The workflow installs
only these six repository-pinned dependencies and resolved transitive dependencies;
it is not the complete application's production dependency environment.

```sh
PYTHONPATH=backend MEZAN_SPECIAL_TEST_MONGO_URI=mongodb://127.0.0.1:27017 \
python -m pytest -q backend/mezan_special_orders/tests --junitxml=special-order-tests.xml
```

The four real-Mongo cases are:
- `test_real_mongo_concurrent_create_single_document_and_event`
- `test_real_mongo_compare_and_swap_has_one_winner`
- `test_real_mongo_carrier_tracking_unique_across_orders`
- `test_real_mongo_financial_reference_unique_across_orders`

Each uses a disposable `test_mezan_special_<uuid>` database. No merchant or
production database was used. Source, catalog, evidence, existing workflow and
MZ2 ledger providers in the tests remain explicit synthetic test doubles.

## Retained evidence

Artifact ID: `10939649265`, archive size: 1892 bytes.

Artifact ZIP SHA-256:
`dd8f19838753332932aa2ee96444cba1b39d1f8e745006419dc63e54c808ed52`

Contained `special-order-tests.xml` SHA-256:
`0ea6c4644d05fe6e29fcc18597a2c9c1adff27f2c2e97132e4217bd9fa739e7f`

Both hashes and all XML test counters were checked after artifact download.
The one pytest warning is Starlette test-client HTTPX deprecation. Separate
GitHub action runtime warnings do not change the test result.

## What this result does not establish

The real production adapters, shared-workflow bindings, actual MZ2 journal
creation, actual PDF/label rendering, persisted Salla balance sidecar, KPI report
consumers and Android integration are not implemented or validated by these tests.
This is a tested isolated nucleus, not completion or release approval of the full
requested feature. No route registration, activation, Merge or Deploy occurred.
