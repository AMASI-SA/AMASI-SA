# P01 release review — preparation only

Synthetic Preview acceptance is complete by owner decision. P01 remains IN_PROGRESS and P02 remains locked. No Production merge, publish, configuration change, database migration or financial write was performed in this review.

## Immutable target and supersession

- Draft release-review PR: https://github.com/AMASI-SA/AMASI-SA/pull/1099
- Source A: `e257c963a986feb6e0bb057361e35909f5b13df6`.
- Branch: `codex/p01-release-candidate-20260919`.
- Reviewed comparison base J: `635e181f7520354029efbdc8e89d76a94de77ff2`.
- Combined tree: `453855007e867005f4ea8fd9a20615c9e03de928`.
- Accepted Preview application source: `080953eb1669987b53d2474a8f52c42e951e2178`; its successor `ce113f89d7ddc08dfa0738b66b519972c5c2fb05` changes documentation only. The candidate adds the current Production base without changing its Qoyod fixes.

| PR | Release treatment |
| --- | --- |
| #1082 | Compatible payout-fee semantics integrated; do not merge its old-main parser wholesale over the newer refund parser. |
| #1083 | Bounded fixture-loop correction integrated. |
| #1085 | Preview recovery tooling excluded; never merge into this release. |
| #1086 | Preview single-sale pilot excluded; superseded operationally by the supported recognition workflow. |
| #1087 | Source-hash conflict guard integrated; later atomic upload improvements retained. |
| #1091 | Integrated, including original attachments, manual tax and Mongo atomic recovery. |
| #1097 | Integrated, including the final daily-only refund decision and unified register. |
| #1099 | One consolidated review candidate replacing separate release integration of the above application PRs. |

No previous PR was merged or closed. Recommended eventual order: repair the remaining test gate on this candidate; approve final source A; obtain a new reproducible artifact and freeze intent-only B; review/rehearse B; merge the complete A/B package once. Do not merge A alone, independently merge the superseded PRs, or squash/rebase A/B after identity freeze.

The initial review-only merge commit `90f844503326271da502b5b5fb770c4f1ab739d4` had exactly the same tree but failed the source-transition check because its second-parent history made the inherited intent appear modified. It was replaced on the newly created review branch with single-parent A above using an exact force-with-lease. Production and the original task branch were not rewritten. The corrected source transition passed CI.

## Decision-to-evidence review

| Decision | Source and evidence | Assessment |
| --- | --- | --- |
| Manual sales VAT, effective versions and audit; missing differs from zero | accounting_sales_tax.py, accounting_sales_tax_service.py, permission-checked routes; real Mongo CI | Retained; source VAT is review evidence only. |
| Original tax allocation remains immutable; full refund consumes remaining allocation | refund_split and approved daily payment calculation; real Mongo CI | Retained; provider commission/settlement VAT stays separate. |
| Qualified receivable recognition, source/owner/currency/date checks, prior journal detection | accounting_recognition_evidence.py and accounting_receivable_service.py | Retained; canonical payment identity is required, not invented. |
| Webhook and provider sync create/reconcile nonfinancial drafts only for managed owners | accounting_refund_drafts.py, accounting_order_refunds.py, ledger_bridge.py | Retained; daily approval is the new refund posting entry point. Legacy-owner behavior remains outside activation scope. |
| Original provider separate from actual executor; bank refunds do not reduce provider receivable | accounting_customer_refunds.py and tests/test_mz2_daily_refunds.py | Retained; channel conflicts require review. |
| Explicit refund and receipt matching, no automatic ambiguous assignment | daily UI case selector and SettlementRefundLink; receipt service | Retained; repeated source identities cannot generate another financial effect. |
| Receipt and statement intake do not post; posting waits for review and matching | receipt service, settlement lifecycle, atomic owner boundary | Retained for new v2 receipt workflow. Historical records are preserved. |
| One register, owner-scoped journal and original XLSX | register, source-file routes and frontend controls | Retained; previous Chromium acceptance and immutable file hash evidence remain applicable. |
| Crash recovery and concurrency | accounting_atomic.py, real Mongo CI | Snapshot transaction + majority/journaled commit; standalone fails closed; no partial group becomes visible. |
| No Preview data/runtime transfer | candidate path/content inspection, clean Git tree, no data-copy command | No new Preview adapters, account fixtures, credentials, acceptance dumps, Mongo data directories or restoration scripts in the release diff. Existing unit-test fixtures are test code, not a live-data migration. |

