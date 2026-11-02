# MZ2-BUNDLE-FINANCIAL-BASE-20261102 — WIP, bundle costing only

Repository: AMASI-SA/AMASI-SA. Branch: `codex/mz2-bundle-financial-base-20261102`.
Reviewed production base: `f8235d57e90f8a1271df6350d7e7919f5621ea11`.
Prior deployed fix: PR1690. Prior documentation checkpoint:
`bb6291a9e56e95d2fa1968046dfd32dab07fe3f6`.

## Controlling user decisions

- Shawl base15 SAR, rose40 only for its Yes selection, embroidery10 only for name-Yes.
- Embroidery remains an existing external-supplier service with its existing shared-resource binding.
- User explicitly approved the bundle correction, merging and publication after tests.
- Latest restriction: preserve the old method for services and components; change **only bundle costing**.

## Scope and design

The former proposed current_product row has not been saved. The live page was reloaded and still shows only the original conditional rose row. Do not add an operational shawl unit, alter sourcing, convert the service to a direct amount or weaken supplier execution guards.

Implement an explicit future-only bundle reporting policy on the existing cost profile. It includes a missing parent base in the frozen financial composition, leaving the operational definition, schema, leaves, reservations, service identity and supplier invoicing unchanged. Ordinary products and bundles without this explicit policy retain their behavior. A physical current_product already covering that base must not be counted twice.

Freeze compact SAR evidence when existing bundle expansion already has the source metadata. Preserve it through intake, frozen replay and reservation plans. Report readers use it without new catalogue/invoice queries. Estimated supplemental base cannot become proven actual merely because operational components were consumed.

Historical corrections remain explicit, bounded and frozen-evidence-only; recognize a base already included financially so repair replay cannot add it twice. No journal, invoice, physical receipt or stock operation is authorized as part of calculating this correction.

## Work and validation state — WIP source checkpoint

Remote parent checkpoint: `d0656e329cda7ef953aef04c6d723915c3b7860f` (initial plan only).
This checkpoint preserves the implementation and current tests; it is not release approval.

Implemented a permission-checked future-only financial policy editor, compact frozen SAR evidence, bundle-only report authority and repair deduplication. No new physical base leaf is created. Existing resource bindings, supplier preparation and invoice references remain unchanged. An already active physical current_product covers its own base; an unselected conditional current_product does not suppress the financial base.

Live read-only preview established that the saved definition is schema2/revision3, with the original single conditional rose row. The existing independent embroidery binding has a known10 price but unresolved operational provenance. Its old operational guard must stay. Financial evidence may include that existing independently priced binding while keeping strict execution blocked. Capture stores it only in reporting_cost_snapshots, never in bundle_intake_snapshots (not even a None key), because the latter changes the legacy supplier export route. First financial evidence outranks a prior base-only manual snapshot and cannot be repriced by a later operational plan. Reconciliation and identity checks remain fail-closed.

Verified milestones (synthetic isolated fixtures, not live order corrections):

- Financial acceptance: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:tests /tmp/mezan-bundle-cost-venv/bin/python -m pytest --noconftest -p no:cacheprovider -q tests/test_bundle_reporting_financial.py --tb=short --junitxml=/tmp/mz2-bundle-reporting-capture.xml` from backend:46 passed,0 failed/skipped,exit0,2.46s. Covers60/100/220/260, mixed160/240, resource-backed external service, same supplier references, unchanged ordinary products, frozen replay, stock20→16, expected versus actual, FX once, nested once/sum and repair idempotence.
- Policy backend:32 passed,0 skipped,exit0, including4 isolated real-Mongo transaction/CAS cases. JUnit `/tmp/mz2-bundle-reporting-policy-full.xml`.
- Frontend: governed Node22.23.2/Yarn1.22.22; BundleEditor/BundleCostPolicy/BundleProductPolicy suites56 passed,0 failed/skipped,exit0,8.936s. JSON `/tmp/mz2-bundle-reporting-frontend-schema2.json`. Three new schema2 cases were red before the label/total fix and green after it.
- Minimum arithmetic red evidence: before the financial-base change, four selected cases returned160/200/40/0 instead of220/260/100/60 (exit1); the added base is60 for four units. These are test fixtures, not the unidentified original80 report cohort.
- Schema2-specific red evidence: both unknown-origin and supplier-origin service cases were unavailable before the financial evidence change; strict execution remains409 after financial totals become available.

Changed paths: bundle_reporting_cost.py, bundle_reporting_cost_policy.py, bundle_catalog_v2.py, bundle_fulfillment_v2.py, order_cost_authority.py, order_cost_reporting_repair.py, scoped backend tests, BundleCostPolicy/BundleEditor and their tests, this record. The lifecycle register/apply integration and its independent tests are still in progress and are not represented as a passing release gate here.

Remaining concrete verification: integrate lifecycle financial/blocked sinks, keep existing legacy-capture historical guard, verify blocked registration cannot claim acceptance, test later-plan priority and conditional-current regression, then rerun affected historical authority/dashboard/read-budget suites. Review current source before release; current tests alone do not authorize deployment.

Production code unchanged in this follow-up. No new production policy, recipe or historical repair has been applied. The previously saved embroidery financial treatment remains independent; no second10 charge was created. No release lease is active from PR1690 and its successful publication must not be repeated.

## Next safe action

Complete the lifecycle integration and the remaining scoped regressions, rerun affected report/lifecycle gates, and update this record/PR with the exact checkpoint SHA. Review and execute the repository's fresh A/B release protocol only after integrated tests succeed; preserve all existing live services/components and apply only the explicitly approved financial policy. No historical production aggregation was rewritten. The original80 report cohort has not been identified from trusted order evidence.

Issue1006 is at its2500-comment cap. This task record and the PR are the continuation record; do not overwrite another operation's comments.
