# P01 — final refund arrival-order matrix

Runtime under test: `080953eb1669987b53d2474a8f52c42e951e2178`, PR #1097 over #1091, isolated Preview database only. No application code changed in this verification round. Documentation commit `580905a4d` was fetched/compared against the remote branch and pushed as a clean fast-forward from `654eda981`.

Seven new synthetic scenarios passed actual Preview HTTP execution: Salla refund-first, and statement-first/refund-first for Tamara, Tabby and Emkan. The previously completed Salla statement-first scenario was not repeated.

For every scenario:

1. Recognize a new eligible synthetic sale through the supported preview/execute API.
2. In statement-first cases, upload the source before the refund webhook; in refund-first cases, upload after daily execution approval.
3. Signed webhook creates one nonfinancial refund draft and leaves ledger count unchanged.
4. Record two distinct partial daily executions. Concurrent approval requests for each execution return the same journal group.
5. Statement rows remain unlinked and require review until explicit refund identities are supplied. An identity-free match request returns 422.
6. Link statement rows to the existing daily executions. Emkan's aggregate refund row links both distinct partial execution IDs.
7. Repeat the webhook twice and repeat source upload concurrently. One source file and one settlement draft remain, with no additional refund journal or refund draft.
8. Read ledger balances and confirm the exact expected effect, unchanged bank balance, and preservation of all pre-existing financial documents.

Raw private evidence: `seven-before.json`, `seven-statement-evidence.json`, and `seven-statement-check.log` in the established private Preview runtime evidence directory. Financial identifiers and amounts are deliberately excluded from public documentation.

The script completed all seven scenarios and its final protected-baseline equality assertion. The code-server host subsequently returned 502 and reported inactivity sleep. Access recovered after opening the project administration page and refreshing Preview; no paid conversation or publish action was used. Source/isolation and release reservations were rechecked. All seven records then passed read-only Chromium visual review, with the correct existing refund group IDs visible in each statement and no settlement journal. Financial scenarios were not rerun. Earlier and current browser acceptance was performed using installed Chromium against the public Preview URL, not the Codex tab. The blank login page in the Codex tab remains unresolved and must not be represented as fixed.

## Full written P01 exit-gate review

References: [PHASE-01-SETTLEMENTS.md](PHASE-01-SETTLEMENTS.md), sections “متطلبات Backend”, “متطلبات Frontend”, “الاختبارات الإلزامية”, “سيناريوهات اختبار المتصفح”, “بوابة الخروج”; [README.md](README.md), “قاعدة قفل المراحل”. Older checkmarks are historical, not release evidence for this candidate.

| Written condition | Evidence and assessment | Remaining |
| --- | --- | --- |
| Unified settlements page | Unified register verified in prior actual browser acceptance | None for synthetic Preview scope |
| Current provider-bank binding protected | Existing provider/shipping bindings, scoped tests and preserved historical snapshots | No binding changes in this round |
| Four provider statement import/matching | Targeted automated coverage plus all eight actual refund arrival-order combinations, cumulatively | None for authorized synthetic scope |
| Draft/match/review/post lifecycle | Prior authorized settlement postings and browser evidence; current automated workflow coverage | New seven statements intentionally remain unposted; this round tests refund reconciliation |
| One balanced idempotent journal per statement | Prior posted-settlement evidence and atomic/idempotency tests; current uploads/linking add no settlement or duplicate refund journal | No reposting of historical settlements |
| Sales/fees/taxes/refunds/adjustments/net visible | Prior browser acceptance; saved source values and explicit refund links; seven new records visually verified after recovery | None for synthetic scope |
| Independent permissions | Prior actual Chromium viewer acceptance plus valid endpoint 403 and unchanged financial snapshots | Named employee is not required by written test |
| Backend checks pass | Previously recorded 34 targeted real isolated-Mongo tests on exact code; seven new live HTTP scenarios | Not a claim of full pytest |
| Frontend checks/build pass | Previously recorded 31 frontend tests and complete Preview build on exact code | Governed Production build remains a release step |
| PR merged into Production branch | Not performed in this task | Review and merge candidate stack under separate authorization |
| Runtime deployed/verified by release guard | Preview isolation/source verification only | Production prepare/build/publish/verification under separate authorization |
| Browser scenarios documented | Prior cumulative browser scenarios, viewer/relogin/saved-original evidence in Chromium, and fresh seven-case visual review | Codex blank login remains an unresolved environment/browser issue, distinct from Chromium application acceptance |
| STATUS and evidence updated | Previous docs checkpoint successfully pushed; this matrix and STATUS record fresh results | Production-branch adoption remains subject to merge |
| P02 opened only after gate | P02 remains locked | No next-phase work authorized |

Verdict: PASS for authorized synthetic Preview acceptance, using cumulative preserved evidence and fresh scoped verification. This is distinct from original bank/provider reconciliation and operational cutover. No opening balances or Production cutoff changes were used. P01 remains IN_PROGRESS pending release requirements; no merge or Production publish was attempted. P02 remains locked. This verdict does not resolve the Codex-tab blank-login issue or establish general Preview uptime.