## Fresh verification and findings

Release readiness: https://github.com/AMASI-SA/AMASI-SA/actions/runs/35466226870

- PASS: Backend compilation and release/identity/adapter unit suites.
- PASS: source A classification; governed Frontend A/B production builds, exact artifact/reproducibility verification, candidate intent artifact creation, clean worktree proof.
- NOT RUN: reviewed-B clean-clone adapter rehearsal; intentionally skipped for unreviewed source A. CI green here is not deployment approval.

Accounting CI: https://github.com/AMASI-SA/AMASI-SA/actions/runs/35466226821

- PASS: 47 focused tests (11 tax/policy, 12 recognition, 7 atomic recovery, 12 receipt, 5 daily refund), including real isolated Mongo recognition-to-ledger/settlement, concurrent ingress and process death/restart.
- PASS: 39 accounting Frontend tests across eight suites and full ordinary Frontend build.
- FAIL: backend contract, one outdated test double. `test_post_snapshots_bank_and_uses_one_balanced_group` calls the real new refund-review read, but `_Db` only supplies accounts/general_ledger and lacks settlement_entries. Both the initial identical-tree run and final A run report 48 passed, one failed. This is a test-fixture compatibility defect, not evidence that the real Mongo transaction failed. It still blocks an all-green release gate. No test or production implementation was edited during this review.
- Minimal remediation: supply realistic source-row/link collections for this existing test, retain the refund-review guard and balanced-journal assertions, then rerun the contract and final-source checks. A changed source SHA requires a new build/intent.

An additional run against the exported exact combined tree executed 52 backend tests: 50 passed, two infrastructure errors. The local standalone fixture on 27019 was unavailable; the missing-identity case hit a Mongo connection interruption and passed its justified single-test rerun. The independent GitHub real-Mongo job passed the standalone fail-closed check. These local fixture databases have unique test names; no new business acceptance records were created. This is not a full-repository pytest claim.

`git diff --check` also reports inherited trailing/EOF whitespace in the consolidated diff; formatting was not changed under this review scope.

## Database, activation and source requirements

1. Confirm the actual Production cluster and database from its runtime configuration without exposing secrets. Preview loopback Mongo is not evidence of Production topology. Reject any configuration pointing to `p01acceptance`, test database names, the Preview URI, or copied acceptance data.
2. The implemented boundary specifically requires `hello.setName` and logical sessions: a writable replica set with transaction support. A standalone or a mongos-only topology does not satisfy this code check. Require a supported server/driver combination, healthy writable primary, snapshot reads and majority+journaled writes. Do not silently fall back or automatically convert Production Mongo.
3. Snapshot the affected Production collections, settings, indexes and release identity before activation; verify the backup is restorable in a separate environment. Capture exact journal/source IDs and balances privately. Never restore the Preview database into Production.
4. Inventory/create required collections under separately authorized setup where server/privileges demand it: mz2_atomic_owners, mz2_recognition_events, mz2_sales_tax_policies, mz2_customer_refunds, mz2_customer_refund_payments, mz2_order_refund_reviews, mz2_statement_refund_links, mz2_bank_receipts, mz2_bank_receipt_requests and accounting_source_files; retain existing ledger/audit/source collections. Natural `_id` uniqueness carries the new identities and the owner serialization row. Do not copy fixture rows.
5. Read/verify canonical payment uniqueness `(user_id, provider, provider_id)`, refund uniqueness `(user_id, provider, provider_refund_id)`, provider-bank uniqueness, settlement idempotency uniqueness and the partial bank-match unique index. Inspect duplicate/conflicting legacy rows before any index change. Index helpers catch errors, so a successful page load is not index-completion evidence. Do not delete financial rows to repair duplicates.
6. New source blobs are stored in Mongo, owner-scoped, hash-checked and bounded to 10 MB per workbook. Preserve their content in backups. Old missing binaries stay unavailable until the exact original is reuploaded; do not fabricate them.
7. No blanket historical backfill or opening balance migration is part of this release. Existing posted groups, tax splits and protected drafts must retain identity and amounts. Resolve pre-existing postings requiring review before any operator recognition.

## Manual tax, cutoff and production authorization

The owner's actual tax rate, effective instant and accounting cutoff are not inferred from synthetic fixtures or Salla tax. Before activation, obtain explicit approval of:

