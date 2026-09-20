# P01 write-balance acceptance checkpoint - PR #1113

**Status: exact source, governed package and isolated Preview HTTP acceptance PASS; browser UI retest unverified.** This evidence checkpoint does not approve Production activation. P01 remains IN_PROGRESS and P02 remains BLOCKED_BY_PREVIOUS_PHASE with its exit gate LOCKED.

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
| Final isolated Preview runtime and HTTP/write smoke | **PASS**, 29 HTTP steps, three HTTP409 no-partial-write checks | Exact source A reported by runtime API; browser UI retest not completed |

Earlier source checkpoint evidence and the invalidated Mongo run remain historical in [P01-WRITE-BALANCE-20260920.md](P01-WRITE-BALANCE-20260920.md). The Mongo process interruption on the prior shared Preview host was not treated as a passing test. The final integrated suite used isolated databases and the dedicated test runtime; earlier acceptance databases were not reused for writes.

The 102-test rerun covers the affected settlement/refund/advance writers, shared report eligibility, and their atomic, closed-period, recovery and pause dependencies. The six frontend tests cover the changed cross-provider evidence controls. The nine governed workflows on each commit and clean-clone rehearsal are explicit release requirements. Unchanged Tabby UI and the entire earlier four-gap Preview acceptance were not repeated.

## A-H acceptance matrix

| Requirement | Defect prevented / correction | Actual isolated test evidence | Final Preview |
|---|---|---|---|
| A - settlement receivable | Untagged provider debit cannot authorize settlement | Insufficient qualified balance rejects; actual qualified sale permits one balanced 2-leg settlement | PASS HTTP409 then qualified settlement200 |
| B - bank execution | Legacy debit and mutable account balance cannot fund refund | Eligible10/request40 rejects despite large legacy balance; qualified settlement then permits40, payable75 | PASS HTTP409 then payment200; dated reports115/75/0 |
| C - negative legacy | Negative unqualified balances cannot block valid execution | Legacy provider credits ignored; valid qualified refund posts exactly2 balanced legs | PASS negative bank sentinel ignored |
| D - provider execution | Only chosen provider's qualified funds can fund payment | Legacy target balance rejects; qualified target activity succeeds; documented Tamara-original/Tabby-execution case tested | PASS documented Tabby executor409 to200 |
| E - liability account | Same-case legacy rows cannot alter payable/advance reconciliation | Refund payable, advance cancellation and advance payment ignore unqualified same-account rows | PASS refund same-case sentinel; advance covered by isolated Mongo |
| F - pre-cutover | Correct operation tag alone does not make an old event eligible | Tagged pre-cutover bank debit cannot fund execution | Not repeated; isolated Mongo PASS |
| G - malformed/unsupported source | Reader cannot silently use partial or uncertain balances | Unknown producer or invalid accounting date rejects with `mz2_balance_not_ready`; missing opening approval also rejects | Not repeated; isolated Mongo PASS |
| H - owner isolation | Matching entity IDs do not broaden tenant scope | Other owner's tagged bank rows cannot fund this owner's payment | Not repeated; isolated Mongo PASS |

Rejection tests compare all persisted nonempty collections, including journal entries, drafts, audits and counters. The permanent owner coordination row is provisioned before the baseline; its transaction revision must still roll back. Successful refund approvals add exactly two balanced legs, without revenue/tax duplication, and repeat approval returns the same group without another journal.

Additional actual tests cover:

- Root database, wrong owner and ended session cannot call the write-balance helper.
- A helper read sees its own three uncommitted qualified sale legs; an independent read sees none. Forced abort leaves no persisted delta.
- Pending/failed/cancelled/unknown provider evidence, wrong or malformed amount/date, currency mismatch, synthetic evidence, absent source, fetch error, duplicate evidence and original-provider execution reject without partial effects.
- Canonical provider ID normalization prevents whitespace variants with different document references from creating a second payment. Existing noncanonical pending identities require review.

The regenerated census contains **769 matching source lines**: **524 legacy/unrelated**, **3 P02**, and **242 tests**. The non-test matches span **66 source files**. The six original P01 balance call sites are separately mapped to their replacements. This is a static consumer inventory, not a count of executed tests. See [census report](P01-WRITE-BALANCE-CENSUS-20260920.md) and [complete CSV](P01-WRITE-BALANCE-CENSUS-20260920.csv).

## Actual final Preview HTTP evidence

