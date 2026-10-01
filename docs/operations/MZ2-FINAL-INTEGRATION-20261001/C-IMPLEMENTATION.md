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

## WIP checkpoint

Implemented only the Native retained-source authority and optional internal
evidence-port injection. Existing default 503 is unchanged. Preserved contract,
isolation and calculator tests: 70 passed + 547 subtests. No Mongo service was
started for this slice. Native routes/writer/UI integration and real Mongo tests
are still pending. This is a recoverable WIP, not release readiness.

Other C gates remain open: complete H2 review history, physical cash
reconciliation, Smoke B proof, full 16-stage business UAT. Each requires its own
source/contract review and actual required evidence; no fabricated PASS.

Production financial writes = 0. Merge = NO. Deploy = NO. Opening Post = NO.
Activation = NO. Write-control = UNCHANGED. No production connection or lease.
