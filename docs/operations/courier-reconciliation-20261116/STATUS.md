# Courier delivery reconciliation — 2026-11-16

## Scope and authorization

Continue the owner's requested Salla/Mezan courier reconciliation and correct
the proved delivery-recognition gaps. Preserve the established rule: a current
delivered order created strictly after operational start may be recognized
without inventing a delivery timestamp. Older orders still need dated evidence
or the existing explicit per-order confirmation. Completed-order shipping fees,
COD custody, booked financial facts, and demand-only refresh remain independent.

Task branch: `codex/courier-delivery-reconciliation-20261116` in
`AMASI-SA/AMASI-SA`. Initial reviewed base:
`71d1dd9010b3fd73de0463a6164f82fcd2b7cff5`.
Latest fetched production base:
`438b461fc660e043726c8dd5ab282d06abd3c17f` (payment-method conflict isolation).
The relevant courier source and existing acquisition test modules are unchanged
between these bases.

## Verified checkpoint: bounded native-evidence recovery

- Added `operational_delivery_order_waybill_unproven` to the existing native
  courier evidence acquisition issue set.
- The actual visible-request entry point now attempts complete, bound native
  evidence reads for that previously omitted issue class.
- Preserved the four-order per-request cap, retry fence, owner filter, complete
  pagination, full canonical-source compare-and-set, and source-only writes.
- Added synthetic public-entry regression coverage in
  `backend/tests/test_operational_balance_courier_waybill_acquisition.py`.
- Included a later generic Salla delivered notice with a null publisher ID and
  different note text; it is ignored by the existing native event classifier,
  while the actual carrier delivery date remains the selected evidence.

Verification used the existing Python environment, with
`PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 PYTHONPATH=backend:backend/tests`
and `python -m pytest --noconftest -q`:

- New regression before the change: **1 expected failure**, no native reads and
  zero candidates despite a genuine waybill-binding failure.
- Final new module after the change: **14 passed**.
- New module plus courier acquisition and delivery-confirmation acquisition,
  excluding the existing real-Mongo scenarios: **85 passed, 17 deselected**.
- Independent in-memory baseline mutation reproduced the expected failure.
- Independent broader run: **75 passed**, with all **17 real-Mongo scenarios
  blocked by connection refusal at the test Mongo endpoint**; these are not
  reported as verified passes.
- `git diff --check`: passed.

## Review and limits

The recovery admission change does not relax native dated-proof validation.
Repeated delivery histories, multiple shipment legs, and conflicting evidence
remain rejected by the existing proof builders.

The initial repeated-delivery hypothesis was narrowed after reading complete
native source samples. A repeated displayed status can be a generic Salla
notice rather than a second carrier transition. The minimized reconciliation
dataset omitted the note and actor fields needed to make that distinction;
raw repeated-label counts therefore cannot predict proof rejection.

No status-only recognition exception, placeholder normalization, or relaxation
of the dated evidence contract was implemented. The speculative local tests
were removed before inclusion. Both `operational_courier_transitions.py` and
`operational_delivery_evidence.py` are unchanged.

Independent review found no blocker in the bounded admission change. Separate
public-entry checks confirmed that generic delivered notices are ignored,
genuine repeated carrier deliveries remain rejected, and a later returned
observation still blocks recovery.

This patch proves recovery for a synthetic, genuinely failed saved binding;
it does not establish the raw canonical shape of every live case, a production
recovery count, or complete courier/financial reconciliation. The read-only
preview database queries did not find the selected live source records and
are not treated as production evidence.

The frozen reconciliation deliverables are unchanged. Live source counts and
financial balances have not been refreshed or reclassified by this task.

## Release and continuity

**Production changed: no.** No merge, deployment, release lease, live balance
refresh, source backfill, fee confirmation, or financial write was performed.
The shared `/app` environment was inspected read-only; another conversation's
active release lease was observed and left untouched. Local work is isolated
in the task worktree. Existing unrelated release and payment work is preserved.

The completed local implementation checkpoint is
`77f883a2ad972b83e83a30e250f318e32ae2516f`. **Automatic approval review initially
rejected the GitHub push** because explicit authorization to publish the code
to that repository was not established in the visible conversation. No
workaround or alternate push/PR route was attempted.

On **2026-11-17 (Asia/Riyadh)** the owner explicitly approved the requested push
to `AMASI-SA/AMASI-SA` and opening a Draft PR to complete its checks. This
authorizes the task-branch push and Draft review/CI handoff. No merge or live
deployment is included in this approval. This status-only checkpoint records
that authorization; the reviewed runtime and regression-test content is
unchanged from the implementation checkpoint above.

Issue #1006 is the repository continuation ledger; its complete comments could
not be retrieved in this session. The task-specific status is retained here.
The Draft PR will record the exact uploaded full commit SHA and serve as the
continuation record under the existing full-ledger convention. The issue body
was retrieved on resumption and confirms its 2,500-comment limit; no attempt
will be made to append another comment or replace its unrelated shared body.

Next safe action: push this completed task branch to `AMASI-SA/AMASI-SA`, open
the approved Draft PR, and inspect its required CI. Independent workflow review
confirmed that Operational Balance Web and Operational Stock Eligibility both
select `test_operational_balance*.py` with an isolated real Mongo service, so
the new regressions and the 17 local environment-blocked scenarios are covered
without a workflow change. A later production publish remains separate and
must follow the v5 release protocol after the shared lease is inactive.
