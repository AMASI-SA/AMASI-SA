# Track E — native MZ2 advertising accounting

Delivery: **MZ2_ADVERTISING_V2_ACCOUNTING_READY_FOR_REVIEW**.
Draft PR #1218; permanent checkpoint: issue #1006. The checkpoint records the
exact tested HEAD/TREE and fresh CI results, including unrelated failures.

## Scope and isolation

Production base HEAD `5a7b44b71c6c9974aba358493b3267a47d6e6314`, TREE
`87f9a44af5dc7d00fbc62417fb17e2f6273d8d93`. Isolated branch:
`codex/mz2-advertising-v2-accounting-20260930`. Follow-up replaces the daily
owner-click design reviewed at `11c167ae2c51fe9ce93d432cecfacc18675bff21`.

Production writes = 0. Merge = NO. Deploy = NO. Opening Post = NO.
Activation = NO. Release Guard = NOT RUN. No scheduler job deployed.
Frozen tracks #1209/#1212–#1217, central AccountingOnboarding, Stage 10,
P01/P02/G47 and Salla implementation/test/runner files are unchanged.
Test-only opening fixtures run solely in random isolated databases.

## Setup once, automatic normal days

Owner confirms canonical V2 account binding, prepaid/postpaid/hybrid mode,
native advertising expense identity, timezone/close/schedule policy and FX
authority. Wallet/payable identities must be active, same-owner, same-currency
`mz2_financial_accounts` of the appropriate type. No name matching or legacy
identity creation. Hybrid automation requires an explicit fixed wallet fraction.

`AutomationPolicy` pins the binding version and expense identity. Policy versions
are immutable and use version CAS. Setup is a closed typed transaction capability;
it can confirm setup while paused, but cannot mutate any financial ledger,
accounting control, opening or bank collection. Audit failure rolls setup back.

`run_batch(db, owner, as_of=None, limit=100)` resolves eligible dates and calls
`run_day`. A trusted scheduler can invoke this service; no job is installed here.
Daily normal posting requires no spend-approval or owner click. Older manual
endpoints remain optional compatibility paths.

Runner requires a closed day, unique complete fresh V2 evidence, exact
account/currency/timezone provenance, active binding/expense and explicit FX.
It atomically creates an immutable native snapshot, sealed native journal and
sealed economic-day marker. Retry/concurrency produces one journal. Journal,
snapshot, original-currency movement, audit and marker share the owner-serialized
Mongo transaction. Period/lifecycle checks still apply. Missing evidence returns
an exact reason; batch records a deduplicated BLOCKED event without a posted
marker. While writes_paused=true, due inspection is read-only; runner attempts
return HTTP 423 without writing markers.

## Deterministic due policy

Every account explicitly supplies start date, business timezone, schedule
timezone, wall-clock run time, close delay, freshness limit and close contract.

| Platform | Policy |
| --- | --- |
| Meta | 01:00 Asia/Riyadh, after source business-day closure |
| Snapchat | America/New_York business day; explicit schedule after closure |
| TikTok / Google | Proven business timezone and configured schedule; no silent default |

Due time is the first scheduled instant at or after local day-end plus delay.
Evidence must also have been observed after the configured close delay.
Autumn DST uses the later fold; nonexistent spring clock times advance to the
first valid minute. Bounded due resolver excludes sealed POSTED/CLOSED_ZERO days.

## V2 producer evidence

Identity comes only from mezan_integration_accounts_v2. Daily stores:

| Platform | Store / producer |
| --- | --- |
| Snapchat | mezan_snapchat_daily_projections_v2 / snapchat_v2/projections.py |
| Meta | mezan_meta_performance_daily_v2 / meta_native_reporting.py |
| TikTok | mezan_tiktok_performance_daily_v2 / tiktok_native_reporting.py |
| Google | mezan_google_ads_performance_daily_v2 / google_ads_reporting.py |

Each producer carries fingerprinted source_close_proof: explicit spend,
account/date/currency/timezone, observation time, complete-response evidence,
zero confirmation. Meta rejects unconsumed pagination/mismatched rows; TikTok
requires a complete matching account response; Google proves actual customer
metadata and complete stream rows instead of fallback timezone; Snapchat
requires all closed hours, complete coverage and explicit raw metrics.
Missing/null metrics, empty response, synthesized zero, incomplete pages and
ambiguous source runs never prove real zero. Native consumption rechecks proof
against the V2 row and policy. Analytics eligibility flags stay unchanged;
setup policy authorizes a separate native path.

Source revisions hash financial identity/amount/currency/date/timezone;
refresh-only timestamp changes are not new expenses. No legacy reads/writes to
counterparties, ad_account_ledger, ad_accounts, snapchat_ad_accounts, accounts,
or general_ledger. Settings is only native lifecycle/opening proof.

## Foreign wallet and SAR journal

