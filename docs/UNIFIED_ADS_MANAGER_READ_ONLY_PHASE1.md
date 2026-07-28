# Unified Ads Manager — Read-Only Phase 1

## Status and authority

Status: **Approved narrow exception — observe only**

Approval date: 2026-07-28

The merchant explicitly approved starting a unified advertising view after the
Apps & Integrations Control Center was production-validated. This approval is a
limited exception to the active phase gate in
`docs/PROJECT_DECISIONS.md` Decision-023. It does not advance the general
marketing roadmap and does not authorize a connector, attribution engine,
campaign copilot, or autonomous advertising action.

The exception is valid only while every invariant in this document remains
true. A change that weakens one of them requires a new reviewed decision.

## Purpose

Phase 1 consolidates advertising facts that already exist locally so the store
owner can:

- compare provider-reported spend and performance;
- compare those facts with booked advertising expense;
- inspect freshness, coverage, and source provenance;
- inspect locally available Meta and TikTok campaign rows; and
- receive descriptive, evidence-linked observations.

It is an observability surface. It is not an advertising control plane.

## Feature flag and kill switch

The sole feature flag is:

```text
MEZAN_ADS_MANAGER_READ_ONLY_ENABLED
```

- The approved default is `true`.
- Setting it to `false` is the emergency kill switch.
- When false, the backend returns `404` before reading advertising data.
- The backend is the authoritative gate. A hidden or stale frontend route must
  not bypass it.
- Re-enabling the flag does not grant any mutation capability because the
  Phase 1 router and service remain GET-only.

## Source-of-truth contract

Phase 1 keeps two different facts separate. It must never collapse them into an
unlabelled “true spend” value.

| Fact | Authoritative local source | Meaning |
|---|---|---|
| `provider_reported_spend_sar` | Provider daily facts with defensible SAR evidence | Spend reported by the advertising platform |
| `booked_ad_expense_sar` | Posted debit legs in `general_ledger` for `expense.advertising` | Advertising expense recognized in Mezan's books |

Provider sources currently allowed are:

- Snapchat: `snapchat_account_daily.spend_sar`, with the legacy
  already-converted `spend` alias accepted only for rows written by the same
  local Snapchat ingestion path;
- Meta: `meta_ads_daily`, converted only when row/account currency evidence is
  sufficient;
- TikTok: `tiktok_ads_daily`, treated as a local data feed and converted only
  when currency evidence is sufficient; and
- integration state and freshness: the read-only Apps & Integrations Control
  Center V2 service.

`ad_account_ledger` is a legacy operational source and is not authoritative for
either Phase 1 headline fact. It must not be queried by the new manager.

Rows lacking a defensible provider identity remain unscoped. Rows lacking a
defensible currency or FX rate remain unknown. Neither may be distributed or
converted by inference.

Snapchat conversion facts carry a separate quality contract:

- `conversion_data_status=available` means the conversion request returned
  explicit numeric purchase and revenue fields; an explicit provider zero is
  valid in this state.
- `partial` or `unavailable` means at least one conversion fact was not
  observed. Purchases or revenue remain `null`; a failed request must never be
  persisted or aggregated as zero.
- historical positive values written before the quality marker may remain
  usable because the failed path only fabricated zero. Historical zeroes
  without the marker remain unknown until a fresh sync proves them.

Spend ingestion remains independent: a failed conversion request does not
discard a successfully fetched spend fact.

For a locally configured USD account, the reader uses the stored
`ads_currency_settings.usd_to_sar_rate`. If that settings document has never
been persisted, it uses the existing application policy default `3.7544` and
exposes the evidence as `approved_default`. The rate is never applied until
the account currency is independently established as USD.

## Reconciliation

`gap_sar` is a diagnostic comparison:

```text
provider_reported_spend_sar - booked_ad_expense_sar
```

When exact account-and-day keys match, `comparison_basis` is
`account_day_aligned` and the result may be `matched` or `drift`. Matching
requires the value at every account × day key to be within tolerance; equal
period totals cannot hide offsetting account-level differences.

When both period totals are complete but the account-and-day identities do not
align, the response may still show the arithmetic gap with
`comparison_basis=aggregate_period_only`. Its status remains
`not_comparable`; it is labelled as a period-level diagnostic and must not be
described as an accounting match or settlement. A material period gap may
carry warning severity so it is visible to the owner.

When either total is incomplete, the gap remains `null` and the comparison
basis is `unavailable`.

A reconciliation gap is not an accounting adjustment. Viewing it must never
post, reverse, reconcile, or edit a ledger row.

## Unknown, zero, and partial coverage

- Missing or unprovable facts are `null`, never `0`.
- Zero is valid only when the source explicitly observed zero.
- A failed Snapchat conversion request is unknown even when spend succeeded.
- A disconnected provider is `unavailable`, not zero.
- Daily series preserve `null` gaps.
- Combined totals include only known, currency-safe facts and expose coverage.
- Each provider exposes performance coverage separately from spend freshness.
  Incomplete spend coverage, missing performance days, stale rows, unverified
  zeroes, invalid dates, or truncated reads make the provider ineligible for
  performance ratios.
- Combined ROAS, CPA, CPC, CPM, and CTR remain `null` unless every selected
  provider has complete, current coverage and the exact inputs required for
  that ratio; stale or partial providers are never mixed into a headline
  ratio.
