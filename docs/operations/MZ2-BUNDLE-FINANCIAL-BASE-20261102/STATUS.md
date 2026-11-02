# MZ2-BUNDLE-FINANCIAL-BASE-20261102 — WIP, bundle costing only

Repository: AMASI-SA/AMASI-SA. Branch: `codex/mz2-bundle-financial-base-20261102`.
Reviewed production base: `f8235d57e90f8a1271df6350d7e7919f5621ea11`.
Prior deployed fix: PR1690. Prior documentation checkpoint:
`bb6291a9e56e95d2fa1968046dfd32dab07fe3f6`.

## Controlling user decisions

- Shawl base15 SAR, rose40 only for its Yes selection, embroidery10 only for name-Yes.
- Embroidery remains an existing external-supplier service with its existing shared-resource binding.
- User explicitly approved the bundle correction, merging and publication after tests.
- Latest restriction: preserve the old method for services and components; change **only bundle costing**.

## Scope and design

The former proposed current_product row has not been saved. The live page was reloaded and still shows only the original conditional rose row. Do not add an operational shawl unit, alter sourcing, convert the service to a direct amount or weaken supplier execution guards.

Implement an explicit future-only bundle reporting policy on the existing cost profile. It includes a missing parent base in the frozen financial composition, leaving the operational definition, schema, leaves, reservations, service identity and supplier invoicing unchanged. Ordinary products and bundles without this explicit policy retain their behavior. A physical current_product already covering that base must not be counted twice.

Freeze compact SAR evidence when existing bundle expansion already has the source metadata. Preserve it through intake, frozen replay and reservation plans. Report readers use it without new catalogue/invoice queries. Estimated supplemental base cannot become proven actual merely because operational components were consumed.

Historical corrections remain explicit, bounded and frozen-evidence-only; recognize a base already included financially so repair replay cannot add it twice. No journal, invoice, physical receipt or stock operation is authorized as part of calculating this correction.

## Work and validation state

In progress: financial snapshot/authority integration; permission-checked isolated policy editor; regression coverage using a real resource-backed supplier service rather than a direct amount.

Required evidence: 4-unit totals60/100/220/260; mixed totals160/240; same operational leaves and service invoices; quantity/FX once; immutable replay; existing current_product deduplication; no false actual promotion; historical repair deduplication; unchanged ordinary-service/component behavior; bounded report reads.

Production code unchanged in this follow-up. No new production policy, recipe or historical repair has been applied. The previously saved embroidery financial treatment remains independent; no second10 charge was created. No release lease is active from PR1690 and its successful publication must not be repeated.

## Next safe action

Complete and test the isolated bundle financial calculation, update this record with exact commands/results and push a recoverable checkpoint. Review and execute the repository's fresh A/B release protocol only after integrated tests succeed; preserve all existing live services/components and apply only the explicitly approved financial policy.

Issue1006 is at its2500-comment cap. This task record and the PR are the continuation record; do not overwrite another operation's comments.
