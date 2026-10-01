# Authorized C closure — implementation checkpoint

The user's explicit C implementation authorization supersedes the earlier stop
at C. Execution starts with `rich_shipping_approval`; no C is yet closed.

Frozen reviewed integration B: `9e884f697c03e52bb98513f1cea141b0d31261aa`
(tree `6212cde4f47bfda536808bb0aba1a0f7c4e54255`). Its source A is
`2cce72f04a9f41e4e732ac7495c24ab3ca577f31`; their only content difference is
`release/release-intent-v5.json`. The successor source branch starts at A to
preserve the v5 two-commit contract and all prior runtime fixes. PR #1236 stays
Draft and unchanged as the frozen checkpoint. Production/base/rollback remains
`901568ccaaf510dc1f84d9c28f38d368d07dc64d`.

## C1 contract and minimum change

- Reuse `ShippingContractInput/Version`, `require_shipping_contract_charges`,
  and the reviewed Decimal tier/VAT calculator. Carrier payment mode is existing
  metadata; it does not introduce a carrier prepayment wallet or change accrual.
- Reuse the Native courier registry and owner setup CAS for draft, explicit
  accountant evidence review, immutable approval and evidence revocation.
- Review real retained `accounting_source_files` bytes. An upload or text
  reference alone is not approved evidence. Review needs the explicit existing
  `accounting.shipping.contracts.review` permission, even for owners.
- Supply this authority only to the existing evidence port's Native consumer.
  The dormant Legacy candidate's 423 and default evidence-service 503 remain.
- Extend existing Native `accrue_fee` pricing and legs: shipping expense and
  shipping input VAT, COD commission expense and commission input VAT, carrier
  payable. Keep COD recognition, recognition time, settlements, idempotency key,
  owner transaction, 423 and write-control unchanged. No new financial writer.
- Bind the existing rich Stage 7 editor to Native metadata endpoints; preserve
  opening facts as a separate contract and preserve flat-rate behavior.

## Required proof before C1 closure

Real replica-set HTTP/ledger tests: exact tier boundaries and separate VAT,
non-COD, prepaid/postpaid metadata, missing/ambiguous identity and evidence,
explicit reviewer permission, revocation/tampering, setup CAS, repeated and
concurrent posting, atomic rollback, owner isolation, paused-write denial,
retained old candidate guards, zero Legacy financial reads/writes. Frontend
tests must exercise draft/review/approve errors and exact canonical transport.
CI and read-back are required on the committed source. Isolated evidence does
not by itself close Smoke B or full business UAT.

## Backend checkpoint

Native metadata draft/review/revocation/approval routes now use owner setup CAS.
The explicit reviewer can download the exact hash-verified retained original.
Approved rich terms feed the existing Native fee writer and same transaction;
retention links reference the actual journal group, or the contract for zero-cost
events without journals. Replays return the original result before repricing.
Readiness validates the same current rich proof as posting. The old candidate's
423 and default evidence 503 remain unchanged.

Root integrated check: **148 passed + 529 subtests**, 71.75s, exit 0. Includes
29 new real Mongo HTTP cases, original Native shipping, contracts/isolation,
current-carrier regressions and readiness metadata. Earlier adjacent check:
116 passed + 543 subtests, 32.60s. No assertions were removed. New suite is
included in the existing Track F CI workflow.

Environment: task-owned Mongo 8.0.12 replica `mz2c`, loopback 27130, unique
synthetic database per fixture; Python 3.13.15. These are integration tests,
not full business UAT or Smoke B. Independent actual concurrency probe verified
revocation waits for an active fee transaction, then blocks the next fee. Its
readiness finding was corrected and independently rechecked.

Evidence: `evidence/c1/backend-results.xml`, `evidence/c1/independent-review.md`.
Stage 7 now connects the existing editor to Native draft/review/approval, shows
readable persisted terms and audit identities, and requires an authenticated
original download before explicit evidence review. Draft/approval source and
purpose are selected explicitly, without copying balances or bank into terms.
Uncertain requests keep their original request ID and payload for retry; normal
navigation is blocked until resolved. A forced reload requires inspecting server
state; no in-memory request is represented as persisted.

Root frontend verification: **5 suites / 53 tests PASS**, 4.53s, Node 22.23.2.
Includes conversion, exact fraction/Riyadh instant, canonical choices, explicit
permissions, real transport URLs, failed download, retry, CAS refresh and readable
saved terms. Evidence: `evidence/c1/frontend-results.json`.

