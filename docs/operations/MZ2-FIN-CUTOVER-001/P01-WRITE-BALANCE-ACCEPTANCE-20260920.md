# P01 write-balance acceptance checkpoint â€” PR #1113

**Status: integrated source verified; final Preview acceptance PENDING.** This evidence checkpoint does not approve Production activation. P01 remains IN_PROGRESS and P02 remains BLOCKED_BY_PREVIOUS_PHASE with its exit gate LOCKED.

## Frozen candidate and preserved work

| Reference | Exact identity |
|---|---|
| PR | [#1113](https://github.com/AMASI-SA/AMASI-SA/pull/1113) |
| Fresh reviewed Production base J | `6365a042dfcb125e81e5e198ea1ff1537373ce51` |
| Source A | `e8818bb67d84ccd0487e126a06f5e9c3e09702c7` |
| Direct intent-only B | `c66dec17f9defbd50518b90561ca1a350a631608` |
| Runtime release identity | `rg5-9893ba2ded2e533d3cdbadd74dfc39e41ebeba83c6f6db9cfb50fcf0d0a78816` |
| Evidence branch | `codex/p01-write-balance-evidence-20260920` |

B changes only `release/release-intent-v5.json` after A. This separate evidence branch is not a replacement runtime/package source. The frozen #1109 and #1110 A/B pairs and prior Preview evidence remain preserved. #1113 carries the accepted accounting/report work plus the transactional write-balance correction onto fresh J; earlier candidates are not sequential deployment steps.

The 13-file independent Production delta had no overlapping paths with the source transfer. Integration had no conflicts; byte comparisons preserved those Production changes. No merge, Production publish, financial write, index change or configuration change was performed by this task.

## Defect and correction

The prior #1110 A reproduction approved a bank refund of 40 with only 10 eligible MZ2 bank units because an untagged legacy debit inflated the shared balance: actual HTTP 200 instead of required 409. This reproduction used a disposable synthetic database; its group was `749fbcf9-aa0c-41ee-8b79-fd44f08c6ddd`.

`read_mz2_write_balances` now consumes the accepted MZ2 eligibility reader inside the same active owner transaction. Settlement receivables, refund execution and payable reconciliation, and advance cancellation/payment use it. Approved opening/zero evidence and qualified post-cutover journals are required. No legacy `current_balance`, unqualified ledger row, wrong-owner row or pre-cutover event authorizes these writes. Shared legacy accounting primitives retain their existing behavior.

Documented execution by a different provider is supported as expressly authorized: uploaded provider document, execution reference and canonical refund identity bind the execution to its original case. Contradictory existing evidence, confirmed original-provider execution, mixed approved channels and repeated execution identities reject. This does not reverse commissions or tax automatically.

## Fresh verification on frozen A

| Evidence | Observed result | Limit |
|---|---|---|
| Integrated real Mongo suite on exact A | **102 PASS**, 107.90 seconds | Dedicated isolated test databases; not Production capability evidence |
| Focused settlement unit tests | **9 PASS** | Focused logic checks |
| Economic-date unit tests | **3 PASS** | Date contract checks |
| Affected refund/advance frontend tests | **6 PASS** | Component tests; not final browser smoke |
| Source A governed CI | **9/9 green** | Applies to A above |
| Intent B governed CI | **9/9 green** | Applies to B above |
| Final clean-clone B package rehearsal | **PASS**, run `35526499584`, job `106119598149` | Four raw proof files downloaded and verified from artifact `10610081405` |
| Final isolated Preview runtime and UI/write smoke | **PENDING** | No current runtime SHA, new Preview journal IDs or live report values claimed yet |

Earlier source checkpoint evidence and the invalidated Mongo run remain historical in [P01-WRITE-BALANCE-20260920.md](P01-WRITE-BALANCE-20260920.md). The Mongo process interruption on the prior shared Preview host was not treated as a passing test. The final integrated suite used isolated databases and the dedicated test runtime; earlier acceptance databases were not reused for writes.

## Aâ€“H acceptance matrix

| Requirement | Defect prevented / correction | Actual isolated test evidence | Final Preview |
|---|---|---|---|
| A â€” settlement receivable | Untagged provider debit cannot authorize settlement | Insufficient qualified balance rejects; actual qualified sale permits one balanced 2-leg settlement | PENDING |
| B â€” bank execution | Legacy debit and mutable account balance cannot fund refund | Eligible10/request40 rejects despite large legacy balance; qualified settlement then permits40, payable75 | PENDING |
| C â€” negative legacy | Negative unqualified balances cannot block valid execution | Legacy provider credits ignored; valid qualified refund posts exactly2 balanced legs | PENDING |
| D â€” provider execution | Only chosen provider's qualified funds can fund payment | Legacy target balance rejects; qualified target activity succeeds; documented Tamara-original/Tabby-execution case tested | PENDING |
| E â€” liability account | Same-case legacy rows cannot alter payable/advance reconciliation | Refund payable, advance cancellation and advance payment ignore unqualified same-account rows | PENDING |
| F â€” pre-cutover | Correct operation tag alone does not make an old event eligible | Tagged pre-cutover bank debit cannot fund execution | PENDING |
| G â€” malformed/unsupported source | Reader cannot silently use partial or uncertain balances | Unknown producer or invalid accounting date rejects with `mz2_balance_not_ready`; missing opening approval also rejects | PENDING |
| H â€” owner isolation | Matching entity IDs do not broaden tenant scope | Other owner's tagged bank rows cannot fund this owner's payment | PENDING |

Rejection tests compare all persisted nonempty collections, including journal entries, drafts, audits and counters. The permanent owner coordination row is provisioned before the baseline; its transaction revision must still roll back. Successful refund approvals add exactly two balanced legs, without revenue/tax duplication, and repeat approval returns the same group without another journal.

Additional actual tests cover:

- Root database, wrong owner and ended session cannot call the write-balance helper.
- A helper read sees its own three uncommitted qualified sale legs; an independent read sees none. Forced abort leaves no persisted delta.
- Pending/failed/cancelled/unknown provider evidence, wrong or malformed amount/date, currency mismatch, synthetic evidence, absent source, fetch error, duplicate evidence and original-provider execution reject without partial effects.
- Canonical provider ID normalization prevents whitespace variants with different document references from creating a second payment. Existing noncanonical pending identities require review.

The regenerated census contains **769 matching source lines**: **524 legacy/unrelated**, **3 P02**, and **242 tests**. The non-test matches span **66 source files**. The six original P01 balance call sites are separately mapped to their replacements. This is a static consumer inventory, not a count of executed tests. See [census report](P01-WRITE-BALANCE-CENSUS-20260920.md) and [complete CSV](P01-WRITE-BALANCE-CENSUS-20260920.csv).

## Final Preview evidence to append

The exact A source has been prepared at `/app/.worktrees/p01-write-final-preview-20260920` but **has not yet been activated**. New isolated database: `mz2_write_balance_preview_20260920`, replica `p01writepreview`, loopback port27038. These prepared identities are not served-runtime proof.

A full Preview filesystem blocked setup. The task's existing runtime dependency cache was relocated to `/opt` after verification of bytes, modes and links for **69,859 entries**, with a symlink retained at the original cache path. Source files and prior acceptance data were unchanged. This cache operation is not a candidate source change or Production deployment.

Pending coordinator verification: exact served source SHA and runtime identity after activation, preserved prior-acceptance hashes, actual journal group IDs, rejected request statuses, before/after qualified report balances and duplicate outcomes. No test value is a Production setting. This section must remain PENDING until observed evidence is supplied.

## Independent release gates and owner decisions

Production database readiness remains **UNVERIFIED**: confirmed Production environment/provider connection, transaction support, required unique indexes, backup recency/coverage, and successful restore into a separately authorized isolated environment. A backup file's presence is not restore evidence. Preview and `/app` do not establish Production database readiness. The required access is the Salla Analytics Production deployment/database administration page identifying its actual database provider and cluster, plus that provider's backup/restore administration page; a confirmed service/cluster identity is still needed.

Owner decisions remain explicit: tax rate and effective date; cutover date/time/timezone; named activation owner and accountant permissions; approved opening evidence including zero declarations; activation sequence and write-stop/resume ownership. Existing pause and closed-period controls, prior Preview acceptance and fee/refund separation remain preserved. P01 is not closed, P02 is not opened. Production merge or deployment requires independent authorization.
