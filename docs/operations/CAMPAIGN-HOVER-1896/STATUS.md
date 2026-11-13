# Campaign hover — source A preparation, 2026-11-13

## Authorization and production boundary
Owner explicitly authorized implementation, merge and governed publication, then requested continuation. Production inspected as `b64121c265d8d6e158df0d9fb88debc52c95317d`. This task has NOT changed production, advertising settings, accounting records, or a live release lease.

## Recoverable source
PR1905: `codex/snapchat-account-local-campaign-hourly-20261113`.
Previous combined source `5a00e603486176ef6d97f80876ffcde3618baf60` is preserved at `checkpoint/campaign-hover-1905-before-source-a-20261113`. It includes exact PR1903 source and PR1905 NY/Riyadh hourly work. The replacement source is prepared directly above current production, before any intent freeze, to avoid carrying a second branch's historical release-intent transitions. No release guard, classifier, or old intent bytes are altered.

## Verified results before this checkpoint
On combined `5a00e603486176ef6d97f80876ffcde3618baf60`, frontend/security, Salla/dashboard, Snapchat integrations, midnight handling and other triggered feature checks passed. Mezan Release Readiness run43437326575 failed before dependency installation in candidate-role classification. Its log described an intent-B/governed-source mismatch. Read APIs returned differing adapter revisions by path and Git tree; immutable tree/blob evidence was used where available. Do not bypass classification or treat the release as ready from application checks alone.

## Functional scope preserved
- Shared homepage and OrdersV2 popup, bounded prefetch, no hover/focus/tap network calls or new polling/cron.
- Independent Salla orders, units and SAR sales, exact campaign catalogue account binding (two Snapchat accounts), raw UTM query coverage, no name guessing.
- Account-local NY/Riyadh spending from hourly facts, dense elapsed-hour coverage, DST 23/25-hour days, recorded USD/SAR rate only.
- Source accessibility labels and touch navigation guards. No cross-session browser financial cache.
- Bounded tenant-scoped server cache/concurrency and fail-closed unknown values.

## Additional correction
The HOUR orchestration now rejects failed top-level/subrequests, malformed rows and an unfinished pagination chain before publishing either daily projection. Existing facts are retained, not overwritten with a partial amount. The account-local writer receives explicit source-completeness evidence. Dedicated offline orchestration tests cover failed requests, pagination cap and a successful last page exactly at the cap.

## Remaining gates
1. Exact new-source CI, including new provider-response acceptance, all existing regressions and unmodified Mezan Release Readiness.
2. Inspect current production/source ancestry again; build reproducible candidate A and freeze intent-only B. Do not merge source-only production or reuse the old intent.
3. Measure cold/warm reads and reconcile real NY/Riyadh campaign hours, FX and Salla attribution. Unit fixtures are not live financial acceptance.
4. Check `/app` release guard lease before any terminal action, rehearse approved V5, publish once, and require successful live runtime identity plus three probes. CI or a merge is not proof of deployment.