Backend checkpoint `cc84b44bfb983a1a717ba1bd50074df64d193bc0` was read back from
GitHub; 37/39 workflows passed with CodeQL and A+B still running at that snapshot.
The combined UI/source commit needs its own CI and browser evidence. C1 final
closure remains pending; full business UAT and Smoke B remain unclaimed.

The user's subsequent environment decision allows Acceptance/Preview Smoke B
only if its actual harness contract supports that environment. Source inspection
currently finds the Track A API contract's Production proof requirement and no
executable Smoke B proof consumer. This is an unresolved environment/acceptance
contract gap, not permission to substitute isolated assertions for execution.

Other C gates remain open: complete H2 review history, physical cash
reconciliation, Smoke B proof, full 16-stage business UAT. Each requires its own
source/contract review and actual required evidence; no fabricated PASS.

Production financial writes = 0. Merge = NO. Deploy = NO. Opening Post = NO.
Activation = NO. Write-control = UNCHANGED. No production connection or lease.


## C1 connected browser milestone

Source f3be643522d8d3709a0508a9d3aba96fdbab727a: GitHub 39/39 workflows
PASS, including Security, CodeQL, G47, Track F and A+B. The added mobile fix
and opt-in harness require their own exact-head CI after this checkpoint.

Fresh actual browser/HTTP/FastAPI/replica-set proof: C1 5/5 PASS; unchanged
original onboarding browser scenario 23/23 PASS. Root inspected the mobile
screenshot and reran affected UI suites: 43 tests / 3 suites PASS. The original
mobile overflow failure was corrected by wrapping full evidence IDs; neither
IDs nor assertions were removed. See evidence/c1/browser for request traces,
source hashes, screenshots and cleanup. Synthetic fixture DBs were removed.
Every database collection except permitted setup/session metadata remained
unchanged. No financial writer, opening or activation was called by the browser.

C1 code and connected source acceptance are implemented and tested. This is
not a replacement for complete business UAT or Smoke B. C2 native read-only
history is being implemented separately; C3 requires a factual opening-cash
source and exact remittance binding, with no inference from COD. C4/C5 remain
unproven. Release Ready NO; Production writes 0; all production locks remain.


## C2 native decision-history slice and adjacent chronology correction

Required contract: immutable owner/review/revision decisions, explicit Native
coverage, verified journal proof and separate POS/bank effects. Reused the
existing driver-payment writer's sealed EVENTS and V2 journal/audit verifier.
Added a read-only bounded GET, exact owner/filter-bound keyset cursor, persisted
permission checks, gap reporting and H2 presentation. No writer added; current
pending queue remains separate. Later reversals are verified and labelled, not
used to erase the original decision. Reads remain available while paused and
leave every DB document unchanged. A missing prior revision is disclosed,
including when the current operational review is pending after resubmission.
No pre-V2 history is invented or imported.

Baseline GET404; independently reproduced defects (a resealed rejection hiding
an existing journal, and missing prior revision hidden by current queue state)
now fail closed or disclose exact missing review/revision. Agent30 realMongo
tests PASS. Root combined fresh C2 + chronology + manualPOS + richshipping:
94 PASS in86.98s; affected UI43 tests/3 suites PASS in2.752s. Evidence: evidence/c2.
New suites are included in Track F CI. Actual C2 browser execution remains next.

The independent chronology regression was 7 FAIL/4 PASS before repair, 11 PASS
after. A backdated cash receipt must have dated cash authority, not be funded by
earlier noncash/opening COD. Only the existing settle() cash guard changed;
accounting timing, total balance, fee separation, 423 and atomicity preserved.
This correction does not create the missing C3 physical-cash fact/source.

C3: see C3-PHYSICAL-CASH-SOURCE-GAP.md; factual opening custody and exact old
operational remittance links are not provided by COD balances. Source/attestation
decision remains pending. C4/C5: see C4-C5-ACCEPTANCE-CONTRACT.md. The historical
Smoke B safe blocked HTTP probe DOES exist; Production-specific acceptance and
readiness proof consumption are not equivalent to isolated Preview test success.
No C3 source, Smoke B PASS consumer or business acceptance contract is invented.
Production financial writes0; Merge/Deploy/OpeningPost/Activation NO; controls
unchanged. Release Ready remains NO.
