# Meta Business / Marketing API — Mezan OS V2 setup

## Objective

Mezan owns one Business-type Meta app. Every merchant authorizes their own
Business portfolio and advertising assets through Facebook Login for Business.
Each grant is stored encrypted and isolated by Mezan `user_id`; merchants never
enter Mezan's App Secret.

## Create the Meta app

In Meta for Developers:

1. Create an app intended for **Business** use.
2. Add the **Marketing API** product.
3. Add **Facebook Login for Business** (or the current Business Login product
   exposed by Meta's dashboard).
4. Add the exact Valid OAuth Redirect URI:

   `https://mezansalla.com/api/integrations-v2/meta/callback`

5. Set App Domain to `mezansalla.com`.
6. Configure a production Privacy Policy URL, Terms URL, and User Data Deletion
   callback/instructions before App Review.
7. Enable App Secret Proof for server API calls where exposed in app settings.

## Permissions requested by Mezan

- `ads_read`
- `ads_management`
- `business_management`
- `catalog_management`
- `pages_show_list`
- `pages_read_engagement`
- `leads_retrieval`
- `instagram_basic`
- `instagram_manage_insights`

Standard Access is sufficient for testing the app against assets owned by the
same business/app roles. A multi-tenant Mezan production app managing merchants'
ad accounts needs **Advanced Access** for the relevant permissions and Meta App
Review. Request only permissions backed by real UI flows and reviewer test
instructions.

## Production secrets

Add to Emergent Production Custom Secrets:

- `META_BUSINESS_APP_ID`
- `META_BUSINESS_APP_SECRET`
- `META_BUSINESS_REDIRECT_URI=https://mezansalla.com/api/integrations-v2/meta/callback`
- `META_TOKEN_ENC_KEY`

Optional configuration/rotation:

- `META_GRAPH_API_VERSION=v25.0`
- `META_BUSINESS_SCOPES`
- `META_TOKEN_ENC_KEY_OLD`
- `META_OAUTH_STATE_SECRET`
- `META_USD_TO_SAR_RATE=3.75`

The direct reporting data plane is disabled by default. Enable it only after the
OAuth connection and owner account selection are verified:

- `META_NATIVE_REPORTING_SYNC_ENABLED=true`

Generate the Fernet encryption key:

```bash
python -c "import os,base64; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
```

## Native reporting routes

```text
GET  /api/integrations-v2/meta_ads/accounts-selection
PUT  /api/integrations-v2/meta_ads/accounts-selection
POST /api/integrations-v2/meta_ads/sync-async
GET  /api/integrations-v2/meta_ads/sync-async/{run_id}
```

When OAuth discovers exactly one Meta ad account, Mezan selects it
automatically. When more than one account is discovered, the owner must select
the Amasi accounts explicitly before reporting can run.

The reporting request uses account-level Insights with:

- the ad account's configured attribution setting;
- Meta's unified attribution setting;
- one daily bucket per account;
- spend, impressions, clicks, purchase count, and purchase value.

Daily rows are stored only in:

```text
mezan_meta_performance_daily_v2
```

Each row is marked `source_only=true` and `accounting_eligible=false`. The
reporting connector does not write campaigns, `ads_daily`, `general_ledger`,
Qoyod, or legacy Meta collections.

## Safe rollout

1. Authorize Meta and confirm the discovered Business and ad accounts.
2. Select only the Amasi ad accounts.
3. Enable `META_NATIVE_REPORTING_SYNC_ENABLED=true`.
4. Run a 7-day sync from the Meta card.
5. Compare spend, purchases, and purchase value with Meta Ads Manager using the
   same account attribution setting and date window.
6. Expand to 30 days only after the bounded comparison succeeds.
7. Keep any existing legacy feed available for audit until the direct data is
   stable.

## App Review evidence

Prepare a reviewer flow that shows:

1. Merchant opens Mezan 2 → Apps & Integrations.
2. Merchant clicks **Connect Meta**.
3. Merchant grants access to their Business and ad account.
4. Mezan displays only the merchant's authorized ad accounts, businesses,
   pixels, catalogs, and Instagram professional accounts.
5. The merchant selects the accounts to include in reporting.
6. Mezan runs a read-only daily report and displays the audited result.
7. No campaign or accounting mutation occurs.

For `ads_management`, explain the future approval-gated campaign management
workflow. For catalog, Page, Instagram, or lead permissions, include a working
screen and exact reviewer navigation for each permission requested.

## Security properties

- OAuth state is signed, short-lived, persisted, and consumed once.
- Callback completion requires an HttpOnly browser-binding cookie.
- The short-lived token is exchanged server-side for a long-lived user token.
- The token is validated with Meta's debug endpoint and must belong to the
  configured App ID.
- Every Graph API request includes `appsecret_proof`.
- Access tokens are encrypted in `mezan_meta_oauth_credentials_v2`.
- V2 public projections contain no token, App Secret, authorization code, or
  funding-source details.
- The native connector does not read or write `meta_connections` or
  `meta_ads_daily`.
- Reporting is owner-scoped, selected-account-only, asynchronous, and disabled
  by default.

## Current delivery boundary

This delivery provides OAuth, long-lived encrypted credentials, token
validation, direct discovery of Businesses/ad accounts/pixels/catalogs/
Instagram accounts, safe balance/spend-cap fields, owner account selection, and
read-only daily reporting.

Events Manager diagnostics, Conversions API event sending, billing document
retrieval, and provider mutations remain separate bounded releases. Mutations
stay blocked behind proposal, preview, approval, verification, audit, and
rollback controls.
