# Financial table dependency and partial tariff display checkpoint

Base: production `de76aed339be775e2878071c92063d0ab9df06df`.
Isolated branch: `codex/financial-table-profit-20261108`. Draft only; no deployment, production database write, historical reprocessing, or /app mutation.

## Implemented
- Classifies only `canonical_salla_source_missing` with a positive integer or nonzero ASCII decimal native order reference. The canonical order collector skips order payment/cost/delivery processing, but salary contracts, advertising snapshots, and external-party ledger balances have independent sources. The diagnostic no longer blocks those four independent table coverage paths.
- Suppliers remain blocked because supplier receipt attribution can depend on canonical order identity. Banks, cash, customer wallets, custody, providers, couriers, drivers, other balances and consolidated position remain blocked. Unknown codes/missing identities continue to block all tables. Independent salary/ad/bank diagnostics are not removed.
- Displays known tariff fee/net subset and known/unresolved order counts as separate columns. Full totals stay unknown when incomplete. Unknown count stays unknown. The subset is explicitly not an additional amount or full balance; common server columns feed screen, print and export.
- This display is compatible with old provider summaries (absent new fields remain null). The provider-summary parallel task supplies the actual known subset fields; integrate it before expecting production subset values.

## Proof
- Source-to-reconciliation fixture with a real canonical-missing order: salary contract revisions produce the identical nonempty accrued days and obligations; independent opening/table amounts are unchanged. Cash, supplier and bank coverage remain held.
- Negative fixtures: missing identity, invalid identity shape, unknown canonical code, independent ad/employee bank gaps.
- Backend coverage/position/source suites: 137 passed; also 137 passed integrated with provider summary SHA `3857fd1a1012693f46e17df7be0091805256b129` (source temporarily copied for test, not included in this patch).
- Profit period/expenses/returns/salary suites: 54 passed. Profit/net sales canonical source requirement is deliberately unchanged.
- Frontend financial position: 33 passed, including same-snapshot screen/print/workbook equality for unknown full totals with known tariff subset and an unknown unresolved count.

## Still unresolved
- Live historical canonical missing count previously 4,759 is not established as irrelevant to suppliers, money or period sales. Need canonical Salla order identities, original raw source and dated business events to resolve or prove exclusions per order. No amount inferred from root order totals or timestamps.
- Prior external/supplier 4,992 unique diagnostics cannot be translated into remaining affected orders without fresh live grouped evidence; tables may retain other real failures.
- Profit remains evidence driven: canonical sales, tax, received product cost, shipping VAT, payment fee VAT and the same-period recorded expenses must be proven. No profit derived from bank balance and no empty source converted to zero.
- Parent owns integration, official V5 release and three live verifications, Issue #1006 update and final production completeness audit. This checkpoint is not proof of live completeness.

## Review follow-up
Canonical source references now reject placeholders (`...`, `unknown`), zero/negative values, booleans, objects, whitespace, non-ASCII digits and floats. These remain blocking for every table. Native positive integer and ASCII decimal references alone receive the reviewed dependency classification. No provider fields changed.
Validation after review: 160 backend coverage/financial-position/source tests passed.
