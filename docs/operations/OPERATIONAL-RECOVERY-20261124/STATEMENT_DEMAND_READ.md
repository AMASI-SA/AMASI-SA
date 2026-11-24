# Statement reads use the existing background demand contract

## Problem and change

The operational statement screen still called the legacy synchronous statement
GET even though its API adapter and server already supported a demand read.
Large period views and print preparation therefore bypassed the isolated saved
read path used by the other accounting screens.

The screen now explicitly requests that existing demand path. Its current abort
signal also owns the demand consumer, so hiding, leaving, changing filters or
changing the authorization scope releases pending work. A new print request
uses a separate consumer and purpose and waits for a freshly authorized result.
The canonical server statement reducer, complete period pagination, financial
dates, monetary values, access checks and print grants are unchanged. Opening
the panel still performs no statement request until the user requests a view.
The change does not start financial recalculation.

## Verification

The new component suite keeps the real statement API adapter and demand
transport and mocks only the HTTP boundary. All six cases failed against the
previous component, then passed with the one-line demand selection. They cover
202 preparation followed by a complete 301-row result, fresh print preparation,
hidden/unmounted/changed-permission cancellation with exactly one release, and
authorization failure after preparation without stale display or printing.
Fixtures contain synthetic identities, dates and amounts only.

```bash
CI=true python scripts/frontend_release_toolchain.py exec -- \
  yarn --cwd frontend test --watchAll=false --runInBand \
  src/pages/operational/SimpleBalanceStatement.demand.test.jsx \
  src/pages/operational/SimpleBalanceStatement.test.jsx \
  src/pages/operational/simpleBalancesApi.test.js \
  src/lib/demandRead.test.js
```

Result: 4 suites and 53 tests passed in 3.128 seconds. Independent review and
the integrated release gates are recorded in the PR continuation record.
These tests establish behavior, not a production latency measurement; live
period-view, print and navigation timings remain a deployment verification.
