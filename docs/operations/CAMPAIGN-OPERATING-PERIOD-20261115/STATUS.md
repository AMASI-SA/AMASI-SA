# Campaign operating period and cohort correction

## Scope and verified base

Task CAMPAIGN-OPERATING-PERIOD-20261115, PR 1932, branch `codex/campaign-operating-period-20261115`.
Production base: `324dab9fd01fab17a28a5f7de4bb2bd298d7aea9`, tree `c2d683827c54574fcd60ee4102c9de7c163f5ef8`.
Prior PR 1929 completed publication and three-probe verification. Its frozen intent and release receipts are historical and must not be reused for this correction.

## Confirmed defect and implemented behavior

The published hover selected its operating denominator using the current Asia/Riyadh day, independently of the visible executive summary's chosen period. At Riyadh midnight that basis could change while the campaign account day and selected executive period remained unchanged.

The correction carries the selected executive inclusive date range and payment, shipping and report-status cohort through AdvancedDashboard, the hook, API, immutable normalized cache/pending key, profit enrichment and bounded operating reader. Campaign commerce and spend retain the advertising account day. Returned basis metadata identifies the actual operating period and cohort.

The hook rejects old-context results immediately during rendering and after late responses. An explicit null dashboard context means the selected executive snapshot is pending. Standalone order-page scope follows the current Riyadh day only when existing orders/totals data refresh; it does not add a timer or polling. The initial proposal to pin the standalone page-entry day was revised after independent review to preserve overnight refresh behavior.

Range calculations load each existing collection once, apply canonical salary/recurring/calendar/status/currency rules and round each range subtotal once. Existing disabled-pilot, completeness, ownership, read-limit, timeout and cancellation protections remain. Ratios above 100% and real negative profits are not capped.

## Focused evidence and limitations

The exact production M6 public regression, with synthetic data at before/after Riyadh midnight, fails for its monetary defect: allocation changes from 20 to 2,000 for a fixed selected day. The corrected candidate keeps 20 across both times and honors an explicitly selected new day independently.

The isolated backend runner passes 103/103 cases. Root independently repeated the candidate run: exit 0, 1.678 seconds; baseline regression: expected exit 1, 1.290 seconds. The runner uses real scope, route, cache, attribution, currency, calendar and profit arithmetic, with synthetic in-memory persistence and substituted direct-product pricing seams. This is not a full pytest result. Full exact-source repository CI remains required.

Backend source and test fingerprints were independently reviewed without an unresolved finding. All 9 frontend files pass the installed Babel parser; 4 new mounted regressions and the revised standalone overnight contract are ready for full React/Jest execution in CI. The acceptance workflow includes the new public operating-context tests and full mounted AdvancedDashboard.cardRefresh suite.

The operating reader retains its 4,000-order bound; large selected periods may return incomplete instead of using partial data. Matching scope does not establish exact generated_at snapshot identity: independently refreshed saved data can be fresher than the visible executive cache.

## Release and continuity

Production changed by this task: no. No new lease, merge or publication.
Read-only original inspection at 2026-11-14T21:22:06.790532Z found guard inactive, HEAD at the verified base, tracked worktree clean and 120 untracked files. Current publication UI remained 75ee080 with Deployment finished Nov 14, 10:45 PM. Fresh official status is required again before any original write or rehearsal.

Source-A CI, independent integrated review, a fresh reviewed intent-only B, exact B/host checks, ordinary tree-equivalent merge M, merge push checks, original governed build, prepare/prepublish, one authorized publication and actual three-probe verification remain pending. Disabled new operator templates cannot run until all new identities and actual receipts are pinned and independently reviewed. Do not invoke old PR 1929 runners.

No financial, payroll, order, advertising/budget/bid, provider-sync or paid agent actions. Preserve unrelated original files, unsaved buffers and other owners' leases.

Issue 1006 is full; PR 1932, this task branch and a separate continuation branch/checkpoint are the durable fallback. Next: integrate the frozen implementation, run exact-source CI and resolve concrete failures before freezing a fresh intent.
