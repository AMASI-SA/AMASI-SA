# MZ2_ONLY_SSOT_AUDIT_COMPLETE

**Verdict: MEZAN2_ONLY_SSOT_AUDIT = FAIL / NOT PASS. Implementation remains paused.**

Completion means the twelve requested audit questions have a resolved source/dispatch finding or an explicitly demonstrated missing contract. It does not mean the requested MZ2-only properties hold. Several do not. The earlier occurrence inventory is a search index, not a claim that every transitive module is a post-cutover path. This report resolves the material dynamic dispatches for the requested flows; it does not claim live execution of financial requests or provenance of unqueried tenant records.

## Targets and evidence boundary

- Fresh Production SHA `728730366cfee6c906172c7c14a2a67251ed1065`, tree `70b4c3d7b501f7c8a31767ceb61d5ac4cb204df9`.
- Production inspected in detached clean worktree `C:/Users/amasi/.codex/tmp/mz2-ssot-prod-audit` to avoid confusing #1210 WIP with deployed code.
- PR #1209 remains open Draft, unmerged; exact reviewed head `3763e04b46eb97891c07760fab2bce7331356cb9`, base Production above. Separate detached inspection worktree.
- Static call/route/producer tracing plus local runtime probes with synthetic read-only objects; no Mongo connection in probes. Existing isolated reporting/warehouse tests described below. No Production data entered or mutated.
- The earlier Stage06 choice is resolved by the owner: design a new MZ2 registry, never counterparties. Stage11 likewise requires a new effective-dated MZ2 fee-policy design. These are designs below, not implementations.

## Dynamic dispatch resolved

1. `server.py:198` supplies a normal Motor database. `AccountingDatabase` (`accounting_write_control.py:19-35`) delegates the same collection name to a request-local transaction. `SessionDatabase` (`accounting_atomic.py:60-76`) adds session binding, not name conversion. Runtime probe preserved `accounts`, `general_ledger`, `mz2_financial_accounts`, `accounting_general_ledger_v2` exactly.
2. `ledger_core.post_txn_group` (`:407-424`) enters `atomic_owner`, then explicitly asks for writer **legacy**; ledger entry insertion is `general_ledger` (`:360`). It never dynamically becomes `post_journal_v2`.
3. Writer state is persisted in `mz2_atomic_owners`. `legacy_active` permits legacy writer; `transition_blocked` rejects; `v2_active` rejects legacy with `accounting_legacy_writer_disabled`. Missing transition fields default to legacy state, not V2. Financial pause is a separate earlier guard.
4. V2 writer `accounting_ledger_v2.post_journal_v2` writes `accounting_journal_groups_v2`, `accounting_general_ledger_v2`, `accounting_audit_log_v2`, with `accounting_ledger_sequences_v2`; explicit owner, operation, transaction, opening-integrity, period/cutover and idempotency contracts apply.
5. Reporting dispatch (`accounting_mz2_reports.py:306-327`) selects legacy or V2 by persisted writer state and refuses transition-blocked. No missing-V2-data fallback to legacy exists in that selector. Separate consumers still introduce legacy identity dependencies, as below.
6. Settlement lifecycle routes register before compatibility routes (`financial_provider_apps.py:133-140`); first-match Starlette dispatch chooses lifecycle post. Runtime router construction confirms handler ownership. Provider binding remains the old binding handler.

## Complete requested financial flow map

Financial provider accounting routes below use the registered prefix `/api/financial-provider-apps/accounting-module`; suffixes are shown for clarity. Store-delivery and purchase routes retain their own prefixes.

