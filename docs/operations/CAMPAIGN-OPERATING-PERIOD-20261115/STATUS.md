# Campaign operating period and cohort correction

## Current state

WIP diagnosis and implementation. Production has not changed in this task. Verified production base: `324dab9fd01fab17a28a5f7de4bb2bd298d7aea9`, tree `c2d683827c54574fcd60ee4102c9de7c163f5ef8`. Prior PR1929 completed publication and three-probe verification; its intent and receipts are historical and must not be reused for this correction.

## Confirmed defect

The published campaign hover computes its campaign metrics using the advertising account day, but obtains the operating allocation denominator from the current Asia/Riyadh day. The dashboard retains the selected executive period across midnight. The selected executive period and cohort never reach the hover API or its cache key. A read-only deterministic execution of the actual production operating reader confirmed that its basis flips at Riyadh midnight while the campaign account day remains unchanged. Synthetic basis data reproduced a change from 20 to 2000 in the allocation without changing campaign sales. No private financial values or identifiers are recorded here.

## Implementation contract

Carry the explicit selected executive from/to dates and payment, shipping and report-status filters through dashboard, hook, API, normalized cache/pending key and bounded operating reader. Keep campaign commerce/spend account-day semantics. The response identifies the actual operating period and cohort. The hook must not render the previous context while a new executive selection is pending. Standalone order-page scope is resolved explicitly once on entry.

Range calculation uses bounded existing saved-data reads and executive salary/recurring/cohort/currency rules. Accumulate raw daily values across the range before subtotal rounding. Preserve incomplete states, limits, deadlines and disabled-pilot safeguards. No percentage cap or negative-profit clamp. A selected-scope recalculation may be fresher than an already displayed cached executive snapshot; no exact snapshot identity contract currently exists.

## Verification and next action

Confirmed: source trace and deterministic prior-defect reproduction. Pending: implementation, real mounted frontend context/race regression, public backend midnight/cache/cohort/range regressions, affected CI, independent review and fresh Release Guard v5 source A / intent-only B / merge M sequence. Add the real mounted dashboard regression to the campaign acceptance workflow.

Exclusive ownership: backend and frontend agents use separate candidate paths in an isolated scratch tree; root owns integration, workflow, remote checkpoints and original release actions. No original /app mutation, release rehearsal, lease, merge or publish has occurred in this task.

Before any original write or rehearsal, read fresh official guard status and continue only if inactive. Preserve unrelated work and other leases. No advertising, payroll, order, financial-data, provider-sync, new polling or paid agent actions.

## Continuity

Issue1006 has reached its comment limit. This task branch, its Draft PR and task status/checkpoint documents are the continuation fallback. After every material milestone update the durable record, retaining exact source and verification identities. Next: complete and independently verify the isolated implementation before freezing a new source A.
