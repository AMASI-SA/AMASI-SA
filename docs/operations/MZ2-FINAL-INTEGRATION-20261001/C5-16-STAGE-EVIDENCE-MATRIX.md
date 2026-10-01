# C5 — 16-stage business acceptance evidence matrix

Read-only audit, 2026-10-02 (Asia/Riyadh). Final source readback is HEAD
`d4f2cce588c290a77b4e318604637dcb1a1ffa72`, TREE
`1de5898efc491689669e1ce56dca92c1ef911d72`. C3 source is now committed;
the root is still preparing the browser/matrix evidence checkpoint.
Production/base/rollback remains `901568ccaaf510dc1f84d9c28f38d368d07dc64d`.
This is an evidence inventory, not an exact-head regression certificate. The
integration owner must attach the final committed source identity to any new run.

**C5 = NOT YET PROVEN.** No new C5 scenario was executed for this audit. Existing
artifacts demonstrate substantial real HTTP/Mongo and UI behavior, but not a
single completed, traceable business acceptance run covering all sixteen stages.
The missing run must not be replaced by navigation, component tests, summed test
counts, or a green CI result.

The latest user authorization permits C closure using existing contracts and
isolated Acceptance scenarios. It does not require another approval merely to
execute those scenarios. Acceptance-only Smoke B is now expressly authorized;
the earlier Production-only conclusion in
[C4-C5-ACCEPTANCE-CONTRACT.md](C4-C5-ACCEPTANCE-CONTRACT.md) is superseded for that
Acceptance gate. At this audit, its harness is prepared, with no execution
artifact yet. It must retain `production_verified=false` and cannot open Stage16.

## Scope and source anchors

The sixteen stages are the exact ordered IDs in
`frontend/src/pages/accounting/onboarding/onboardingStages.js:2`. They are not
sixteen financial sections. The existing seven sections are `banks_cash`,
`providers`, `couriers_cod`, `inventory`, `suppliers`, `payroll_obligations`, and
`equity`. Projection and sibling preservation are in
`onboardingFinancialAdapter.js:5,110`; the implemented API contract is
[Track A V1](../MZ2-TRACK-A-CUTOVER-20260930/API_CONTRACT.md).

`backend/accounting_onboarding.py:95,143` compiles evidence and exact identities;
its save/preview/review lifecycle writes setup metadata only. Its readiness at
`:198-254` explicitly separates source completeness, planned inventory valuation,
and live enablement. `OnboardingWizardView.jsx:39` keeps final opening/activation
unavailable. A successful test of that lock proves the lock, not activation.

Evidence labels below mean:

- **AB23:** [23 recorded connected scenarios](evidence/c1/browser/default23-results.json),
  executed by `scripts/testing/mz2_onboarding_ab/browser.cjs`. The first twelve
  include real UI/API operations. Later scenarios use real HTTP in a separate
  synthetic database. They do not all operate through the corresponding screen.
  The file explicitly records `business_uat=BLOCKED`.
- **C1:** [five connected rich-contract UI scenarios](evidence/c1/browser/browser-c1-results.json)
  and [backend evidence](evidence/c1/backend-results.xml): 148 cases plus 529
  subtests, including native posting/rollback. Browser source hashes are in
  [its manifest](evidence/c1/browser/source-manifest.json). This proves the bounded
  new workflow; it is not a complete sixteen-stage run.
- **C2:** [six connected H2 history scenarios](evidence/c2/browser/browser-results.json)
  and [94 backend cases](evidence/c2/backend-results.xml). The browser reads a
  prepared native history; it does not perform all original financial decisions.
- **D:** [delivered Stage10 contract/evidence](../MZ2-TRACK-D-20260930/README.md)
  and its `browser-results.json`. This is historical connected metadata evidence,
  with product/variant/component handling and refresh. It does not attest stock.
- **C3 local:** `C:/Users/amasi/mz2-c-evidence-20261001/c3-capture-full.xml`
  records 73 passing Real Mongo cases. A final fresh-authorization change is
  followed by `c3-capture-replay-final.xml` (3 passing cases). These are synthetic
  operational/financial integration tests, not a completed business UAT record.
  The completed [C3 browser result](C:/Users/amasi/mz2-c-evidence-20261001/c3-browser/browser-run1/browser-results.json)
  now records seven passing cases: actual driver modal/proof/upload/delivery,
  lost-response retry, multiple deliveries, explicit H2 matching, preserved
  later source change and unchanged financial/control fingerprints. These are
  real connected synthetic business scenarios for C3, not all sixteen stages.