[Raw acceptance JSON](evidence/write-balance-20260920/preview-http-acceptance.json) records **29 HTTP steps**, three denied HTTP409 requests with identical full-collection before/after hashes, and successful qualified posting and duplicate checks. Requests reached the running Preview backend on loopback8001 with the Preview Host/Origin. The runtime policy returned exact A `e8818bb67d84ccd0487e126a06f5e9c3e09702c7`; source/boundary verification passed. Runtime source path: `/app/.worktrees/p01-write-final-preview-20260920`; isolated database `mz2_write_balance_preview_20260920`, replica `p01writepreview`, port27038.

The browser's public metadata navigation was blocked with `ERR_BLOCKED_BY_CLIENT`. This is actual Preview HTTP acceptance, **not a completed browser accountant-UI retest**. No old acceptance-database hash comparison is claimed: the prior Mongo instance on27018 was unavailable, the prior databases were not accessed, and this run wrote only the new isolated database.

The three no-partial-write denials were: legacy provider receivable attempting to fund settlement; legacy bank/current_balance attempting to fund a refund; and legacy Tabby receivable attempting to fund documented execution of a refund originating with Tamara. Valid qualified sales and settlement subsequently allowed the intended postings. Negative bank and same-case liability legacy sentinels did not change the accepted result.

### Actual journal groups

| Economic event | Accounting date | Journal group | Entries |
|---|---|---|---|
| Original main sale | 2026-08-01 | `b8fd924b-2ff8-4208-8891-223f30c3e602` | Provider receivable115 debit; revenue100 and VAT15 credit |
| Confirmed main refund entitlement | 2026-08-31 23:30 +03:00 | `d6701b2a-f9a9-47db-a273-2581f404fe06` | Revenue100 and VAT15 debit; refund payable115 credit |
| Qualified provider settlement | 2026-09-01 | `fdd84634-0dbf-4672-a2fe-0f07aa6b3c93` | Bank115 debit; Tamara receivable115 credit |
| Main refund payment40 | 2026-09-02 10:00 +03:00 | `195cf836-4fc9-4d35-8f5e-c778bd7c8eb5` | Refund payable40 debit; bank40 credit |
| Main refund payment75 | 2026-09-05 10:00 +03:00 | `7f0bb85b-0023-48ce-83cc-2a9e2fc6b7cc` | Refund payable75 debit; bank75 credit |
| Separate documented Tabby execution40 | 2026-09-02 10:00 +03:00 | `0d906071-4098-4fe8-bdff-6fbd989a0140` | Separate-case payable40 debit; Tabby receivable40 credit |

Both main payments and the separate-provider payment contain only two balanced legs; none repeats revenue or tax. Duplicate approvals returned the same respective groups.

### Historical report readings and their scope

These HTTP readings were captured **after both main payments and before creation of the independent second cross-provider case**:

| Report date, end of day Riyadh | Main-scenario refund payable | Qualified bank |
|---|---:|---:|
| 2026-08-31 | 115 | 10 |
| 2026-09-02 | 75 | 85 |
| 2026-09-05 | 0 | 10 |

The August report remained115 after September's two payments. Opening bank10 plus September settlement115 less payments40 and75 explains the bank amounts. The later independent case adds another entitlement115 under the same owner, group `e937770f-2368-422b-8672-d36495ce9f57`, then pays40 through Tabby. Therefore the table is the recorded main-scenario checkpoint, **not a claim that the final combined database's aggregate August liability is115**.

The final isolated snapshot contains **35 ledger rows / 15 groups**, including deliberately retained synthetic legacy sentinels. Its hash is `ba5df09c73b571cfb1a49a5e1882decddf305c905b2a14442c020a4236403f72`. These counts are not 15 newly approved MZ2 events; the raw JSON distinguishes seeded sentinels from approved journal groups. Test tax15 and cutover2020 are synthetic only.

Before activation, filesystem pressure was resolved by relocating the task's own dependency cache to `/opt`, verifying bytes, modes and links for69,859 entries and retaining a symlink at the original path. Source files and prior acceptance data were unchanged by that operation. The task-owned Preview reservation was released after source/boundary verification.

## Independent release gates and owner decisions

Production database readiness remains **UNVERIFIED**: confirmed Production environment/provider connection, transaction support, required unique indexes, backup recency/coverage, and successful restore into a separately authorized isolated environment. A backup file's presence is not restore evidence. Preview and `/app` do not establish Production database readiness. The required access is the Salla Analytics Production deployment/database administration page identifying its actual database provider and cluster, plus that provider's backup/restore administration page; a confirmed service/cluster identity is still needed.

Owner decisions remain explicit: tax rate and effective date; cutover date/time/timezone; named activation owner and accountant permissions; approved opening evidence including zero declarations; activation sequence and write-stop/resume ownership. Existing pause and closed-period controls, prior Preview acceptance and fee/refund separation remain preserved. P01 is not closed, P02 is not opened. Production merge or deployment requires independent authorization.
