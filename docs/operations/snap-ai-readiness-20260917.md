# Snapchat AI readiness — 2026-09-17

Status: IN_PROGRESS. Production base f8fae757409b94e84a4994f64297fbfd1f90cb4d (PR #1071), verified live 3/3 and lease inactive. No new deployment.

Fresh UI evidence after period fix: Aug 1–Sep 17, account timezone America/Los_Angeles, campaign match 3946/3968 (99.45%), 22 unresolved. Current Sep17 snapshot 53/53. Previous 82.7% is superseded; changing population means this is not a frozen-cohort comparison. Independent partition diagnostic gap zero. Existing readiness passes last closed Sep16; Decision Intelligence remains isolated.

Acceptance: expose full-population, account-local campaign attribution coverage and bounded reason counts through the unified contract; distinguish complete data retrieval from complete attribution; retain missing/ambiguous orders without guessing campaign identity; fail closed when evidence is unavailable or inconsistent; preserve provider, budget, bid, financial and accounting write isolation. Verify with focused regressions, affected contracts and live diagnostic before claiming AI readiness. Accounting/other-provider deficiencies are separate owner tasks.

Next: implement and test attribution diagnostics and consumer gates; measure remaining reasons from Production after reviewed release. No automatic campaign execution authorized by this task. Continuity ledger: Issue #1006.

## Implemented candidate

Full-population account-local campaign attribution counts and missing-identity/click-only/catalog/ambiguity reasons are now retained by the strict unified summary. Readiness and Snapchat decision evidence reject missing, partial or inconsistent coverage; other providers keep their existing behavior. The page exposes the current selected period's reasons under details. No matching policy, order attribution or provider state is rewritten.

Regression evidence: 5 new failures reproduced the prior missing proof / permissive decision gate. After implementation, 129 tests passed with 1 real-Mongo-only test skipped locally (covered by CI Mongo7). Additional boundary cases and UI coverage added; CI remains pending. Next: run fresh affected suite and CI, independent diff review, freeze a reviewed release, then production validation and classify the 22 historical gaps. Production unchanged.
