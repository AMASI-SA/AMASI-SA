# Mobile review and assembly screen reads

AMASI staff Build 62 selects the pending review queue, reviewed preparation catalog and in-progress assembly board. The native route and query parameters used here were taken from its pinned source. A separate sidebar entry opens the browser ready-to-ship board. [Native fulfillment hub](https://github.com/AMASI-SA/amasi-mobile/blob/eea242896f3001234b30cf6d552b0e3f394af02a/frontend/app/(tabs)/fulfillment.tsx#L153-L163) · [Pending queue](https://github.com/AMASI-SA/amasi-mobile/blob/eea242896f3001234b30cf6d552b0e3f394af02a/frontend/src/features/orderReview/repository.ts#L547-L565) · [Reviewed catalog](https://github.com/AMASI-SA/amasi-mobile/blob/eea242896f3001234b30cf6d552b0e3f394af02a/frontend/src/features/reviewedPreparation/repositoryCommon.ts#L267-L277) · [Assembly board](https://github.com/AMASI-SA/amasi-mobile/blob/eea242896f3001234b30cf6d552b0e3f394af02a/frontend/src/features/assemblyShipping/repository.ts#L300-L337) · [Browser entry](https://github.com/AMASI-SA/amasi-mobile/blob/eea242896f3001234b30cf6d552b0e3f394af02a/frontend/src/features/mezanAssembly/webAdapter.ts#L1)

The 240 scheduled HTTP reads returned 200 and matched independent fixture expectations. Each screen was accessed by an employee with only its matching mobile page grant. A separate employee without grants received 403 on each route. The fixture used the real native employee-to-merchant permission bridge after injecting an already-authenticated synthetic session.

| Screen | Initial GET under /api/ | Checked response | Shared p95 in ms | Separate process p95 in ms |
| --- | --- | --- | --- | --- |
| Pending review | order-reviews-v1/pages?page=1&limit=10 | The one pending order, 101, and the first-page count | 11.86–13.25 | 9.95–12.06 |
| Reviewed preparation | reviewed-products-v1/catalog?limit=2000 | One remaining physical unit from reviewed order 202 | 53.63–59.03 | 10.99–21.82 |
| Assembly and labeling | preparation-work-v1/assembly/orders?state=in_progress&limit=20&offset=0 | Order 303 with one outstanding Mezan preparation piece | 29.98–40.25 | 13.56–28.49 |

Data in each timing cell are the two observed block p95s, expressed as a range. Each block submitted 20 reads per route at 80 ms intervals, staggered across screens. Ten scheduled product-cost jobs processed 350 four-line dashboard orders apiece at 160 ms intervals. Both architectures ran twice, in shared/separate/separate/shared order, using identical fixture seeds. The web process either executed the actual cost function or ran alongside a separate Python process that executed it using the same database. All 40 jobs returned their expected amount. Measurements include lateness from scheduled arrival. Each per-block p99 is the maximum of 20 reads. [Recorded primary results](primary.json)

A fresh run from remote SHA `188b1c5b0515d081ad4ebef3f6b7a44998a9fe0e` repeated all 240 response checks with 40 dashboard jobs. Pending-review p95 overlapped across arms in this repeat. The reviewed and assembly screens showed lower per-block p95 under the separate process in both runs. [Independent repeat](primary-repeat.json)

The reviewed order count badge and orders subtab returned order 202 at limits 1 and 50. The assembly badge returned order 303 at limit 1. The reviewed screen also awaits an optional product-sales GET from August 1 through the Riyadh date. The reviewed-only grant received the expected page-permission 403. Adding the products grant returned the expected two units and SAR 160. The WebView GET to `/api/fulfillment-v2/ready-to-ship?limit=100` returned the single queued order 404 for an authorized shipping employee and denied an ungranted employee. These supplementary checks were untimed. [Recorded supplementary results](supplementary.json)

The run used real FastAPI routes and MongoDB 8.0.12 on a private loopback port. Dashboard and employee fixtures belonged to different synthetic merchants on the same Mongo instance. HTTPX exercised the ASGI boundary without starting the full server. Device rendering, login/token acquisition, shared platform capacity, proxy and network delays, remote providers, push delivery and carrier label printing were outside this experiment.

## Reproduction

Use a Python interpreter with backend test dependencies and a local mongod executable. Both backend SHAs in the JSON files identify the same full Git tree, `8408bc99f0d05c721483e7a53785f153730ba851`. The remote draft checkpoint is `188b1c5b0515d081ad4ebef3f6b7a44998a9fe0e`. No credentials, existing Mongo endpoint or external service are used.

```sh
./run.sh "$repo" 188b1c5b0515d081ad4ebef3f6b7a44998a9fe0e "$mongod" "$python" primary-local.json
./run.sh "$repo" 188b1c5b0515d081ad4ebef3f6b7a44998a9fe0e "$mongod" "$python" supplementary-local.json extra_smoke.py
```