WalletOpening lets Stage 12 confirm original-currency units, evidence, explicit
FX snapshot, native opening transaction ID, SAR amount and effective instant.
It does not execute Opening Post. On use, the service verifies the actual
sealed native SAR opening before materializing its original-currency movement.

mz2_ad_wallet_movements_v2 is append-only and sealed: owner, platform,
integration account, wallet financial identity, currency, signed original
amount, movement type, source ID, business date, FX snapshot, native journal,
version, actor/time/evidence and content hash. No mutable balance authority.

Spending 120 USD from confirmed 1000 USD gives 880 USD and an independent SAR journal
(450 SAR at explicitly approved 3.75). Original units are preserved exactly.
Both original-currency and SAR historical running capacity are checked inside
the transaction; later funding cannot hide a negative historical interval.
History limits fail closed. Hybrid applies the confirmed fraction to original
units and rounded SAR allocation.

SAR uses identity conversion. Foreign FX is an explicitly confirmed fixed rate
with date validity/evidence, or a unique approved daily snapshot. Rate, source,
timestamp, evidence and original amount remain attached. Analytics spend_sar
and implicit rates never supply financial authority.

## Zero and revision lifecycle

An explicitly proved complete closed zero creates sealed CLOSED_ZERO, linked
to platform/account/date/source revision and immutable snapshot. No zero journal.
The day leaves the normal due queue. Missing source is BLOCKED, never zero.

For sealed days, a source-revision reconciliation caller invokes run_day.
The /adjustments/propose route requires an existing posting/zero marker.
It creates an immutable REVIEW_REQUIRED proposal, not a second full expense.
The normal due queue does not rescan posted days; deployment must wire source
revision events or an explicit reconciliation invocation to this service.

Owner exception approval checks current source, prior posting sequence and
sealed evidence. It appends target-minus-prior deltas: 100→110 is +10; 100→90 is -10.
Wallet movements follow original-currency delta. Original journal, old/new
snapshots/revisions, review evidence and audit remain linked. Original-day FX
stays pinned. Proposal identity includes prior sequence, so A→B→A→B works.
Retry is idempotent; stale review is rejected. Original records are not replaced.

## API and integration dependencies

Owner-only prefix /api/accounting-module/advertising-v2:
GET /stage-12; PUT /binding; POST /expense-identity; POST /fx-snapshot;
POST /automation-policy; POST /wallet-opening-evidence; GET /due-items;
POST /automatic-run; POST /adjustments/propose; POST /adjustments/approve.
Compatibility: /daily-source, /spend-approval, /spend-post, /bank-movement.
Fresh persisted owner authority is checked; clients cannot inject runner time.
Stage 12 context supplies binding, original-wallet position, policy, schedule,
readiness and exact missing-contract reasons.

Remaining integration dependencies, not internal E blockers:

1. Track A #1212: bank identity/evidence and posting integration. Funding remains
   fail-closed even if its identity hook alone is replaced. Contract carries
   bank SAR amount, original wallet units/currency, FX snapshot and separate
   bank fee/evidence. No Track A code copied or executed bank transfer claimed.
   Funding/settlement never creates advertising expense.
2. Stage 12/H2 #1217: frontend consumes setup/context routes; central onboarding
   untouched. Deployment orchestration separately enables trusted runner and
   source revision reconciliation after integration.

## Verification and changed files

Dedicated CI checks out exact PR HEAD, starts Mongo 8.0.12 replica set and sets
both MZ2 test URI variables. Tests cover automatic posting, not-due,
incomplete/stale/missing source, pause/no marker, retry/concurrency, foreign
opening/debit/insufficiency/FX, all-platform true zero, positive/negative/repeated
revision adjustments, evidence seals and rollback. Existing sealed-ledger,
financial-account, onboarding and provider regressions run alongside Track E.
Final counts and fresh CI links are recorded in issue #1006. Unrelated Salla
Order Revision P0 failure is reported without modifying its test or runner.

Implementation: backend/accounting_advertising_{contract,setup,sources,bridge,
routes,policy,automation,wallet}.py. Producer proof:
backend/integrations_control_center/ad_daily_close_proof.py,
meta_native_reporting.py, tiktok_native_reporting.py, google_ads_reporting.py;
backend/snapchat_v2/client.py and projections.py. Tests:
backend/tests/test_mz2_advertising_{v2,automation,wallet,policy_review}.py and
test_ad_daily_close_proof.py. Workflow: .github/workflows/mz2-advertising-v2.yml;
this report. Original PR also registers the independent router in backend/server.py.

Fresh integrated local verification: **242 passed, zero skipped**, 97.29s;
all Python changes compile and git diff --check passes. Run the exact pytest
file list in the dedicated workflow's final step with both MZ2_AD_TEST_MONGO_URI
and MZ2_TEST_MONGO_URI set to an isolated replica set. The 14 suites include 83
Track E cases, 76 producer cases and 83 existing native accounting regressions.
