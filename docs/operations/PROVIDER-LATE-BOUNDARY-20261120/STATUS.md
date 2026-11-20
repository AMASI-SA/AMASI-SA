# Late provider payment boundary

Branch: codex/provider-late-payment-boundary-20261120
Base: 734b977de28254c2c089148b4db8972a03ba19eb (PR2031 merged).
User authorized root-cause repair. User reports November 1 opening boundary and
November 15 18:46 Riyadh order creation. Prior screenshot has two held PayPal
orders and a saved calculation at November 20 02:26 Riyadh.

Confirmed code defect: _context ignored unlinked payouts predating creation only
when the order was newer than engine.as_of. The same payment seen after a later
snapshot could therefore be held as historical. Removed snapshot freshness from
that exclusion. Explicit order, credit, allocation links still block before the
date exclusion. Unknown/same/later dates, malformed evidence and prestart orders
still block. No opening/payout mutation, no fee or currency policy change.

Reproduction: 10 parametrized cases failed before the fix (5 providers x SAR/KWD),
9 safety cases passed. After fix + engine idempotency case + existing history and
order-page suites: 114 passed, 1 Mongo HTTP test deselected. No live financial
writes. This code defect is not yet proved to be the cause of the two live holds.

Read-only diagnostic scripts/diagnose_provider_post_start.py reads only owners
matching requested review order numbers, fails if ambiguous, expands archived
state through read(), and prints dates, block codes and scoped payout summaries.
It compares current _context with snapshot-age exclusion disabled in an in-memory
copy, without updating or refreshing stored data. No customer contacts or secrets.
Run against existing /app imports via MEZAN_DIAGNOSTIC_ROOT=/app from a temporary
file; no checkout, application edits, release lock manipulation or mutations.

Production changed by this task: no. No intent frozen. Previous release2031 was
ready_to_publish; screenshot shows new pending UI but official verify output not
received. Do not clear prior lease. Need live diagnostic results to distinguish
chronology defect from stale snapshot, changed evidence or real payout overlap.
Next: obtain scoped read-only diagnostic, then verify targeted reconciliation on
a copied snapshot before any persisted refresh or release preparation. Preserve
safe historical guards. PR is handoff because Issue1006 has reached comment limit.