|Flow|Actual source and identity|Bank/cash|Writer/destination|Tenant/cutover/allocation result|
|---|---|---|---|---|
|Invoice supplier payment: POST `/api/purchase-invoices/{inv_id}/payments`|`supplier_identity_service.require_linked_supplier`: **suppliers + counterparties(kind=supplier)**; invoice/liability must be approved `schema_version=g47-v1`|`mz2_financial_accounts` then **accounts fallback**|`supplier_payment_service.pay_supplier` -> `post_journal_v2` -> V2 groups/ledger/audit; `mz2_supplier_payment_operations`, revisions, liability projection|Owner-scoped, V2 active, unpaused, verified opening/inventory/cutover; exact invoice liability/journal identity and allocation; source identities FAIL|
|Unallocated supplier payment: POST `/api/purchase-invoices/supplier/{cp_id}/payments`|Same legacy supplier bridge|Same fallback|Same V2 writer, no arbitrary invoice allocation|Unallocated payable = aggregate payable minus verified remaining invoice allocations; CAS and idempotency; identity FAIL|
|Supplier statement / ledger-detail|Same identity bridge; G47 projections + V2 ledger; detail route switches by writer state|Payment context enumerates legacy and MZ2 banks|Read-only V2 branch; older branch when not V2|Amounts can be V2 while supplier identity remains Legacy; FAIL|
|Daily expense / supplier classify-outgoing|`mz2_daily_movements`; **suppliers**, **expense_categories** or built-in code|**accounts** via `_bank_or_409` -> `_find_bank`|`ledger_core.post_txn_group` -> **general_ledger**; only then event/consumed movement/audit|Owner movement, unclassified, provider ambiguity denied, active cutover, strictly after cutover, not future; V2-active writer rejects. Capability GAP + identity FAIL|
|Daily manual incoming/outgoing/upload|Owner statement/manual evidence; MZ2 movement/request/file/audit records|**accounts**|Metadata intake; downstream financial classifier still legacy|MZ2 collection name does not establish canonical bank. Intake is not financial posting|
|Driver COD remittance / driver earning payment|`store_drivers`; operational driver collections/earnings/settlements|**accounts** active bank/cash|`store_delivery_accounting.post_settlement_journal` -> legacy post_txn_group -> **general_ledger**|P02/event/cutover gates; V2 writer transition blocks journal. Not a V2 bridge|
|Driver explicit net-settlement|Same source; explicit positive earning offset only|Same legacy bank|Gross COD, payable and bank legs through legacy writer|No silent netting found; non-net types reject offset. Preserve this in any later migration|
|Shipping P02 COD / courier fee recognition|`mz2_salla_order_evidence`, exact driver/assignment, approved `mz2_shipping_rate_policies`|Subsequent movement/binding still legacy-dependent|`post_txn_group` -> **general_ledger**, `mz2_shipping_accounting_events` registry|Owner, order-created/event cutover and P02 evidence gates. Correct blocking does not make writer V2-ready|
|Shipping settlement preview/post|Exact store driver or policy courier; `mz2_daily_movements`|Bank ID copied from movement, **no canonical FK revalidation** here|legacy journal, MZ2 event + movement consumption within owner transaction|Independent COD/payable ceilings; explicit net type only. FAIL bank lineage + missing V2 bridge|
|Provider settlement binding|Canonical provider catalog, `accounting_provider_bank_bindings_v2`|**accounts**, fallback `settings.default_bank_for_*` labelled legacy_copy|Binding metadata under protected financial router|No MZ2 FK. Collection suffix v2 does not fix it|
|Provider reviewed settlement post|`accounting_settlements_v2`, matched/reviewed evidence, receipt and refund gates|Post service re-resolves **accounts**|`accounting_settlement_service.post_reviewed_settlement` -> legacy `post_txn_group`; updates settlement/receipt state|Owner scoped; balance reader opening/cutover predicates; lifecycle is authoritative; V2-active journal blocked. FAIL|
|Advertising native V2 reporting|`mezan_integration_accounts_v2`, exact integration ID + provider/external account; native daily reporting sources|No approved Production MZ2 wallet/payable binding|**No Production MZ2 spend accounting bridge found**|Analytical spend is not a ledger posting. GAP; cannot assert zero-legacy financial path exists|
|Advertising old financial endpoints|`counterparties`, `ad_account_ledger`, legacy liability/bank paths|Legacy identity path|Legacy ledger sink, with some legacy side writes before sink|Reachable old router; financial pause at ledger sink alone is not proof earlier side writes are prevented. BLOCKER; no live probe sent|
|New opening financial draft/post|`mz2_financial_accounts`, explicit opening/evidence snapshot, versioned draft|Canonical accounts on this route|V2 opening journal plus `mz2_opening_balance_drafts`|Does not disable separate old opening route. Generic entity category is not registry verification|

### Supplier and daily evidence

`purchase_invoices_routes.py:223-237`; `supplier_identity_service.py:6-14`; `supplier_payment_service.py:47-62,80-145,234-243,294-338`; `purchase_receiving_service.py:29`; `supplier_ledger_detail_routes.py:82-89`; `liabilities_routes.py:1848-1863`. Shared `purchase_invoices`/`liabilities` in G47 are **schema-isolated MZ2 projections**, not blanket Legacy: exact schema and journal identity validation distinguish this branch. Ordinary legacy liabilities are not thereby approved.

