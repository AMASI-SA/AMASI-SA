# TRACK_A_ONBOARDING_API_CONTRACT_V1

Shared base: `1d9d65d8576d5bc852f52262b84af150356b9385`, tree `82ed0d05eee935d22c7a4050f21e66cdb9f4f551`. Branch: `codex/mz2-cutover-core-20260930`. This is the implemented and isolated-test-verified source integration contract. These new endpoints are not deployed.

## Boundary and existing services
New setup session endpoints live under `/api/accounting-module/onboarding`. Setup save/preview/review writes only setup metadata; it never opens financial writes, changes settings, posts a journal, imports inventory, or activates P01/P02/G47. It remains usable while the financial writer is paused. Real financial accounts/evidence/opening draft services stay authoritative under `/api/financial-provider-apps/accounting-module/financial-accounts`. No parallel ledger or inventory store.

Existing account/evidence and canonical opening lifecycle mutations retain their financial pause/permission gates. An incomplete setup can be saved with unresolved account/evidence IDs; preview cannot call that complete. No request may supply owner/user identity: fresh persisted auth + accounting_owner_id determines scope. New sessions require explicit opening-view/drafts-manage permissions; review requires opening-view/review. A role name alone does not grant these permissions.

## Session endpoints and concurrency
- `GET /definitions`: schema_version=1, sections, category/meaning mappings, existing service paths, supported statuses, target cutover.
- `POST /sessions`: `{"idempotency_key":"client-stable-key","cutover_at":"2026-10-01T00:00:00+03:00","cutover_timezone":"Asia/Riyadh"}`.
- `GET /sessions`: owner's session summaries.
- `GET /sessions/{session_id}`: resume complete saved document.
- `PUT /sessions/{session_id}/cutover`: `{version,idempotency_key,cutover_at,cutover_timezone,cutover_evidence_file_id}`.
- `PUT /sessions/{session_id}/sections/{section_id}`: `{version,idempotency_key,status,reason,evidence_file_id,data}`.
- Every accepted mutation increments global integer `version` once (initial=1). CAS on owner + session + version. Stable key+same payload replays without a second effect; same key+different payload conflicts. Stale version requires reload, never blind overwrite.
- Session state: `draft → previewed → reviewed`. Saving after preview invalidates that preview; reviewed/handoff sessions are immutable (start a separately reviewed replacement instead). All changes carry actor/time/reason and revision history.

## Sections and save payload
Use the seven existing financial evidence IDs; the 16 UI stages map onto them:
`banks_cash, providers, couriers_cod, inventory, suppliers, payroll_obligations, equity`.
Statuses: `not_started, incomplete, complete, not_applicable`.
`data={"lines":[...], "provider_bindings":[...], "inventory_valuation":{...}}`; unused optional keys may be omitted. Lines may be incomplete objects while status=incomplete; preview validates the full OpeningLine schema. Unknown fields are rejected. A section save is a full replacement of that section, not a patch/merge of arrays.
not_applicable requires an explicit reason, evidence and zero lines; it is not a zero balance. It may not hide an active financial account or configured required mapping. Complete requires evidence and valid data; the final server preview still rechecks identities/coverage across all sections.
A frontend editor whose data has no backend contract must stay domain-local, not be silently submitted in financial data.

## Explicit zero
Missing/null/empty amount is unknown, never zero. An explicit zero line is:
`{"category":"financial_account","financial_account_id":"<real-id>","meaning":"zero","original_amount":"0.00","original_currency":"SAR","fx_rate_to_sar":"1","evidence_file_id":"<section-file-id>"}`.
Nonzero amounts are positive decimal strings. Meaning dictates debit/credit; negative cash is forbidden; overdraft is a distinct liability account. Each existing active financial account must appear explicitly, including zero.

## Account/entity identities
- Financial account IDs come from existing `GET /api/financial-provider-apps/accounting-module/financial-accounts`; creation stays existing `POST` with `name,account_type,currency,external_ref,idempotency_key`.
- Types: bank, cash, overdraft, ad_prepaid_wallet, ad_payable. Ad wallet and debt remain separate and must reference the same actual ad-account identity via external_ref when applicable.
- `GET /identities/{kind}` returns bounded, owner-scoped selectable `{id,label,kind}` identities only, no legacy balances. Kinds: bank, provider, employee, supplier, external_person, courier, store_driver, ad_account.
- Providers: salla/tabby/tamara/emkan explicit identities; do not generate duplicate mada/Apple Pay balances or infer receivable from payment method.
- Supplier ID must be exact-ID linked in existing suppliers + counterparties(kind=supplier). No name matching/netting.
- Employee IDs resolve from the existing owner-scoped employee source; salary payable/advance/custody are three independent lines.
- External person resolves an existing counterparty(kind=general); courier uses approved financial policy courier_id; driver uses existing store_drivers ID. Ads use exact counterparties(kind=ad_account) identity.
- General asset/liability/tax IDs require explicit documented account reference and section evidence; they are never inferred from a display label.
- Foreign, inactive, ambiguous or unresolved identity blocks preview/review/handoff.

