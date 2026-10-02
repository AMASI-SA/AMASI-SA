# Opening UAT: A/B remediation

Implementation starts at deployed `78dcf31af73581ceba3677c464c657a0b9b2c4fc`, tree `806e935c8ecc3268f98096cd2b477050d235c99e`. This is an implementation branch, not a release candidate or a deployment authorization.

## Approved boundaries and root causes

| Stage / scope | Classification and defect | Reused contract / change |
| --- | --- | --- |
| 2 | A/B: selectable canonical accounts were not readily inspectable | Existing `mz2_financial_accounts` catalogue; searchable type/currency/identity table and explicit empty state |
| 4 | A/B: opening required legacy financial_entity_id even though Native employee creation leaves it null | Existing `require_employee_v2_identity`; exact owner-scoped Employee OS id, no new identity |
| 7/8 | A/B: operational-to-financial shipping context hidden | Existing GET courier-bindings/rich-contracts/statements, explicit exact-id comparison; COD AR and courier AP separate |
| 9 | A/B: existing driver custody and review sources absent from preparation UI | Existing GET driver list, Native statement, C3 captured-cash and last 50 payment decisions; no inferred opening totals or POS balance |
| 10 | A/B: missing variant IDs became array positions; customization options demanded nonexistent variants; components mixed into one editor | Preserve unresolved IDs, reject duplicates, real combination search, independent component area with no SKU requirement, explicit pending values and allocation differences |
| 11 | A/B: policies and account binding not visible; existing min/max omitted | Existing effective-dated fee policies, provider bank bindings, existing optional limits and currency catalogue |
| 12 | A/B: external_ref used instead of exact Track E binding; both accounts demanded in all modes | Exact binding account IDs; prepaid/postpaid/hybrid fields and persisted FX snapshots |
| 13/14 | A/B: existing facts/context hidden | Existing recurring obligation metadata and Native facts; receivable/payable labels; no implicit deposit classification |
| Currency | B: original amount restricted to SAR-like 2 decimals | Preserve original decimal precision (including 1.234 KWD), currency and rate/time/source; SAR reporting contract unchanged |
| Context | B: Promise.all concealed every source when one failed | Independent source results and safe error labels; disable only affected shared-section saves; review remains fail closed |
| 15 | A/B: full server snapshot absent from review UI | Display existing snapshot/version/hash/reviewer/evidence fingerprints/entries/FX/debit/credit/equity difference; owner business approval not inferred |
| Navigation | A/B: preparation navigation and edit status difficult to follow | Sticky 16-stage RTL sidebar, search/tables/cards and Previous/Save/Next; no new execution controls |

## C remains unchanged

- Public Opening remains `409 opening_onboarding_required`.
- Activation remains `409 onboarding_activation_locked`.
- `423` and write-control remain unchanged, including evidence upload restrictions.
- No Opening execution, initialization, Activation/P08, G47 safe_active sequencing, financial writer or journal change.
- Missing deposit-specific contract is not invented; existing unknown inventory-location provenance stays visibly unapproved.
- Owner business acceptance cannot be inferred from an accounting permission review, a balanced preview, setup completion, or a synthetic browser fixture.
- Historical persisted index-derived variant IDs are not rewritten or guessed. Owner source data and any unavailable canonical bindings remain pending.

## Evidence rules

All database executions use loopback replica set `mz2opening` on port 27148, UUID disposable fixture databases. Existing browser harnesses deny outbound requests and verify non-session/non-setup collection fingerprints. This is isolated technical acceptance, not Live UAT or final Business UAT. Production financial writes by this task = 0.

The Git tree containing this file identifies the implementation checkpoint. Final exact SHA/TREE and CI are recorded in Issue #1006 and the Draft PR after push, avoiding a self-referential tracked SHA.
