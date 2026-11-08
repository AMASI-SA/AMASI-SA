# Android operations recovery — backend candidate

Owner-requested scope: direct Ready when preparation receipt is optional; explicit one-order refresh from Salla and canonical Mezan state; manual completion-and-label refresh distinct from ordinary print; My Products/monitoring assignment dates.

Base source: 207078635d1df96ef6738fcda3d6037cfcaa787c, inherited employee-invoice task snapshot. This is not the current production branch. New task source is a draft, with dependencies needing reviewed production integration. Do not deploy the whole base merely to deliver this task. Production changed: no.

Mobile companion: AMASI-SA/amasi-mobile PR #346, dd60b89fb7f9fe78671863aea2e4624019558b29. Mobile additions require these backend APIs before delivery.

Implemented:
- Optional preparation receipt allows direct Ready while preserving supplier/services/holds/order validation; records optional Ready proof, never fabricates an employee receipt.
- Manual mobile refresh reauthorizes actual employee/store/page before fetching, before persistence and afterward; disables auto fulfillment/delivery observation/billing eligibility.
- Explicit complete-and-refresh label action uses real all-piece coverage, order-level DB lease, a single status POST for in_progress/completed and acknowledgement success validation, then immediate shipment lookup. Ordinary print unchanged. Background worker respects manual lease. No automatic mutation retry.
- My Products date endpoints and fallback chronology use actual employee assignment, not file registration date.

Fresh focused validation command: PYTHONPATH=backend:backend/tests python -m pytest -q backend/tests/test_mobile_assembly_refresh_actions.py backend/tests/test_preparation_progress_dates.py backend/tests/test_preparation_piece_operations.py backend/tests/test_amasi_shipping_compat.py backend/tests/test_assembly_completion_delivery.py backend/tests/test_assembly_completion_scheduling.py backend/tests/test_assembly_direct_preparation_receipt.py backend/tests/test_mobile_app_request_context.py --disable-warnings --maxfail=3

Result: 168 passed, 20 skipped, 59 subtests passed. Skips require a real Mongo replica environment. py_compile and diff checks pass. No live Salla operation/device acceptance/performance measurement claimed.

Remaining: reviewed integration with moving production and inherited date endpoint dependencies, Mongo replica acceptance, native/mobile CI and physical-device mixed cycles, governed backend deployment, signed APK build and verification. Current mobile build transport explicitly NOT_COMMISSIONED; no APK or distribution available.
