# Report-local statement fee profile reuse

## Scope and production status

Base: production merge `0ffa962d3cfda3eb49b5750c7bdc2f932a645d4d`.
Task branch: `codex/financial-report-fee-memo-20261124`.

This patch changes only saved report projection and its regression tests. It
does not change financial calculations, source collection, saved financial
state, release identities, demand budgets, scheduling or authorization. No
production operation, deployment, merge or push was performed by this worker.
The root coordinator owns integration, remote checkpoints and deployment.

## Measured mechanism

Every financial-position view uses the common provider balance projection to
retain complete money and coverage semantics. For orders sharing one saved
statement average profile, the old projection selected and validated the same
profile and rebuilt its description for every order.

On 4,000 synthetic orders sharing one profile, `_valid_average_profile` ran
8,002 times. The candidate runs it four times. The remaining checks include
source disclosure, profile selection and the applied-profile description.

The candidate keeps a selection memo only inside one `provider_balances` call.
The immutable owner and saved profile collection belong to that call. The key
is the exact provider, native currency and merchant account. Both successful
and unavailable selections retain the original validation result. The memo
stops growing at 256 scopes and uses the original selector for further scopes.
Nonstandard owner/provider/currency/payment/account types use the original
selector directly. No value survives another report call.

Each provider total also constructs a validated profile description only once
per profile ID. The original signature binds every profile field. Per-order
policy evaluation, principal, fees, rounding, tax, refunds, FX and settlements
remain unchanged; this patch does not memoize monetary results.

## Verification

The new regression failed against the original code: 60 orders caused 122
validations, exceeding the bounded maximum of four. It passes on the candidate.

The four affected suites passed independently: **184 passed in 11.47 seconds**,
exit status 0. They cover all twelve financial-position views, invalid profile
owners/currencies/providers/accounts/rates/hashes, nonfinite tax, malformed
profile entries, ambiguous scopes, exact account types, multiple providers and
native currencies, a deliberately reduced memo cap, subsequent report
revalidation, money/coverage parity and input preservation.

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend:backend/tests \
  /workspace/scratch/56d90f85c1cc/test-venv/bin/python -m pytest -q -p no:cacheprovider \
  backend/tests/test_operational_provider_report_profile_memo.py \
  backend/tests/test_operational_balance_statement_fees.py \
  backend/tests/test_operational_balance_configured_provider_fees.py \
  backend/tests/test_operational_financial_position_views.py
```

A separate differential experiment loaded `operational_provider_summary.py`
directly from the exact production D commit with `git show`, without replacing
any checkout files. It compared all twelve views on each of the rich, scoped
and forty-order repeated-profile fixtures. **All 36 serialized report outputs
were byte-identical; all fixture inputs remained unchanged.** The large
benchmark fixture also produced byte-identical employee report output.

Independent review found no blocking issue. Reviewed file SHA-256 values:

- Source: `43c56472267cc97537cee35236059c8fcf75acf68cd8647a6a930e3bedba96da`
- New test: `a321bb1c020d5a4e5c0c38f7810925d321a3e7a545781bac9deb7dd54404dc58`

## Synthetic timing evidence

These are local synthetic measurements, not production page-load times. Both
versions used the same process, imported dependencies, state and clock. Five
timed rounds alternated their execution order after warming both paths. Timing
did not use cProfile. The fixture contained 4,000 repeated-profile provider
orders, 5,000 COD obligations and 10,000 movements; compact serialized input was
6,255,750 bytes. It was assembled from the existing pure fixtures
`repeated_state(4000)` and `cod_fixture(movements=10000, claims=5000)`.

| Round | Production D wall ms | Candidate wall ms | Production D CPU ms | Candidate CPU ms |
|---|---:|---:|---:|---:|
| 1 | 1459.59 | 1055.82 | 1459.39 | 1055.31 |
| 2 | 1402.58 | 1074.34 | 1402.42 | 1074.27 |
| 3 | 1519.66 | 1014.70 | 1519.57 | 1014.60 |
| 4 | 1572.92 | 1015.74 | 1572.04 | 1015.69 |
| 5 | 1539.18 | 1176.25 | 1539.09 | 1175.54 |

Median wall time decreased from **1519.66 ms to 1055.82 ms**, a **30.52%**
reduction. Similar wall and CPU measurements support a CPU reduction for this
workload. They do not quantify production savings or memory savings.

## Remaining evidence and next step

The actual production snapshot could not be safely profiled: the configured
preview database returned zero rows for the exact observed production account
anchor, and no official full saved-state export exists. No query was broadened.
Production's earlier common cold-read latency of roughly 13–16 seconds remains
a separately measured issue; this patch has not yet been deployed or measured
there. Root must integrate the immutable patch, run combined checks and the
release protocol, and then compare actual visible readiness and demand CPU
metrics on equivalent saved snapshots. The intervening financial recovery must
be recorded when interpreting before/after production timings.
