# Track A — canonical financial identity foundation

Base Production: 728730366cfee6c906172c7c14a2a67251ed1065 / 70b4c3d7b501f7c8a31767ceb61d5ac4cb204df9.
Separate source branch; no WIP implementation imported from #1210. No balance migration or writer transition.

## Shared contract for Track C, Shipping and Advertising

Import from `accounting_financial_identity`:

```python
bank = await find_financial_account(
    db, owner, financial_account_id, account_types=("bank",), currency="SAR")
choices = await list_financial_accounts(
    db, owner, account_types=("bank", "cash"), currency="SAR")
```

`owner` must come from the fresh authenticated accounting actor. IDs are exact `mz2_financial_accounts.id`, never names, legacy aliases or external IDs. The lookup returns a projected canonical document or None. It requires owner, exact ID, active status, context account type, explicit matching currency and absence of archive/delete/inactive flags. Currency on stored data is never defaulted. The default context currency is SAR because existing settlement consumers are SAR; another context must request its actual currency explicitly. An unsupported context type/currency is a caller error.

Lists are deterministic, limited to 1000; overflow or duplicate/missing canonical identity fails closed. No reads from `accounts` or settings, even on same-ID collision. Lookup/list never writes or creates indexes, and never reads balances. Pass the transaction-bound DB supplied by the existing financial boundary for any posting-time lookup; do not unwrap it. A successful identity lookup is not permission to post, evidence of funds, or authorization to enable a writer.

Consumer must reject None. Provider binding reports `MZ2_LINK_REQUIRED`. Other established consumers retain their existing invalid-bank error contracts where applicable. Do not implement another resolver in a downstream Track.

## Explicit provider binding

Existing collection `accounting_provider_bank_bindings_v2` is retained. A valid provider settlement bank requires:

- server-resolved owner/provider and `bank_account_id` FK;
- `bank_account_source = mz2_financial_accounts`;
- `identity_contract_version = 1`;
- currently active canonical **bank**, currency SAR;
- existing confirmation/evidence/verification requirements.

Only explicit supported rebind/save writes these provenance fields. Old/unmarked bindings return configured=false, needs_confirmation=true, code/binding_status=MZ2_LINK_REQUIRED, even if a canonical account shares the ID. Diagnostic old ID may be returned; it cannot satisfy `_verified_binding_bank_id`. No automatic conversion or settings write-through occurs. Financial pause remains in the existing binding route wrapper; this source change performs no live rebind.

Courier binding uses the same provenance marker and central bank/cash identity resolver. Courier catalog/rates, netting and settlement business rules are unchanged.

## Converted identity-only seams

|Module|Change|
|---|---|
|accounting_settlement_routes|Central bank/cash lookup/list; provider bank-only FK; no settings fallback or write-through; explicit legacy rebind gap|
|accounting_settlement_service|Posting-time canonical bank/cash resolution; journal business logic unchanged|
|accounting_bank_transfer_receipts|Exact canonical bank ID only; no name/fuzzy/settings alias resolution|
|accounting_courier_bank_routes|Central bank/cash resolver; explicit binding provenance|
|accounting_customer_advances / refund_routes|Canonical bank choices; existing writer validation already uses settlement resolver|
|accounting_daily_movements|Canonical bank/cash choices; existing `_bank_or_409` inherits central resolver; no supplier/expense logic change|
|accounting_onboarding_identities|Stage03 bank choices and provider-bank validation use central resolver|
|accounting_onboarding_domains|Canonical Stage02 bank/cash identities are not invalidated by unrelated Legacy same-ID records|

Stage02 frontend already loads financial-account API backed only by `mz2_financial_accounts`; Stage03 uses active SAR bank choices from that source. No UI redesign is included. Existing opening valuation/FX contracts remain unchanged.

## Deliberate gaps and remaining legacy references

- Bank-transfer order evidence containing a display name rather than canonical FK now yields `MZ2_LINK_REQUIRED`. A future explicit audited evidence-to-account binding is required; no guessed name/alias migration.
- Supplier payment service and store-driver settlement bank adoption belong to their downstream Tracks; they must use this resolver. Their existing legacy fallbacks identified in the audit are not claimed fixed here.
- Old compatibility opening/ledger routes and report historical/classification reads remain separately classified audit blockers. This Track does not port writers, alter activation or redefine report semantics.
- Courier `settings.shipping_companies` remains courier business catalog only; not bank identity. Bank-transfer settings reads for cutover remain gate metadata only.
- `account_transactions` and `general_ledger` duplicate-payment/history evidence and existing writers remain unchanged. They are not bank identity fallback. Their broader SSOT audit findings still stand.
- Employee/supplier/advertising identity registries in the older onboarding domain adapter are not changed by this bank-only Track.

## Guard and validation

`test_financial_identity_foundation` centralizes a denylist boundary for converted operational modules (AST check for legacy accounts access) plus runtime DB traps rejecting forbidden reads and all writes. Companion provider/consumer tests cover old bindings, exact canonical IDs, same-ID collisions, inactive/foreign/wrong-type/wrong-currency accounts, bank/cash choices and no non-financial financial delta. Existing transactional suites use explicit canonical fixture records; legacy same-ID fixtures remain where testing existing legacy writer/report compatibility. Assertions of economic results are preserved.

No Production DB writes, merge, deploy, Post, activation, pause changes, release intent or Release Guard changes are authorized or performed.
