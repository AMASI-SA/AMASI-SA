# Current-day Snapchat chart and executive profit

Task branch: `codex/snap-zero-source-20261026`. Reviewed starting Production:
`cb08def11cdf5ef01ea28df253bd201891b91adc` (includes unrelated Google1522).

## Acceptance and present state

PR1519 was deployed and verified with three Release Guard probes, source
`b21814aaaf6314c5a723a4d858a768f8d3be5437`, deployment
`e6694a7198ab828996852cbf383149d0ab06364f`, runtime
`rg5-f0ac0fa23585221808f842f34aef4f64b5e2bfa1cdc09cceb01c9496dc033c85`.
Its known-subtotal card did not satisfy the owner's chart/profit requirement.
The owner supplied fresh evidence showing the available Snapchat amount while
hourly chart and executive profit remained incomplete.

This increment is WIP, not approved for release. Production has not changed by
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
35 passes, 0.78s; valid collection/imports, not setup failures. New parser and
actual reader/portfolio/route/storage regressions: 76 passes, 1.00s.

Broader affected backend selection: 257 passes, one conditional skip and four
failures in the old heartbeat fixture, which rejected the newly accessed
projection collection. The fixture now models that collection with isolated
mongomock storage; rerun pending. No production guard was relaxed.

Independent review found canonical dashboard aggregation still coercing absent
operational conversion metrics to zero. A bounded Snapchat aggregation and
executive denominator correction plus actual canonical-snapshot regressions
are being authored. This checkpoint is not final acceptance.

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
