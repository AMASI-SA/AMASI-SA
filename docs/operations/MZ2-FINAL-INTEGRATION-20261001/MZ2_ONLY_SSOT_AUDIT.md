# MZ2-only SSOT audit — NOT PASS

Current wiring-only completion: [WIRING-COMPLETION.md](WIRING-COMPLETION.md), source `9d0c41b1d0d6f5e51c337dc51e3dddea1e31a25c`. Shipping bank connectivity metadata/UI now matches its existing resolver while actual missing-identity409, pause423 and proof503 remain. [REMAINING-BLOCKERS.md](REMAINING-BLOCKERS.md) is the current11-group financial register, including actual native-book expense423 and the separate missing movement adapter for the existing C1 supplier writer. SSOT remains **NOT_PASS**.

Latest wiring-only follow-up: [WIRING-VERIFICATION.md](WIRING-VERIFICATION.md) supersedes earlier evidence limits where explicitly stated. Driver delivery now reaches its existing native observer; bank selection/submission/resubmission use canonical Track A identity. Advertising resolves canonical bank identity while retaining its evidence/posting barrier. A new real native-book payroll probe reaches the actual financial services and proves423 with unchanged persisted documents. General non-COD/provider sales remain distinct from the delivered native COD producer. Audit remains **NOT_PASS**.

Audit date: 2026-10-01. Runtime source checkpoint `fb688621c99cdf413604f514beffa274e58d73ea` (tree `5e2dc70ef0b81b7efabf1ffc6b3132cc7ce2a1aa`). The only change from the inspected runtime `257ef6eb` is a package-qualified test fixture import. Production base/rollback reference: `5a7b44b71c6c9974aba358493b3267a47d6e6314`. This is a reference, not an instruction or proof of a new production deployment.

## Existing native paths verified

| Surface | Source and preserved authority | Evidence and limit |
| --- | --- | --- |
| Shipping/COD/fees | `accounting_shipping_native._post` uses `post_journal_v2`; owner transaction, fresh permissions, writer state, P02, verified opening, period, sealed source and replay gates remain. Bank port delegates to Track A using the same bound database. | Native P02 has 13 passing cases and a Legacy-access listener. COD150 -> collect130 leaves20 COD; paying20 fees only clears the fee payable; collect20 clears COD. Cash cannot settle pending noncash review; no repeated revenue. |
| Supplier payment | Existing writer uses `post_journal_v2`; production router now supplies canonical Track A bank/cash port. Supplier V2 identity is enforced at sink before sequence allocation. | Real production-router test: invoice1000, partial400 leaves payable600; advance200 remains separate; replay does not duplicate; legacy/missing bank IDs reject; zero `general_ledger` writes. Full supplier CI selection142 passed locally. |
| Supplier invoice/G47 | `supplier_native_invoice_v2` uses the native writer with caller transaction, fresh permission, exact identities/mappings, verified opening and immutable evidence. | Canonical supplier fixture restored. Negative case leaves legacy links intact but removes V2 supplier:409 and zero financial/stock/occupancy effects. Real-Mongo G47 results recorded in verification summary. |
| Advertising spend | `accounting_advertising_bridge.post_spend` uses native writer with source binding/revision, FX/expense evidence, opening, period and transaction gates. | Existing native coverage; bank funding is a separate BLOCKER, not a working consumer merely because spend posts. |
| Employee setup | Restricted employee setup transaction writes canonical employee/contract metadata. | Setup identity is V2; payroll financial services still use Legacy sink and are BLOCKED after native cutover. |
| Onboarding setup | Native fee policies, prepaid selections, typed facts and external persons are metadata operations with fresh actor permission. Restored existing UI hooks and six API exports; Track D draft API preserved. |108 focused frontend tests pass.12 browser scenarios use real HTTP and synthetic Mongo, including explicit policy selection, preview/review, CAS, evidence,16-stage desktop/mobile navigation. Non-session fingerprint unchanged throughout UI execution. |
| Native reports | Verified journal metadata and exact canonical entities; `read_verified_journal_metadata_v2` preserved. Exact existing `store_delivery` expense now classified. | Native report tests in193-test combined run; no generic fallback or unknown-identity acceptance added. Advance/payable and COD/fee balances remain separate. |

## Unresolved / blocked

See [WRITER-BLOCKERS.md](WRITER-BLOCKERS.md) for per-route writers, collections, contracts, evidence limits and future closure. Missing sales, refund entitlement, refund payment, provider settlement and payroll producers remain Legacy-backed and forbidden after native cutover. Driver payment approval requires an absent verified bank/POS proof adapter. Advertising bank movement still lacks evidence/posting orchestration. These paths have not been made to work by removing the transition barrier or changing state to Legacy-active.

Successful tests that substitute driver payment proof verify the consumer only. Source inspection and guard tests establish the blocked Legacy sinks, but refund/settlement tests fail at their initial sale prerequisite; downstream assertions remain unexecuted. No direct payroll native-route rejection result is claimed.

Onboarding readiness still returns `ready_for_live_post=false`, `stage_16_locked=true` and `Smoke B: BLOCKED_BY_ENVIRONMENT`. Its dependency labels include P02 even though the native shipping producer exists; this is the readiness contract's hold, not evidence of a missing shipping implementation. No readiness gate was relaxed.

## Decision

`MZ2_ONLY_SSOT_AUDIT = NOT_PASS`. Twelve browser scenarios and navigation are PASS within their stated scope; complete16-stage business acceptance is NOT_PASS while business writers and final activation are blocked. Smoke B is BLOCKED_BY_ENVIRONMENT. No Release Candidate is declared. Broad frontend regression and exact-checkpoint CI failures remain visible.

Production financial writes=0. Deploy=NO. Opening Post=NO. Activation=NO. Production merge=NO. Production write-control changes=0. No production lease created or changed; synthetic test fixtures are not production actions.
