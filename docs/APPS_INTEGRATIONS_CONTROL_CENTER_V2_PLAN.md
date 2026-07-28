# Apps & Integrations Control Center V2 — Phase 1 Plan

## Scope

Build one owner-only Mezan OS V2 page at `/integrations-v2` that reports
connection state, accounts, permissions, health, data quality, sync activity,
errors, and AI readiness for:

1. Salla
2. Snapchat Ads
3. TikTok Ads
4. Meta Ads
5. Google Analytics 4
6. Google Search Console
7. Google Merchant Center
8. Google Ads
9. Qoyod
10. Shipping companies (future)

Phase 1 is a control and observability layer. It does not create, edit, pause,
resume, or delete campaigns, budgets, ads, creatives, accounting records,
shipping records, credentials, or tokens.

## Existing Sources — Transitional Read Only

| Provider | Existing read source | Phase 1 treatment |
|---|---|---|
| Salla | `salla_integrations`, `salla_sync_logs` | Read sanitized store, scopes, sync, expiry, and error state. Never copy encrypted tokens. |
| Snapchat Ads | `snapchat_connections`, `snapchat_ad_accounts`, `snapchat_account_daily`, `ads_accounts` | Read connection/account/freshness metadata. Never return plaintext legacy secrets. |
| TikTok Ads | `tiktok_connections` when present, otherwise `tiktok_ads_daily` data feed | Distinguish native connection from `data_feed`; insights may be available without campaign management. |
| Meta Ads | `meta_connections`, `meta_ads_daily`, `ads_accounts` | Read account, connection, expiry, sync, and sanitized error metadata. |
| GA4 | No canonical connection found | Report `not_configured`; do not infer a connection. |
| Search Console | No canonical connection found | Report `not_configured`; do not infer a connection. |
| Merchant Center | No canonical connection found | Report `not_configured`; do not infer a connection. |
| Google Ads | No canonical connection found | Report `not_configured`; do not infer a connection. |
| Qoyod | `qoyod_credentials`, `qoyod_settings`, `qoyod_invoices` | Read credential presence/fingerprint and operational health only. Never call posting paths. |
| Shipping companies | Existing operational shipping settings are not provider integrations | Report `planned`; no migration or write in Phase 1. |

The legacy collections remain untouched and are not the final source of truth.
During transition, providers with an existing connector are read live through
an explicit provider-to-legacy-source allowlist so a stored health check cannot
freeze their current connection state. Providers without a legacy connector
use native V2 snapshots when those become available.

`connection_status` and `connection_provenance` are separate:

- `connection_status` describes operational state (`connected`,
  `data_available`, `needs_reauth`, and so on).
- `connection_provenance` describes what Mezan actually has:
  `api_connection`, `legacy_integration`, `data_feed`, `disconnected`,
  `planned`, or `unknown`.

Current production classification is intentionally explicit: Salla and Meta
are API connections; Snapchat and Qoyod are existing legacy integrations;
TikTok is a Make-fed data feed; Google providers are disconnected; shipping
connectors are planned. Data feeds are never counted as connected.

## New MongoDB Collections

| Collection | Purpose | Key indexes |
|---|---|---|
| `mezan_integrations_v2` | One normalized provider integration per tenant | unique `(user_id, provider)`; `(user_id, connection_status)` |
| `mezan_integration_accounts_v2` | Normalized stores, ad accounts, properties, and future shipping accounts | unique `(user_id, provider, external_account_id)`; `(user_id, provider, connection_status)` |
| `mezan_integration_permissions_v2` | Observed/inferred permission snapshots and missing requirements | unique `(user_id, provider, permission_key)` |
| `mezan_integration_health_v2` | Append-only health and data-quality check results | `(user_id, provider, checked_at desc)`; `(user_id, health_status, checked_at desc)` |
| `mezan_integration_sync_runs_v2` | Append-only normalized sync/test activity | unique `(user_id, run_id)`; `(user_id, provider, started_at desc)` |
| `mezan_integration_errors_v2` | Append-only sanitized provider errors | unique `(user_id, error_id)`; `(user_id, provider, occurred_at desc)` |
| `mezan_campaign_product_links_v2` | Future product → campaign → ad set → ad → creative → landing-page identity graph | unique idempotency key; `(user_id, provider, product_id)`; `(user_id, provider, campaign_id)` |

No credential, access token, refresh token, API key, client secret,
authorization header, raw provider payload, or encrypted ciphertext is stored in
these collections.

The future campaign/product record is explicitly shaped with `link_id`,
`idempotency_key`, `mezan_integration_account_id`, `product_id`, `campaign_id`,
`ad_group_id`, `ad_id`, `creative_id`, `landing_page_url`, performance
metrics, `cost`, `revenue`, `profit`, `currency`, status, and timestamps. Phase
1 creates no endpoint that writes this graph.

## Normalized Account Identity

Every account response uses:

```text
mezan_integration_account_id
provider
external_account_id
store_id
ad_account_id
display_name
currency
timezone
connection_status
connection_provenance
capabilities
permissions
last_sync_at
data_delay_minutes
health_score
source_mode
```

