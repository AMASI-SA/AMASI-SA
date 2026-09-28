# R2 verified checkpoint — MZ2-SPECIAL-ORDERS-001

**IN_PROGRESS_NOT_READY_TO_DEPLOY**. This is completion of the isolated nucleus
and its first actual catalog/canonical-read integration checkpoint, NOT completion
of the requested full operational/accounting feature. The existing live factories
remain Salla-only; route registration, creation, commands and dispatch stay OFF.

## Exact tested identity

- Core Draft PR: #1171; UI Draft PR #1167 remains separate and unmerged.
- Branch: `feature/mezan-special-orders-core-20260927`.
- Frozen baseline: `1cbeccd84d58fb3397dcd42822d5feb92de06275`.
- R2 source: `9e3c551fb987c78b85bfca3f6d1c3a69ea09467d`.
- R2 source tree: `b817bc4f45783e79642efca112e739d51fd8ebce`.
- GitHub PR test-merge checkout: `80e51a6b2ad91c6a6f25ba8ac47832e43ba979b6`.
- GitHub's test-merge tree was read back and is exactly the source tree above.
  It is NOT a merge into the production branch.
- This evidence/status update is documentation-only on top of the tested source;
  it must not be described as a separately tested runtime implementation.

## Actual verification

Workflow **Mezan Special Orders Core**, run **36349065001**, job **108704030454**
completed successfully. Actual log results:

- Frozen ordinary-order baseline: **78 passed**.
- Candidate: **184 passed, 1 warning**.
- Downloaded JUnit XML: zero failures, zero errors, zero skipped for BOTH runs.
- Compileall also completed successfully.

The candidate contains the SAME 78 baseline tests plus 106 special-order core,
source-adapter, HTTP and Mongo tests. Seven real-Mongo cases are INCLUDED in 184,
not additional. Do not add the repeated baseline run to imply 262 distinct tests.
The warning is Starlette test-client HTTPX deprecation, not a failed assertion.

Workflow: https://github.com/AMASI-SA/AMASI-SA/actions/runs/36349065001
Artifact: https://github.com/AMASI-SA/AMASI-SA/actions/runs/36349065001/artifacts/10941469074

## Retained artifact independently inspected

Artifact ID: **10941469074**. ZIP size: **5805 bytes**.
GitHub-reported archive digest equals the locally recomputed digest:
`a512cf0afcfe9767479118b2cff41b71c5a73418d244cfad773bdaba32cd22cb`.

- `special-order-tests.xml`: 184 tests, 0 failures/errors/skips.
  SHA-256: `80cbc3d6d358d4da84222ab563ce19cfd46b049a540a45ca9a169978882f90d6`.
- `special-order-baseline-tests.xml`: 78 tests, 0 failures/errors/skips.
  SHA-256: `a2c044107ec8e8fe4242a35d3b64329c6d74442596ab04320a99c14aed0164b7`.

## Tested real-Mongo cases

1. Concurrent create yields one document/event.
2. Compare-and-swap has one winner.
3. Carrier tracking unique across local orders.
4. Financial movement reference unique across local orders.
5. Canonical single/batch reads distinguish sources and are opt-in.
6. Mixed-source cursor has no missing/duplicate/foreign-tenant records in the tested cases.
7. Pending/reviewed/processing queues derive local current status from the existing
   shared workflow, so delayed aggregate observation does not return a local order
   to waiting review in the tested cases.

Mongo runs use disposable `test_mezan_special_<uuid>` databases on the workflow
service. Records are synthetic. The actual Mongo repository and canonical readers
run, but these tests are not evidence of live Salla/provider, financial or fulfillment
mutations. No merchant/Production database was used.

## Implemented through R2

The R1 package implements local identities, original/option snapshots, per-line
contributions, pending receipt versus confirmed movement distinction, bank/COD/
custody/remittance/refund rules, cost-unit coverage and reversals, replay/CAS and
an atomic aggregate/audit/outbox boundary. It does not create a second ledger.

R2 implements scoped reads from Product V2 and the original-order contract, real
option/variant IDs and multi-select preservation, canonical Mezan order/item DTOs,
explicit opt-in mixed-source get/list/batch/pending discovery, current shared stage
visibility, and selected-option linkage to the existing inventory/cost helpers.
Ordinary Salla wire payloads do not acquire the new special-order metadata.

Only five existing backend files were changed additively: order_engine models,
repository and service; order_item_engine models; selected option cost tokens.
The ordinary-order regression subset passes on both baseline and candidate.
This does not mean every existing application behavior was covered.

## Still required implementation before release

- Source-aware shared review/preparation mutations and source freeze handshake.
- Private bank-receipt/carrier-label evidence storage/read verification and actual
  supplier/assembly/courier print binding; product file-upload options are not
  silently supported without their secure attachment binding.
- Real MZ2 posting-owner and verified movement allocation integration for bank,
  supplier/inventory, carrier, driver custody and settlement. Current proof contracts
  and synthetic test providers are not completed accounting integration.
- Persistent approved Salla balance sidecar and its actual courier collection flow.
- Actual report consumers and Android/old-client compatibility checks.
- Full ordinary/special-order lifecycle baseline/candidate tests, Preview UAT and
  a rollback drill that preserves in-flight local orders and financial history.

No UI changes, Merge, deploy, Preview change, runtime activation, permission
widening, real merchant/provider/financial writes, Android publish, release lease
or release intent change were performed. Continue from this branch/checkpoint;
do not start over, merge the old UI PR, or label this checkpoint ready to deploy.
