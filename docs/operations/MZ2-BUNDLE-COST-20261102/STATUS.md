# Current deployment state

Publication has been explicitly authorized and is now in progress. See [DEPLOYMENT.md](DEPLOYMENT.md) for the current release/lease checkpoint; the merged handoff below records the earlier, pre-publication state.

# MZ2-BUNDLE-COST-20261102 — merged handoff

Status: merged into `hotfix/prod-snap-meta-final` through [PR1690](https://github.com/AMASI-SA/AMASI-SA/pull/1690) after the user explicitly authorized continuing and merging on 2026-11-02.

Deployed Production changed: **no**. Live catalog, orders, historical backfill, stock and approved ledgers changed: **no**. The repository production branch changed by the authorized merge only.

## Immutable source and merge record

| Role | Full SHA |
| --- | --- |
| Previously reviewed production base J | `70576e2a06237eb193b1c927f11ced92e2229589` |
| Tested implementation source A | `dc96a12e72ba3e1e1766c44e37e494ba3b69250f` |
| Reviewed intent-only B | `f953c374239ae89a9311b3e311945ffa4e7933a5` |
| Authorized merge M | `f8235d57e90f8a1271df6350d7e7919f5621ea11` |
| B and M tree | `aba404781e09f0ba58ddc43827ecff95e1848d2f` |

Task branch: `codex/mz2-bundle-cost-snapshots-20261102`.
Documentation archive: `codex/mz2-bundle-cost-merge-handoff-20261102`.

M parents are exactly J and B. Its complete tree equals B; no additional source changes were introduced. A and B were preserved through a normal merge, without squash or rebase.

## Verified behavior

One tenant-scoped historical SAR cost authority now feeds executive profit, top products, sold-product reports, snapshot GET/POST, Snapchat, all-provider marketing, customer cohorts and attribution reporting projections. Physical base15 plus selected rose40, quantity4, costs220; a synthetic old cost80 increases by140 and reduces profit by140. Optional embroidery10 is separate. Frozen prices survive catalog edits. Quantity, sale FX, return policy and component/source segments cannot double count.

Historical correction is bounded to explicitly selected owners/orders, uses frozen evidence and reviewed manifest hashes, applies atomically and idempotently, and never changes approved ledgers or operational fulfillment evidence. Prospective bundle-definition repair adds one explicit physical current_product through the existing service; it does not rewrite old snapshots. Both remain unapplied to live data.

## Verification evidence

- Source A:33/33 workflows succeeded.
- Reviewed B:33 applicable workflows succeeded. The extra TikTok Creator Release Candidate workflow was skipped by its existing branch-only condition; it is unrelated to this task.
- Cost/report acceptance:291 cases, zero skips/failures/errors.
- Existing complete G47:327 cases plus127 subtests, zero skips/failures/errors.
- Dashboard runtime:40 cases and164 exact JSON comparisons. Source/regression stage:115 cases plus5 subtests, including35 strict read-audit cases. Suites overlap and are not summed as unique tests.
- Both reviewed-B clean-clone adapter rehearsals succeeded under Host Node20.20.2 and24.19.0: [run41472228228](https://github.com/AMASI-SA/AMASI-SA/actions/runs/41472228228).
- `git diff --exit-code B M`:exit0. Clean-clone `load_release_intent` and `verify_release_intent_git` on M:exit0.
- All three automatic push workflows on M succeeded: [Release Readiness](https://github.com/AMASI-SA/AMASI-SA/actions/runs/41473635007), [Security Gate](https://github.com/AMASI-SA/AMASI-SA/actions/runs/41473635212) and [CodeQL](https://github.com/AMASI-SA/AMASI-SA/actions/runs/41473635053). Both clean-clone rehearsals passed again on M; the final handoff job records manual deployment requirements only.

The B intent is the exact successful source-A CI artifact13153179707 from run41469317753. ZIP SHA-256:`e4df1510d0ae3d2d6a431e34e5117302fc2eebcbe83320de19f84bf6c443efba`. JSON SHA-256:`d295c0e46080d1836c755557d184bc04b7d7253a1665544f19ba051876fd3b81`. The expected runtime release ID is `rg5-0f3fe5bc5d7da01a5dd3c052cb36637a7a49db1536afe476d7ff862d0fac285a`; no claim is made that it is deployed.

Local performance evidence remains in `evidence/authority-benchmark.json`:six logical reads for1/80/250 orders, load-and-resolution medians6.847/73.145/229.216ms, and79.6% less returned BSON at250 orders. This is added authority overhead, not a production page latency guarantee.

## Live evidence and remaining scope

UI and the Salla connector distinguish Salla shawl cost70 from explicit Mezan base15; Mezan rose40 and optional embroidery10 remain separate. A bounded Salla read of1–2November confirmed the known2November group:four rose-plus-name units and one plain-plus-name unit. Its current-catalog arithmetic is260+25=285, not proof of historical frozen costs or the original80 cohort. No private order identifiers or customer data are recorded here.

The original80 cohort and trustworthy Mezan historical database evidence remain unresolved. The previously available preview DB did not match the target catalog. Never use today's price to certify an old sale or present synthetic before/after results as live repair.

## Next safe actions

1. Use merge M as the reviewed repository checkpoint; this archive branch changes documentation only after M and is not the frozen deployment candidate.
2. Obtain the original affected order cohort and trusted historical evidence. Generate the reporting and prospective-definition dry runs with the confirmed owner and sourcing policy. Review actual options and frozen base prices before any apply.
3. A separately authorized publish must follow AGENTS protocol v5 from the shared `/app`:check guard status, respect any other owner's lease, rehearse the adapter, prepare/prepublish, publish, and verify the governed identity. No lease or publish was performed in this task.
4. Apply only reviewed manifests within the correct live owner/cohort; verify all reports, unchanged approved journals, repeat-apply no-op, and live dashboard/order latency.

Issue1006 remains at GitHub's2500-comment cap. PR1690 and this task-specific archive preserve the final continuation record. Do not edit unrelated comments to evade the cap.
