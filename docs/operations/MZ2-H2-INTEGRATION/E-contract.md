# Track E UI adapter contract

Reviewed source: PR #1218, `8c91ab2a9faa59978ac6ccdb4b0818e3c65d1344`. Backend source is inspected with `git show origin/review-h2-e`; no Track E backend code is copied into H2.

`AdvertisingPanel` is a standalone default export. The daily accounting screen embeds it inline. It performs native GETs only and does not activate a policy, post a spend, fund a wallet, execute a batch, or call a legacy source.

## Exact read contracts

`backend/accounting_advertising_routes.py` installs `/accounting-module/advertising-v2`; `backend/server.py` mounts it under the existing `/api` prefix. The shared Axios client already supplies `/api`.

| GET route | Contract consumed | Presentation |
| --- | --- | --- |
| `/stage-12` | `stage=12`, `identity_source=mezan_integration_accounts_v2`, `items[]` | Snap, Meta, TikTok and Google Ads native identities and account currency |
| `/stage-12` item | `wallet_binding`, `payable_binding`, `readiness`, `missing_contract_reason` | Separate wallet/payable identity bindings, not computed balances |
| `/stage-12` item | `original_wallet.currency`, `posted_original_balance`, `opening_confirmed` | Backend original-currency wallet balance; missing stays unavailable; confirmed opening evidence is explicitly not a posted balance |
| `/stage-12` item | `daily_spend_readiness`, `daily_spend_gap`, `schedule_timezone`, `run_at` | Automatic policy configuration only, not assertion that posting occurred |
| `/stage-12` item | `bank_movement_readiness`, `bank_movement_gap` | Track A bank integration blocked |
| `/due-items?limit=100` | `items[]` of platform/integration account/business date/policy/due time | Bounded backend queue; absence is not posted or zero |
| `/daily-source?platform=…&integration_account_id=…&business_date=…` | Original amount/currency, source revision, explicit `native_approval_required=true`, contract reason | Inline source evidence for a user-selected date; never presented as completed accounting |

Sources: `backend/accounting_advertising_bridge.py:stage12_context`, `backend/accounting_advertising_sources.py:account_view,daily_source`, `backend/accounting_advertising_automation.py:due_items`, `backend/accounting_advertising_wallet.py:wallet_position`.

## Exact blocked capabilities

- `track_a_require_financial_ledger_identity_not_integrated`: Track E itself marks bank movement not ready. No bank destination is invented.
- `ad_posted_daily_status_read_contract_missing`: adapter dependency label. There is no GET for sealed posting history or persisted `CLOSED_ZERO`. Those statuses occur in `run_day` / POST `/automatic-run` results. H2 must not execute posting to obtain display data. `due_items` deliberately excludes sealed positive and zero days. Empty queue cannot be transformed into `CLOSED_ZERO`.
- `ad_fx_readiness_read_contract_missing`: adapter dependency label. Current GETs do not expose FX snapshot readiness for a business day. No inference from currency, no FX calculation, no call to mutation `/fx-snapshot`.
- `ad_sar_wallet_and_payable_balance_read_contract_missing`: adapter dependency label. Stage 12 exposes foreign original wallet position, but not SAR wallet balance or payable balance. Binding IDs are not balances; opening evidence is not substituted for posted balance.
- `ad_native_route_not_integrated`: HTTP 404 when Track E routes are not in the running backend. H2 does not bundle the Track E backend.

These labels identify frontend dependency records, not invented backend response fields. Explicit backend error codes are displayed where returned. Network/auth/server failures remain error states. Malformed or non-native identity payloads fail closed. Native source dates/account IDs/currency must match the request.

## Validation

`advertisingAdapter.test.js` covers native routes, native identity enforcement, no fallback, source/account matching, due queue contract, explicit zero preservation without CLOSED_ZERO inference, blocked vs transport/auth errors.

`AdvertisingPanel.test.jsx` covers native-only reads, inline details, separate identities, missing balance != zero, wallet display in original currency, no opening-evidence-as-balance substitution, exact dependencies, empty/loading/error/unavailable states.

All fixtures use synthetic IDs. Production writes = 0; policy activation = NO; Opening Post = NO.

Focused local verification: Node 22 / CRA Jest `--runInBand --no-cache --testMatch '**/h2/advertisingAdapter.test.js' '**/h2/AdvertisingPanel.test.jsx'`: **2 suites, 20 tests PASS** (2026-10-01). `scripts/testing/mz2_track_h/h2-advertising-fixture.js` provides native synthetic responses for the parent-owned desktop/mobile harness, including explicit posted wallet zero and unavailable SAR wallet/payable balances.
