# Snapchat AI readiness — 2026-09-17

Status: IN_PROGRESS, MERGED_NOT_DEPLOYED. PR #1074 merged at 0cea6ed2f5dc3489510ab30a0f072a09c948fd8e. Frozen source A ac1ece749b91e8ed2c48d71543dc9ce9e07ee4a6; intent B 431e4232192589ccbe3efaf65fd0f62dd1867e37. Deploy the reviewed merge, not this later documentation checkpoint.

Runtime: rg5-ce1101d0b001ad62be0828b3768d46da417fd438c3806805cda844f6ddd50fba. Reviewed base and last verified Production release: f8fae757409b94e84a4994f64297fbfd1f90cb4d (PR #1071), verified 3/3 previously. No new Production deployment or lease was started by this task.

Fresh live baseline before this release: Aug 1–Sep 17, America/Los_Angeles, campaign match 3946/3968 (99.45%), 22 unresolved. Current Sep17 snapshot 53/53. Prior 82.7% superseded; populations differ, not a frozen-cohort comparison. Independent date-partition gap zero. Decision Intelligence remains isolated.

Implemented: full-population account-local attribution counts and bounded missing-identity/click-only/catalog/ambiguity reasons retained in strict unified summary. Snapchat readiness and decision attribution evidence fail closed on absent, partial or inconsistent proof. UnifiedMarketingOrdersPanel exposes reasons without new requests. Matching policy unchanged; no guessed assignments, sync/backfill, provider, budget, bid or financial writes. Other providers retain behavior.

Verification: 5 regressions reproduced before implementation. Final exact backend selection locally 142 passed, 1 real-Mongo-only skip, exit 0. CI Mongo7 selection 143 passed. Attribution UI, unchanged Snapchat page, native backend, frontend builds, security, CodeQL and clean-clone Emergent adapter rehearsal all passed on B. Settings scope guard passed without weakening. Source A deterministic build run 35274666191 produced artifact 10520710175; archive SHA256 012b6c8fcfe3bb99c727edf4e48d042002a98cc6256357ba4c46c051d1cee041. Candidate intent validated locally. A..B changes only release/release-intent-v5.json.

Blocker: cloud terminal shows Preview Unavailable / resting after inactivity. Cloud browser recovers read access but project-open actions time out and remain on Emergent Home. No paid platform chat used, no other lease cleared, no /app update. Current lease state cannot be asserted from old scrollback.

Next: restore existing Salla Analytics project and terminal; fresh guard status must be inactive and checkout inspected before any /app change or local rehearsal. Verify Production branch has not advanced. Rehearse exact reviewed merge, prepare own v5 lease, prepublish, publish once, explicit newer Deployment Succeeded, verify 3/3 and inactive status. Then measure 22 gap reasons and latest closed-day readiness plus all decision evidence gates. Do not claim full AI readiness until live evidence supports it. Missing historical identities may require upstream evidence; never force matching to reach 100%.

Canonical ledger: Issue #1006. Other accounting/provider work preserved.
