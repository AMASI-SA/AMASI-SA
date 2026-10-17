# MZ2 composition expansion — acceptance complete, publication pending

Task: comprehensive Smart Bundles extension attached2026-10-17. Existing Product V2/Components V2 engine; no parallel inventory/supplier system.

Current source:5ee379556c8a05f75a2f77e1727afaa87aac0de5 on codex/mz2-composition-current-prod. Draft PR1426 https://github.com/AMASI-SA/AMASI-SA/pull/1426 supersedes1424. Source tree exactly equals47554c20fed335d132d50f2f73683f752d982c90. Task-only commits replayed on production29ccdb81af9eb51ca2670b1bca95afa4666fb9c7 to satisfy full-history source/intent protocol; old task branch retained, no force push.

Implemented: direct add; product/resource cards; schema2 stable conditional rules; local inherited component exclusions; frozen cost provenance; mapped child options/variants/text; automatic exact-stock-first sourcing and approved supplier splitting; source-context-safe amendments; mixed receipt UI and recursive cost permission redaction. Final review selected-option merging and native toggle retry defects fixed and tested.

Acceptance: all45 scoped criteria PASS in docs/mz2-composition-acceptance.md. Local backend491+7subtests; focused UI76; identical-tree CI frontend1888/285suites, G47 backend277+106subtests/UI118/build PASS. Local full UI1870 executed passed but3 suites could not load local dependency junctions; clean-install CI all passed. Current clean branch candidate CI pending; source-base contract validation passes locally. No guard disabled.

Evidence: docs/evidence/mz2-composition six actual-component local screenshots. Hoodie50+necklace30+box3+wrap5=88; sale2 consumes176 once, excluded bags untouched and original bindings preserved. Choices2/3/4 cost88/113/153, save/reload decisions preserved, current-product3x2=6. Original snapshots remain frozen after policy edits.

Limits: unknown provenance/undivided included costs/unsupported independent supplier assembly remain explicitly unresolved, not guessed or executed. Policies live in existing cost/operation profiles; no backfill or original component-binding rewrites.

Production changed: no. No merge into production, release lease, release-intent freeze or publication. Fresh owner approval and all release gates remain required. Untracked frontend/build-bundle-verification and build-composition-verification-20261017 preserved. Next safe action: inspect current PR1426 CI, record final result in Issue1006; keep publication blocked pending owner approval.

Final CI result: code-equivalent documentation checkpoint d49047ccaa9de24377b7092544182622d41f412d passed all PR checks, including Frontend build39048678179, full frontend/security39048678301 and G47 integration39048678368. Operational Balance39048678203 initially failed package download with registry502; failed-job rerun passed without source changes. No known source/test failures remain. Release intent is not frozen and production approval remains required. Source code remains5ee379556c8a05f75a2f77e1727afaa87aac0de5; subsequent changes are documentation only.