Historical artifacts retain their original claims. In particular, AB23's old
`stage_7_full_courier_draft=C_NEW_SCOPE_REQUIRED` describes rejection of rich
fields by the flat `/rates` contract. The newer C1 rich endpoint is implemented
and separately proved; retaining that intentional 422 does not imply C1 is absent.

## Actual stage matrix

Every row is **PARTIAL evidence / full business stage not yet certified** unless
the narrower result is explicitly stated. “Next executable case” is authorized
Acceptance work using the existing contract, not a request to add a feature.

| # / exact stage | Existing contract and required business result | Recorded proof and its limit | Next executable case / remaining proof |
|---|---|---|---|
| 1 `cutover` — تاريخ القطع وبداية الدفتر | Explicit aware Riyadh cutover and retained original cutover evidence; no assumed date. Session CAS/replay; no opening write. `accounting_onboarding_contract.py:19-40`. | AB23 creates/saves/reloads a session. `test_accounting_onboarding.py:179,361` covers invalid date/FX evidence. This is setup, not the actual business cutover. | In the acceptance dataset, upload the declared cutover source, save the exact date, reload and verify actor/revision/hash and the same date in final preview. No new contract needed. |
| 2 `banks` — البنوك والصناديق والحسابات المالية | Exact active bank/cash/overdraft IDs; currency/FX evidence; every active account present, including explicit zero. No negative cash or default account. Projection `onboardingFinancialAdapter.js:38,76`. | AB23 explicit zero, bank identity and stale CAS; onboarding tests cover coverage/evidence. It does not prove completeness of a real owner bank list. | Use an independently enumerated synthetic account register, balances and retained bank/cash source files; verify every ID and sign through save/reload/preview, including zero, omitted and foreign-ID rejection. Existing account services only. |
| 3 `providers` — منصات الدفع وأرصدتها | Existing Salla/Tabby/Tamara/Emkan receivable identities plus explicit verified SAR bank binding; no balance derived from a card method. `accounting_onboarding_identities.py:143`. | AB23 persists provider17.00 and exact bank binding. `test_accounting_onboarding.py:303` proves missing-binding rejection. It is not a provider settlement execution. | Exercise all applicable declared provider identities, explicit zero/N/A with source evidence, bank binding/reload and final preview; independently compare to the synthetic provider statements. No new provider writer required for this setup stage. |
| 4 `employees` — الموظفون: رواتب وسلف وعهد | Exact native employee identity; salary payable, advance and custody remain three balances. Projection `onboardingFinancialAdapter.js:8`. | AB23 HTTP saves30.00 payable,10.00 advance,5.00 custody; real native employee/payroll suites are separate regression evidence. No connected business run links its employee screen to those flows. | Use the native employee/contract source and retained three-balance schedule, enter via the actual screen, reload and verify independent preview legs. If payroll is included in business UAT, reference a separately executed existing payroll scenario; setup persistence alone is insufficient. |
| 5 `suppliers` — الموردون وأرصدتهم الافتتاحية | Exact C1/C2 supplier identity; payable and advance distinct, no netting. `test_accounting_onboarding.py:330`; native supplier invoice/payment contracts already exist. | AB23 HTTP saves25.00 payable and10.00 advance. Separate supplier native tests do not demonstrate this complete user journey. | Actual screen input/reload against the same canonical supplier and independently documented balances; verify both preview legs and sibling external-person preservation. Reuse existing supplier lifecycle for any separately specified invoice/payment business scenario. |
| 6 `external_persons` — الديون الخارجية | Existing general-party contact with actual ID; receivable/evidence belongs to that identity. No name-based match. `accounting_onboarding.py` external-person API; `test_accounting_onboarding.py:533`. | AB23 creates a person through HTTP, rejects an empty name and saves receivable12.00. It does not prove every screen interaction. | Create/select through the actual UI, supply the evidence-backed amount, refresh and verify exact ID/phone/reference and supplier sibling lines; foreign/inactive identities must fail. Existing metadata contract suffices. |
| 7 `courier_contracts` — شركات الشحن: العقود وشرائح COD | Existing rich `ShippingContractInput`/calculator, retained-original reviews, immutable approval and existing native `accrue_fee`; current evidence required. `accounting_shipping_native_rich_contracts.py:19-90`, `accounting_shipping_native.py:194`. | C1 five browser scenarios perform real save/download/review/approve/reload/revoke. Real Mongo tests exercise tiers, independent VAT, non-COD, replay and atomic rollback. These are executed scenarios, but not linked to the full acceptance dataset. | Include the approved contract/version/evidence in the same acceptance scenario and compare concrete fee outcomes to the independently calculated existing contract. Prove revocation blocks new accrual, retry creates no second event and COD/fees remain separate. No new writer or prepaid economics may be inferred. |
| 8 `courier_balances` — شركات الشحن: الأرصدة الافتتاحية | Exact courier COD receivable and fee payable, separately saved in `couriers_cod`. Balances entered with courier editor, persisted from Stage8; they are excluded from rich contract terms. `onboardingFinancialAdapter.js:12`, `OpeningCourierEditor.jsx:77-80`. | AB23 HTTP saves150.00 receivable/20.00 payable with drivers alongside. This does not prove the combined Stage7 edit→Stage8 save→refresh interaction. | Execute that UI chain, verify independent balances and bank identity against retained opening evidence, refresh and compare final preview. Preserve driver sibling lines. Existing contract; no net130 settlement substitute. |
| 9 `drivers` — موصلو المتجر | Driver COD receivable and fee payable remain separate; POS review creates POS receivable only, later bank settlement is separate. C3 actual cash confirmation is independent of expected COD and explicitly matched to existing handover; C2 history preserves revisions. | AB23 saves100.00/15.00 opening balances. C2 proves history UI, C3 local73+3 proves capture/matching/replay/variance/rollback, and the completed C3 browser7 proves the actual driver→H2 cash workflow including shortage, multiple deliveries, lost-response retry and explicit matching. These are separate datasets from the onboarding session. | Tie the executed driver/H2 cash evidence to the overall stage acceptance record; independently confirm unchanged expected COD/fee economics. Include existing approve/reject bank/POS scenarios and later POS settlement if declared by business plan; no external processor source. Historical missing confirmation remains an explicit gap, not inferred physical cash. |
| 10 `inventory` — المخزون والتكلفة ومواقع التخزين | Delivered Track D catalogue/draft/valuation plus existing G47 physical import/approval, which is a separate lifecycle. `accounting_onboarding.py:124-140` only proves planned account valuation. `opening_inventory_service.py:132,258,283` requires verified active opening, exact item/location evidence and immutable approval. | D proves catalogue/variants/components/calculation/draft refresh; AB23 verifies account70.00+30.00=100.00 valuation. `test_g47_opening_inventory.py:100,111,128,238,262` exercises real import/approval/retry/guards/rollback with a synthetic sealed opening. None is an actual stock count, nor a wizard→physical-approval business journey. | Execute catalogue/draft/per-account valuation/evidence/refresh through UI under existing setup authority. A separate isolated G47 scenario can reuse its already delivered contract and a clearly declared verified-opening fixture; it cannot be called the prohibited actual opening/activation or evidence of counted Production stock. If the acceptance claim requires real physical stock, the missing item is an authoritative count/location/provenance artifact, not a new inventory writer. Do not set the wizard's physical-approval flag true. |
| 11 `payment_fees` — عمولات طرق الدفع والضرائب | Persisted effective-dated fee policy with percentage, fixed fee, VAT treatment, currency/evidence, selected by exact ID; overlap/missing-policy fail closed. `accounting_onboarding_ssot.py:50-119`. | AB23 UI selects a policy pre-created by the harness (`server.py:82`), not UI policy creation. `test_onboarding_ssot_contracts.py:80,93,230,264` covers selection/overlap, actual HTTP creation and real Mongo concurrency. | Through the actual fee screen create/select the declared policy, verify source/effective date/tax and reload; exercise overlap rejection and unchanged sibling provider/ad data. Independent expected fee calculation must use existing semantics. No fee-learning feature needed. |
| 12 `advertising` — الحسابات والمحافظ الإعلانية | Confirmed Track E binding to explicit financial wallet/payable IDs; original currency/FX preserved; wallet and payable distinct. `accounting_onboarding_identities.py:35`; existing Track E spending/funding writers remain authoritative. | AB23 HTTP saves40.00 wallet/15.00 payable. Binding, zero-opening and native report suites separately prove exact identity. No integrated UI→native funding/spend business record is implied. | UI select the confirmed native identities and record independent statements/FX, reload and verify separate preview legs. Attach separately executed Track E native scenarios when testing funding/payment/spend; never treat profile name or untyped external_ref as the financial identity. |
| 13 `prepaid` — المصروفات المدفوعة مقدمًا | Existing paid native obligation/invoice, calendar-day coverage, explicit selection and preserved source snapshot. `accounting_onboarding_ssot.py:122-198`. Automated amortization is not part of this setup contract. | AB23 HTTP selects invoice3660.00 with remaining3250.00 and exact replay; UI component tests verify displayed coverage/payment/selection. It is not a complete business UI journey. | Select the existing paid invoice from actual screen, independently recompute inclusive calendar days, save/reload/preview and prove source change invalidates the snapshot. Do not execute a scheduler or invent a capitalization policy. |
| 14 `obligations` — المستحقات والودائع والالتزامات الأخرى | Existing typed accrued expense, other payable/receivable and separate sales/input VAT identities/evidence. `accounting_onboarding_ssot.py:232-273`. `deposit` is deliberately not a supported economic category. | AB23 creates five facts10.00 each and rejects `deposit`; tests prove exact selected amount/currency/source and no netting. No complete UI/business evidence for these facts yet. | Create/select the supported facts through UI with explicit IDs/source amounts, refresh and verify independent legs. If the actual case includes a deposit, stop at its absent classification contract; no evidence presently establishes that such a balance exists or is necessary for this acceptance dataset. Do not silently recategorize it or claim generic deposit support. |
| 15 `review` — المراجعة النهائية والأدلة | Backend compiles/rechecks all seven sections, exact identity coverage, original bytes/FX, selected contracts and preview hash; review locks session. `accounting_onboarding.py:143-196`; no GL effect. | AB23 previews/reviews two synthetic sessions and verifies selected independent legs. C1/C2/C3 artifacts are separate and not a final unified acceptance review. | One traceable reviewed acceptance session must tie all applicable stages to the same declared source register, record independently calculated totals/legs and prove no omitted identity/netting. Changed evidence/identity must fail; reviewed edits remain locked. Record explicitly separate operational scenario evidence rather than claiming the setup preview validates those operations. |
| 16 `approval` — الاعتماد النهائي وفتح المحاسبة | Delivered contract is an explicit locked final stage: source readiness is distinct from live readiness. `accounting_onboarding.py:237-254`, `OnboardingWizardView.jsx:39`; opening-draft still uses atomic_owner/423. | AB23 proves visible lock, no post/activation call, live readiness false and no non-setup changes. Acceptance Smoke B execution is still pending at cutoff. These are guard passes, not actual final approval/opening. | Execute the actual locked-screen/readiness/423 acceptance case and attach Acceptance Smoke B evidence when available. This is permitted and needs no new economic contract. Actual Opening Post, activation, Production proof and changes to live readiness remain prohibited; they cannot be reported as executed or PASS. |

