# Current-day Snapchat chart and executive profit

Task branch: `codex/snap-zero-source-r2-20261026`. Reviewed starting Production:
`219c4db4a45cbfc2bc5499909c0fcad5e3ddb318` (retains Google1522 and TikTok1524).

## Acceptance and present state

PR1519 was deployed and verified with three Release Guard probes, source
`b21814aaaf6314c5a723a4d858a768f8d3be5437`, deployment
`e6694a7198ab828996852cbf383149d0ab06364f`, runtime
`rg5-f0ac0fa23585221808f842f34aef4f64b5e2bfa1cdc09cceb01c9496dc033c85`.
Its known-subtotal card did not satisfy the owner's chart/profit requirement.
The owner supplied fresh evidence showing the available Snapchat amount while
hourly chart and executive profit remained incomplete.

Supersedes source PR1525: https://github.com/AMASI-SA/AMASI-SA/pull/1525.
The fresh task PR carries release evidence. Initial remote WIP
checkpoint: `e1077221ac75071edb0ae56c19acf4f1c8ccd187`. Final source CI,
governed release and live acceptance are pending. Production has not changed by
this increment. No campaign activation or budget change is a test action.
Saudi campaigns being paused does not disable the account or its reads.

## Bounded implementation

- Separate current-Riyadh-day spend-only native HOUR observation, with direct
  account HOUR first and fixed campaign HOUR fallback only on HTTP400.
- Strict account/window/success/completeness validation; fresh complete empty
  observation means provisional operational no-activity, never authoritative
  financial zero. Future hours stay null. Unknown conversions remain unknown.
- Fifteen-minute request freshness, exact source-run/owner/attempt binding,
  newer-run invalidation, contradictory positive-source veto and snapshot CAS.
- Dashboard-only provisional spend/hour overlay; canonical facts, financial
  status, close proof, ledger and AI evidence remain unchanged.
- At most one same-filter canonical dashboard GET after saved full provisional
  spend becomes available, to recompute profit/fees on the backend. Null and
  known-only subtotals cannot create profit on the client.
- Explicit no-activity/reporting-lag hint only with numeric available
  provisional spend and the validated reason code.

## Fresh evidence

Actual old1519 reader/portfolio regression: four intended behavioral failures,
35 passes, 0.78s; valid collection/imports, not setup failures. Native transport
and actual reader/portfolio/route/storage regressions initially passed76 tests
in1.00s, before the additional canonical-snapshot cases below.

The first broader selection exposed four old heartbeat-fixture failures: its
FakeDB rejected the newly accessed projection collection. The fixture now
models that collection with isolated mongomock storage. All five heartbeat
tests pass in2.08s. No production guard was relaxed.

Independent review found canonical aggregation coercing absent operational
conversion metrics to zero. The bounded Snapchat correction preserves unknown
metrics and the incomplete platform conversion denominator in both executive
builders, including a zero-spend account. Actual stored-fixture tests traverse
reader, portfolio, canonical ads snapshot and both executive builders; Salla
orders/sales and spend stay numeric. The frontend complete-spend transition
also preserves unknown conversion metrics and backend-owned profit/fees.

Fresh final local backend selection:297 passes, one conditional skip,6.61s
(all reporting-v2 tests, multi-account/range proof, four-platform spend,
hourly dashboard and issue727 regressions). Separate existing executive and
inline-fallback suites:7 passes,0.78s. Two inherited Pydantic deprecation
warnings. Frontend tests are selected explicitly by reporting CI; final exact
source CI and live acceptance are still required.

The first exact-source CI run exposed one stale placement assertion shared by
five frontend workflows: it required `const response` after the loader gained
its bounded canonical recomputation and now uses `let response`. The existing
assertion was aligned with that declaration; the date/race/source guards and
runtime loader regressions remain intact. A new source checkpoint and fresh
CI/artifact are required; no intent from the prior source is reusable.

## Release and live acceptance still required

Fresh full affected checks and independent review, current Production overlap
check, exact source A and intent-only B, all final CI checks, actual Emergent
clean-clone rehearsal, normal merge and governed publication. Shared guard was
read as inactive; no lease acquired for this increment.

Normal authenticated current-day sync must prove the actual native account
HOUR shape and persist qualified operational evidence. Then verify numeric
provisional full Snapchat spend, elapsed-hour chart, future nulls, and numeric
server-recomputed executive profit on the live dashboard. Synthetic transport
fixtures alone do not prove provider compatibility. An attempted remote DB
binding was unproven, so it made zero native calls and no writes; it is not a
Production data source.

The Public Profile sharing/read403 remains a separate ad-launch capability
blocker. No wider authorization or real campaign launch is implied here.
Issue1006 has reached its comment limit; the task PR and committed task records
carry continuation evidence.

Production advanced to the reviewed TikTok1524 merge while source CI was running.
Its19 changed paths do not overlap this17-path Snapchat increment. The source integration in PR1525 was rejected by the existing full-history
intent guard: its merge commit changed intent relative to the former task
head. That candidate is not releasable. This fresh source is created directly
from current Production J, with only the17 reviewed Snapchat paths and no
historical task/intent commits between J and A. Its intent remains
byte-identical to J; no protocol guard was weakened. Fresh exact-source CI/artifact is required again; prior-source artifacts
are not deployment inputs.
