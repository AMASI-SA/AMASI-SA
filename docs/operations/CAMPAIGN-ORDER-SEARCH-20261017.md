# Campaign order search and automatic date filtering

Status: implemented and locally verified; not merged or deployed.
Base: `29ccdb81af9eb51ca2670b1bca95afa4666fb9c7`.
Task branch: `codex/campaign-order-search-20261017`.

## Behavior

- Reuse the dashboard Arabic date-range picker on OrdersV2. Applying the range searches automatically; draft clicks do not issue requests. Full selected dates use Riyadh midnight with an exclusive following-day end.
- Every existing search field can be used alone or combined. Wait 500 ms after the last edit, cancel obsolete client requests, and reject late responses. Preserve selected status; clear resets all search fields.
- Stop combined-result polling from replacing all matching orders with the first order. Do not repeat expensive combined searches every ten seconds.
- Search campaign IDs in the same UTM identity used by existing campaign display enrichment, without per-scanned-order campaign catalog lookups.
- A missing creation date no longer aborts the entire search. Report skipped invalid rows rather than claiming a complete result. Yield between groups of 50 mapped rows so a large search does not monopolize the API event loop.

## Verification

- Before fix: two added backend regressions failed for the intended reasons (OrderMappingError on an unrelated malformed order; UTM campaign ID returned zero results).
- After fix: `test_order_engine_campaign_search_http.py`, `test_order_engine_operational_attribution.py`, `test_order_engine_service.py`: 17 passed, exit 0.
- HTTP test uses the actual Order Engine router, scoped fake storage and real mapping/search/enrichment. It includes valid matching and excluded date-boundary rows and one invalid row. Unrelated fulfillment route installers are excluded from this focused test.
- React tests: `OrdersV2.search.test.jsx`, `ArabicDateRangePicker.test.jsx`, `useOrders.shippingRefresh.test.jsx`, `orderSearchDates.test.js`: 17 passed, exit 0. Covers committed vs draft date selection, campaign/date combination, field clearing, debounce, obsolete-response suppression and multi-result preservation.
- `git diff --check`: exit 0.
- Broader backend run: 17 passed, 10 failed. Eight HTTP setup failures are caused by missing `email-validator` in the reused local test environment. Two other failures (router static dependency assertion; campaign catalog lookup-order expectation) reproduce on the untouched base in `D:/codex-worktrees/campaign-search-baseline-20261017`, exit 1. No claim that the full suite is green.
- Live production read-only search of the supplied campaign ID returned no matching orders during this turn. The earlier screenshot's HTTP 500 was not reproduced in that attempt; its exact production traceback remains unverified.

## Limits and next step

- Existing server scan cap (latest 5000 orders) and response cap (50 matches) are unchanged. The UI now warns when the scan cap is reached. No production throughput benchmark or guarantee of complete historical results.
- No production data writes, branch merge, `/app` changes, release lease, or deployment.
- Review the patch, run CI and a visual browser check on the candidate, then follow the repository's current release protocol if publication is authorized. Do not reuse the shipping release's identity/intent.
- Main shared checkout and unrelated Qoyod changes were left untouched.
