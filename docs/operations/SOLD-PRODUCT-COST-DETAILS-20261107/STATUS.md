# Sold product cost details — implementation checkpoint

Scope: expand each product inside SoldProducts; dynamic per-unit base/service/component columns; group identical choices/costs; retain partial known totals without claiming completeness; refresh every product for current date/status filters; separate summary/all-details print views.

Shared authority: same order_cost_authority resolver as executive dashboard. Added additive detail evidence to ordinary and bundle catalog calculations and frozen bundle SAR evidence; never reprice frozen historical totals from today's catalog. Old snapshots with insufficient breakdown explicitly show unavailable detail. Missing priced choices now fail closed even when another choice is answered. Existing isolated automatic Salla option recovery remains the sole background acquisition worker; this page repeats the read-only report after recovery, never performs provider writes.

Source base: 8947eb0b4ad5b0370efecdaadc044268693122da. Branch codex/sold-product-cost-details-20261107.

Validation: Final integrated local run: 85 backend tests passed (2 existing Pydantic deprecation warnings), 6 frontend behavior tests passed with isolated available Jest/Babel runtime, and git diff --check passed. GitHub workflow pending at checkpoint. New CI covers backend contracts and frontend refresh/printing. No production mutation, lease, merge or deployment for this UI task.

Remaining: final tests, source/CI review and browser visual QA. No claim this feature is live. User example is test fixture only (8 units / 255 SAR); no business costs changed. Preserve other tasks and avoid reserving production deployment.
