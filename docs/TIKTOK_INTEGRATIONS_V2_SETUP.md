# TikTok Marketing API — Mezan OS V2

This connector is native to Mezan OS V2. It does not read `tiktok_connections`,
`tiktok_ads_daily`, or the Make.com webhook feed.

## Architecture

- One TikTok developer app is configured for the Mezan platform.
- Every merchant authorizes their own TikTok for Business account through OAuth.
- Access tokens are encrypted and stored per Mezan `user_id`.
- Advertiser accounts are discovered directly through Marketing API v1.3.
- Native daily reporting is stored in a dedicated V2 analytical collection.
- Make.com remains a legacy source during reconciliation and is not projected
  into the V2 card or native reporting collection.
- Provider mutations remain blocked until the approval and rollback lifecycle is
  complete.

## TikTok developer app

Create or open a TikTok API for Business developer app and enable the Marketing
API permissions needed for:

- Ad account information
- Campaign, ad group, and ad reads
- Ad and creative reads
- Audience reads
- Consolidated reporting and conversion reporting
- Campaign/ad/budget management permissions for future approved execution

Mezan intentionally omits the `scope` query parameter by default, which asks
TikTok to grant all permissions enabled in the approved developer app. To narrow
the request, set `TIKTOK_MARKETING_SCOPE` explicitly.

Configure the account-holder callback URL exactly as:

```text
https://mezansalla.com/api/integrations-v2/tiktok/callback
```

## Production secrets and settings

Add these values in Emergent Production Custom Secrets. Never commit their
values to GitHub.

```text
TIKTOK_MARKETING_APP_ID
TIKTOK_MARKETING_APP_SECRET
TIKTOK_MARKETING_REDIRECT_URI=https://mezansalla.com/api/integrations-v2/tiktok/callback
TIKTOK_TOKEN_ENC_KEY
FRONTEND_URL=https://mezansalla.com
```

`JWT_SECRET` already exists and is used for signed one-time OAuth state. An
optional dedicated state secret may be configured:

```text
TIKTOK_OAUTH_STATE_SECRET
```

A Fernet-compatible token key can be generated locally without printing any
other secret:

```bash
python -c "import os,base64; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
```

Native reporting has a separate operational kill switch. Keep it disabled until
OAuth authorization succeeds and the first advertiser account is verified:

```text
TIKTOK_NATIVE_REPORTING_SYNC_ENABLED=true
TIKTOK_USD_TO_SAR_RATE=3.75
```

`TIKTOK_USD_TO_SAR_RATE` is operational configuration, not a secret. Accounts in
SAR use an implicit rate of `1`. Other currencies remain without a fabricated
SAR value until a verified rate is configured.

## Routes

```text
POST /api/integrations-v2/tiktok/connect/start
GET  /api/integrations-v2/tiktok/callback
POST /api/integrations-v2/tiktok_ads/test-connection
POST /api/integrations-v2/tiktok_ads/sync-async
GET  /api/integrations-v2/tiktok_ads/sync-async/{run_id}
```

All start, test, and reporting operations are owner-only. The callback is
protected by:

- signed, short-lived state
- one-time state consumption
- browser-bound HttpOnly SameSite cookie
- encrypted credential persistence

The reporting job returns immediately, then moves through
`queued → running → complete/partial/failed`. It cannot write to TikTok,
campaigns, accounting, or Qoyod.

## Collections owned by V2

```text
mezan_tiktok_oauth_credentials_v2
mezan_tiktok_oauth_states_v2
mezan_tiktok_performance_daily_v2
mezan_integrations_v2
mezan_integration_accounts_v2
mezan_integration_permissions_v2
mezan_integration_health_v2
mezan_integration_sync_runs_v2
mezan_integration_errors_v2
```

No TikTok token is copied into public V2 collections or API responses. Daily
performance rows are marked `source_only=true` and `accounting_eligible=false`.

## Migration from Make

1. Deploy the native connector and configure the TikTok developer app.
2. Authorize the production TikTok account from the V2 integrations page.
3. Verify advertiser IDs, currency, timezone, permissions, and health.
4. Enable native reporting for a bounded 7-day window.
5. Reconcile native spend, impressions, clicks, and conversions against TikTok
   Ads Manager and the Make feed.
6. Expand to 30 days only after the bounded reconciliation passes.
7. Disable the Make TikTok scenario after sustained reconciliation.
8. Retain legacy rows for audit until the old Mezan pages are retired.

The Make endpoint may continue receiving data during the transition, but native
V2 reporting does not read or modify it.
