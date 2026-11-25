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

## Superseding checkpoint: publication and load requirement — 2026-11-25

The historical release-state statements above are superseded by this section.
PR #2130 is merged. Reviewed source A is
`ca426cee9e4ed4fa730dece2409f916003249f83`, intent-only B is
`66ac128fd6c9c25b86f1e8cdb7c5ccb929310636`, and deployment D is
`17a0c8c58a71962d1733e7888eeac31d20bc40c0`. Its runtime release is
`rg5-48825e8e9f14da864b75269ea230fa1d0f4a5c146a462a14c840317ce0225ab7`.
Exact-B CI passed 20 workflows; one unrelated conditional TikTok run was skipped.
Operational CI passed 7,735 backend tests plus 151 subtests, all 44 new bank
receipt cases, the real Mongo replica-set concurrent replay, and 1,850 frontend
tests in 115 suites. Both final-B adapter rehearsals and all three D workflows
passed. The original shared adapter build, package checks, prepare and prepublish
passed. Emergent recorded the authorized direct publication finished at
22:51 Asia/Riyadh, version `04fc573`, without Preview or paid agent chat.
The original three-probe verifier is running; its matching lease must remain
untouched until that verifier closes it. Live form inspection confirms the
bank reference and initially unchecked arrival confirmation are displayed.
No financial entry was submitted by the inspection.

The merchant added a controlling requirement that this update must not add
unnecessary pressure to Mezan. Focused independent review found no new recurring
job, polling, source resync or extra courier-write query, but identified two
avoidable costs. The next reviewed release is based on D and uses task branch
`codex/operational-payment-performance-20261125`:

- Empty payment evidence previously dispatched a calculation through two extra
  authorization/lease checkpoint rounds. The same pure helper now runs inline
  only for an absent or empty dictionary inbox. It retains counter validation
  and reset, while avoiding eight metadata reads, two lease renewals and one
  thread handoff for that empty stage. Nonempty and malformed inboxes keep the
  original guarded path. Surrounding authorization, CAS and retry checks remain.
- Dashboard split-payment pricing now builds one immutable conflict lookup per
  breakdown and reuses it. Full-cohort and current-cohort conflicts remain
  combined, including when pricing subsets. Direct list callers remain valid.
  No context is mutated, persisted or globally cached. This removes repeated
  conflict-set construction that grew quadratically in a synthetic conflict
  cohort; memory remains linear in conflict count.

Local implementation checkpoints are `9cdc3b6d26fca7db50700052b0106af9e1d553b7`
and `a8074de8e4e31fec54db61773b9688d6221985ff`, both with sole parent D. The first
passed 175 affected tests, including 15 focused cases that first reproduced the
extra work. The second passed 100 focused plus 168 related tests; its operation
counter reproduced superlinear work before the fix. Independent review found
no blocking issue in either patch. These are synthetic/counting-boundary checks,
not production capacity estimates. The composed exact source still requires
its own CI, new reviewed intent-only handoff and original release checks.

Before this publication, three normal health requests took 352.4–369.3 ms and
returned the previous verified source. The hosting dashboard already reported
79% memory usage with a 100% peak, CPU 22% with a 54% peak, and several severe
resource intervals before the 22:39 publication start. These observations do not
establish the cause of resource pressure or a before/after performance result.
No production stress test, capacity purchase, historical money backfill or
experimental payment-flag activation was performed.

Next: complete the original live verifier without repeating publication, run
exact-source CI for the two narrow optimizations, obtain its matching candidate
intent, commit intent-only B2, merge and publish through the unchanged original
v5 guard. Preserve all frozen first-release identities and foreign shared files.
Do not reuse the first release's intent or active lease for the next release.