## Opening line schema and economic gaps
Reuse OpeningLine: `category,financial_account_id OR entity_id,label,meaning,original_amount,original_currency,fx_rate_to_sar,fx_at,fx_source,fx_evidence_file_id,evidence_file_id`.
Categories already present: financial_account, provider_receivable, courier_cod_receivable, courier_payable, store_driver_cod_receivable, store_driver_fee_payable, employee_salary_payable, employee_advance, employee_custody, supplier_payable, customer_receivable, inventory_asset, sales_vat_payable, input_vat, other_receivable, other_payable.
V1 adds explicit `supplier_advance` (supplier/advance, debit, suppliers), `prepaid_expense` (asset/prepaid_expense, debit, equity), `accrued_expense` (liability/accrued_expense, credit, equity). These are distinct accounts, never silently netted against payable or treated as current-period expense. Monthly prepaid amortization/scheduler is deferred.
Debit meaning=`available_to_us`; credit meaning=`owed_by_us`; zero meaning=`zero`.
SAR requires rate=1. Foreign currency needs positive FX, offset-aware fx_at, source and/or FX evidence; currency must match financial account. Server freezes SAR conversion and FX snapshot.

## Provider and inventory contracts
Provider binding saved inside providers section: `{provider,bank_account_id,evidence_file_id}`. Bank ID must be an active SAR financial bank account in the same owner scope; evidence must be a verified providers-section artifact. These setup bindings are proposals, never live settlement configuration writes. Preview reports verified mappings and preserves their evidence in its snapshot.
Inventory section contains `inventory_valuation={"total_sar":"<decimal>","evidence_file_id":"<inventory-file>","manifest_hash":"<sha256>","account_totals":{"<inventory-entity-id>":"<decimal>"}}` when inventory applies. This is Track B's valuation summary contract; Track A checks each account and total against inventory_asset lines. It does not import/approve quantities and does not treat a financial equality as physical stock proof. Final activation readiness must separately verify authoritative opening inventory via the existing service.

## Evidence
Use existing multipart `POST /api/financial-provider-apps/accounting-module/financial-accounts/opening-balances/evidence`: purpose=cutover (no section), opening_balance (one of seven sections), or fx_rate(section). Returns source_file_id/sha256/size. No client hash alone is evidence. Server verifies preserved original bytes, owner, purpose and section; preview/review freeze content hashes and account/entity/FX mappings. Any change after review blocks handoff/post; no overwrite/delete of locked evidence. not_applicable and explicit zeros still require evidence.

## Preview / review / canonical handoff / post
- `POST /sessions/{id}/preview`: `{version,idempotency_key,note}`; validates complete sections, exact identities, explicit zeros, evidence, FX, provider bindings, inventory valuation. Returns updated session + `preview={hash,lines,entries,zero_accounts,debit_total,credit_total,balanced,mappings,inventory_reconciliation}`. Equity counterpart uses existing V2 compiler; balance alone never implies completeness.
- `POST /sessions/{id}/review`: same action body; rechecks preview snapshot/evidence/identity then locks session. No GL or activation.
- `POST /sessions/{id}/opening-draft`: same action body; explicit gated handoff to the existing V2 draft/preview/review services, with stable IDs and one atomic transaction. Requires view + draft-manage + review. Returns session + `opening_draft={id,version,status}`. It still fails 423 while writes_paused and does not alter that control.
- Actual financial post remains ONLY `POST /api/financial-provider-apps/accounting-module/financial-accounts/opening-balances/drafts/{draft_id}/post`, existing `{version,idempotency_key,note,effective_at?}`. It requires explicit post permission, unpaused atomic_owner, v2_active, immutable reviewed opening, evidence/account coverage and open periods. Its existing effect includes setting cutover active: UI MUST NOT present it as a harmless save.
- Writer transition stays existing financial-accounts/transition: legacy_active → transition_blocked → v2_active, CAS expected_revision, explicit activation_ref, approved opening/legacy-isolation/later-phase checks. No new activation endpoint. Do not call any live transition/post during this task.

## Readiness
`GET /sessions/{id}/readiness` returns:
`{schema_version:1,session_id,version,status,source_ready,opening_verified,inventory_reconciled,writer_transition,financial_writes_paused,ready_for_live_post:false,blockers:[{code,section_id?}],live_gates:{smoke_b:"BLOCKED_BY_ENVIRONMENT",owner_authorization:"REQUIRED"},p02_activation_allowed:false,g47_activation_allowed:false}`.
Source_ready is setup completeness, not financial enablement. Missing/invalid prerequisites are blockers; no success from absent values. Live proof/authorization is an operational hard hold, not a client-supplied boolean.