Unknown values remain `null`/`unknown`; they are never converted to zero,
healthy, disconnected, or granted without evidence.

## Capability and Safety Model

Advertising providers expose the complete matrix:

```text
campaigns.read
campaigns.create
campaigns.update
campaigns.pause
campaigns.resume
budgets.read
budgets.update
ads.read
ads.create
ads.update
creatives.read
creatives.create
audiences.read
insights.read
conversions.read
```

Capability states are:

- `available`: implemented and supported by current evidence.
- `approval_required`: provider support may exist, but Mezan has no approved
  execution path yet.
- `blocked_missing_permission`: required permission is absent.
- `blocked_missing_data`: required account/data identity is absent.
- `not_connected`: no connection evidence.
- `planned`: not implemented in Phase 1.
- `unknown`: evidence is insufficient.

All advertising mutations are unavailable in Phase 1 and carry
`approval_required`. A broad provider scope such as `ads_management` does not
enable execution on its own.

Advertising reads are also evidence-based at field level. A generic daily row
does not grant campaign, ad, insight, or conversion access: a real campaign/ad
identity or the corresponding performance/conversion fields must be present in
the sanitized local projection.

The future mutation lifecycle is fixed as:

```text
proposal → preview → approval → execution → verification → audit → rollback
```

## API Contract

All endpoints are authenticated, owner-only, tenant-scoped, bounded, and
secret-safe.

| Method | Path | Behavior |
|---|---|---|
| `GET` | `/api/integrations-v2/overview` | Ten provider cards, summary KPIs, action metadata, and safety policy |
| `GET` | `/api/integrations-v2/capabilities` | Provider capability matrix |
| `GET` | `/api/integrations-v2/sync-runs?provider=&limit=` | Newest-first V2 activity log |
| `GET` | `/api/integrations-v2/errors?provider=&limit=` | Newest-first sanitized errors |
| `POST` | `/api/integrations-v2/{provider}/test-connection` | Explicit read-only provider probe where supported; persists only V2 health/activity snapshots |

There is no Phase 1 endpoint for disconnecting providers or mutating campaigns,
budgets, ads, creatives, accounting, Salla, Qoyod, shipping, employees,
permissions, or product uploads. The UI displays dangerous actions as disabled
with a reason returned by the backend.

## Planned Files

### Backend

```text
backend/integrations_control_center/
  __init__.py
  catalog.py
  models.py
  legacy_readers.py
  service.py
  routes.py
backend/tests/test_mezan_integrations_v2.py
```

Narrow wiring only:

```text
backend/server.py
```

### Frontend

```text
frontend/src/pages/AppsIntegrationsControlCenter.jsx
frontend/src/components/integrationsV2/ProviderMark.jsx
frontend/src/components/integrationsV2/IntegrationCard.jsx
frontend/src/components/integrationsV2/CapabilityMatrix.jsx
frontend/src/components/integrationsV2/IntegrationActivityPanel.jsx
frontend/src/services/integrationsV2.js
frontend/src/services/integrationsV2.test.js
```

Narrow route/navigation wiring only:

```text
frontend/src/App.js
frontend/src/components/Layout.jsx
frontend/src/components/Sidebar.jsx
```

### CI

```text
.github/workflows/apps-integrations-v2.yml
```

The workflow runs deterministic targeted backend tests, frontend service tests,
the production frontend build, Python compilation, and a guard that rejects
changes to protected employee/RBAC/product-upload paths in this PR.

## UI Structure

The single responsive page contains:

1. Header and an explicit read-only/safety banner.
2. Exact classification cards for API connections, existing legacy
   integrations, data feeds, disconnected providers, planned connectors, and
   insufficient/unknown evidence. These buckets always sum to the provider
   total.
3. Provider cards with multi-account support.
4. Capability matrix.
5. Sync and sanitized error activity.

Cards show provider mark, connection status and provenance, linked
account/store, current and
missing permissions, last sync, delay, latest error, integration health, data
quality, test/reconnect/settings/disconnect controls, and explicit AI can/cannot
lists.

The Phase-1 "test" action is labelled as a local inspection. It does not contact
the provider, refresh a credential, or prove current provider reachability.
Unobservable permissions remain `unknown`; absence of a connection or scope
record is not reported as a confirmed permission denial. Permission rows carry
an observation ID, so a newer empty/unknown observation cannot revive stale
“current” or “missing” rows from an earlier local check.

## Protected Boundaries

This branch must not modify:

- product-file upload or product import implementation;
- product preparation implementation;
- employee, team, role, permission, or auth implementation;
- existing Salla, Snapchat, TikTok, Meta, Qoyod, webhook, or shipping write
  paths;
- Qoyod invoice/payment posting or accounting logic;
- stored keys, tokens, secrets, or legacy records.

Before merge, the branch is refreshed against `origin/main`, the protected-path
guard is run, all targeted checks pass, and GitHub reports no merge conflict.
