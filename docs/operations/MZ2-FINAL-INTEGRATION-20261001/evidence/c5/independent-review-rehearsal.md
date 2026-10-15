# C5 independent acceptance review — in progress

Reviewer scope: source/contract/oracle review only. Authorized output is this file;
no product or harness edits, no database/process operation, no Opening Post or
activation. First source inspection: `e7cc654d76283522542fcf2a5c80f2d3e89f66df`,
2026-10-02 Asia/Riyadh. The executing agent owns
`scripts/testing/mz2_business_uat/`; final evidence and exact immutable source
identity must be reviewed before an acceptance verdict.

## Pre-execution review

**No confirmed material product finding from source inspection so far.** A
candidate Stage 8 visibility finding was reported and then **retracted** before
execution: the initial reviewer incorrectly inferred that `termsOnly` hid opening
fields. Full JSX at `OpeningCourierEditor.jsx:77-80` proves these bank/opening
fields are rendered unconditionally inside the selected courier editor. The prop
only changes validation, labels and source-kind selection. The integration owner
was notified to make no change on that report. This was a reviewer error, not a
product correction or a test defect.

The intended existing journey remains valid to execute: enter independent
opening150/20 fields in Stage 7, save the shared financial facts from Stage 8,
then reload. `shippingTerms` explicitly excludes those amounts from the rich
contract payload. Execution must prove both forms of persistence separately.

## Independent monetary oracle

Read `source-register.json` before browser execution. Arithmetic below was
calculated independently with Python Decimal/date and explicit declared inputs;
no production compiler or financial writer was called for expected amounts.

| Canonical ledger identity | Side | SAR |
|---|---|---:|
| bank / c5-bank / main | debit | 1000.00 |
| payment_gateway / tabby / receivable | debit | 17.00 |
| employee / c5-employee / advance | debit | 10.00 |
| employee / c5-employee / custody | debit | 5.00 |
| supplier / c5-supplier / advance | debit | 10.00 |
| external_person / actual returned person ID / receivable | debit | 12.00 |
| courier / c5-courier / cod_receivable | debit | 150.00 |
| store_driver / c5-driver / cod_receivable | debit | 100.00 |
| asset / inventory-products / inventory | debit | 280.00 |
| asset / inventory-components / inventory | debit | 15.00 |
| ad_account / c5-wallet / balance | debit | 40.00 |
| asset / actual selected invoice identity / prepaid_expense | debit | 3250.00 |
| asset / actual c5-other-receivable fact ID / other_receivable | debit | 30.00 |
| tax / actual c5-input-vat fact ID / input_vat | debit | 10.00 |
| liability / c5-overdraft / bank_overdraft | credit | 40.00 |
| employee / c5-employee / salary_payable | credit | 30.00 |
| supplier / c5-supplier / payable | credit | 25.00 |
| courier / c5-courier / payable | credit | 20.00 |
| store_driver / c5-driver / delivery_fee_payable | credit | 15.00 |
| ad_account / c5-ad-payable / debt | credit | 15.00 |
| liability / actual c5-accrual fact ID / accrued_expense | credit | 10.00 |
| liability / actual c5-other-payable fact ID / other_payable | credit | 20.00 |
| tax / actual c5-sales-vat fact ID / sales_vat_payable | credit | 25.00 |

Totals before equity: **14 debit legs = 4929.00; 9 credit legs = 200.00**.
The already-delivered compiler's explicitly defined opening counterpart is
`equity / opening_balance_equity / main`, **credit 4729.00**. Thus the exact
preview must have **24 nonzero entries, debit = credit = 4929.00**. This existing
counterpart is a preview contract, not a new/default account or an executed
opening. Explicit `bank / c5-cash / main = 0.00` must occur in zero accounts and
source lines, never disappear as an unspecified/omitted balance.

Canonical source: `accounting_financial_accounts.py:52-120,333-485`, plus
`accounting_opening_categories.py:10-30`. Dynamic IDs must be obtained from the
actual create/select response and crosschecked to the persisted source reference
and the same preview identity, not substituted by names.

Independent date/valuation checks:

- Cutover `2026-10-01T00:00:00+03:00` is `2026-09-30T21:00:00+00:00`.
- Planned inventory: 2 × 50 = 100; 3 × 60 = 180; 1.5 × 10 = 15.
  Product account 280, component account 15, total **295**. Physical inventory
  approval must remain false; this is synthetic planned valuation only.
