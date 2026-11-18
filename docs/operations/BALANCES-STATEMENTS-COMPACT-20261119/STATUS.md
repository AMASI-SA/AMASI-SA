# Balances, period statements and receipt reviews

## Scope and authorization

The owner explicitly authorized merging, publishing the approved balances
workflow and issuing the companion APK on 19 November 2026 (Riyadh), after
reviewing the compact UI, statements and receipt-review behavior. The current
server stage prepares an isolated source-A / intent-B release. The release
coordinator owns the shared host, production merge, lease and publication; the
server-source writer must not perform those actions. No live financial
transactions are authorized for acceptance testing.

- Repository: `AMASI-SA/AMASI-SA`
- Release source branch: `codex/balances-release-source-20261119`
- Preserved verified feature branch: `codex/balances-statements-source-20261119`
  at `1485a0ae1700b806af2a5cf81a6cc41de3c24134`.
- Initial verified Production baseline: `80b43ca2f4699fecd895f34a24f77fc997234417`
- Current integrated Production baseline: `c3c1cd26d93ab24d6e08003278b9910526055cc7`
  (normal merge of PR2001; independently verified with live remote ref).
- Companion: `AMASI-SA/amasi-mobile`, based on operational branch
  `feature/operational-order-compensation-20261101` at
  `a8a7a55e22b4f7430c7619bb792c5d7d540b4f43`.
- Preserved earlier Draft PR: https://github.com/AMASI-SA/AMASI-SA/pull/2003
- Preserved verified feature PR: https://github.com/AMASI-SA/AMASI-SA/pull/2004
- New release source PR and exact source SHA: recorded in the new task PR at
  this source checkpoint; never change source after freezing intent B.
- Native Draft PR: https://github.com/AMASI-SA/amasi-mobile/pull/364
- Integrated current Production `77d824a780df582b5c32f4dda87a959e35e1ed1e`
  into task source `903e477bae520553bd0ca184d626b9c98694879b`, preserving
  the already released intent byte-for-byte.
- Production changed by this task: **no**. No shared-host change, release
  lease, intent freeze, production merge, publication, live grant update or
  financial write has occurred in this source-integration stage. PR2001
  publication is a separate ongoing release owned by another conversation.
- Continuation: this task document and its task PR. Repository issue #1006 is
  already at its comment limit, as documented in the preceding bulk-access task.

## Acceptance contract

| Requirement | Source behavior / evidence needed |
| --- | --- |
| Compact layout | Entity/type in header, small Add, compact summary, five-row table; mobile visual inspection. |
| One-step Back | Detail/proof/history/report first, then original filtered page; web behavior plus native source checks. Physical device acceptance is separate. |
| Daily salary/delivery history | Only the latest proven automatic event in main ledger; manual money remains; current month detail and prior monthly totals; count deliveries rather than fee components. |
| Running balances | Compute from all canonical movements before filtering/paging/compaction; signed green/red display; no invented balances for incomplete or restricted history. |
| Period statement | Explicit visible request, date validation, every period movement, prior opening and period-end closing, currency and preparer; no background refresh or second balance store. |
| Permissions | New resource `report`/`print` default false; intersect movement scope; own-only is limited activity; no actual balances without full balance and movement rights; fresh server check on print. |
| Bulk permissions | Current resource snapshot with per-resource exceptions; preserve existing grants, operations and versioned save; future resources receive no wildcard. |
| Reviews | Separate pending/approved/rejected queues; driver and bank scope, five-row pages, authenticated bound proof and explicit matching; canonical transactional writer with fresh policy checks on every attempt. |
| Financial integrity | Direct bank/POS payment is informational on the courier ledger; fee remains independent; unconfirmed COD stays outside confirmed balance. No extra credit to the courier. |

## Implemented source checkpoint

Backend changes are in the workspace/access modules and new
`operational_workspace_projection.py` and `operational_balance_reviews.py`.
The review service accepts a trusted internal authorization callback so scoped
workspace review can retain its existing transactional evidence, idempotency,
phase and monetary rules. Legacy callers retain their original accountant gate.

Web changes include the compact workspace, resource permissions editor, explicit
statement/print view and scoped receipt-review component. Print is a new
server-authorized read before opening the system print dialog. This does not
claim to prevent operating-system screenshots or printing already visible data.

