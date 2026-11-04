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

The fee-table API suite passed eleven tests against isolated Mongo, covering versioned edits, concurrency, scope restrictions, source activation, preview and financial-state fingerprints. The owner form has thirteen-row pagination, editing, revision history and a direct owner navigation link. Component metadata, fee limits, optional legacy suggestions and event-specific previews passed focused and independent verification.

The final combined operational suite passed 2,694 tests and eighteen subtests with isolated Mongo. The shipment and DTO suite passed 357 tests, and its expanded service and refresh suite passed 374 tests. The combined operational frontend run passed 414 tests. Four additional completeness UI tests passed. Independent replay confirmed source identities, timezone semantics and repeat-run stability, including forty rejected ambiguous or contradictory observations.

The owner expanded the scope to an editable Mezan 2 fee table, including per-event transfer and refund charges, as the source for operational balance estimates. Explicit source-mode activation is implemented. Provider estimates and affected readers use the same saved policies, with actual statement fees taking precedence. Additional executed-refund charges and cumulative refund rounding are integrated. Transfer fees use the actual recorded settlement amount, because historical movement defaults cannot distinguish an omitted fee from an explicit zero.

Final BNPL completeness checks passed 34 tests with isolated Mongo, including twelve API and generation boundary cases and the existing generation, matching and provider suites. Weekly and reconciliation views retain known amounts while exposing incomplete fees. Registration preview reports a review error, and invoice generation returns incomplete periods before their write path. CI explicitly includes the new fee, completeness and UI regressions.

The extended Order Engine route fixture requires a database stub with `unified_orders`; its first three setup cases stop before route assertions. The fixture and its relevant router dependencies match the base commit. Service, refresh, mapper, repository and model consumers passed the expanded suite.

Independent arithmetic replay passed all supplied authentic BNPL events and the separately retrieved stored settlement rows. The source review identified unavailable current installment-plan evidence, incomplete provider pricing evidence and a rejected provider authentication probe. Operational adoption dates and these coverage gaps require explicit disclosure in the completion report. Private analysis records preserve the source provenance and arithmetic comparisons.

## Pending work

- Complete remote CI, freeze the v5 A/B contract and use the authorized release workflow.
- Save supported merchant fee policies through the owner UI and verify live reconciliation and repeat-run stability.

Implementation and local verification are complete. Production release and owner fee configuration are pending. The next step is the source A and intent-only B handoff, remote CI, clean-clone rehearsal and authorized publication, followed by live acceptance and fee configuration.