Daily evidence: `accounting_daily_movements.py:178-201,343-347,895-923,934-1025,1057-1117,1148-1178,1237,1271`. Daily supplier classification only checks aggregate payable; it does not use the dedicated invoice allocation/revision contract. A future migration must unify these contracts rather than merely swap ledger function names.

### Driver/courier evidence

`store_delivery_settlement_routes.py:74,82-123,128-154,174-241`; `store_delivery_accounting.py:58-108,183-217,340,372-390`; `accounting_shipping_settlements.py:100-184,223-291,350-436,474-519`; `accounting_shipping_p02.py:596-600,656-795,1106,1136`.

Courier binding is another explicit legacy branch: `accounting_courier_bank_routes.py:106-150` reads `settings.shipping_companies` or defaults, resolves `accounts`, and writes `accounting_provider_bank_bindings_v2`. This is not the MZ2 operational courier identity source used by the proposed Wizard fix.

## Old opening routes: reachable, guarded, not removed

Actual router construction from exact Production found:

- GET `/financial-provider-apps/accounting-module/opening-balances`, original `get_state`, not mutation-wrapped.
- POST same base `/preview`, `/approve`, `/activate`, all registered and wrapped by `protect_accounting_routes`.
- New canonical financial-account opening paths are distinct; new onboarding does not shadow or unregister the old paths.

`create_opening_preview:502`, `approve_opening_preview:589`, `activate_p01:714` begin with `assert_writer_allowed(...,'legacy')`. Local execution with synthetic `v2_active` row returned 423 `accounting_legacy_writer_disabled` for all three, before any source read beyond `mz2_atomic_owners`. Runtime evidence is in `runtime-route-evidence.json`.

Under paused=true the outer transaction prevents callback. Under `legacy_active` and allowed pause/permission state, old paths remain eligible and read legacy entities (`accounting_module_opening_balances.py:336,372,462,492-493`). GET `opening_state:760+` still reads accounts/employees regardless of new Wizard adoption. Thus **BLOCKER under the owner's reachable-legacy-route rule**, while accurately recording that V2-state writes are guarded. A calendar date alone does not force `v2_active`. No live POST, approve or activation was attempted.

## Advertising: all provider dispatches resolved

|Provider|V2 identity selector|Native daily evidence collection|Identity carried into daily facts|
|---|---|---|---|
|snapchat_ads|snapchat_native_data_sync.py:50-61|mezan_snapchat_performance_daily_v2|External account plus mezan_integration_account_id; performance writer :271-273,326-335|
|meta_ads|meta_account_selection.py:251-266, API provenance + selected|mezan_meta_performance_daily_v2|Tenant/provider/ad_account_id/date; meta_native_reporting.py:37,398-415|
|tiktok_ads|tiktok_native_reporting.py:189-204|mezan_tiktok_performance_daily_v2|Tenant/provider/ad_account_id/date; writer :321-340|
|google_ads|google_ads_reporting.py:225-246|mezan_google_ads_performance_daily_v2|Tenant/provider/ad_account_id/date; writer :514-535|

All selectors start from mezan_integration_accounts_v2. Daily facts do not uniformly carry its exact immutable ID: future bridge must explicitly validate tenant/provider/external-account linkage to the exact Integration V2 ID and preserve it in the accounting snapshot. Never infer a Legacy counterparty link. Production has no approved MZ2 daily-spend accounting bridge; wallet/payable opening account types alone do not supply one.

Legacy dynamic sync is resolved from `ad_account_routes.py:166-177,236`: Snapchat reads `snapchat_account_daily` scoped to external account, then empty-result fallback to unscoped `snapchat_ads_daily`; Meta reads `meta_ads_daily` scoped account_id; TikTok reads unscoped `tiktok_ads_daily`; Google has no source in this dispatcher. Tenant/date filters exist, but requested date window is not a MZ2 cutover predicate. An in-memory call to actual `_fetch_daily_spend` recorded these sources/filters with no DB connection. `_run_sync_for_all:2930-2971` starts from Legacy counterparties; server scheduler imports at `server.py:4711` establish another entry point requiring separate enforcement.

