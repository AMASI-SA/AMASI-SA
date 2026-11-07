# Employee invoice prices, local services, and assignment dates

## Owner request and scope

The employee app must submit changes to a product or service's permanent price for the owner's approval in the dashboard. An invoice retains the price entered by its employee. Approval changes the catalog default only; approving or rejecting the proposal must not change the recorded invoice or supplier payable.

The second invoice-draft page must offer services from the current product's cost category. A product without a category shows `لا يوجد تصنيف لإضافة خدمة له`. Adding a service and associating it with a customer option applies only to the selected pieces in that invoice. It must survive draft refresh/reopening and must not create a product-wide binding or change other/future invoices. Existing approved product bindings are preserved.

My Products / In Progress uses the product-file creation date. Monitoring / In Progress uses the employee assignment event. Directory dates, selected-day filters, counts, and detail cards must agree, in Asia/Riyadh. A later supplier dispatch or receipt cannot move the employee's original assignment day. In-progress eligibility is unchanged.

The additional product-page request separates components from services in independent selectors, including customer-option cost editing and the general catalog. Cross-category component selection shares the existing resource with the product's current category and links it to that product, while retaining the original categories, inventory and cost identity. This deliberate catalog sharing is separate from invoice-local service selection.

## Source and isolation

- Backend/dashboard repository: `AMASI-SA/AMASI-SA`.
- Reviewed starting commit: `0241e162bfe0d7d10faa85f3f4c023929a96ea2c` from `hotfix/prod-snap-meta-final`.
- Mobile repository: `AMASI-SA/amasi-mobile`.
- Reviewed starting commit: `b9fc2972a4582c93021bcb8d50f6dda8488fd45a` from `codex/android73-monitoring-selection-20261106`.
- Task branch in both repositories: `codex/employee-invoice-approvals-20261108`.
- Mobile local workspace is a source snapshot: all 430 downloaded source/test/config blobs were verified against their GitHub blob SHA before editing. Its local baseline commit is not remote ancestry. Remote delivery must apply only reviewed changed paths to the original complete remote tree, preserving native assets, dependencies, release files, and unrelated work.
- No live database, order, invoice, payment, supplier, or provider operation is authorized by the tests in this task. No merge, release lease, deployment, APK build/distribution, or OTA has been performed.

## Implementation state

Backend/dashboard implementation and bounded local verification are complete; native integration and independent review are being finalized. A source-only checkpoint is not publication readiness. All three backend/dashboard slices received independent review. One catalog-selection regression was reproduced (a newly created hidden service could populate another component's cost draft), fixed by selecting and populating the same filtered item, and covered by a behavioral React test.

New native writes must use distinct review/local-service endpoints and must never fall back to old endpoints that can change catalog data immediately. The updated backend also intercepts legacy mobile price writes and rejects the former permanent supplier-invoice service endpoint.

## Fresh validation

- Root repeated the five dashboard suites on 2026-11-08: `CI=true node node_modules/react-scripts/bin/react-scripts.js test --watchAll=false --runInBand --runTestsByPath src/pages/ProductPriceChangeRequest.test.jsx src/components/NotificationBell.priceChanges.test.jsx src/pages/AlertsPage.priceChanges.test.jsx src/components/OwnerOnlyRoute.test.jsx src/components/Layout.deferredWorkspaces.test.jsx` from `frontend`. Exit 0; 31 tests passed in five suites.
- Root integrated backend run: 187 passed in 4.88s, exit 0, across the 18 suites listed below. This includes actual in-process HTTP with synthetic data, invoice-local refresh and per-piece customer choices, exact halala proposals, owner-only decisions, quantity-only changes, category sharing, employee/file dates, split-day pagination 10+2, and the production DTO hydration wrapper.
- Independent component-share review repeated its 9 tests successfully; independent date author ran 51 tests successfully.
- Root repeated the final eight dashboard suites: 49 passed in 2.105s, exit 0, including the additional catalog-selection regression and explicit option-quantity review display.
- `git diff --check` succeeded. Existing deprecation warnings were observed, with no failing behavior.
- The four deliberately changed supplier function golden hashes were advanced after root diff review. The display identity function and four financial-file hashes remain unchanged. No ledger posting or integrity writer was edited.
- MongoMock/in-process HTTP tests do not establish real Mongo replica rollback or concurrency. No database daemon or production connection was started. Native device/UAT, production behavior, and a released APK are not claimed.

## Next safe action

Preserve this verified backend/dashboard checkpoint in a draft pull request, then finish the independent native review and its source checkpoint. Keep production and installed app versions unchanged until a separately authorized release.

## Reproducible integrated backend verification

```bash
PYTHONPATH=backend:backend/tests python -m pytest -q \
  backend/tests/test_employee_price_approval.py \
  backend/tests/test_supplier_invoice_local_services.py \
  backend/tests/test_supplier_receiving.py \
  backend/tests/test_supplier_invoice_service_eligibility.py \
  backend/tests/test_supplier_refresh_wrapper.py \
  backend/tests/test_supplier_invoice_service_candidates.py \
  backend/tests/test_supplier_invoice_live_cost_session.py \
  backend/tests/test_component_category_required_group_picker_v2.py \
  backend/tests/test_component_edit_policy.py \
  backend/tests/test_component_status_lifecycle.py \
  backend/tests/test_product_cost_setup_routes.py \
  backend/tests/test_order_option_cost_snapshots.py \
  backend/tests/test_product_component_category_share.py \
  backend/tests/test_preparation_progress_dates.py \
  backend/tests/test_monitoring_assignment_projection.py \
  backend/tests/test_mobile_employee_monitoring_workspace_owner.py \
  backend/tests/test_preparation_supplier_dispatch.py \
  backend/tests/test_supplier_display_financial_boundary.py
```

## Deployment state

Production changed: **no**. No merge, Release Guard lease, preview/publish, data backfill, APK distribution, or OTA. Task source is to be preserved in draft PRs only.