## Statement design confirmed by the owner

The report uses the balances table: description, for-party, for-us, balance after
movement. A fifth note column appears only if the selected period contains an
original nonempty movement note. Date/time is inside the description cell.
The header names the account type and account/person and explicitly prints
`الفترة من DD/MM/YYYY إلى DD/MM/YYYY`. The footer includes period totals for both
sides, opening and closing (including opening), with signed green/red values and
side labels. Restricted scopes never gain balance information from this design.

## Verification checkpoints

- Fresh backend run after integrating current Production: **230 passed in 35.99 seconds** across workspace,
  statements, access, refunds, receipt-review scope, existing noncash review
  evidence, packed-state preservation and the inherited engine/refresh-budget
  regressions. Synthetic isolated data only.
- Fresh integrated web run: **1080 passed in 65 suites**, including the complete
  operational UI collection and adjacent navigation, shipping, supplier, employee
  and spreadsheet safety contracts.
- The source-bound real-component preview built successfully (101 modules).
- Ten scoped review HTTP tests include exact-amount, bank-bound candidate
  pagination and reviewer metadata. Candidate selection does not import or
  classify anything; the existing writer revalidates original statement bytes.
- Three new real-Mongo acceptance cases are collected for the scoped callback:
  bank/POS exactly-once approval and rollback when the grant is revoked before
  commit. They require a disposable replica set; the preceding source passed
  all three in remote CI with 5496 backend tests. Exact final-head results are
  recorded in the task PR.
- Manual mobile-width inspection verified compact layout and no 320px body
  overflow. Direct-browser history → detail → browser Back → history Back
  returned to the original employee ledger correctly. A 390px statement showed
  all five headers, original note, 1900 opening +200 credits −100 payments =2000
  closing, and the exact selected period. No physical phone acceptance is claimed.
- Independent web/backend and native reviews finished with their confirmed
  findings fixed. Native source `16e5ac699440249fd9d9dd5fc9788a323cb76150`
  (tree `034c98bdbfd1212db5b0436508744cab9301345d`) is remotely verified,
  clean locally and passed Mobile CI run `44414785378`, including TypeScript,
  ESLint and 45 balance groups. The baseline native install has no frozen-lock
  gate; no dependency change was introduced. No APK/OTA was built.
- Main source `add94d64ab41978dc772036f5e6a6deb1a191813` passed Security Gate,
  CodeQL, Release Readiness and Operational Frontend. All 11 real-component
  browser scenarios passed, including statements, original notes, fresh print
  authorization and review/history Back behavior. Screenshot artifact
  `14480500175` has a source manifest matching all 20 preview source files.
  Browser locator/wait repairs retain every assertion. Fresh CI results for
  the final exact-money query patch are recorded in the task PR.
- The final exact-money/detail-date display fix passed 27 affected web tests.
  These results are not a Production-release authorization.
- The final review-candidate patch rejects a legacy numeric amount whose
  float conversion changes its exact decimal value, before query counting or
  pagination. Three HTTP regressions cover a rounded large amount and valid
  numeric integer/fractional legacy amounts; the ten-test scoped suite passed.
  The canonical writer's existing exact-decimal approval check is unchanged.

## Data limits retained deliberately

Running balances reflect the currently approved saved ledger and its business
dates, not an archived view of values before a historical source was corrected.
Incomplete/unreconciled records do not get invented historical balances. A period
before the operational start is refused. Undated monetary refunds remain blocked.
Default legacy workspace notes indistinguishable from generated text are hidden;
other original notes remain separate from descriptive labels.

## Clean source lineage

The earlier branch imported current Production with merge `903e477`. Its intent
bytes equal Production, but v5 correctly rejects a source-A history containing
an intent-changing merge against its first parent. Source history is therefore
rebuilt on a new task branch directly from Production J
`77d824a780df582b5c32f4dda87a959e35e1ed1e`, carrying the aggregate source diff and
all verified tests. The prior branch and Draft PR remain preserved. The release
guard and intent file are unchanged. The new source commit has J as its sole
parent; the merged branch is not an additional parent.

The browser acceptance fix also uses label-bound locators for the report dates
inside the dialog (the Page-only helper cannot be nested in a dialog locator).
Assertions, row counts and permission checks are retained.

