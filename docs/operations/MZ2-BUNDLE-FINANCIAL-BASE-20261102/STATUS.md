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

Initial plan checkpoint: `d0656e329cda7ef953aef04c6d723915c3b7860f`.
First source WIP: `8a40e3ea05c2008644407e59875203ae1c893505`.
Concurrent same-task contributions were fetched and reviewed at `1f71410386729a4f61ed8bfcdd4c74d6d387a1c3`, `31dc419fcbc5274b6ed2eeb9b64f8ac4836f091d` and `c4b435c094989ef12476b10c2648686101cc5233`. The latter is this checkpoint's expected parent. Their lifecycle capture, regression test and CI requirements are preserved, with the canonical sale-date fence and stronger pre-acceptance guard integrated. The persisted block field is `bundle_financial_blocked` and previous financial records are copied with deepcopy.
Draft PR: https://github.com/AMASI-SA/AMASI-SA/pull/1692.
This checkpoint preserves the implementation and current tests; it is not release approval.

Implemented a permission-checked future-only financial policy editor, compact frozen SAR evidence, bundle-only report authority and repair deduplication. No new physical base leaf is created. Existing resource bindings, supplier preparation and invoice references remain unchanged. An already active physical current_product covers its own base; an unselected conditional current_product does not suppress the financial base.

Live read-only preview established that the saved definition is schema2/revision3, with the original single conditional rose row. The existing independent embroidery binding has a known10 price but unresolved operational provenance. Its old operational guard must stay. Financial evidence may include that existing independently priced binding while keeping strict execution blocked. Capture stores it only in reporting_cost_snapshots, never in bundle_intake_snapshots (not even a None key), because the latter changes the legacy supplier export route. First financial evidence outranks a prior base-only manual snapshot and cannot be repriced by a later operational plan. Reconciliation and identity checks remain fail-closed.

Verified milestones (synthetic isolated fixtures, not live order corrections):

- Financial acceptance: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:tests /tmp/mezan-bundle-cost-venv/bin/python -m pytest --noconftest -p no:cacheprovider -q tests/test_bundle_reporting_financial.py --tb=short --junitxml=/tmp/mz2-bundle-reporting-capture.xml` from backend:46 passed,0 failed/skipped,exit0,2.46s. Covers60/100/220/260, mixed160/240, resource-backed external service, same supplier references, unchanged ordinary products, frozen replay, stock20→16, expected versus actual, FX once, nested once/sum and repair idempotence.
- Policy backend:32 passed,0 skipped,exit0, including4 isolated real-Mongo transaction/CAS cases. JUnit `/tmp/mz2-bundle-reporting-policy-full.xml`.
- Frontend: governed Node22.23.2/Yarn1.22.22; BundleEditor/BundleCostPolicy/BundleProductPolicy suites56 passed,0 failed/skipped,exit0,8.936s. JSON `/tmp/mz2-bundle-reporting-frontend-schema2.json`. Three new schema2 cases were red before the label/total fix and green after it.
- Minimum arithmetic red evidence: before the financial-base change, four selected cases returned160/200/40/0 instead of220/260/100/60 (exit1); the added base is60 for four units. These are test fixtures, not the unidentified original80 report cohort.
- Schema2-specific red evidence: both unknown-origin and supplier-origin service cases were unavailable before the financial evidence change; strict execution remains409 after financial totals become available.

Changed paths also include fulfillment_v2_routes.py, test_bundle_financial_lifecycle.py and the existing G47 workflow (new suites are required, with zero-skip checks). No runtime dependencies or lockfiles changed.

Additional verified milestones:

- Active physical current_product now has a covered_bases policy proof and a financial projection with zero added base. FX4USD×3.75 plus rose40SAR gives220 for quantity4 in both schemas. Pending independent supplier service gives260 without an extra base.
- Financial acceptance after the date fence and concurrent test merge:72 passed,0 skipped,exit0,2.61s (`/tmp/mz2-bundle-reporting-financial-final.xml`). The concurrent contributor test is retained with original Salla creation evidence. SHA256 of the acceptance file: `8ccd26e06215d43f32d16ae060a9533a581c1ab19e2323e381cea96cf4a26c84`.
- Lifecycle register/apply real Mongo:10 passed,0 skipped,exit0,16.14s after merging the block field name; all10 UUID fixtures removed. Includes original260, exact supplier export parity, four concurrent replays before/after price changes, cancellation, missing original creation, pre-policy sale and forged newer DTO date. An earlier broader lifecycle gate passed42 tests plus8 subtests with zero skips.
- Independent review reproduced and then verified fixes for inactive current_product, active physical FX, later-plan repricing and first capture of a sale older than the financial policy. The final date review passed11 focused cases plus15 independent date assertions. Prices can be known for preview while historical certification remains false.
- Future-only is based on original canonical Salla creation, not a DTO, order_date or refresh timestamp. Each frozen added/covered policy records policy_effective_at. Missing/invalid/pre-policy creation cannot certify a historical amount. Every opted-in financial capture keeps its first compact envelope, including executable bundles, so later plans cannot drop this restriction or reprice it.
- The27-file integrated local cost run executed390 cases:388 passed,2 failed with the existing503 resource_pressure response. Both failures were reproduced unchanged on deployed-source-equivalent B (`f953c374239ae89a9311b3e311945ffa4e7933a5`, tree`aba404781e09f0ba58ddc43827ecff95e1848d2f`). The shared8GiB environment was95.8–96.6% occupied; the guard correctly refused admission. No guard threshold or response oracle was changed. Final exact-head CI remains required; this run is not described as green.

Remaining concrete blocker: a real isolated legacy fixture proved that writing a new blocked lifecycle after dropping its financial proof can replace an old legacy audit and prevent allow_legacy_component_execution / ready-piece / consume-piece. Original strict capture raised409 before registration and left that audit unchanged. The minimal correction in progress restores the original register transaction rollback for legacy blocked captures; it must pass before this branch is release-ready. Final exact-head CI and governed release checks also remain. This WIP is not release approval. Production is still unchanged by this follow-up.

Production code unchanged in this follow-up. No new production policy, recipe or historical repair has been applied. The previously saved embroidery financial treatment remains independent; no second10 charge was created. No release lease is active from PR1690 and its successful publication must not be repeated.

## Next safe action

Complete the lifecycle integration and the remaining scoped regressions, rerun affected report/lifecycle gates, and update this record/PR with the exact checkpoint SHA. Review and execute the repository's fresh A/B release protocol only after integrated tests succeed; preserve all existing live services/components and apply only the explicitly approved financial policy. No historical production aggregation was rewritten. The original80 report cohort has not been identified from trusted order evidence.

Issue1006 is at its2500-comment cap. This task record and the PR are the continuation record; do not overwrite another operation's comments.
