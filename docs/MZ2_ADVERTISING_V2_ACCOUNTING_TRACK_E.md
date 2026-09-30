# Track E — native MZ2 advertising accounting

Delivery classification: **MZ2_ADVERTISING_V2_ACCOUNTING_BLOCKED_BY_EXACT_GAP**.
The native spend/setup implementation is reviewable; this does not claim that
all advertising accounting or Stage 12 is operationally closed.

## Base and isolation

- Repository: AMASI-SA/AMASI-SA; permanent handoff: issue #1006.
- Fresh `git fetch origin hotfix/prod-snap-meta-final` matched the supplied base.
- Production HEAD: `5a7b44b71c6c9974aba358493b3267a47d6e6314`.
- Production TREE: `87f9a44af5dc7d00fbc62417fb17e2f6273d8d93`.
- Branch: `codex/mz2-advertising-v2-accounting-20260930` in its own worktree.
- No frozen branch was copied. No changes to PRs #1209/#1212–#1217, central
  AccountingOnboarding, Stage 10, P01, P02 or G47 implementation files.
- The only existing runtime file changed is `backend/server.py`, adding the
  standalone advertising router. All other implementation files are new.
- This work makes **zero production financial/provider/advertising writes**.
  Merge NO; Deploy NO; Opening Post NO; Activation NO; Release Guard NOT RUN.

## Actual platform source map

All account identity comes exclusively from `mezan_integration_accounts_v2`.
The API maps `provider` to platform, `mezan_integration_account_id` to
`integration_account_id`, and `external_account_id` to `platform_account_id`.
It preserves `display_name`, `currency`, `connection_status` as status, and
provider account status separately. Connection status is not an invented
provider-active assertion; TikTok and Google projections omit provider status.

| Platform | Provider | Actual account producer | Actual daily producer/store |
| --- | --- | --- | --- |
| Snapchat | snapchat_ads | `integrations_control_center/snapchat_discovery.py`, `snapchat_projection.py` | `snapchat_v2/projections.py`: `mezan_snapchat_daily_projections_v2` |
| Meta | meta_ads | `integrations_control_center/meta_discovery.py`, `meta_projection.py` | `meta_native_reporting.py`: `mezan_meta_performance_daily_v2` |
| TikTok | tiktok_ads | `integrations_control_center/tiktok_discovery.py`, `tiktok_projection.py` | `tiktok_native_reporting.py`: `mezan_tiktok_performance_daily_v2` |
| Google Ads | google_ads | `integrations_control_center/google_discovery.py`, `google_projection.py` | `google_ads_reporting.py`: `mezan_google_ads_performance_daily_v2` |

The native path never reads/writes `counterparties`, `ad_account_ledger`,
`ad_accounts`, `snapchat_ad_accounts`, `accounts`, or `general_ledger`.
Settings is read only by the existing sealed V2 lifecycle/opening verification;
it is never used as an advertising or financial account identity.

## Binding, setup and authority

`mz2_ad_account_bindings_v2` records owner/user_id, platform,
integration_account_id, platform_account_id, optional wallet/payable financial
account IDs, funding_mode, currency, status, confirmed_at/by, version, payment
terms evidence, request hash and ID. Owner/platform/integration identity is
hashed into Mongo's unique `_id`. Explicit version CAS protects updates.

- Wallet references must resolve to an active, same-owner, same-currency
  `mz2_financial_accounts` row of type `ad_prepaid_wallet`.
- Payable references require `ad_payable` under the same checks.
- Prepaid accepts only a wallet; postpaid only a payable. Hybrid requires both
  and `hybrid_policy=explicit_split`; each post supplies its exact wallet share.
- A financial identity cannot be assigned to two integration accounts.
- No name matching, external-ref authority, auto-binding or legacy creation.
- Every post revalidates canonical identity and financial binding.

No approved expense identity registry was found in the production MZ2 contract.
`mz2_ad_expense_identities_v2` therefore supplies a narrow, immutable,
owner-confirmed global advertising expense identity and a separate optional
bank-fee identity. Each has `entity_type=expense`, an explicitly approved
`entity_id`, `sub_account=null`, confirmation, evidence, version and audit.
The two purposes cannot share an entity ID. No platform name infers an expense.

`accounting_advertising_setup.setup` is a closed, typed operation, with no public
arbitrary callback. Its restricted session capability permits changes only to:

- `mz2_ad_account_bindings_v2`
- `mz2_ad_expense_identities_v2`
- `mz2_ad_spend_snapshots_v2`
- `mz2_ad_fx_snapshots_v2`
- `mz2_ad_setup_audit_v2`
- `mz2_ad_setup_owners_v2` (advertising setup serialization only)

