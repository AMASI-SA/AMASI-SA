# Snapchat hierarchy readiness provenance diagnostic

This vertical adds a bounded database read for diagnosing why campaign, ad-squad or ad period coverage is incomplete. It does not sync, backfill, repair, call Snapchat, read tokens or environment variables, inspect commerce/customer records, write the database, change readiness gates, or authorize advertising actions. Source mapping was read from GitHub ref `5065c605004595e06606e2bfc99454ec629c733e`; no shared `/app` session was used as Production evidence.

Root must register `attach_snapchat_readiness_provenance_routes(router, db, current_user, require_owner)` in the existing integrations router and include the tests in CI. This module itself makes no registration/deployment claim. With the existing `/api/integrations-v2` router prefix, its proposed read is:

```text
GET /api/integrations-v2/snapchat/readiness-provenance/{account_id}?date_from=2026-10-22&date_to=2026-10-24
```

The owner check runs before account or fact reads. The account must have one connected, API-provenance, explicitly Mezan-selected record in `mezan_integration_accounts_v2`. The active V2 account registry must also exist and be connected, with a valid reporting timezone. Every facts, run and catalog query includes the exact authenticated owner, provider and ad account. Account IDs and inclusive date periods are validated; the maximum period is seven days.

## Grounded source map

| Diagnostic surface | Actual writer and reader contract |
| --- | --- |
| Mezan selection | `integrations_control_center/snapchat_account_selection.py::_selected_account_rows` and `_load_selected_accounts`: `user_id`, `provider=snapchat_ads`, `mezan_selected=True`, connected, `connection_provenance=api_connection`, account ID/metadata. The diagnostic uses a direct allowlisted projection and never enters credential-loading helpers. |
| Reporting account | `snapchat_v2/accounts.py::resolve_account` and `list_enabled_accounts`: active `mezan_snapchat_accounts_v2` registry intersected with Mezan activation, connected reporting metadata. |
| Mutable TOTAL facts | `snapchat_v2/total_facts.py::normalize_total_fact`, `upsert_total_facts`, `load_total_facts`: `mezan_snapchat_daily_total_facts_v2`, exact account/date/entity, account timezone, action report time and attribution key; fact `coverage.status`, `sync_run_id`, `external_id`. |
| Committed reader rows | `snapchat_v2/committed_totals.py::publish_committed_totals`, `committed_period_rows`: `mezan_snapchat_committed_totals_v1`, account/date/timezone/action/attribution, embedded `rows.campaign`, `rows.ad_squad`, `rows.ad`. `load_total_facts` uses a committed date in preference to mutable rows for that date. The diagnostic reproduces that selection and bounds/sanitizes embedded rows in the database projection. |
| Producing-run proof | `snapchat_v2/period_evidence.py::total_period_complete`: every requested date must have actual facts; each fact must have complete coverage and a producing run ID. Each referenced run in `mezan_snapchat_sync_runs_v2` must match owner/provider/account and have overall, financial and relevant level status complete, cover its fact dates, and use the same action report time. A recent unrelated successful run cannot substitute for a referenced producing run. |
| Known coverage metadata | `snapchat_v2/client.py::_coverage` and `fetch_breakdown_daily_total_facts`, `snapchat_v2/sync_pipeline.py::_sync_breakdown_performance` TOTAL path, `sync_runs.py`: per-level status/data state, expected/completed requests, received rows, reason, campaign/parent-match counts. Only these scalar fields, known stage/status/time fields and `last_error.code` are exposed; raw summaries, stage details and provider payloads are omitted. |
| Current identity catalog | `snapchat_v2/entities.py::normalize_entity` and `sync_entities`: active rows in `mezan_snapchat_entity_facts_v2`. Counts are explicitly current catalog counts, not period coverage proof. |
| Actual readiness consumer | `unified_marketing/readers/snapchat_v2.py::load_snapchat_v2_entity_readiness_evidence`: selected V2 account, conversion reporting, default 28-day swipe / 7-day view attribution, `load_total_facts`, then `total_period_complete`. |

## Reading the response

`levels` reports the three hierarchy levels separately, with per-date selected source, observed/exact counts, coverage counts, missing dates and explicit producing-run blockers. `raw_filter_mismatch_counts` identifies rows excluded by reporting timezone, action report time or attribution key. Those mismatch counts are independent and can overlap. Raw counts are bounded observations; exactness is stated explicitly. `producing_runs` contains only referenced, account-scoped rows, while `recent_overlapping_runs` is a separate bounded context sample.

`period_complete` means only the stored hierarchy coverage/producing-run predicate. It is not freshness, amount completeness, profit accounting, attribution acceptance, eligible recommendations or permission to execute. The response always declares decision eligibility false with reason `provenance_diagnostic_only`.

Missing facts never become zero-valued metrics or an empty complete hierarchy. Current committed publication does not represent a provider-confirmed empty hierarchy level. The diagnostic therefore reports `empty_level_proof=unavailable_current_fact_contract` when dates lack rows, even if a recent run says `confirmed_no_data`. It does not infer that actual ads or provider data are absent. Missing reporting metadata is explicitly unavailable. Truncation, malformed committed identities or excessive producing-run cardinality return unavailable proof and null exact counts/completeness, with only bounded observations retained.

The read is not a transaction: concurrent refreshes can change collections between queries. It retains each returned fact's actual producing run and never labels the most recent run as its producer. No result is claimed for the live SAR account until this code is registered, published through the repository release protocol and read in the verified Production runtime.

## Bounds and verification

Each Mongo query has a 1,500 ms server deadline, and the whole diagnostic has an eight-second deadline. The raw TOTAL read is capped at 2,000 rows plus a truncation sentinel; committed arrays are sliced before mapping to at most 2,000 rows plus a sentinel per day/level; the period has at most seven days. Producing-run reads and emitted per-level run evidence are capped at 100; recent overlapping context is capped at five runs. Identity aggregation emits at most three counts. Timeout/database exceptions produce sanitized 504/503 error codes without connection details. No index creation or write-stage aggregation is used.

The new `backend/tests/test_snapchat_readiness_provenance.py` covers producing-run predicates, missing children/no-data conservatism, committed-over-mutable preference, reader filter mismatch, owner/account isolation, malformed embedded identities, truncation, selected account requirements, bounded periods, query/deadline contracts, sanitized failures and owner-only routing. Its self-contained real-Mongo fixture accepts only a dedicated `SNAPCHAT_PROVENANCE_TEST_MONGO_URL` with a loopback hostname, creates a randomly named disposable database, and drops it afterward. `REQUIRE_SNAPCHAT_PROVENANCE_MONGO=1` makes that fixture mandatory in configured CI rather than silently skipping it. The real-Mongo case exercises the actual committed aggregation projection; it imports no autonomy policy module or test fixture.

Suggested focused root check from the repository root:

```text
PYTHONPATH=backend python -m pytest -q backend/tests/test_snapchat_readiness_provenance.py
```

Authoring agent ran no processes or tests. Root owns execution, source checkpoint, route composition, CI, independent review and any publication. Until those complete, executable verification and live SAR findings remain unverified.