Reachable raw ad router: `server.py:4252` -> `attach_ad_account_routes`; `ad_account_routes.py:265` reads counterparty identity; banks at `:541,1106,1330`. Spend directly changes counterparty at `:1397`, liabilities at `:1413-1447`, ad_account_ledger via `_ledger_write:418`, before `_post_spend_to_ledger:1482`. The latter uses legacy compute_balance/post_txn_group (`:445-518`). The separate financial-provider route wrapper does not encompass those prior side effects. This is source-semantic evidence; **no live spend request was sent**. An attempted additional in-memory route-order probe stopped at FastAPI `_IncludedRouter` lacking `.path`, before endpoint execution, and is not counted as a successful runtime test.

## Reports: amounts versus identity provenance

`accounting_mz2_reports.py:306-327` resolves backend state. V2 journal/trial-balance reads use `accounting_general_ledger_v2` via `accounting_ledger_v2.py:1640-1667`, owner + exact operation + posted state + as-of predicate; validate immutable groups/audit and opening. Caller applies cutover and canonical account/explicit-zero coverage (`accounting_mz2_reports.py:226-294`). No legacy amount reconstruction is found in this V2 reader.

However `mz2_financial_position` unconditionally reads `accounts` at `:352` and merges account classification with canonical accounts. Local execution given a valid synthetic V2 scope reached `DENYLIST_READ:accounts`. **Amounts remain ledger-derived; classification is legacy-dependent.** Do not misreport this as proof legacy balances are summed.

Driver summary uses `store_driver_ledger_balances` -> `ledger_core.compute_balance` -> `general_ledger`, outside the MZ2 report cutover/operation predicate. Supplier statements' V2 amount path still depends on the legacy supplier identity bridge. Therefore the statement “all post-cutover reports cannot reconstruct from Legacy” is **not proven and materially false for the audited combined surface**.

## Warehouse provenance: resolved negative proof

Product and component V2 writers are identifiable; warehouse tables are shared:

- `warehouse_location_v2_routes.py:13` imports original `warehouse_locations_warehouses`, `warehouse_locations_cabinets`, `warehouse_locations` constants.
- `order_engine/__init__.py:137-139` registers original and V2 writers. V2 warehouse/cabinet writes at `warehouse_location_v2_routes.py:147,196`; barcode/QR fields `barcode_value` / `qr_value` at `:190-191`, also `warehouse_room_routes.py` writer path.
- Production `frontend/src/services/onboardingInventoryCatalog.js:14` requests old `/warehouse-locations/locations`; filtering at `:45` checks disabled state, not creation contract.
- Neither registration nor selectable record predicate proves every warehouse/cabinet/location was created through the accepted V2 contract. No immutable per-row accepted-contract provenance is required.
- #1210 WIP catalog still reads these shared tables, without provenance. Its barcode projection differs from actual V2 field names; recorded only, not fixed.

**Result: BLOCKER / missing provenance contract.** Existing rows cannot be silently relabelled V2. Proposed later explicit validation/link record must bind owner, exact warehouse/cabinet/location IDs, relationship revision, barcode/QR identity, accepted contract version and audit evidence. Owner-controlled review is needed for historical rows. Financial inventory valuation remains separate from physical quantity approval.

## PR #1209 salary audit

Exact head `3763e04b46eb97891c07760fab2bce7331356cb9`; no merge. Three layers must be distinguished:

1. **Salary setup**: `employees_v2_routes.py` uses `employee_setup_atomic_owner`; `operational_atomic.py:27-32,279+` permits writes only to `mezan_employees_v2`, `mezan_employee_salary_contracts_v2`, `mezan_employee_events_v2`. This is real V2 setup, not a write-pause bypass for accounting. History read protection uses general ledger/events and must not be mistaken for a financial writer.
2. **Identity**: `employee_payroll_status.contract_salary_row:307-316` returns `id = legacy_salary_id or contract.id`, alongside `employee_v2_id`. `_employee` (`accounting_employee_finance.py:121-139`) consumes that as canonical financial ID; fallback V2 employee accepts `financial_entity_id` / `legacy_employee_id`. Local pure-function probe with V2 employee `v2-employee` and legacy_salary_id `legacy-salary-identity` returned financial `id=legacy-salary-identity`. Reading a V2 collection therefore does **not** prove exact V2 employee financial identity. New native salary contract may use employee ID; migrated ones retain compatibility identity.
3. **Financial execution**: PR's accrual/payment still import legacy `ledger_core.post_txn_group` (`accounting_employee_finance.py:28,263,647`). Cash gets bank ID from MZ2 daily movement whose intake resolves legacy accounts. Under V2-active the sink is rejected; the PR does not introduce a V2 payroll writer. `ledger_core.py:319+` changes employee validation for payroll source only; other employee paths retain operating_salaries/employees fallback.

