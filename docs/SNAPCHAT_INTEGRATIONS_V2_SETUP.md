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

`snapchat-profile-api` is intentionally not requested. Public Profile API is a
separate allowlisted product and is not required for advertising, pixels,
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
- `MEZAN_SNAPCHAT_NATIVE_SYNC_V2_ENABLED=true`

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
  `snapchat_ad_accounts`, `snapchat_account_daily`, or the old analytics engine.
- Provider campaign writes are available only through Mezan's guarded management
  lifecycle. Accounting and Qoyod writes remain separate and are never implied
  by a Snapchat proposal.

## Guarded campaign management v2

Every new campaign, ad squad, ad, or creative follows this lifecycle:

1. immutable preview;
2. explicit approval;
3. one fenced provider execution;
4. provider readback and exact verification;
5. audit and optional verified rollback.

Delivery entities are always created with `status=PAUSED`. Activating an entity
is a separate update that requires an explicit spend acknowledgement and the
independent activation switch.

For an ad squad that uses a `PIXEL_*` optimization goal, the caller must select
an account-scoped `pixel_id` and an explicit `conversion_window`. Mezan reads the
complete Pixel catalogue directly from Snapchat and accepts only an active Pixel
belonging to the selected ad account. It then verifies the exact optimization
goal and conversion window through Snapchat's campaign-eligibility endpoint.
`ELIGIBLE` and `ELIGIBLE_WARNING` are accepted and persisted; incomplete,
ambiguous, inactive, or ineligible results fail closed. The full association and
eligibility check runs once for preview and again after the durable execution
lock immediately before the provider write.

If a delivery-entity create request has an uncertain provider outcome, Mezan
does not retry it. For campaigns, ad squads, and ads, the read-only
reconciliation path scans every bounded, trusted provider page (including
deleted entities), matches the complete immutable intent and parent
relationship, and verifies a single candidate by ID. Multiple matches require
manual review. Absence is accepted only after two complete zero-result scans
separated by at least five minutes; partial results never prove either presence
or absence. Snapchat does not expose the same deleted-aware catalogue contract
for creatives, so an uncertain creative create remains manual-only and is never
automatically adopted or classified as not applied.

## Rolling deployment gate for management v2

Never roll out a management protocol change while provider mutations are
enabled. The safe order is:

1. set `MEZAN_SNAPCHAT_CAMPAIGN_MUTATIONS_ENABLED=false` and verify every live
   replica reports `execution_enabled=false`;
2. deploy the new Git SHA through `scripts/production_release_guard.py` and
   complete its multi-replica verification;
3. verify readiness reports `safety_protocol_version=2` and
   `mutation_rollout_requires_homogeneous_replicas=true` on the homogeneous
   release;
4. enable mutations only after that proof and re-publish the same code SHA;
5. keep `MEZAN_SNAPCHAT_CAMPAIGN_ACTIVATION_ENABLED=false` until an operator has
   explicitly approved an action that can begin spending.

Protocol-v2 previews use `previewed_v2` and `approved_v2`. Older replicas do not
understand these states and therefore cannot execute them during a rolling
deployment.

## Native data synchronization

The **مزامنة 30 يوم** action reads the authorized V2 accounts and synchronizes:

- campaigns;
- ad squads;
- ads;
- creatives;
- campaign-level daily performance;
- account-level daily aggregates.

The bounded data plane uses Snapchat pagination and a provider-call budget. It
stores only V2 analytical collections:

- `mezan_snapchat_entities_v2`
- `mezan_snapchat_performance_daily_v2`

Performance rows preserve account currency and SAR-normalized values. The first
metrics include impressions, swipes, spend, video views, view completion,
purchases, purchase value, CTR, CPC, CPM, ROAS, and cost per purchase. Missing
conversion fields remain unknown and are never converted into false zeros.

The current attribution label is:

`swipe_28d_view_1d_conversion_time`

This sync is source-only: it cannot create, pause, resume, or edit campaigns and
cannot post to accounting or Qoyod.

## Remaining bounded deliveries

The next deliveries add detailed Pixel/Conversions API diagnostics,
billing/invoice/funding-source reads, product/order/profit identity mapping, and
finally campaign mutations behind proposal, preview, approval, verification,
audit, limits, and rollback.
