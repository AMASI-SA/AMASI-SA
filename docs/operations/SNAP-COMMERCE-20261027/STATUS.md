# Snapchat commerce evidence continuation — 2026-10-27

Base: production merge `72ab1aee0fa9520b3a5f91f924906a3be3337531`.
Branch: `codex/snap-commerce-proof-20261027`. No deployment, provider writes,
advertising changes, or production database mutations were made by this task.

The Salla periodic light-order discovery reads one bounded page, maintains a
process-local watermark, and can advance it even after some per-order failures.
It cannot establish complete upstream coverage or freshness. The date-range
sync had a separate incorrect completion signal: when its page limit was reached
without total-page metadata, it returned and stored `truncated=false`. This
change requires an observed, non-contradictory end of pagination and rejects
malformed page data. Each page's returned page number and total-page count
must remain consistent: changes or intermittent omission make the range
truncated. Missing pagination metadata on every page may only end on an
explicit empty page. This does not manufacture a commerce coverage proof.

Verification: focused pagination plus AI execution-evidence suites passed
75 tests. The pagination regression reproduced on the unchanged base before
the fix. An expanded suite had 81 passing and two pre-existing failures in
`test_salla_marketing_attribution_recovery.py`; both reproduce on unchanged
base `72ab1aee0` (an outdated attribution expectation and a mocked persistence
seam that no longer matches the owner transaction path).

The existing Review Completion Resumable workflow now runs the 21 pagination
cases when the Salla sync source or the test changes, and its JUnit guard
requires all 21 to execute without skips, failures, or errors. The exact
workflow-style standalone command passed locally (21 passed); the combined
remote workflow and its real Mongo checks remain subject to fresh exact-head CI.

The current Snapchat scale gate remains fail-closed with
`campaign_commerce_freshness_unproven`. Completing the end-to-end contract
requires a bounded, exhaustive, tenant- and period-bound Salla snapshot,
confirmed promotion of each order, and a persisted cost/cohort revision that
is invalidated when orders or relevant product costs change. Neither the
current light discovery timestamp nor a recent provider-ad sync can prove
these facts. Do not infer proof from those timestamps or this pagination fix.
