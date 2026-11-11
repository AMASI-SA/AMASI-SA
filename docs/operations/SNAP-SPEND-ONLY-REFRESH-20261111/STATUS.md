# Snapchat spend-only refresh — final status

**2026-11-11 — COMPLETE: published, live button acceptance passed, official verification passed three times, final release lease inactive.**

## Release identity

| Item | Frozen identity |
| --- | --- |
| Source A | `dcde835e08dbf7c0fe3acb5d2bbe92e7b05588f3` |
| Intent-only B | `a908fb0cff5f9f74b90cfbec03a21295a7cceb6e` |
| Merged production M | `d3c9b0c946afe2418aecf0b96629e46d7f14f196` |
| Pull request | #1883 |
| Verified runtime release | `rg5-2592df843af84da961459fe1f158ae677cb5ecd0173cde83483c97372af8070d` |
| New publication | `9d4db97`, 21:10 Asia/Riyadh |

B contains the frozen release intent for A. The reviewed producer, reader, and button files match merged M. Official production verification established that the observed deployed identity exactly matches the frozen release intent.

## Implemented scope

The dashboard button now requests Snapchat **spend only** for the selected Riyadh date period and the enabled accounts captured when the job starts. It uses account-level hourly statistics with `fields=spend`, without a breakdown. It does not fetch campaign, ad, conversion, or hierarchy statistics. The asynchronous job returns promptly and exposes account outcomes while the page polls for completion.

Successful observations are stored separately and read through the actual unified dashboard reader as provisional spend. Existing currency conversion and bank-fee rules remain authoritative. The refresh does not alter canonical financial facts, reconciliation or closing proofs, campaign metrics, or ledger entries. A newer canonical result replaces a manual observation only when the requested period is ready for display, including the required financial-source and reconciliation evidence.

The frontend checks the job scope, dates, account outcomes, and refreshed dashboard observations before showing completion. A successful provider request alone cannot produce a success message. Partial failures are explicit, previous valid readings remain available, and an unchanged amount is valid when the observation is newly confirmed. Missing hourly data is not interpreted as zero.

## Bounded execution

The job allows at most 10 enabled accounts, a 93-day selected period, seven-day provider windows, and 128 provider HTTP attempts. Limits are 15 seconds per HTTP request, 120 seconds per account, and 300 seconds per job. Frontend polling has a six-minute overall deadline and does not automatically retry the submission request.

Frozen account identity, authorization rechecks, an active-job uniqueness constraint, and revision-fenced claims and recovery prevent duplicate execution and stale workers overwriting newer results. Snapshots are eligible for display for 24 hours, become visibly stale after two hours, and have seven-day storage retention. Unsupported account-hour requests fail explicitly without falling back to heavy campaign synchronization.

## Validation and independent review

| Evidence | Result |
| --- | --- |
| Affected frontend suites | 132 unique tests passed |
| Disposable MongoDB 7 producer-to-dashboard and job tests | 41 passed; zero skipped |
| Existing affected backend reader and operational tests | 87 passed |
| CI workflow runs at exact B | 18 successful; one expected conditional skip |
| Actual shared-adapter frontend build | Passed in 62.84 seconds |

The MongoDB tests exercise the real producer, parser, publication rules, and dashboard reader with provider HTTP responses controlled at the transport boundary. Coverage includes currency/timezone handling, DST, explicit zero versus missing data, partial failures, account disablement during a request, frozen periods, duplicate claims, and canonical supersession. Local validation did not access production data or live providers.

Independent review identified premature supersession by unreconciled canonical data and an unbounded frontend transport wait. Both were corrected and covered by regressions. The reviewed scope had no additional material issue reported before publication.

## Production acceptance

After publication `9d4db97`, the release owner observed one button click at **18:12:21.690 UTC** and visible success at **18:12:40.549 UTC**, success was observed within **18.859 seconds** of the click. Both enabled Snapchat accounts advanced their displayed observation time to **21:12 Riyadh**, and their native spend changed. Other providers' amounts remained unchanged. The provisional label was correct.

This interval is an upper bound from two UI observations for that invocation, not a provider-job latency benchmark or a general performance percentage. No amounts, account identifiers, or customer data are included here.

## Legacy dependencies and exclusions

The older native Snapchat scheduler was retained because reachable consumers still use its daily performance and rich entity snapshots: the ads-manager overview, the native campaign workspace, and management settings on the current Snapchat accounts page. The newer reporting pipeline writes different collections. Dashboard spend migration alone therefore does not establish that the older writer is unused. This button bypasses heavy synchronization; retiring the older writer requires migrating its remaining consumers first.

Preparation batch/file creation and the existing two-hour automatic Snapchat cadence were outside this change and remain unchanged.

## Official release verification — PASSED

The official command `python -B scripts/production_release_guard.py verify --url https://mezansalla.com` completed with exit0 in **676.25 seconds**, returning `verified:true` and `checks:3`. Its output matched A, source base J `4234a88a5e01ce179c5d5711ad80107ecd208a88`, deployment M and the runtime release identity above. Completion was observed at18:22:58UTC on2026-11-11.

The official checks verified runtime source identity, critical-file hashes, backend source, release-control source, frontend build proof, and exact governed public bytes. Canonical and cache-busted build metadata returned the required JSON and content type. Observed boot identity: `2026-11-11T18:09:30.052989+00:00`. Verified frontend artifact tree: `be9dcb70201124024db23b07229bf83654b7ff3e94f02bcc653b22c9bbd147f0`.

The subsequent official status returned **`active:false`** and the wrapper printed `SNAP_1883_VERIFY3_AND_INACTIVE_CONFIRMED`. Only the matching owned lease was closed; no foreign lease was cleared. Raw operator outputs remain at `/tmp/snap-spend-1883-verify.json` and `/tmp/snap-spend-1883-final-status.json` on the shared platform. Sanitized durable evidence is in [verification-summary.json](verification-summary.json), with [publication proof](published.png).

No further source change or publication is required for this task. Frozen A/B are unchanged. This documentation-only branch must never be deployed; production remains the reviewed merge M. Any future legacy retirement must first migrate the active consumers identified above.
