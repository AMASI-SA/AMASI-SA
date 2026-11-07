# Sold product cost details — implementation checkpoint

Scope: expand each product inside SoldProducts; dynamic per-unit base/service/component columns; group identical choices/costs; retain partial known totals without claiming completeness; refresh every product for current date/status filters; separate summary/all-details print views.

Shared authority: same order_cost_authority resolver as executive dashboard. Added additive detail evidence to ordinary and bundle catalog calculations and frozen bundle SAR evidence; never reprice frozen historical totals from today's catalog. Old snapshots with insufficient breakdown explicitly show unavailable detail. Missing priced choices now fail closed even when another choice is answered. Existing isolated automatic Salla option recovery remains the sole background acquisition worker; this page repeats the read-only report after recovery, never performs provider writes.

Source base: 8947eb0b4ad5b0370efecdaadc044268693122da. Branch codex/sold-product-cost-details-20261107.

Validation: Final integrated local run: 90 backend tests passed (2 existing Pydantic deprecation warnings), 7 frontend behavior tests passed with isolated available Jest/Babel runtime, and git diff --check passed. Initial checkpoint 835bd43de551dba07cf9c9038b6afb23bb789b4a passed focused GitHub CI and complete reproducible frontend build. Final follow-up CI pending. New CI covers backend contracts and frontend refresh/printing. No production mutation, lease, merge or deployment for this UI task.

Remaining: final tests, source/CI review and browser visual QA. No claim this feature is live. User example is test fixture only (8 units / 255 SAR); no business costs changed. Preserve other tasks and avoid reserving production deployment.

Review fixes: preserve each consumed physical unit cost and split differing actual costs; group only equal actual evidence; sum before rounding. Distinct report_key keeps same-SKU variants independent in accordion and completion counts. Independent backend/UI reviews found these issues and both are fixed with regression tests.

PR: https://github.com/AMASI-SA/AMASI-SA/pull/1795 (Draft). Issue #1006 rejects new comments at GitHub 2500-comment limit; PR conversation is the continuation ledger fallback. Next safe action: final CI and browser print/layout verification; no production merge/deployment performed.
