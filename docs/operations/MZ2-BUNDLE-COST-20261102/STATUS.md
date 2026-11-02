# MZ2-BUNDLE-COST-20261102 — production code verified

Status: PR1690 is merged and the user-authorized code publication completed on 2026-11-02. Release Guard verified the intended production identity in three consecutive probes and closed the matching lease.

Production code changed: **yes**. Live bundle-definition save: **blocked by automatic approval review and not applied**. Historical cost repairs, approved ledgers, stock operations and supplier-policy writes: **not applied**.

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


## Production verification

- Deployed merge M: `f8235d57e90f8a1271df6350d7e7919f5621ea11`.
- Runtime source A: `dc96a12e72ba3e1e1766c44e37e494ba3b69250f`.
- Release ID: `rg5-0f3fe5bc5d7da01a5dd3c052cb36637a7a49db1536afe476d7ff862d0fac285a`.
- Shared /app: inactive guard and clean tracked files verified before fast-forward from J to M; 110 pre-existing untracked files and the unrelated unsaved editor were preserved.
- Governed adapter rehearsal: exit0, built:true and isolated Frontend/Backend package checks passed.
- Own lease actor: `mz2-bundle-cost-publish-20261102`; prepared at `2026-11-02T16:02:09.473045+00:00`.
- prepublish: exit0, ready_to_publish:true, exact A/M/release identities.
- One Publish changes click. The new platform event is literally **Deployment finished Nov 2, 07:12 PM**, event `fe2fe67a-4302-5e1d-8732-2e5efd3bd499`; latest published version `7882d26`, previously `5b4a0a6`. The UI wording differs from the older “Deployment Succeeded” label; no literal label is invented.
- Production verify: exit0, verified:true, checks3, 42 Frontend requests per probe. All required health flags, source/critical hashes, exact JSON bytes/MIME and cache-busted/canonical routes passed.
- Final guard status: **active:false**. Do not rerun verify without a new lease or repeat the publish action.
- The UI exposed runtime logs, not a complete Cloud Build transcript. The rehearsal and production probe evidence do not establish unobserved platform install argv or package-snapshot ordering.

See [DEPLOYMENT.md](DEPLOYMENT.md) and [production-deployment.json](evidence/production-deployment.json).

## Tested behavior and CI

The shared tenant-scoped historical SAR authority feeds executive profit, top products, sold-product reports, snapshot GET/POST, Snapchat, marketing, customer cohorts and attribution projections. Base15 + selected rose40, quantity4, is220 in acceptance tests. A synthetic old80 therefore changes cost by+140 and profit by−140. Frozen prices survive future catalog edits; quantity, FX, return policy and sourcing segments are applied once.

- Source A:33/33 workflows succeeded.
- Reviewed B:33 applicable workflows succeeded; unrelated TikTok branch-only workflow skipped by its existing condition.
- Acceptance:291 cases, no skips/failures/errors.
- Existing G47:327 cases +127 subtests, no skips/failures/errors.
- Dashboard runtime:40 cases and164 exact JSON comparisons.
- Source/regression:115 cases +5 subtests, including35 strict read-audit cases.
- These suites overlap and are not summed as unique tests.
- [B clean-clone rehearsals](https://github.com/AMASI-SA/AMASI-SA/actions/runs/41472228228) passed under Host Node20.20.2 and24.19.0.
- All M push workflows succeeded: [Readiness](https://github.com/AMASI-SA/AMASI-SA/actions/runs/41473635007), [Security](https://github.com/AMASI-SA/AMASI-SA/actions/runs/41473635212), [CodeQL](https://github.com/AMASI-SA/AMASI-SA/actions/runs/41473635053).

The benchmark remains in `evidence/authority-benchmark.json`: six logical reads for1/80/250 orders, load+resolution medians6.847/73.145/229.216ms; returned BSON at250 orders decreased79.6%. This is helper overhead, not a production page-latency guarantee.

## Live smoke and limits

- The dashboard loaded after publication; executive and top-product cost totals matched, and orders newer than the deployment appeared.
- AMS13281 still appears as cost-incomplete. Neither220 nor a historical+140 adjustment is claimed as a completed live repair.
- Live product metadata still distinguishes explicit Mezan base15 from Salla fallback70. The persisted bundle has the conditional rose row but lacks an explicit physical current_product.
- A preview for quantity4, roseyes and nameno failed the existing option-visibility validator. Local inspection identifies a likely UI/backend mismatch in native Salla conditional visibility and empty-text presence. No visibility or source policy was changed to bypass this rejection.

## Concrete pending definition and approval block

The UI draft preserves the original rose quantity1 and selection condition and adds one unconditional required current_product quantity1, with auto sourcing and no variant. Embroidery scope is `per_unit`, because it belongs to each physical shawl. The base15 itself is unchanged.

Automatic approval review rejected **Save bundle definition**: publication authorization was not accepted as separate authorization for a live catalog change that affects future costing, inventory requirements and sourcing. No alternative save, CLI write or policy mutation was attempted after that rejection.

Read-only inspection shows that embroidery cost treatment and assembly origin are both **unknown**, its evidence reference is blank, and no supplier is selected for stock shortages. Saving schema2 must not manufacture provenance: even with a quantity scope, name-selected bundles can remain incomplete until these facts are established. Adding the current_product also makes the physical shawl a required fulfillment unit; auto uses existing stock and may return needs_source when stock is insufficient and no approved supplier exists.

The draft is pictured in [live-bundle-draft-pending-approval.jpg](evidence/live-bundle-draft-pending-approval.jpg). It is **not saved**.

## Next safe actions

1. Obtain explicit approval for the concrete live definition above and establish whether embroidery10 is independent of base15 and who actually performs it. Preserve or explicitly approve the source policy; never invent evidence.
2. Re-read the current definition/revision and product policy before saving. The screenshot is a draft, not evidence of persisted state.
3. Identify the original80 order cohort and trustworthy historical snapshots. The bounded Salla sample proves customer selections only; today's catalog cannot certify old costs.
4. Dry-run the explicit owner/order correction manifest and apply only proven historical deltas, atomically and idempotently, without approved-ledger or fulfillment-evidence writes.
5. Resolve the preview visibility mismatch as a separately reviewed code change if needed. Verify the corrected live reports and production latency against an actual baseline.

The code candidate is M; this archive branch changes documentation only and is not a deployment candidate. No paid agent chat or billing change was used. Issue1006 remains at its2500-comment cap; PR1690 and this archive preserve continuity.
