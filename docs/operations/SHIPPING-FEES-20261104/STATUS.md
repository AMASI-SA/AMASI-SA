# Shipping and payment-fee reconciliation

## Scope

Repair acquisition of delivered courier shipments and their native delivery history. Preserve shipment identity, event provenance, period boundaries and idempotent fee recognition. Reconcile payment-fee calculation with owner-supplied settlement evidence and persist supported terms through the existing owner settings flow.

## Baseline

- Repository `AMASI-SA/AMASI-SA`
- Branch `codex/shipping-fees-reconciliation-20261104`
- Base `cec4c3854031c357577f07568f91a44c082905fc`
- Production source observed at task start `424525026a2224ca805e091e163353641c0e2012`
- Release Guard status observed inactive before any shared workspace change

## Verified checkpoint

The component contract and owner form preserve an explicit VAT rounding mode. Existing components default to half-up. Capture-only policies can retain unknown refund and cancellation treatment until the corresponding executed event requires it. An evidenced card network resolves an enclosing wallet label while conflicting networks remain unresolved.

The focused owner-form suite passed four tests. The schema regression passed one test. The fee implementation and its integration tests continue in the task branch. Private source documents, identifiers, pricing and comparison reports remain outside Git.

## Pending work

- Finish source-scoped product mapping and reuse of saved fee policies across affected estimates.
- Finish shipment acquisition and carrier-event validation, including replay and identity fences.
- Run independent source replay, integration and regression tests.
- Review all source changes, complete CI, freeze the v5 A/B contract and use the authorized release workflow.
- Save supported merchant fee policies through the owner UI and verify live reconciliation and repeat-run stability.

This checkpoint is work in progress. Production has not changed for this task. The next safe step is to finish the assigned implementation slices in this checkout and run their focused tests before release preparation.