- Paid annual invoice: inclusive 2026-08-21 through 2027-08-21 = **366 days**;
  41 consumed days before 2026-10-01, 325 remaining. 3660/366 = 10/day;
  consumed **410**, remaining prepaid **3250**. Matches the documented native
  inclusive-end contract (`accounting_onboarding_ssot.py:122-158`).
- Declared rich courier rate: shipping 20 plus 15% exclusive VAT = **23**.
  For a concrete COD 150, commission 0.01 × 150 + 2 = **3.50 gross**, already
  inclusive of its own VAT. Total fee **26.50**; COD remains **150**, never a
  net 123.50 collection. This is an independent quoted-outcome oracle, not
  evidence that an accrual scenario has run or that opening payable20 changes.
- Provider policy 2.5% and fixed1, exclusive VAT: source policy persistence is
  testable. Do not assume a VAT rate or claim a settled fee amount without its
  separate declared tax/source contract and an actual applicable operation.

## Stage-specific acceptance requirements

All entries remain **NOT EXECUTED / awaiting supplied evidence** for this review.

| Stage | Observable minimum in the connected scenario |
|---|---|
| 1 cutover | Original artifact, exact aware date, save/reload actor/version/hash and unchanged final cutover. |
| 2 banks | Explicit three-account register1000/0/40, exact IDs/currency/signs; missing amount/foreign identity refusal. |
| 3 providers | Tabby17 plus explicit c5-bank binding, retained evidence and reload; declare other providers N/A or explicit zero without inference. |
| 4 employees | Same native employee, separate30 salary credit/10 advance debit/5 custody debit; no net15. |
| 5 suppliers | Same supplier, payable25 and advance10 independently retained; preserve person sibling. |
| 6 external persons | Actual UI creation/selection, persisted returned ID/phone/source reference and receivable12; supplier legs preserved. |
| 7 courier contracts | Actual save/original download/review/approve/reload bound to declared courier/version. Only separately executed native accrual/revocation proves economics at the writer boundary. |
| 8 courier balances | Actual Stage7 UI editable150/20 → Stage8 save/reload and distinct preview legs; existing rich terms/driver sibling preserved. |
| 9 drivers | Distinct100 COD and15 payable; clearly attribute any POS/cash operational subscenario to its separate dataset/run, never imply opening preview exercised it. |
| 10 inventory | Real catalogue variant/component selection, exact planned quantities/costs/account totals, metadata persistence/reload; no attested count or physical approval claim. |
| 11 payment fees | Actual UI create/select2.5%, fixed1, exclusive VAT, SAR, effective2026-01-01 with evidence; overlap rejected; siblings preserved. |
| 12 advertising | Explicit existing confirmed hybrid binding, exact wallet40/payable15 financial IDs, source identity/reload; no net25 or posting claim. |
| 13 prepaid | Actual invoice selection, exact source snapshot/days325/amount3250, reload; do not imply scheduler execution. |
| 14 obligations | Actual UI create/select five supported facts10/20/30/25/10, actual IDs/references retained; sales and input tax remain separate. Declared deposit absence does not grant deposit support. |
| 15 review | Same session, all seven sections source-complete, exact24 entries/one zero identity, original hashes verified, persisted review hash/version and subsequent edits refused. |
| 16 approval | Actual locked UI, no post/activation request, ready_for_live_post false; separately paused owner's423 must be labeled as that owner's probe, not the reviewed owner's state. |

## Evidence boundaries

The proposed fixture seeds canonical employee/supplier/driver/accounts/ad binding,
paid invoice and inventory catalogue as explicit prerequisites. Their existence
does not prove UI creation of those prerequisites. It declares one unpaused
setup owner and a distinct paused guard owner; neither write-control may change.
Setup/preview/review can change allowed metadata, but zero journal groups/legs,
zero opening drafts/posts, no activation and no physical approval are required.
Its actual JWT verifier is useful authority evidence; minted fixture tokens are
not evidence of a login/MFA journey. C4 Smoke B is a separate run.

Do not declare full financial operation or Production readiness from the
sixteen setup screens. Record supported setup outcomes, separate operational
proof, and held opening/activation/physical-stock actions explicitly. Final
verdict remains **PENDING** until actual run artifacts and source hashes arrive.