- the real owner/store and providers in scope;
- manual rate, including an explicitly chosen zero if applicable, effective timestamp/timezone and audit reason;
- `settings.mezan2_financial_cutover.operation_id = MZ2-FIN-CUTOVER-001` and the real `cutover_at`;
- existing journal inventory and handling of pre-cutoff/reclassified orders;
- provider-bank bindings and separate create/approve/view permissions.

Recognition uses the later verified capture/delivery instant and refuses pre-cutoff evidence. Sales tax selection uses that recognition instant, never settlement date. Saving tax policy or setting the operation activates managed-owner bridge behavior; therefore coordinate configuration with the worker/webhook/accountant write boundary, not as a harmless cosmetic setting. Do not delete the policy/cutover to pause writes: managed_owner may become false and restore legacy bridge behavior.

## Exact release sequence requiring later approval

1. Resolve the failed test gate; inspect current Production head again. If it moved, integrate only on a review branch, review the changed scope and rerun affected checks. Freeze a new final source A after all source/test changes.
2. Produce clean governed builds using Node 22.23.2/Yarn 1.22.22 and the approved public client origin. Review the artifact/manifest and create B changing only release/release-intent-v5.json. Do not reuse this review's intent if A changes. Run the clean-clone Node-20-dispatch adapter rehearsal on B; it must prove isolated package boundaries and the build-meta route.
3. Obtain explicit authorization for Production backup/index or topology work, settings/cutoff/permissions activation, and the defined write pause/resume window. Topology conversion, if needed, is a separately reviewed operation.
4. Obtain authorization to merge the reviewed A/B package into hotfix/prod-snap-meta-final without rewriting its frozen identities. Merge is not publication. Old superseded PRs can then be marked as such; Preview tooling remains excluded.
5. Inspect /app and release guard status; do not disturb another lease or unrelated files. Synchronize exact authorized remote source, preserving unrelated files. No runtime overlay/file-by-file copy or Preview .env may substitute for the release package.
6. Only after the reviewed rehearsal succeeds, run `python scripts/production_release_guard.py prepare --actor "MZ2-FIN-CUTOVER-001"`, then `python scripts/production_release_guard.py prepublish` immediately before the separately authorized Re-publish action. Use the current v5 guard; close old leases only by their exact owner with their originating guard.
7. Require explicit newer Deployment Succeeded plus `python scripts/production_release_guard.py verify --url https://mezansalla.com`. All three probes must agree on frozen identity/source and critical hashes, frontend artifact/proof and real JSON build-meta bytes. Confirm the lease closes. Do not approve accounting writes on a partially verified deployment.
8. Perform authorized read-only Production smoke checks and source/journal continuity checks. Enable actual accounting operations only under their approved scope; no synthetic journals are part of Production smoke testing. Update P01 evidence; keep P02 locked until its own gate decision.

## Rollback and incident boundary

- Before any new accounting write: stop activation, preserve evidence, and deploy an explicitly reviewed last-known-good release through the same current guard. Do not use reset --hard or blindly revert frozen intent. Abort only the exact owned failed lease with its expected SHA/release ID.
- After new accounting writes: pause all affected accountant/API/worker ingress first, determine transaction outcome by canonical event/payment/settlement IDs, and preserve committed balanced groups. Do not delete a group, repost it, clear idempotency rows or reverse it merely to roll back code. Response loss is resolved by reading and retrying the same identity through the supported transaction path.
- An old backend may resume automatic legacy refunds or mishandle new tax/receipt state. Code rollback alone is therefore unsafe without compatibility review and a controlled write pause. Prefer a forward fix retaining data; any accounting correction/reversal needs separate explicit authorization.
- Restore a Production backup only as an independently approved disaster-recovery operation after identifying all later writes and proving no valid transactions will be lost. Never substitute p01acceptance or selectively restore ledger legs without their event/status/audit records.

## Verdict and browser issue

Preview synthetic acceptance remains accepted. Release readiness is PARTIAL/BLOCKED by the failed backend contract and the not-yet-reviewed intent B/rehearsal/Production preflight. No new application regression was established by the fixture error, but the required checks are not all green.

Browser acceptance was performed in Chromium. The blank login in the Codex browser tab remains unresolved and separate from application acceptance; no claim is made that this review fixes that tab or Preview host uptime.
