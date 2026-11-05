# Supplier grouped queue — WIP, not released

Branch codex/supplier-grouped-queue-20261105 starts from production branch d80661fd2 (PR1759 merged). Mobile companion codex/supplier-grouped-selection-20261105 is based on Android68 and requires this API before distribution.

Adds authenticated waiting-groups summaries (no dispatch history, file bodies or customer option details), optional literal group identity filter on ten-piece waiting pages, and prefilters already dispatched/received rows before current-order hydration. Keeps current status, employee and merchant scopes and existing write guards. Legacy missing identity and whitespace pending fields covered. No financial, approval, Ready or shipping mutation changes.

Verification: isolated Mongo on127.0.0.1:27858, temporary database per test; waiting-current-status, index-lifecycle and dispatch tests99PASS, exit0. New HTTP tests cover group counts, ten/ten/three pages, current status changes, tenant/employee isolation, literal selector values and legacy identity fallback. No real supplier writes. Device timing and release gates pending. Existing untracked frontend/build-performance-* preserved.

Review incident observed separately: one explicitly authorized native completion attempt returned component_source_event_stale at2026-11-05 13:59:33UTC after exact-attempt closure had unblocked UI. Root condition remains unproven: this code also covers signed display/binding/fingerprint freshness. No safety guard bypassed. Native log has no server detail. Supplied production logs show health timeouts across two instances, Qoyod409 and shipment permission denials, but not exact review conflict. Health handler is local boot identity/state, without DB/provider reads. Do not infer that Qoyod409 or shipment401 caused the review rejection or all latency.

Next: controller/browser layout UAT, source CI, reviewed v5 A/B intent/rehearsal, then separately guarded publication and Android build. Need actual review_completion_409 stage or sanitized source_diagnostic for root repair. No deployment lease or publish performed. Production changed by checkpoint: no.