`find_employee_salary` and `employee_salary_rows` themselves read V2 employee/contracts, not operating_salaries. Do not falsely call those helpers legacy collection readers. Existing migration/diagnostic code in employees_v2_routes still reads operating_salaries; scope it to explicit history/migration only. `liabilities_routes.py:689-770` now uses the new salary calculation but retains legacy `liabilities` salary generation; its cash/payment paths still reference accounts. These inherited consumers are not newly introduced storage, but PR reuse does not satisfy the new all-path denylist.

Salary change safety (`employees_v2_routes.py:319-351`) consults general_ledger and MZ2 employee events, not the V2 ledger itself. Future V2 financial history coverage must be explicit. **Verdict: setup capability partly conforming; financial identity/writer/consumer chain NOT MZ2-only. Do not merge as proof of this audit gate.**

## Contract/design only: Stage06 external people

Proposed new registry `mz2_external_persons` (name proposed, not created): tenant owner + opaque immutable person_id, person kind, display name, optional contact fields, active/archive state, version, created/updated actor/time, append-only audit. No legacy alias accepted as person_id. Duplicate detection yields explicit conflict; matching a name/phone never silently links a record. Optional historical cross-reference is diagnostic and cannot satisfy an operational FK.

Opening external receivable line must reference exact tenant-scoped active person_id plus typed receivable account/claim ID and evidence, currency/FX; an explicit reviewed liability/claim contract supplies financial meaning. Native create/save is setup metadata only; financial posting remains a separate guarded contract. Missing registry/entity -> MZ2_ENTITY_MISSING; owner-approved explicit mapping -> separately audited link, never auto-migration.

## Contract/design only: Stage11 provider fees

Proposed `mz2_provider_fee_policies`: owner, provider identity, policy_id, fee_type (explicit approved enum), calculation mode percent/fixed/combined, decimal rate/fixed_amount, currency for fixed amounts, VAT treatment, explicit tax-policy version/reference, effective_from, effective_to, revision, status, evidence IDs and audit actor/time/reason. Half-open effective windows with overlap prevention, deterministic rounding rule and snapshot hash. Unknown currency/tax treatment/evidence -> GAP, not SAR/default fee inference. Updates create forward revisions; historical accounting retains frozen policy snapshot. No reads from settings/payment_methods as operational fallback. Owner still needs to supply actual contract rates/evidence and approve effective windows; design does not choose tax treatment or rates.

## Contract/design only: Stage14 exact entities

|Opening class|Existing evidence/capability|Required authoritative contract, not yet implemented|
|---|---|---|
|Accrued expense|Opening category and manual line only; recurring V2 obligation may supply evidence|MZ2 accrual ID, exact approved obligation/invoice or explicit accrued-service claim, covered period, counterparty FK, amount/currency, recognition evidence, unpaid amount and immutable revision|
|Other payable|Generic opening category is not registry|MZ2 payable/claim ID, exact creditor registry/FK, legal/business reason, source document, due date, currency, principal/settlement state, revision/audit|
|Sales VAT payable|`mz2_sales_tax_policies` provides effective policy; order recognition has snapshots|MZ2 tax balance identity by owner/tax registration/jurisdiction/period, source tax evidence/return and reconciled balance. A rate policy alone cannot prove liability amount|
|Input VAT|G47 purchase tax evidence/journal may be typed evidence|Exact purchase tax evidence/document/line identity + tax-period/control-account binding, recoverability classification and reconciliation; do not treat legacy liability row or manual category as source|
|Deposits / other liabilities|No separate accepted opening identity contract established|Typed MZ2 deposit/obligation registry, exact holder/creditor, refundable/nonrefundable terms, source receipt/claim, currency, remaining obligation, revision and audit. No silent other_payable catch-all|

All records need owner scoping, immutable source identity, explicit zero/evidence semantics, canonical financial-account FK where applicable, idempotency/CAS, no netting without explicit approved operation, and transaction-bound posting to V2 ledger after all existing gates. These proposals do not authorize record creation or posting.

