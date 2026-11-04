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

The fee-table API suite passed eleven tests against isolated Mongo, covering versioned edits, concurrency, scope restrictions, source activation, preview and financial-state fingerprints. The owner form has thirteen-row pagination, editing and revision history. Its component metadata roundtrip and event-preview display are undergoing final focused verification.

Shipment acquisition and delivery-recording DTO work passed 355 backend tests with Mongo and eleven frontend tests. Two additional clock regressions passed in a final 143-test pure slice. Independent replay confirmed source identities, timezone semantics and repeat-run stability, including forty rejected ambiguous or contradictory observations. The combined final Mongo run is pending.

The owner expanded the scope to an editable Mezan 2 fee table, including per-event transfer and refund charges, as the source for operational balance estimates. Explicit source-mode activation is implemented. Provider estimates and affected readers use the same saved policies, with actual statement fees taking precedence. Final integration of additional executed-refund charges and cumulative refund rounding is in progress. Transfer fees use the actual recorded settlement amount, because historical movement defaults cannot distinguish an omitted fee from an explicit zero.

Independent arithmetic replay passed all supplied authentic BNPL events and the separately retrieved stored settlement rows. The source review identified unavailable current installment-plan evidence, incomplete provider pricing evidence and a rejected provider authentication probe. Operational adoption dates and these coverage gaps require explicit disclosure in the completion report. Private analysis records preserve the source provenance and arithmetic comparisons.

## Pending work

- Finish executed-refund event fees, cumulative refund rounding and the component-edit roundtrip.
- Run independent source replay, integration and regression tests.
- Review all source changes, complete CI, freeze the v5 A/B contract and use the authorized release workflow.
- Save supported merchant fee policies through the owner UI and verify live reconciliation and repeat-run stability.

This checkpoint is work in progress. Production release and owner fee configuration are pending. The next step is to finish the assigned implementation slices in this checkout and run the combined regression suite before release preparation.
