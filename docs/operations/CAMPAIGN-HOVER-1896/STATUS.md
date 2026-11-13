# Campaign hover — MERGED, live deployment not performed

Checkpoint: 2026-11-13, after explicit owner instruction to continue and merge.
Repository: AMASI-SA/AMASI-SA.
This status update lives ONLY on the isolated ops handoff branch; do not merge that branch or its preparation workflows into production.

## Exact recoverable identities

- Production branch: hotfix/prod-snap-meta-final.
- Previous production J: b64121c265d8d6e158df0d9fb88debc52c95317d.
- Reviewed source A: c05108b41a3cb603955b210acb0fba32743d5d5f.
- Reviewed intent-only B: 9b35e287604130f2d04243c2fcade6861d14be78.
- PR #1905 MERGED at 2026-11-13T15:48:51Z.
- Production merge M: 8d25f7a6fa9a310b319b753c32457e70987f7c51.
- Task branch: codex/snapchat-account-local-campaign-hourly-20261113, retained at B.
- Release backup branch: release/campaign-hover-1905-intent-b-c05108-20261113, retained at B.
- Runtime release ID: rg5-9f65056bf0d9f1bc5e40da895de78a8288fee5e532ecaaa26daf4f468522894b.
- Reviewed intent Git blob: cc339628e488d58aa8f860248c036a047b12cd4f.
- Reviewed intent JSON SHA256: 347667bc3423d9bc762d1d4eec0350c417d8d598f08dbee54fe678728a6c164f.

## Completed functionality

The main release combines the homepage LatestOrders and OrdersV2 campaign card, independent Salla campaign-attributed orders/piece quantities/SAR sales, exact Snapchat campaign/account catalogue resolution and account-local New York/Riyadh spending. Recorded USD/SAR conversion and DST boundaries are preserved.

Owner correction is implemented: valid available Snapchat spend is shown and used for CPA/ROAS even when hours are missing. Coverage is advisory; missing hours are NOT fabricated as zero. Invalid/conflicting/future evidence, wrong account-day facts and missing recorded FX still remain unavailable. Salla commerce stays independent.

No request is initiated by hover, focus or tap. Bounded component prefetch and tenant-scoped server cache remain. No new polling, cron, runtime query or sync-frequency change was introduced by the partial-spend policy.

## Verification and merge evidence

1. All triggered source-A workflows passed, including Mezan Release Readiness43452250648 and backend/UI coverage.
2. Artifact14053265932 supplied the exact reviewed intent; downloaded bytes matched its SHA256, and the remote Git blob matched independently.
3. Isolated preparation43454758030 passed the unmodified adapter.load_release_intent A/J/B verification. Python -B prevents bytecode pollution; no validator or release guard was relaxed. The preparation workflows were not included in the release.
4. Exact-B checks passed before merge, including Mezan Release Readiness43454994616 (both Host20.20.2 and24.19.0 clean-clone adapter rehearsals, artifact/package/runtime JSON probes), Campaign hover source coverage43454993078, Security Gate43454992738, CodeQL43454992658, Snapchat Integrations43454992996, Page/read performance43454992746, Operational Stock43454992492 and Operational Balance43454992809, plus the other application regressions. Earlier cancelled duplicate Snap runs were superseded by successful latest runs. The unrelated TikTok creator candidate workflow was skipped by its condition, not counted as passed.
5. Production still equalled J immediately before normal merge. The merge used expected head B and did not squash or rebase source A or intent B.
6. Read-back: PR1905 is merged, and the production ref equals M. Compare B..M has zero changed files. Compare A..B has exactly one changed file: release/release-intent-v5.json. Unrelated production changes were retained.
7. PR1903 closed WITHOUT separate merge because its work is incorporated and corrected in PR1905. Do not merge that obsolete branch.

## Production and live-runtime boundary

Production Git branch changed: YES, verified at M.
Live Emergent deployment performed by this task: NO.
No /app mutations, release lease, provider campaign/budget changes, accounting writes, manual sync, database backfill or real-data reconciliation were performed in this merge step.

A merge and clean-clone CI are not proof of the live website running the release or of actual campaign monetary reconciliation. Account-local snapshots need the existing Snapchat sync to populate them after deployment; hover itself does not refresh the advertising provider.

## Next safe action

Follow AGENTS.md for a separately authorized deployment. Before touching /app, inspect the current production ref and run the release guard status; do not change another conversation's active lease. Rehearse the current approved A/B release, prepare/prepublish only according to V5, publish through Emergent, then require newer explicit deployment success and three verify probes matching the source/release identity. Verify homepage/OrdersV2 cards and actual NY/Riyadh campaign account, spend, recorded FX, Salla order/piece/sales and cold/warm latency. Do not use git reset --hard or reuse an old intent.

Issue1006 had a verified comment-capacity403 in earlier work. The task PR comments and this isolated handoff document preserve continuation without retrying that blocked ledger or altering the frozen release.
