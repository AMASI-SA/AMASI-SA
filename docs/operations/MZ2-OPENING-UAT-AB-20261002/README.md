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

## Integrated verification

- Focused backend: **241 PASS + 10 subtests** in the isolated replica set. Suites: `test_onboarding_employee_native_identity`, `test_accounting_onboarding`, `test_accounting_onboarding_domains`, `test_product_v2_full_details`, `test_onboarding_inventory_draft`, `test_g47_opening_inventory`, `test_mz2_driver_physical_cash`, `test_mz2_shipping_native`, `test_accounting_onboarding_ad_binding`, `test_onboarding_ssot_contracts`. Command: `python -B -m pytest --noconftest -q backend/tests/<suite>.py --junitxml=<evidence>/backend.xml`.
- Focused frontend: **185 PASS / 18 suites**. Command from frontend: `node node_modules/react-scripts/bin/react-scripts.js test --watchAll=false --runInBand --testMatch '**/onboarding/*.test.jsx' '**/onboarding/*.test.js' '**/onboarding*.test.js' '**/accountingOnboarding.test.js' '**/CurrencyFields.test.jsx'`. Node22.23.2.
- Browser: default HTTP client to real mounted routers and disposable Mongo: **23 scenarios PASS**, including desktop/mobile navigation through 16 stages, persisted identities, separate balances, idempotency, stale-version409, immutable review, and no non-setup effects. Independent Stage10 real-browser persistence proof PASS. Commands: `node scripts/testing/mz2_onboarding_ab/build.cjs`, loopback uvicorn fixture, `node scripts/testing/mz2_onboarding_ab/browser.cjs`; equivalent `mz2_inventory_d` build/server/browser for Stage10.
- Live Preview is **UNVERIFIED**: Codex browser fails before connection with `failed to write kernel assets / os error3`. Screenshots are labeled synthetic loopback fixtures and are not deployed UI or Business UAT evidence.
- Final full frontend rerun: **249 suites / 1460 PASS**, Node22.23.2. Rich-shipping browser harness: **5 PASS**, real saved terms, source-byte download, explicit evidence review/approval, reload and revocation. The latter intentionally changes shipping setup metadata in its UUID fixture; its financial/control fingerprint remains unchanged.
- Evidence artifacts are in `evidence/`; `tested-source-manifest.json` pins runtime file bytes tested locally. Full frontend / fresh affected CI results are recorded on the exact PR head.

## Initial CI failures and contract-preserving corrections

The first checkpoint's Track G and Accounting jobs exposed three old assertions requiring `financial_entity_id` for a valid Employee OS id. That is the exact Stage4 defect authorized for correction: Employee OS creates the canonical id while the historical alias may be null or unrelated. The replacement tests assert exact report/trial totals for the valid native id and retain stronger negative cases for missing, foreign, archived and mismatched native ids. Native-only fakes and real Mongo command monitoring prove zero Legacy reads; collection snapshots prove reports do not write. Both report suites: **30 PASS + 6 subtests**. No report writer or accounting rule changed.

The first connected-browser run correctly exposed missing fixture mounts/permissions for new read panels. The fixture now mounts the actual driver and recurring routers with the same persisted synthetic actor. The employee still receives **403 owner_required** on the recurring source; the browser asserts that exact response and visible denial. All other browser errors and external requests must remain absent, and all prior idempotency, balance and fingerprint assertions remain. No production permission or guard was changed.

The original browser result metadata calls the legacy full-editor payload `C_NEW_SCOPE_REQUIRED`; that historical payload is rejected by the existing Native setup contract. This label does not classify the current rich-contract implementation or create a new C: rich contracts were already delivered and are not altered by this A/B change. See `browser-c1.cjs` for their separate harness. The final financial and physical approval limits remain unchanged.