## Stable errors
HTTP 403: accounting_permission_required, accounting_owner_scope_missing.
404: onboarding_session_not_found, onboarding_identity_not_found.
409: onboarding_version_conflict, onboarding_idempotency_conflict, onboarding_session_locked, onboarding_sections_incomplete, onboarding_identity_invalid, onboarding_provider_binding_required, onboarding_inventory_value_mismatch, onboarding_snapshot_changed.
422: onboarding_payload_invalid, onboarding_not_applicable_conflict, onboarding_explicit_balance_required; existing OpeningLine/FX/cutover validation errors retained.
Existing opening_evidence_missing_or_foreign, opening_evidence_contract_mismatch, opening_duplicate_account, opening_accounting_meaning_mismatch, opening_financial_account_missing_or_inactive, opening_account_currency_mismatch and financial 423 codes remain intact.
Never render raw server exceptions. Validation errors may include safe field paths; unknown failures use stable public codes.

Smoke B remains BLOCKED_BY_ENVIRONMENT. No release-intent change, merge/deploy, live DB write, live post or activation is authorized. Track B should bind only these contracts, keep live actions disabled, and retain its independent checkpoint while reconciling to the shared base. The initial Issue contract has been clarified with the source-backed details below; later contract changes require an explicit version and notification.

## Implementation clarifications (authoritative V1)

Issue source: comments 5900935996, 5900990152 and 5901180648 in AMASI-SA/AMASI-SA#1006.

- The new onboarding base is `/api/accounting-module/onboarding`; the existing financial base includes `/financial-provider-apps`. Existing routes remain unchanged.
- A completed section has one or more complete lines. An empty section is never an inferred zero: use an explicit evidence-backed `not_applicable` decision. Incomplete lines can omit fields while saving; preview cannot.
- The explicit entity coverage check runs again at preview, review and handoff. Every active employee requires salary payable, advance and custody facts; every active linked supplier requires payable and advance facts; each financial courier/store driver requires both COD receivable and payable facts; each external person requires a receivable fact. Zero is allowed only explicitly. Active ad accounts require distinct prepaid-wallet and payable accounts/facts.
- Employee IDs come from owner-scoped `operating_salaries(category=employee)`, using its canonical `id` or `employee_id`, matching existing payroll. Rent/other expense rows and arbitrary employee aliases are not financial identities.
- Courier IDs come from approved `mz2_shipping_rate_policies.versions[].courier_id`, matching existing P02 counterparties. Operational shipping names are not silently normalized into a financial counterparty. Catalog reading does not activate or modify P02.
- Suppliers require the existing exact-ID `suppliers` + `counterparties(kind=supplier)` bridge. Missing links block preview. `external_person` maps explicitly to owner-scoped `counterparties(kind=general)`; ad accounts use `kind=ad_account`.
- Provider values are the exact enums salla/tabby/tamara/emkan; bank/evidence IDs are strict nonempty strings. Object/query-operator IDs fail HTTP422. Provider balances are explicit; no balance or bank is inferred from a payment method.
- `inventory_valuation` has exactly `total_sar, evidence_file_id, manifest_hash, account_totals`. `account_totals` maps the exact inventory entity IDs to decimal strings; both each account and the total must match compiled opening values. Manifest hash is lowercase SHA-256. This is planned valuation evidence, not quantity import or physical approval.
- The original financial account and evidence-upload endpoints remain behind the financial pause barrier. Setup can save incomplete facts while paused, but cannot claim source readiness without actual valid mappings and immutable original evidence. No new evidence/account bypass is introduced.
- Session mutations only change `mz2_onboarding_sessions`. `opening-draft` is different: an explicit guarded handoff into existing canonical create/preview/review in one Mongo transaction, never a post or activation. Paused/missing controls reject it with 423 before its callback.
- Live posting continues to require separate authorization and the existing financial transition/write gates. This source-only release does not provide a mechanism to mark Smoke B passed. Readiness always reports `ready_for_live_post=false`, `smoke_b=BLOCKED_BY_ENVIRONMENT` and owner authorization required.
- Each mutation uses `version` and a stable `idempotency_key`. Reusing a key with different payload is a conflict; identical retry returns the current session with `existing=true`, never replays effects or overwrites a newer edit.
- No startup migration/index initializer is added. Deterministic `_id` and Mongo's built-in primary-key uniqueness serialize session creation; CAS serializes subsequent updates. A session has a bounded 1000-revision audit/idempotency history.

### Additional stable validation codes
`onboarding_explicit_balance_required`, `onboarding_entity_balance_required`,
`onboarding_supplier_link_required`, `onboarding_provider_binding_required`,
`onboarding_inventory_value_mismatch`, `onboarding_identity_scope_too_large`,
`onboarding_revision_limit`, `onboarding_transaction_binding_required`.

Malformed request schemas use the existing HTTP422 validation envelope. Existing V2 compiler/evidence/account codes remain unchanged.