- Platform conversions, purchases, revenue, and ROAS are explicitly
  platform-reported or platform-attributed.
- Platform ROAS is not net profit, causal attribution, or a cross-source truth.

## Allowed operations

Phase 1 may:

- execute the owner-only `GET /api/ads-manager/overview` endpoint;
- read bounded, tenant-scoped local projections;
- filter by provider, date range, and campaign text;
- paginate already-local campaign rows;
- calculate descriptive ratios from available facts; and
- emit evidence-linked observations with an explicit confidence level.

## Forbidden operations

Phase 1 must not:

- call Meta, Snapchat, TikTok, Google, Salla, Qoyod, or shipping APIs;
- sync, relink, refresh, test, or rotate a provider connection or token;
- create, edit, pause, resume, or delete a campaign, ad, creative, audience, or
  budget;
- change targeting, bidding, delivery, or spend;
- insert, update, delete, replace, bulk-write, or index a database collection
  as a consequence of a manager GET;
- post, reconcile, reverse, or modify an accounting movement;
- claim product/order attribution, contribution margin, gross profit, or net
  profit;
- turn a descriptive observation into a recommendation or execution request;
  or
- expose credentials, tokens, cookies, authorization headers, ciphertext, or
  provider payloads.

Any future write requires the lifecycle already fixed by the integrations
control center:

```text
proposal → preview → approval → execution → verification → audit → rollback
```

It also requires a separate approved phase and is not enabled by this feature
flag.

## Access and query bounds

- Backend access is owner-only; frontend visibility is not an authorization
  boundary.
- Every collection query includes the authenticated `user_id`.
- Identical provider account or campaign IDs belonging to another tenant must
  never affect the result.
- Date ranges are valid ISO dates, cannot end in the future, and are limited to
  90 inclusive days.
- Campaign query text is limited to 120 characters.
- Campaign page size is limited to 10–100 rows.
- Raw collection reads have explicit projections and hard upper bounds.
- Stored fact dates are revalidated as exact ISO calendar dates after reading;
  malformed rows are ignored, named in source warnings, and make affected
  totals and daily series unavailable.
- Every bounded reader fetches `limit + 1` to detect truncation. When a source
  exceeds its bound, the response names it and leaves affected totals, ratios,
  daily series, and reconciliation gaps `null`.
- Response models reject undeclared fields.

## Phase 1 response policy

Every successful response carries:

```json
{
  "mode": "observe_only",
  "mutations_allowed": false,
  "advertising_mutations_enabled": false
}
```

The frontend must treat mutation capability as false even if an unexpected
payload claims otherwise.

## Acceptance and regression gates

`backend/tests/test_unified_ads_manager_phase1.py` must prove, without MongoDB
or provider credentials:

- the router exposes one GET-only operation;
- disabling the kill switch returns `404` before any data read;
- employees receive `403` before any data read;
- a manager GET performs zero writes and zero provider-network calls;
- every query is tenant-scoped;
- another tenant's facts never leak;
- provider spend and booked expense remain separate;
- `ad_account_ledger` is not queried;
- missing facts remain `null`;
- incomplete or stale provider coverage suppresses derived performance ratios;
- aggregate-only spend gaps remain visibly distinct from exact account/day
  reconciliation;
- invalid, future, reversed, and over-90-day ranges fail before data access;
- secret-bearing fields and sentinel values do not reach the response; and
- campaign pagination is bounded and deterministic.

`.github/workflows/ads-manager-readonly.yml` is a required PR check. It:

- uses a strict file allowlist for this phase;
- rejects database mutations, provider networking, and non-GET routes through
  an AST guard;
- proves that Snapchat conversion failures remain unknown while explicit
  provider zeroes remain valid;
- invokes both live Snapchat sync route implementations hermetically and
  proves that failed conversions persist as `null + unavailable`;
- constrains operational Snapchat / Ads V2 edits against the reviewed base
  with a route, import, URL, mutation, network, and helper-effect AST delta;
- runs the hermetic manager tests and integrations safety regressions; and
- runs targeted frontend tests plus a production build.

Legacy Ads V2 tests that use stored credentials or mutate a real database are
not part of this gate.

## Verification commands

```bash
cd /app/backend
python -m compileall -q \
  ad_spend_reporting.py \
  ads_manager \
  ads_v2/models.py \
  ads_v2/sync/adapters.py \
  ads_v2/sync/core.py \
  snapchat_routes.py \
  tests/test_snapchat_conversion_quality.py \
  tests/test_unified_ads_manager_phase1.py
PYTHONPATH=. python -m pytest -q \
  tests/test_snapchat_conversion_quality.py \
  tests/test_unified_ads_manager_phase1.py \
  tests/test_mezan_integrations_v2.py \
  --tb=short
```

```bash
cd /app/frontend
corepack enable
corepack prepare yarn@1.22.22 --activate
yarn install --non-interactive
CI=true yarn test --watchAll=false --runInBand <Ads Manager test files>
CI=false yarn build
```

## Rollback

Operational rollback is setting:

```text
MEZAN_ADS_MANAGER_READ_ONLY_ENABLED=false
```

No data rollback is required because a compliant Phase 1 request writes
nothing. If disabling the flag does not return `404` before data access, the
release is non-compliant and must not be deployed.