## Denylist enforcement points and remediation batches

1. **Identity foundation**: supplier_identity_service, employee payroll identity adapter, external-person registry, expense identity contract. Exact immutable MZ2 IDs; legacy references remain diagnostic only.
2. **Canonical bank binding**: supplier payment context/_bank; daily `_bank_or_409`; provider `_find_bank`/`_binding_view` and post service; courier binding; driver settlement; shipping movement consumption. Require active owner-scoped `mz2_financial_accounts` FK and explicit currency, no accounts/settings fallback. Remove legacy choice enumeration as well as validation fallback.
3. **Operational V2 writers**: daily supplier/expense, provider settlement, shipping/driver, payroll. Port full event/idempotency/allocation/reversal contracts to V2 destination; preserve pause, tenant, period/cutover, P01/P02/G47 and independent activation gates. A changed function name alone is insufficient.
4. **Advertising bridge**: native Integration V2 account ID and currency/evidence -> approved explicit MZ2 wallet/payable binding -> deduplicated versioned spend evidence -> V2 journal/event registry. Audit/decommission operational reachability of legacy ad sidewriters. No runtime writes during audit.
5. **Old routes/reports**: retire or explicitly reject old operational opening endpoints in MZ2 scope; preserve history diagnostics separately; remove financial-position legacy classification and driver statement legacy-ledger path. Existing writer gate protection must remain.
6. **Warehouse provenance**: accepted-contract provenance / owner-reviewed links for shared historical records, exact barcode fields, producer and picker enforcement. No automatic relabelling.
7. **Missing policy/liability contracts**: implement only after separate source authorization and contract review; Stage06/11 owner design direction is already given, no need ask it again.
8. **Verification gate**: per-route recording DB denies forbidden operational identity reads and writes; fixture matrix legacy-only/V2-only/colliding IDs/foreign tenant/archived/unknown currency; old route state matrix; each writer destination and event registry; report V2 scope must never touch legacy identity; warehouse provenance; audit history immutable. Runtime guards supplement static denylist scans. No guard or fix implemented in this audit.

## Remaining owner inputs (not new permission requests)

- Approve final proposed registry/policy schemas and supply actual external-person/creditor identities, fee contracts, liability/tax/deposit evidence when implementation/data entry is separately authorized.
- Decide treatment of existing legacy-linked supplier/bank/employee IDs and shared warehouse records via explicit mapping review; no silent migration is assumed.
- Confirm intended rollout sequencing of operational V2 bridges; keeping financial activation blocked is necessary while capability gaps remain.

## Verification and non-actions

- Fresh fetch and exact detached snapshots: PASS; Production unchanged.
- Real router construction + actual old opening handler guards with read-only doubles: exit 0, all three return 423 on v2_active, no downstream identity reads, no writes. Artifact: runtime-route-evidence.json.
- Local helper probes: legacy supplier+counterparty accepted; legacy bank fallback accepted; daily legacy supplier/expense selected; legacy post writer rejects v2_active; financial-position V2-success branch still reads accounts. These are proofs of defects/guards, not green acceptance tests or live HTTP evidence.
- PR1209 pure identity probe: V2 source returned legacy financial ID as described; exit 0.
- Existing native advertising reporting/warehouse tests run by isolated audit worker: 28 PASS; this proves only tested reporting/warehouse behavior, not the absent accounting bridge or row provenance.
- No source, test, dependency, configuration or release changes in this continuation. Documentation/evidence only. No live financial probes or data entry. **Live DB writes = 0; Merge = NO; Deploy = NO; Post/Activation = NO; write-control unchanged.**

Exact existing-test command (audit worktree, inspected relevant files identical to Production): `PYTHONPATH=backend PYTHONDONTWRITEBYTECODE=1 C:/Users/amasi/.codex/tmp/mz2-track-b-python/Scripts/python.exe -B -m pytest -q backend/tests/test_meta_native_reporting.py backend/tests/test_tiktok_native_reporting.py backend/tests/test_google_ads_reporting_v1.py backend/tests/test_snapchat_native_data_sync_v2.py backend/tests/test_warehouse_location_routes.py`; exit 0, 28 passed in 11.17 seconds. No live DB URI used. Local router diagnostic command: same Python `-B C:/Users/amasi/.codex/tmp/mz2_ssot_runtime_probe.py`, exact Production backend import path, exit 0.