## What can proceed, and what actually needs a decision

1. **Proceed under existing authorization:** complete actual UI/HTTP scenario
   execution for stages1–15 with explicit synthetic source provenance, independent
   expected amounts, real Mongo persistence/reload and exact final source hashes.
   Reuse C1/C2/C3 and existing supplier/employee/advertising/provider native paths
   when the business scenario includes those operations. Their separate test
   results are supporting evidence, not a replacement for recorded scenario steps.
   Stage16's permitted scenario is the deliberate final lock and safe423 probe.
   No second authorization is needed for these already authorized isolated cases.
2. **Existing contract, missing execution/proof:** the Stage10 G47 physical service
   is delivered. Its synthetic setup prerequisites in a test are not evidence of
   a real count or of an end-to-end opening performed by this task. Preserve that
   distinction. The current source intentionally does not promote planned
   valuation to `inventory_physical_approval_verified=true`.
3. **Conditional genuine economic gap:** if a required business balance is a
   deposit outside the documented typed categories, its financial classification
   must be specified before it can be tested/implemented. Current evidence proves
   rejection, not a substitute category. Do not create a default account or add
   this as an unconditional blocker without an actual required source balance.
4. **Prohibited operation, not missing writer:** opening post/transition/activation
   already have contracts, but the user holds those operations. If “full UAT” is
   intended to certify their successful execution rather than correct refusal,
   that acceptance claim cannot be made in this run. Keep an explicit `NOT
   EXECUTED — HELD` result; do not relax the guard or silently mark it PASS. This
   does not prevent completing the permitted setup/business scenarios now.

## Required final evidence record

For each stage record the actual actor/owner, scenario/source IDs, expected
outcome fixed before execution, request/result identities, source HEAD/TREE and
manifest, original-artifact hashes, persistence/reload check, and assertions for
identity/owner isolation, exact retries and relevant atomic rollback. Attach
screenshots only to the operations they show. Scope no-write fingerprints to the
operation: setup may write its permitted metadata; an existing isolated financial
scenario may legitimately create its expected native journal; the C4 paused
probe must preserve its full trusted fingerprint. All Production writes stay0.

Report `PASS`, `FAIL`, `NOT EXECUTED`, and intentional `HELD` separately. Preserve
failed attempts and source provenance. Do not relabel AB23, navigation, an isolated
test, a selected snapshot or an Acceptance-only423 as complete business, physical
stock, Production Smoke B, or live financial readiness.

This audit changed this document only. No product/test code, database, service,
accounting contract, guard or write-control was changed by this audit.
Production financial writes=0; Merge/Deploy/Opening Post/Activation=NO.