## Harness contract review before execution

The source register now explicitly declares Salla/Tamara/Emkan inapplicable to
this synthetic business (not balances inferred as zero). Tabby is the only
configured provider, with its explicit positive balance and settlement bank.

One concrete harness assertion defect was reported to its owner before the
acceptance run: `browser.cjs` Stage15 initially addressed
`preview.zero_accounts[0].financial_account_id`. The delivered compiler at
`accounting_financial_accounts.py:449-454` deliberately exposes zero identities
as `entity_type`, `entity_id`, `sub_account`, `account_snapshot`, and evidence.
The proper exact oracle is bank/c5-cash/main and snapshot.id=c5-cash. Nonzero
canonical lines have a separate `financial_account_id` field (:420-435). This
is a test response-shape correction, not a production contract change and not
permission to remove the explicit-zero assertion.

The current scenario has a complete declared positive setup journey and two
negative API cases (unsupported deposit, reviewed edit), plus a separate
paused-owner handoff refusal. Omitted/foreign bank identity, effective-policy
overlap, changed prepaid snapshot and other failure probes mentioned in the
stage matrix are not executed by the current browser script. Existing separately
executed tests may support those invariants with exact source/run attribution;
they must not be reported as fresh browser steps from this session.

The server's explicit verification probe counts `general_ledger`; therefore a
claim that the entire fixture made zero Legacy reads would be inaccurate. This
verification read is not a business financial authority read. Fingerprints prove
unchanged data, not absence of reads. Any zero-Legacy-access assertion must be
scoped to instrumented business requests or supported by separately attributed
SSOT audit evidence.

## Rehearsal 3 — independently verified execution, 2026-10-02

Evidence directory: `c5-business-uat-rehearsal3` beside this report. Product HEAD
reported by fixture and end-of-run proof is
`586f706301050c5f63274b77b13406696cbbc556`, TREE
`01d377faaeff009670278d836bd1c9f5b2dab854`. At inspection the C5 harness itself was
untracked and main checkout had documentation changes. This is a genuine
rehearsal, not yet a clean immutable complete-source acceptance certificate.
The fixture started a UUID `mz2_c5_business_2f4dc8978555489c8241eacf32cbd480`
on the existing isolated `mz2c` replica. I did not connect to the database or run
the browser; I independently inspected its retained raw artifacts and source.

I executed an offline verifier over the raw JSON, recomputing the following
without importing product calculations or using the runner's expected-legs file:

- All sixteen records belong to the same session
  `onboarding-c4c505e3a90b661697da30908ebe0e5d1bfa201070f4cf5111b1abd680b1abaa`.
  It is reviewed, version25, with contiguous audit versions1..25 and the exact
  same owner/actor throughout. Review hash equals preview hash:
  `d67d2e4991664222481f20a017d41153acd0383abcd969a39beb67291af92527`.
- All seven sections are complete. I reconstructed all24 nonzero canonical legs
  using the declared source amounts and actual persisted person/prepaid/fact IDs.
  They match the real preview as a multiset, with24 distinct canonical triples.
  Debit4929, pre-equity credit200, equity credit4729; bank/c5-cash/main is the
  sole explicitly documented zero. The earlier response-shape assertion defect
  is corrected in the executing harness; no zero assertion was dropped.
- Actual selected paid invoice preserves c5-invoice/c5-subscription and owner;
  independently recomputed366/41/325 days and3250 remaining. Five persisted
  typed-fact references retain the exact categories/sides/amounts. Provider
  policy ID is retained in the shared providers section with2.5%/1.00/exclusive
  VAT/effective2026-01-01. Explicit provider bank and TrackE split identities
  survive into preview mappings; no COD/fee, supplier/advance, employee balances,
  wallet/payable or tax netting appears.
- Nine original-artifact records, the frozen source register and the downloaded
  reviewed rich-contract original all match SHA256
  `99be3dd42b6f9f1f676df5608ec965fb8270f00efa37c92bf3b520197acf12d5`,3995 bytes.
  Each artifact is owner/actor bound; every final line/section evidence ID resolves.
