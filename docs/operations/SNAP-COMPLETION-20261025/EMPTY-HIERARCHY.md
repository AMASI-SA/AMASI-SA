# Snapchat provider-proven empty hierarchy

This increment distinguishes a genuinely empty advertising hierarchy from missing or failed reads. It uses the existing Snapchat V2 sync and committed-day reporting path for both USD and SAR accounts. It introduces no second sync, advertising writes, synthetic campaign facts or per-account sales/profit attribution.

The sync may produce `snapchat_empty_hierarchy_v1` only when all three account-wide identity catalogs (campaign, ad squad, ad) were fetched successfully through all pages and returned no entities; the account DAY campaign report completed all requests with no rows; and the hourly campaign response had no campaign rows. A zero amount, stopped account, paused campaign or successful unrelated run is insufficient.

The proof binds the native account ID, currency, timezone, action report time, attribution key and explicit contiguous reporting dates (at most seven). It retains the complete-empty response coverage of the DAY report and catalogs. Only an overall complete producing run with complete financial/identity/hierarchy statuses may atomically publish each account-day snapshot. The snapshot contains explicit empty arrays for all three levels, not zero-valued fake facts. Failed or partial refreshes cannot replace it.

The report, readiness reader and provenance diagnostic accept only the matching complete committed period and its exact producing run. Diagnostics return zero *actual* fact/entity counts, `empty_hierarchy_proven=true`, and `empty_proof_dates`; no metric rows are manufactured. The diagnostic remains read-only, reports `decision_eligibility.eligible=false`, and does not grant financial correctness, attribution, freshness or action permission. Stored empty-proof contents remain internal and do not appear in its public response.

## Remaining conservative cases

- A paused Saudi account may still have campaigns, groups and ads in its catalogs. This increment does **not** mark such an account empty just because spend or DAY activity is zero. Its hierarchy requires real period facts and producing-run proof. If those are absent, readiness remains incomplete and the diagnostic explains the missing evidence.
- A period mixing empty committed days with nonempty fact days remains incomplete under the existing fact-period predicate. Supporting that combination requires a separately reviewed unified per-day proof contract; it must not manufacture fact rows to satisfy the old predicate.
- Old `confirmed_no_data` sync flags without the new committed proof are not upgraded retroactively. Normal future provider reads must establish the evidence.
- Missing permissions, 403 responses, incomplete pagination, unknown catalog scope, currency/timezone mismatch, malformed snapshots, missing producing runs and failed refreshes remain fail-closed.
- No live SAR findings, successful Production deployment or 100% Snapchat capability completion is asserted by this increment. The release coordinator must run the governed deployment protocol and inspect the actual account's bounded provenance response afterward.

## Verification

Local provider/commit/report/readiness regressions cover both SAR and USD native settings, account/owner/timezone/action isolation, no synthetic facts, stale-identity suppression, malformed/missing proofs and preservation across failed refreshes. Provenance includes a dedicated loopback Mongo projection test and must not be called fully verified if that test was skipped.
