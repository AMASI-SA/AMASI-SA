# DASHBOARD-CARD-CADENCE-20261111

## State
In progress. Scope and source dependencies verified; implementation and tests underway. Production unchanged by this task.

## Owner request and acceptance
- Normal Products recent Salla sync: hourly automatic admission, with manual/explicit recovery still available.
- Advanced Dashboard Top Products: independent 30-minute automatic snapshot and circular icon-only Arabic-labelled refresh button.
- Period Summary: independent five-hour automatic snapshot and visible Arabic تحديث button.
- Manual refresh must work before the automatic deadline, refresh only the selected card, coalesce overlapping work, retain the last successful same-filter result on failure, and never show old-period numbers under new filters.
- Reduce repeated expensive work without changing financial formulas, frozen cost authority, unknown-versus-zero semantics, provider cadence or preparation-file creation.

## Source baseline
Repository AMASI-SA/AMASI-SA; production branch hotfix/prod-snap-meta-final.
Base J: d3c9b0c946afe2418aecf0b96629e46d7f14f196 (published PR1883).
Task branch: codex/dashboard-card-refresh-cadence-20261111.
Existing Snapchat spend-only release is complete; source/intent identities are preserved.

## Design contract
- Dedicated GET /dashboard-v2/cards/top-products, /period-summary and /executive avoid the legacy client response broker's cross-card interference.
- Existing /dashboard-v2 default contract remains compatible.
- Card TTLs 1800 and 18000 seconds; executive saved read TTL60 seconds. Explicit refresh=true bypasses saved value and joins an already-running calculation.
- Owner/filter/day-scoped bounded caches, authentication before cache lookup, and cache hits before heavy-resource admission. Executive invalidates on product-cost revision.
- Executive omits current-month and rich product-row work; light cost calculations retain the same authoritative financial totals.
- Products recent sync receives persisted owner-scoped hourly admission, atomic bounded lease and finite execution timeout. Browser automatic callers stop forcing the endpoint.
- No live provider, invoice, financial, ad, budget or preparation mutation is used as a test.

## Verification and next steps
Read current AGENTS.md and Issue1006; fetched production to FETCH_HEAD and confirmed exact d3c9b0c946afe2418aecf0b96629e46d7f14f196. The local origin tracking ref is limited by its fetch refspec; use explicit fetched SHAs and do not treat the stale tracking alias as a rollback.
Read-only frontend/backend investigations completed. Focused behavioral tests, independent integrated review, release source A/intent B, CI and authorized publication remain pending.
Before shared /app changes or release rehearsal, freshly require official guard active:false. Preserve other work and leases. Final acceptance requires new explicit deployment success, official verify3, inactive lease and visual/manual card readback.
