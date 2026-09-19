# Snapchat AI readiness — 2026-09-17

Status: IN_PROGRESS. Production base f8fae757409b94e84a4994f64297fbfd1f90cb4d (PR #1071), verified live 3/3 and lease inactive. No new deployment.

Fresh UI evidence after period fix: Aug 1–Sep 17, account timezone America/Los_Angeles, campaign match 3946/3968 (99.45%), 22 unresolved. Current Sep17 snapshot 53/53. Previous 82.7% is superseded; changing population means this is not a frozen-cohort comparison. Independent partition diagnostic gap zero. Existing readiness passes last closed Sep16; Decision Intelligence remains isolated.

Acceptance: expose full-population, account-local campaign attribution coverage and bounded reason counts through the unified contract; distinguish complete data retrieval from complete attribution; retain missing/ambiguous orders without guessing campaign identity; fail closed when evidence is unavailable or inconsistent; preserve provider, budget, bid, financial and accounting write isolation. Verify with focused regressions, affected contracts and live diagnostic before claiming AI readiness. Accounting/other-provider deficiencies are separate owner tasks.

Next: implement and test attribution diagnostics and consumer gates; measure remaining reasons from Production after reviewed release. No automatic campaign execution authorized by this task. Continuity ledger: Issue #1006.

## Implemented candidate

Full-population account-local campaign attribution counts and missing-identity/click-only/catalog/ambiguity reasons are now retained by the strict unified summary. Readiness and Snapchat decision evidence reject missing, partial or inconsistent coverage; other providers keep their existing behavior. The page exposes the current selected period's reasons under details. No matching policy, order attribution or provider state is rewritten.

Regression evidence: 5 new failures reproduced the prior missing proof / permissive decision gate. After implementation, 129 tests passed with 1 real-Mongo-only test skipped locally (covered by CI Mongo7). Additional boundary cases and UI coverage added; CI remains pending. Next: run fresh affected suite and CI, independent diff review, freeze a reviewed release, then production validation and classify the 22 historical gaps. Production unchanged.

## Resumption audit — 2026-09-19 (blocked on Production evidence)

Remote candidate `fix/snap-ai-stable-coverage-20260919` was verified at `0af9fe416baf67c1b0a01fd1284d708a9e7d30b6`; the open /app worktree matched and was clean. This is newer than the PR #1079 handoff. Exact-head PR search returned no PR. PR #1079 remains merged at `dfe2554f4719645c1cebd4b81fad3ae5d569ccfa`; it was not redeployed.

The inherited candidate changes `_latest_level_status` in `backend/snapchat_v2/routes.py` and its tests. The requested compact `load_snapchat_v2_entity_readiness_evidence` reader already independently selects a complete, financially complete, period-covering run and inspects TOTAL fact coverage. The candidate does not change that reader. Its relevance to the live failed readiness gate is therefore not established; do not publish it as a proven fix for that gate.

Fresh browser read-only readiness inspection for account `efcdd251-9a4f-4dc0-8358-6a1a91f8892a`, America/Los_Angeles, latest closed day 2026-09-18 returned 6/7: only hierarchy coverage failed; unmatched orders and missing-cost orders were zero. This is not a new per-level diagnostic for requested 2026-09-17.

The available /app code-server terminal has a loopback effective Mongo connection; shell overrides do not establish a Production connection. No Production identity or authenticated Production DB session was verified. No local DB results were substituted for Production. Per-level complete/sync_status/coverage_status/row_count/source_fact_count and latest Production sync-run fields remain unmeasured; missing TOTAL facts versus incomplete sync is not yet distinguished.

Read-only checks: Git status clean; local/remote candidate SHA match; source call-path inspection; live readiness button; Release Guard status `active:false`. No tests rerun or candidate acceptance claimed. No source edit, backfill/manual sync, provider/campaign/budget write, release intent, lease acquisition, merge, or deployment. Production changed by this resumption: no.

Next safe action: obtain the authenticated Production terminal or official Production database viewer, verify environment identity, execute the requested read-only reader for campaign/ad_group/ad on 2026-09-17 and inspect the latest scoped sync run. Establish root cause before changing source or data. Preserve the inherited candidate until evidence shows whether it is relevant.
