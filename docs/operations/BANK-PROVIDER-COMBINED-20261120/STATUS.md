# Combined bank/provider release — production verified

Owner-authorized scope: bank receipt recognition PR #2033 plus provider payout chronology correction PR #2032, one combined publication, read-only financial verification.

Repository: AMASI-SA/AMASI-SA. Production branch: hotfix/prod-snap-meta-final.
PR #2033 merged preserving its A/B at 61ddec6883134b9bf934e0a4735334cdb79800e0.
Final combined PR #2035 merged at c4a83ac18b24f0367b3fbe8052a026c2dbdbac98.
Combined source A: 3103631c38f08730c0b812f67c0b4a97895f5242.
Intent-only B: 89cbd29853f6afac8595d59e0cee11ece99debc1.
Actual verified runtime release: rg5-e38ce9dca65abaa76e2cb34c658b016d6b9f84e39747d2bb7752f9abf4da9f98.

Behavior: bank transfers recognized once with verified Salla in-progress milestone; later statuses require history proof; mixed tender recognizes only bank leg. Other payment recognition policies preserved. Provider chronology excludes unlinked payouts strictly before order creation irrespective of snapshot age. PR #2032 incorporated unchanged through #2035. PR #2034 superseded and never deployed.

Validation: operational 5879 passed +21 subtests; shipping 16 passed +18 subtests; all applicable reviewed-B CI/security/frontend/browser/mobile and Node20/24 rehearsals PASS. Shared Emergent rehearsal, final /app build and package checks PASS. Eleven unrelated untracked host entries preserved.

Production changed: YES. One Publish after prepare/prepublish, deployment b03edd3f-d5bf-53a8-8cdf-61bdd3b8ce7c. Emergent resumed UI shows green "Deployment finished Nov 21, 12:39 AM" Riyadh and published version 100, 774bb42. Exact phrase "Deployment Succeeded" was not exposed in resumed UI; published-version record and runtime identity corroborate completion.

Official command completed successfully on shared /app:
PYTHONDONTWRITEBYTECODE=1 python scripts/production_release_guard.py verify --url https://mezansalla.com
Result: checks=3, exact source/release/deployment above. 662 public frontend requests per probe (1986 total), including canonical/cache-busted build-meta and asset hashes. Runtime identity flags, backend manifest and release control binding passed. First verifier boot timestamp 2026-11-20T21:38:45.420726+00:00. Separate public health observation showed matching release with boot 2026-11-20T21:38:53.678890+00:00.
Then production_release_guard.py status returned {"active": false}. Matching lease closed automatically; no manual abort or lease deletion.
Host evidence: /tmp/mezan-2035-verify.json and /tmp/mezan-2035-final-status.json. These paths supplement this durable record; do not rely on their persistence.

Read-only production UI: bank ledger loads after reload. Existing provider review holds remain visible in stored snapshot; their financial resolution has NOT been established by this deployment verification. No receipt approvals, recalculation or historical ledger writes were performed. Historical bank receipt matching remains separate. No APK required.

Publication task complete; do not republish. Any subsequent financial diagnosis must retain read-only scope unless separately authorized. Documentation-only checkpoint branch codex/bank-provider-publication-evidence-20261121; this document changes no deployed source. PR #2035 is continuation record because Issue #1006 cannot accept further comments (capacity).
