# MZ2 composition expansion — acceptance complete, publication pending

Task: comprehensive Smart Bundles extension attached2026-10-17. Existing Product V2/Components V2 engine; no parallel inventory/supplier system.

Current source:5ee379556c8a05f75a2f77e1727afaa87aac0de5 on codex/mz2-composition-current-prod. Draft PR1426 https://github.com/AMASI-SA/AMASI-SA/pull/1426 supersedes1424. Source tree exactly equals47554c20fed335d132d50f2f73683f752d982c90. Task-only commits replayed on production29ccdb81af9eb51ca2670b1bca95afa4666fb9c7 to satisfy full-history source/intent protocol; old task branch retained, no force push.

Implemented: direct add; product/resource cards; schema2 stable conditional rules; local inherited component exclusions; frozen cost provenance; mapped child options/variants/text; automatic exact-stock-first sourcing and approved supplier splitting; source-context-safe amendments; mixed receipt UI and recursive cost permission redaction. Final review selected-option merging and native toggle retry defects fixed and tested.

Acceptance: all45 scoped criteria PASS in docs/mz2-composition-acceptance.md. Local backend491+7subtests; focused UI76; identical-tree CI frontend1888/285suites, G47 backend277+106subtests/UI118/build PASS. Local full UI1870 executed passed but3 suites could not load local dependency junctions; clean-install CI all passed. Current clean branch candidate CI pending; source-base contract validation passes locally. No guard disabled.

Evidence: docs/evidence/mz2-composition six actual-component local screenshots. Hoodie50+necklace30+box3+wrap5=88; sale2 consumes176 once, excluded bags untouched and original bindings preserved. Choices2/3/4 cost88/113/153, save/reload decisions preserved, current-product3x2=6. Original snapshots remain frozen after policy edits.

Limits: unknown provenance/undivided included costs/unsupported independent supplier assembly remain explicitly unresolved, not guessed or executed. Policies live in existing cost/operation profiles; no backfill or original component-binding rewrites.

Production changed: no. No merge into production, release lease, release-intent freeze or publication. Fresh owner approval and all release gates remain required. Untracked frontend/build-bundle-verification and build-composition-verification-20261017 preserved. Next safe action: inspect current PR1426 CI, record final result in Issue1006; keep publication blocked pending owner approval.

Final CI result: code-equivalent documentation checkpoint d49047ccaa9de24377b7092544182622d41f412d passed all PR checks, including Frontend build39048678179, full frontend/security39048678301 and G47 integration39048678368. Operational Balance39048678203 initially failed package download with registry502; failed-job rerun passed without source changes. No known source/test failures remain. Release intent is not frozen and production approval remains required. Source code remains5ee379556c8a05f75a2f77e1727afaa87aac0de5; subsequent changes are documentation only.

## Authorized release checkpoint 2026-10-17
Owner explicitly requested publication. Source A=f9510f8ddf297a8c79cd27b53d1032ff1eed5683; intent-only B=490527ce7f70b01db1a16539060cb76703921931 on codex/mz2-composition-current-prod, PR1426. Candidate intent retained from successful governed CI run39049541630. B checks running; no known failure. Windows candidate validation cannot validate POSIX modes (0666), so Linux CI and actual cloud adapter remain authoritative; no guard bypass.
Fresh actual /app status reports active=false; HEAD29ccdb81af9eb51ca2670b1bca95afa4666fb9c7, tracked files clean. Untracked deployer-agent-docs preserved. Available /app disk835MiB; /tmp has ample separate space. Clean clone at /tmp/mz2-release1426-rehearsal checked out exact B and actual Host20 adapter is running with external cache and durable sanitized log /root/mz2-release1426-rehearsal.log. Production changed:no. No lease or publish yet. Emergent login restored, project Salla-Analytics, current Publish100/Deployment finished Oct17 18:50 observed.
Next: inspect cloud rehearsal result and B CI; only if passing merge PR preserving A/B, ff-only /app, prepare/prepublish, one publication, explicit newer deployment success plus verify three probes and inactive lease. This status branch is evidence only and must not be merged into frozen A/B.
