# Snapchat AI readiness — 2026-09-17

Status: IN_PROGRESS. Production base f8fae757409b94e84a4994f64297fbfd1f90cb4d (PR #1071), verified live 3/3 and lease inactive. No new deployment.

Fresh UI evidence after period fix: Aug 1–Sep 17, account timezone America/Los_Angeles, campaign match 3946/3968 (99.45%), 22 unresolved. Current Sep17 snapshot 53/53. Previous 82.7% is superseded; changing population means this is not a frozen-cohort comparison. Independent partition diagnostic gap zero. Existing readiness passes last closed Sep16; Decision Intelligence remains isolated.

Acceptance: expose full-population, account-local campaign attribution coverage and bounded reason counts through the unified contract; distinguish complete data retrieval from complete attribution; retain missing/ambiguous orders without guessing campaign identity; fail closed when evidence is unavailable or inconsistent; preserve provider, budget, bid, financial and accounting write isolation. Verify with focused regressions, affected contracts and live diagnostic before claiming AI readiness. Accounting/other-provider deficiencies are separate owner tasks.

Next: implement and test attribution diagnostics and consumer gates; measure remaining reasons from Production after reviewed release. No automatic campaign execution authorized by this task. Continuity ledger: Issue #1006.