It can read canonical V2 sources, financial accounts and fresh owner authority;
it cannot mutate them. It cannot access/write a ledger, control row, opening,
settings or bank collection. User/login mutation is denied. It never invokes
the accounting control bypass. Audit insertion and setup changes commit in one
snapshot/majority transaction; audit failure rolls the changes back.
Setup works while `writes_paused=true`, including absent financial control.

Financial posts use `atomic_owner`, take the advertising setup lock, re-read
owner authority, require `safe_active`, enforce closed accounting periods and
call the sealed `post_journal_v2` in that same Mongo transaction. Pause remains
HTTP 423 and cannot be bypassed by setup.

## Daily source provenance and native approval

Snapchat selects the account-timezone projection and `action_report_time=conversion`,
never adds the Riyadh and account-timezone projections together. It requires
`amount_complete=true`, `data_state=confirmed_data`, source sync runs and fact
count. Amount/currency are `base_spend_native`/`currency`; economic date/zone
are `report_date`/`projection_timezone`; freshness comes from
`source_latest_updated_at`/`updated_at`. BSON datetimes are UTC even when Motor
returns naive Python datetimes; naive timestamp strings remain rejected.

Meta/TikTok/Google use `spend_native`, `currency_native`, `date`,
`account_timezone`, `observed_at`, `updated_at`, and the exact producer mode.
Source identity includes physical record ID and collection, owner-scoped
account, platform and source-local date. A SHA256 of material fields is the
revision. Refresh-only timestamps do not change the financial revision.

These analytical reporters explicitly persist `source_only=true` and
`accounting_eligible=false`. They do not themselves grant posting authority.
The new **owner-confirmed native snapshot** is an explicit separate contract:

1. GET daily-source reads actual V2 evidence and returns its material revision.
2. Owner reviews it and POSTs spend-approval with the expected revision and
   explicit completeness, provider-timezone and native-promotion evidence.
3. Setup re-reads the source and rejects a changed revision. It freezes original
   amount/currency, economic date/timezone, source identity/revision, freshness,
   source flags, confirmation and adapter policy version in an immutable MZ2
   snapshot. Client-supplied amount/currency/timezone cannot substitute for V2.
4. The bridge requires this native snapshot and re-reads the actual daily source.

This does not modify upstream `accounting_eligible`, enable upstream accounting
writes or silently treat analytics as posting authority. Snapshot retry keeps
its first provenance after a harmless refresh. Changed approval content cannot
overwrite an existing snapshot.

Source-local days must be closed and observed after day-end. Missing/ambiguous
rows, absent original amounts/currencies, mismatched timezones, invalid
provenance, incomplete Snapchat coverage and empty provider rows are exact
GAPs. Synthesized zero is not a financial posting and requires a separate
reconciliation contract. Google silently defaults its reporting timezone in
the existing producer; explicit provider-timezone evidence is mandatory in
native approval. No false claim of upstream finality or status is made.

## Journals and FX

| Operation | Debit | Credit | Execution status |
| --- | --- | --- | --- |
| Prepaid daily spend | approved advertising expense | bound ad_account / balance | Implemented for SAR wallets |
| Postpaid daily spend | approved advertising expense | bound ad_account / debt | Implemented with explicit FX for non-SAR |
| Explicit hybrid | approved advertising expense | explicit wallet share + payable remainder | Implemented for SAR wallets |
| Wallet funding | bound ad_account / balance | bank / main | Pure plan tested; Track A port blocks actual post |
| Payable settlement | bound ad_account / debt | bank / main | Pure plan tested; Track A port blocks actual post |
| Evidenced bank fee | separate approved bank-fee expense | additional bank / main credit | Pure plan tested; remains behind bank integration gap |

Funding/settlement never create advertising expense. Fees are never inferred;
absent explicit fee evidence/expense authority yields a GAP. Unknown fees stay
unclassified. No financial balance field is mutated directly.

Wallet spending checks the minimum running V2 SAR balance from the economic
date through all later posted movements, inside the owner transaction. Later
funding cannot hide a historical negative interval. History above 10,000 legs
fails closed pending reconciliation instead of reading a truncated balance.
Concurrent spending is serialized by Mongo, not an in-process lock.

