# Operational payment receipts — 2026-11-25

## Authority and release scope

The merchant explicitly authorized finishing the work and publishing directly
to Production without a Preview deployment, followed by production testing.
This supersedes older Preview/publication-approval wording in the historical
implementation journal. Existing v5 identity, lease and build checks still apply.

Repository: `AMASI-SA/AMASI-SA`. Original checkpoint: PR #2124, branch
`codex/operational-payment-prod-integration-20261125`. Source
`1147d64054427f9eb897e41f87419b7d45087371` has 21 successful workflows. All changes
from latest production `b651eacf5ded173c4f3224d195ef874b8bcea312` are preserved in
recoverable integration checkpoint `4c92f5c8c2b37edcbe965f1c98a83b7e50ac4845`.

Run 45675189001 correctly rejects that merge's intent history: it imports the
new production intent relative to its first parent, despite matching final
intent bytes. Final source A must have current production as its sole parent,
the same reviewed source tree and unchanged production intent. Preserve the
old checkpoint; do not modify the guard or force-rewrite it. The replacement
source branch is `codex/operational-payment-release-20261125`.

## Functional contract and sole writer

### Customer bank transfers

The existing `BankCreditRecognition` adapter and
`operational_balance_service.refresh` remain the sole automatic bank path.
The merchant treats reaching execution as operational confirmation that staff
have checked receipt. Verified execution and later fulfillment statuses retain
the existing four-status policy. This is not an independent bank-statement lookup.

A receipt image or current status alone cannot create a new bank movement.
The source validates owner/order/revision, configured bank, actual tender amount,
paid/due balance and original qualifying Salla history. The writer checks its
original date against cutover and existing manual/automatic money. Mixed bank
legs without their original dates stay pending. Canonical identity remains
`order-bank:<Salla id>`; openings and posted receipts survive later status changes.

### Delivery and external courier COD

Eligible native delivery enters the existing source/reducer. The single
`cod:<order>` receivable belongs to the actual frozen delivering courier, and
does not become bank cash. Existing approved shipping fees remain canonical.
Replays do not create another COD receivable or shipping charge.

Delivery-backed courier responsibility is distinct from independently verified
customer collection. Incomplete/status-only shipment evidence stays pending
where the source contract requires more proof. Never manufacture collection
attestations from status. Cash/POS/bank funds collected by an external courier
remain due from that courier until actual merchant-bank remittance.

### Courier remittances into the merchant's bank

The current change requires explicit arrival confirmation, actual business date
and reference in the canonical `create_movement` path and reachable entry forms.
Every incoming external-courier bank movement is covered, including relabeled
movement kinds. Changing the receiving bank clears earlier form confirmation.
Public schemas reject non-boolean approvals before Pydantic can coerce them.

The existing owner-scoped CAS records the bank movement and allocates courier
debt once. Exact persisted requests retain their original replay result. New
unconfirmed requests cannot add bank cash. Courier cash, internal drivers,
outgoing shipping payments and opening balances retain their existing contracts.
There are no Qoyod or general-ledger writes.

### Experimental payment automation

The new bank/courier `operational_payment_*_policy.py` files are decision-only
contracts, not another live posting path. The provider native-capture flow uses
the existing aggregate; its evidence/posting flags remain default OFF. This
release does not activate them or backfill historical money. Undated paid pages,
ambiguous extra payments/refunds and unmatched statement rows remain in review.

## Verification and remaining release steps

Fresh checks after production integration: all 29 payment-policy workflow
suites passed, 251 tests including two real canonical-reducer parity tests.
Bank verification passed 94 tests and 84 subtests using pure/fake-backed service
paths. All 105 real Jest tests passed across ApprovedEntry, SimpleBalances and
OperationalBalances, including the specialized movement cards and advanced form.

Independent review reproduced numeric-boolean coercion and incoming
payment/refund relabeling bypasses, then verified their fixes. The 44 new backend
cases cover these public/writer boundaries, missing proof, date limits, old
saved-request replay and cash/driver/outgoing compatibility. Combined with the
existing COD flow, 63 cases passed against the real service and reducer with an
isolated mongomock storage boundary; the one concurrency case was not exercised
locally and remains enabled for real Mongo CI. No tracked test is skipped.
Independent source review has no remaining blocking finding in this scope.

The canonical HTTP gate applies to mobile requests as well. The React employee
surface is updated, but the separately installed native client UI/build was not
inspected or released. An older client omitting the required receipt evidence
will receive 422 and cannot credit the bank.

Local Mongo is unavailable: verified official Mongo 8.0.12 aborts during CPU
topology initialization and verified Mongo 7.0.16 hits an operation-not-permitted
error before opening its listener. No permission control was bypassed. Actual
integration acceptance must run on the existing CI Mongo 8.0.12 replica set;
tests must not be skipped to conceal this limitation.

Next: final source review, exact-source CI, matching final-A candidate intent,
intent-only B, both reviewed-B adapter rehearsals, fresh shared guard/ref checks,
then the authorized direct release and runtime tests. Never reuse an old-A intent.

Emergent management is reachable; its original code-server terminal remained
disconnected after a normal reconnect attempt. No shared source, lease, financial
data, feature flag or live publication changed in this task. Local guard state
does not establish the shared lease state.

**Production changed by this task: no.** This is a recoverable implementation
checkpoint, not a claim of completed publication or live automation activation.