- Rich contract
  `cb7c2515cb24b9023857ae3f2b83a5d66558c16ba67a17e56a6423388f5df843`, version6,
  is actually approved by that owner. The post-reload GET contains approved
  contract/shipping_tax/commission_tax reviews tied to the same retained bytes.
  Its immutable terms exclude opening balance fields. No fee accrual was executed
  or inferred from this metadata approval.
- Comparing before/after fingerprint maps myself finds changes only to allowed
  setup metadata: existing opening evidence/shipping setup and new source files,
  contacts, session, facts, prepaid selections and fee policies. All other
  fingerprints are equal. Both owner control records are equal after excluding
  only serialization revision. Setup revision changes0→9; paused guard owner
  stays revision0 and paused. No control flag/reason/revision changes.
- Journal groups, V2 ledger, Legacy ledger, opening drafts and physical inventory
  initialization counts remain0. Four actual negative HTTP results are recorded:
  unsupported deposit422; reviewed edit409/onboarding_session_locked; a distinct
  initially-paused owner's nonexistent session404 then handoff423/mz2_writes_paused.
  No financial post/transition/activation/write-control call appears. Browser
  errors and blocked external requests are empty.

### Actual UI discrepancy: Stage11 remains unresolved at this rehearsal

The retained `stage15-reviewed-desktop.png` visibly reports Stage11 as
`ناقص · دليل ناقص`, with missing evidence, while its persisted provider policy
and evidence exist and the backend reports source_ready=true. This means the
sixteen script PASS records alone are **insufficient to close UI acceptance**.
The script verified persistence/preview but did not assert the displayed Stage11
status/evidence on the final screen.

Confirmed source cause: `onboardingFinancialAdapter.js:5,147-153` restores only
FINANCIAL_STAGE_SECTIONS, which excludes payment_fees. The parent component
separately maps fee saves to providers (`AccountingOnboarding.jsx:47`), but
`OnboardingWizardView.jsx:27,38` reads sections.payment_fees for its navigation
and final-review indicators. The unpopulated presentation data becomes
incomplete/missing evidence. A status/evidence-only projection from the existing
saved SSOT policy contract is an A/B wiring correction, not a new policy writer
or accounting contract. Blindly adding fees to the financial amount projection
map would be inappropriate because `project` expects monetary FIELDS there.
Root and the implementing agent were informed. No product files edited by me.

The Stage7 screenshot actually says `عقود محفوظة ومراجعة مستقلة` (saved contracts,
separate review), matching its deliberate static description. It does **not** say
unsaved, nor claim every contract is approved. No Stage7 bug is established by
that label. The rich contract's actual approval is independently verified above.

### Acceptance classification and residual gap

Parent supplied the binding user wording authorizing closure of the five C items,
including full_16_stage_business_uat, while explicitly prohibiting Opening Post,
Activation, write-control changes and live financial mutation. The later Smoke B
Allowance is expressly Acceptance-only. I read the C4-C5 acceptance-contract audit
with its superseding decision and the delivered stage contracts.

The accurate successful scope after the concrete UI correction and frozen rerun
is **complete sixteen-stage synthetic SETUP business acceptance, with Stage16's
intended lock proved**. This is more than navigation: actual inputs/uploads,
canonical selection, rich review, persistence, reload, independent preview and
review locking are executed. It is not successful opening/activation, a physical
stock attestation, every downstream operational finance journey, or Production
readiness. Those must never be silently included in an unqualified PASS.

No delivered setup contract requires actual Production stock or successful
opening/activation to occur in this prohibited run. `accounting_onboarding.py`
explicitly distinguishes planned valuation from physical approval (:124-140),
source completeness from live gates (:198-254), and the existing final screen is
locked (`OnboardingWizardView.jsx:39`). Their non-execution is **HELD**, not a
newly invented missing-writer/feature prerequisite. Separate C1/C2/C3 operational
scenarios retain separate provenance; separate full-app C4 Acceptance Smoke B
also does not set production_verified.

At this review's cutoff the actual residual C5 acceptance gap is the false
Stage11 visible status plus final clean source freeze/rerun provenance. Thus the
rehearsal's persistence/arithmetic/no-financial-effect checks are independently
PASS, but **C5 closure remains pending that bounded UI correction and exact-source
acceptance rerun**. Physical stock approval, Opening Post, Activation and all
Production enablement remain explicitly NOT EXECUTED/HELD regardless of C5.
