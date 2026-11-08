# Android operations recovery — production-based candidate

Scope: employee app Ready/printing recovery and manual order refresh, optional preparation receipt, assignment-date navigation, operational image validation regression. Production changed: no.

Integrated base: de76aed339be775e2878071c92063d0ab9df06df (hotfix/prod-snap-meta-final). This replaces the stacked PR1826 candidate89e9e0a200fc871022a0076d707342399253fa97. Only five required date paths from770b4be863bff0352eab09e3276a8a41cd2dd4b9 were extracted before applying this task; no employee invoice/catalog approval feature was carried over. Dedicated branch: codex/android-operations-integrated-20261108.

Mobile companion: AMASI-SA/amasi-mobile PR346, source7f7551750151f74d6395e45f8dee0640e8aec66c. Source changes require these APIs before delivery; no APK or backend deployment is claimed.

Implemented:
- Optional preparation receipt allows direct Ready while retaining supplier, service, hold, order and permission checks; never fabricates a receipt.
- Explicit mobile order refresh reauthorizes the actual employee/store/page before fetch/persist and afterward; no automatic fulfillment, billing or delivery observation.
- Explicit complete-and-refresh label action proves all-piece readiness, leases the order, performs one completed status POST even if already completed, verifies its acknowledgement, then immediately looks up shipment. Ordinary printing remains unchanged; no automatic mutation replay.
- In My Products and monitoring in-progress, a selected day means assignment TO the employee. The product must also belong to a supplier dispatch made by that employee. Supplier dispatch date and order date do not select the day. Current canonical order must be reviewed or processing; absent, waiting-review, terminal and unknown orders are excluded. Stale customized review status cannot override a current terminal status.
- Employee date navigation does not read file registry dates. Legacy file-date callers retain registration-day selection and server-side batch narrowing. Product detail hydration remains paginated.
- Server image signature validation remains unchanged; regression rejects mislabeled JPEG/WebP and accepts corrected MIME. Mobile encoder corrects MIME from actual payload bytes.

Fresh coordinator verification on integrated working tree:
PYTHONPATH=backend:backend/tests python -m pytest -q --asyncio-mode=auto backend/tests/test_mobile_assembly_refresh_actions.py backend/tests/test_preparation_progress_dates.py backend/tests/test_preparation_piece_operations.py backend/tests/test_amasi_shipping_compat.py backend/tests/test_assembly_completion_delivery.py backend/tests/test_assembly_completion_scheduling.py backend/tests/test_assembly_direct_preparation_receipt.py backend/tests/test_mobile_app_request_context.py backend/tests/test_order_review_operational_images.py --disable-warnings --maxfail=3
Result:199 passed,20 skipped,59 subtests passed,exit0. Additional independent wider compatibility run:283 passed,29 skipped,64 subtests passed. Diff check and Python compilation pass.

Real Mongo is unavailable locally: existing pinned8.0.12 executable fails isolated startup with Operation not permitted before initialization. No production URI is used. Existing Review Completion Resumable workflow now runs the focused suite against its disposable Mongo replica and rejects any skip/failure/error, retaining JUnit. Remote result pending.

Next: inspect exact integrated remote SHA/CI, finish mobile monitoring two-column UI requested afterward, verify native/mobile combined candidate, then governed release and signed APK through the operational build/signing environment. Current repository APK transport is ClosedTransport/NOT_COMMISSIONED. No version bump, OTA, real Salla mutation, deployment or device acceptance has occurred.