## Superseding release checkpoint — 2026-11-25 22:39 Asia/Riyadh

This section supersedes the historical release-state statements above. This
branch is documentation-only release evidence; do not merge or deploy it.

- Reviewed source A: `ca426cee9e4ed4fa730dece2409f916003249f83`.
- Intent-only B: `66ac128fd6c9c25b86f1e8cdb7c5ccb929310636`.
- Deployment D: `17a0c8c58a71962d1733e7888eeac31d20bc40c0`.
- Production base J: `b651eacf5ded173c4f3224d195ef874b8bcea312`.
- D/B tree: `039cacf3578f645fb2a0172cb5a2e2e15112a3c5`.
- Release: `rg5-48825e8e9f14da864b75269ea230fa1d0f4a5c146a462a14c840317ce0225ab7`.
- PR #2130 is merged into `hotfix/prod-snap-meta-final`; #2124 is closed as
  superseded with its recoverable checkpoint preserved.

Final exact-B verification is complete: 20 workflows succeeded and one
unrelated conditional TikTok workflow was skipped. Operational Balance Web
run 45678365557 passed all four jobs. Real MongoDB 8.0.12 replica-set coverage
passed 7,735 tests plus 151 subtests, including all 44 new receipt cases and the
ten-request concurrent replay test (one bank effect and unchanged shipping).
Demand-read passed 106 tests; shipping passed 16 tests plus 18 subtests.
Frontend passed 115 suites / 1,850 tests, including the 105 changed-form tests.
Both final-B clean-clone adapter rehearsals executed and passed in readiness
run 45678365247 (Node 20.20.2 and 24.19.0). All three D workflows passed:
readiness 45680815295, security 45680815085, and CodeQL 45680815170.

The original shared code-server recovered through a normal Reload Window.
Original /app advanced cleanly to D with the exact B tree. Existing foreign
roots were preserved, and prior frontend/node_modules was moved into a
same-filesystem identity-checked backup. No secrets were read, copied or hashed.
Platform-managed backend/.env was externally replaced before source mutation;
its regular-file ownership/mode contract was verified without accessing its
contents. The original v5 adapter build passed in 94.81 seconds with pinned
Node 22.23.2 / Yarn 1.22.22. Original frontend and package verifiers passed.

The original v5 prepare and prepublish commands passed. The active lease is
owned by actor `operational-payment-20261125` for the A/D/release identities
above. Emergent Manage -> Republish -> Publish changes was invoked once for
https://mezansalla.com, and the platform recorded Deployment Started at
Nov 25, 10:39 PM Asia/Riyadh. The platform is building production. No Preview
deployment or paid agent chat was used.

**Production publication: started; completion and runtime verification pending.**
No financial entries or historical backfill were submitted, and no experimental
payment flags were enabled. The source contract and native-client boundary above
remain unchanged.

Next safe action: observe this publication to a new explicit success state,
then run the original shared `production_release_guard.py verify --url
https://mezansalla.com` with bytecode disabled. Require three successful probes
and final inactive matching lease before declaring completion. Do not click
Republish again, clear the lease manually, or modify the frozen A/B/D lineage.

PR and evidence:
- https://github.com/AMASI-SA/AMASI-SA/pull/2130
- https://github.com/AMASI-SA/AMASI-SA/actions/runs/45678365557
- https://github.com/AMASI-SA/AMASI-SA/actions/runs/45678365247


## Verified functional publication and performance handoff — 2026-11-25 23:09 Riyadh

The preceding pending-publication checkpoint is superseded. Original publication
version `04fc573` finished at 22:51 Riyadh, and the original v5 verifier exited
0 at 2026-11-25T20:09:23.113722+00:00. It matched all identities in three consecutive
probes, with 770 public frontend requests per probe, and automatically closed
the matching lease. The sanitized proof subset is stored in
`verification/initial-production-proof.json`; the complete original proof
remains in the task's /app/.git stage. **Production changed: yes, verified.**

The merchant then required avoiding extra load. Independent review identified
and fixed two costs: empty evidence-stage authorization/lease/thread overhead,
and repeated split-payment conflict-set construction. The combined source
passed 63 focused tests; constituent affected suites passed 175 and 268 tests.
The review found no new recurring job, polling or source fetch introduced by
these optimizations. Preexisting memory pressure is not claimed resolved.

The next performance release is frozen in PR #2131:
- Branch: `codex/operational-payment-performance-20261125`.
- Base: `17a0c8c58a71962d1733e7888eeac31d20bc40c0`.
- Source A2: `164466b6d1caacb956e23b9fe1a8b2cf3c65a9aa`.
- Intent-only B2: `514bd0d15b08a48353ab4566c1b243d1279e792b`.
- B2 tree: `4d56be96f39b4e15db172a61cb5ae96a9190d649`.
- Release: `rg5-0d4df1ef2bc9e0c6f95f60361e2201ba934fbe4100b9524d0f98c425c23819b7`.
- Candidate A2 readiness: 45690834626; final B2 readiness: 45691509189.

Both original candidate and reviewed-B validators passed in clean checkouts.
Final B2 full contract tests are still being collected. No performance-release
merge, shared source mutation, lease preparation or publication has happened.
Next: finish exact-B2 gates, verify fresh original shared guard/ref/foreign-file
state, merge without squash/rebase and publish using a fresh matching v5 lease.
Never reuse the first release's intent or manually clear an unrelated lease.