## Authorized release integration — 19 November 2026

The clean release source starts directly on current production J
`c3c1cd26d93ab24d6e08003278b9910526055cc7`. Its feature content is the aggregate
33-file diff from `77d824a780df582b5c32f4dda87a959e35e1ed1e` to verified source
`1485a0ae1700b806af2a5cf81a6cc41de3c24134`, without importing the earlier task
branch ancestry. The previous PRs and branches remain intact for recovery.

All 33 transferred files initially matched the verified feature source exactly;
this task STATUS is then updated to record the newly authorized release stage.
There is no changed-path overlap with PR2001. All nine production changes,
including the Ready policy and reviewed release intent, remain byte-identical
to J. The intent SHA256 remains
`b852bc5798708a28b63d306955cb6dc9195d909c5b872c44fed02867f04890c1`.
No guard or release workflow has been changed to relax a gate. The task's
existing CI additions only require the new statement/review/Mongo/browser
contracts; the Ready workflow is retained unchanged.

The recovered feature source already passed all 11 exact-head workflows:
5499 backend tests, 1081 frontend tests in 65 suites, all three real-Mongo scoped
review transaction cases, and 11 balances browser scenarios. This is evidence
for source1485, not a substitute for the new release source's CI. Its browser
artifact is `simple-balances-browser-results`, ID `14480262869`, run
`44418111813`. The source integration adds only the already reviewed Ready
baseline and this record; no balances behavior was reimplemented.

Fresh integration verification: **254 passed in 41.35 seconds**, exit0, no
skips, across the scoped access/workspace/statement/review/refund contracts,
existing noncash evidence and packed-state rules, and the newly inherited
Ready policy. Command (from the release worktree, using an isolated test
interpreter and PDF-dependency path):

```bash
env -u OPERATIONAL_TEST_MONGO_URI \
  PYTHONPATH=<isolated-test-dependencies>:backend:backend/tests \
  PYTHON_DOTENV_DISABLED=1 <test-python> -B -m pytest --noconftest \
  -p no:cacheprovider -q \
  backend/tests/test_operational_balance_access.py \
  backend/tests/test_operational_balance_workspace.py \
  backend/tests/test_operational_balance_reviews.py \
  backend/tests/test_operational_workspace_statements.py \
  backend/tests/test_operational_workspace_refunds.py \
  backend/tests/test_store_delivery_noncash_review.py \
  backend/tests/test_operational_driver_review_packed_state.py \
  backend/tests/test_operational_item_ready_policy.py \
  --asyncio-mode=auto --tb=short
```

`git diff --check` and `node --check scripts/testing/simple_balances/browser.cjs`
also passed. No local browser automation or financial mutation was performed.

The official toolchain bootstrap returned `warm_verified`, Node v22.23.2 and
Yarn v1.22.22. It creates no lease. The local Ready test import required the CI
PDF dependencies that were absent from the shared test environment; they were
installed in an isolated test-dependency directory outside both Git worktrees,
without changing project dependency files or the shared test environment.

## Next safe steps

1. Save and push this clean source checkpoint, create the release source Draft
   PR and record its exact SHA/tree and focused test results in that PR.
2. Complete the required CI on that source. Preserve PR2004 and its branch.
3. Wait for a fresh original shared-host guard result `active:false` from the
   release coordinator before any adapter rehearsal or intent freeze. Another
   conversation owns the current publication; do not clear or reuse its lease.
4. After that handoff, run the unchanged official toolchain, locked install,
   governed clean A/B build and source verification with the exact public
   client origin `https://mezansalla.com`; freeze intent outside the worktree,
   review it, then create an intent-only B. Do not squash/rebase A and B.
5. Require exact-B CI and clean-clone adapter/package/route verification before
   handing the immutable A/B pair to the coordinator for the separately owned
   production merge, shared-host adapter, prepare/prepublish and publication.
6. Production completion still requires newer visible Deployment Succeeded,
   the unchanged three-probe verifier and inactive guard. Live UI acceptance
   uses read-only operations. APK preparation remains owned by the native task.

Canonical continuation remains this STATUS and the release task PR. The
repository issue1006 body and task PRs confirm its 2500-comment limit; do not
replace the crowded historical issue body or publish sensitive runtime data.