SAR uses explicit identity conversion. Other currencies require an immutable
owner-confirmed `mz2_ad_fx_snapshots_v2` record bound to source currency and
business date, with decimal-string `fx_rate_to_sar`, timezone-aware `fx_at`,
`fx_source` and evidence. The journal preserves original amount/currency, rate,
timestamp/source/evidence and computed SAR amount, rounded half-up to cents.
The analytics reporters' `spend_sar` and implicit USD=3.75 rates are never read
as financial authority. Foreign prepaid/hybrid wallets fail closed because
the production ledger has only SAR balance authority; FX alone cannot prove
the original-currency wallet will not go negative.

## Idempotency, API and integration dependencies

`mz2_ad_postings_v2` has one deterministic `_id` per owner/platform/provider
account/business date. The sealed ledger uses the same economic-day identity.
Approved source revision and request hash are retained. Exact retry returns
the existing journal; concurrent retry posts once. A different revision after
posting yields `ad_adjustment_reconciliation_required` and cannot overwrite
or add a second daily expense. Posting-marker failure rolls back journal,
ledger audit and sequence changes in the same transaction.

Owner-only API prefix: `/api/accounting-module/advertising-v2`:
`GET /stage-12`, `PUT /binding`, `POST /expense-identity`, `POST /fx-snapshot`,
`GET /daily-source`, `POST /spend-approval`, `POST /spend-post`,
`POST /bank-movement`. Authentication and persisted owner status are re-read.

Stage 12 context enumerates only canonical V2 accounts, with wallet/payable
binding, currency, mode, setup readiness, missing contract reason, daily
readiness and the bank port GAP. `SETUP_READY` never means ready to post a day.
Legacy-only accounts are absent; no V2 accounts returns GAP/NOT_READY semantics.

Remaining integration dependencies (not implemented by copying frozen tracks):

1. **Track A #1212**: integrate its actual
   `require_financial_ledger_identity` contract. The local port always fails
   closed. Add approved bank statement/evidence, bank balance and idempotent
   posting integration tests before enabling funding, settlement or bank fees.
   Replacing the port alone deliberately cannot enable bank posting.
2. **Foreign wallet original-currency balance**: approved native balance/FX
   reconciliation authority is required. SAR ledger balance is insufficient.
3. **Stage 12 frontend / Track H integration**: consume the independent backend
   context and confirmation routes; central AccountingOnboarding was untouched.
4. **Source review**: real owner confirmation of daily completeness/timezone,
   expense/binding authority and any FX snapshots remains an operational step.
   No live accounts, balances, facts or credentials were accessed for testing.
5. **Adjustments and zero reconciliation**: explicit future reconciliation
   contract required; this implementation detects and blocks instead of guessing.

## Verification

`backend/tests/test_mz2_advertising_v2.py` runs on a dedicated real Mongo 8.0.12
replica set with a fresh random database per test, then drops only that database.
Fixtures simulate prior approved lifecycle state; they do not execute Opening
Post or activation against production. Mongo command monitoring proves zero
legacy reads/writes, including tests with legacy-only sentinels.

Coverage includes all four V2 platforms, correct prepaid/postpaid/hybrid legs,
wallet exhaustion/races/backdated intervals, source refresh/revision changes,
exact gaps, required FX, foreign/inactive/wrong-type bindings, owner isolation,
owner-only HTTP routes, paused setup and 423 financial posts, immutable expense
and FX contracts, setup-audit rollback and journal-marker rollback. Funding,
settlement and fees have pure plan assertions plus actual fail-closed port
tests; **executed bank transfer posting is not claimed**.

Run from `backend` with an isolated replica set:

```powershell
$env:MZ2_AD_TEST_MONGO_URI='mongodb://127.0.0.1:27268/?replicaSet=mz2tracke'
$env:MZ2_TEST_MONGO_URI=$env:MZ2_AD_TEST_MONGO_URI
python -m pytest tests/test_mz2_advertising_v2.py tests/test_accounting_ledger_v2.py tests/test_financial_accounts.py tests/test_accounting_onboarding.py -q --tb=short
```

Fresh local result: **126 passed, zero skipped**, 92.83 seconds; 43 are native
Track E acceptance cases. Compilation and `git diff --check` also passed.
An earlier run skipped 32 existing real-Mongo tests because their separate URI
variable was absent; the final run above configured both URIs and executed them.

Dedicated CI: `.github/workflows/mz2-advertising-v2.yml`; exact PR commit checkout,
isolated Mongo replica set, native contracts, sealed ledger, financial-account
and onboarding regressions. It sets both test URI variables so none are skipped.
Final tested commit/tree, local result, Draft PR and fresh CI evidence are
recorded in issue #1006 so they can identify the report's own commit.
