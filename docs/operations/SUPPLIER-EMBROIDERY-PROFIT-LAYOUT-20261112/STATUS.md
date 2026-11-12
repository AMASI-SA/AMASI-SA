# Supplier embroidery first scan and executive profit layout

- Task: SUPPLIER-EMBROIDERY-PROFIT-LAYOUT-20261112
- Repository: AMASI-SA/AMASI-SA
- Branch: codex/supplier-embroidery-profit-layout-20261112
- Reviewed base: a1f2d2b27b909c2d9db06ac001459b0bf88df351
- Red baseline: 151d1a90143fafa2a0bb2b4472bcb615daf6bab0
- Draft PR: https://github.com/AMASI-SA/AMASI-SA/pull/1895

## Diagnoses

1. Supplier first-scan event built invoice_services before live option selection resolved. The appended live_invoice_services represented the confirmed customer-selected service, but invoice_services stayed empty in direct and saved recovery responses. Current session loading recomputed it separately. Existing live merge also did not exclude services already complete on the physical piece.
2. Profit layout was **not a delayed CSS download**. A spend-warning paragraph was inserted before KPI/expense/footer sections. Global CSS styled those sections using direct nth-child(1), (2) and (3). The warning shifted each index, applying the green net-profit hero styling to the expense rows. The separate sizing stylesheet also relied on structural offsets.

## RED reproduction on exact unchanged production code

- GitHub PR workflow: 43260303962, baseline 151d1a90143fafa2a0bb2b4472bcb615daf6bab0.
- Isolated transaction-capable Mongo backend: 2 failed, 2 passed. Selected 'yes' first scan returned zero invoice services; a completed service was wrongly revived by session loading.
- React/CSS frontend: 4 failed, 18 passed. Optional warning changed the styling hooks; the stable hooks were absent.
- Dedicated workflow only runs on PR. It has no schedules, provider sync, or production access.

## Implementation (under verification)

- Central supplier invoice merge uses current selected live services only, pending eligible existing services, exact service_id de-duplication, and completion exclusions. Direct and recovered scan event paths use the same public builder; session wrapper reuses the merge helper.
- Three stable section test IDs replace nth-child selectors in the global stylesheet and footer sizing stylesheet. Business metrics and refresh cadence are unchanged.
- Frontend/mobile mapScan is deliberately unchanged; explicit [] remains authoritative when no customer-selected service exists.
- No production/financial/provider/employee data was modified.

## Verification and next safe action

Run the exact-head PR acceptance (real loopback Mongo) and frontend suites after updating this branch. Preserve source A separately from any v5 release-intent commit B. If checks pass, inspect unrelated CI regressions and review the diff. Do not merge or publish without separate authorization satisfying AGENTS.md; production changed: **no**.
