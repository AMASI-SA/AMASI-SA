# Snapchat Marketing API — Mezan OS V2 setup

## Objective

Mezan owns one OAuth application. Every merchant authorizes their own Snapchat
user and receives an encrypted tenant-scoped token grant. Merchants never enter
Mezan's Client Secret.

## Create the OAuth app

The operator must be an **Organization Admin** in Snapchat Business Manager.
Open the Organization, then **Business Details → OAuth Apps**, and create:

- App name: `Mezan OS Snapchat Ads Integration`
- Redirect URI: `https://mezansalla.com/api/integrations-v2/snapchat/callback`

Keep the generated Client Secret outside GitHub and browser storage.

## OAuth scopes

Mezan requests:

- `snapchat-marketing-api`
- `snapchat-offline-conversions-api`

The first scope grants read/write access to Snapchat Marketing APIs. The second
covers Conversions API workflows. Provider writes remain blocked by Mezan's
proposal/preview/approval/verification/audit/rollback policy.

` snapchat-profile-api ` is intentionally not requested. Public Profile API is
a separate allowlisted product and is not required for advertising, pixels,
reporting, catalogs, audiences, campaigns, or billing diagnostics.

## Production secrets

Add to Emergent Production Custom Secrets:

- `SNAPCHAT_MARKETING_CLIENT_ID`
- `SNAPCHAT_MARKETING_CLIENT_SECRET`
- `SNAPCHAT_MARKETING_REDIRECT_URI=https://mezansalla.com/api/integrations-v2/snapchat/callback`
- `SNAPCHAT_TOKEN_ENC_KEY`

Optional rotation/settings:

- `SNAPCHAT_TOKEN_ENC_KEY_OLD`
- `SNAPCHAT_OAUTH_STATE_SECRET`
- `SNAPCHAT_MARKETING_SCOPES`

Generate the Fernet encryption key:

```bash
python -c "import os,base64; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
```

## Required Snapchat roles

For the initial Amasi authorization, use a maintained company Snapchat user
with sufficient access. Organization Admin provides broad organization and ad
account control. Verify the user also has the needed Ad Account and Catalog
roles for every asset Mezan must manage.

## Security properties

- OAuth state is signed, short-lived, persisted, and consumed once.
- Callback completion requires an HttpOnly browser-binding cookie.
- Access and refresh tokens are encrypted in
  `mezan_snapchat_oauth_credentials_v2`.
- V2 public projections contain no token, Client Secret, or authorization code.
- The native connector does not read or write `snapchat_connections`,
  `snapchat_ad_accounts`, or `snapchat_account_daily`.
- Legacy settings/pages remain untouched during migration.

## Current delivery boundary

This delivery provides OAuth, encrypted credentials, account discovery,
permission evidence, health, and local connection tests. Native reporting,
pixel/CAPI diagnostics, billing/funding-source reads, and campaign mutation
execution are separate bounded deliveries. The V2 card does not invoke the
legacy analytics backfill.
